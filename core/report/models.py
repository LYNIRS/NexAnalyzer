"""
Data models for the QC Report feature: a folder-scan result describing a
9-point measurement grid (Raman/PL spectra + OM images), aggregated per-peak
fit statistics computed across a technique's 9 fits, and the per-class summary
of an optical segmentation.

These are the row and scan contracts a module fills in for the report
renderer. They live here, not in a technique package, because the renderer
draws Raman, PL and optical results side by side and `core` must stay unable
to import `modules`.
"""

from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Tuple


@dataclass(frozen=True)
class SampleScan:
    """
    Result of scanning a sample folder for Raman/PL spectra and OM images.

    Attributes
    ----------
    folder : str
        Absolute path to the scanned folder.
    sample_name : str
        Folder's basename, used as the report title.
    raman_files : Dict[int, str]
        Point index -> absolute path, for files matching ``Raman_<N>.txt``.
    pl_files : Dict[int, str]
        Point index -> absolute path, for files matching ``PL_<N>.txt``.
    image_files : Dict[str, Dict[int, str]]
        Magnification label (e.g. "100x") -> {point index -> absolute path}.
    ignored_files : List[str]
        Basenames that matched none of the naming patterns above.
    """

    folder: str
    sample_name: str
    raman_files: Dict[int, str] = field(default_factory=dict)
    pl_files: Dict[int, str] = field(default_factory=dict)
    image_files: Dict[str, Dict[int, str]] = field(default_factory=dict)
    ignored_files: List[str] = field(default_factory=list)

    def magnifications(self) -> List[str]:
        """Magnification labels found, sorted alphabetically."""
        return sorted(self.image_files.keys())


RAW_STAT_LABEL = "Raw"
"""Label marking a `PeakStat` as the empirical row — measured off the spectrum
itself rather than produced by a fit. Reserved: the report renderer keys its
caption off it, and a fitted peak must never use it. The on-screen Fit Results
table and the master CSV label the same quantity the same way."""


@dataclass(frozen=True)
class PeakStat:
    """
    Mean/std/n of one fitted peak's parameters, aggregated across a
    technique's per-point fits (grouped by ``FittedPeak.label``).

    ``intensity_*`` is peak intensity — the maximum of the fitted component
    curve — the same quantity the CSV export and the on-screen Fit Results
    table call "Intensity". It is deliberately *not* ``FittedPeak.area``, the
    area lmfit solves for, which differs by a factor of FWHM x 1.064;
    reporting that here made the report disagree with every other surface while
    using the same column heading.

    ``fwhm_mean``/``fwhm_std`` are None only for a row whose width could not be
    measured — the empirical "Raw" row on a spectrum with no half-maximum
    crossing. The renderer dashes that cell rather than printing a 0.0 that
    would read as a measurement. Fitted peaks always have a width.

    ``fwhm_v1_mean``/``fwhm_v1_std`` are the pre-v3.4.0 Gaussian-only width
    (see ``FittedPeak.width_fwhm_v1``), aggregated the same way as
    ``fwhm_mean``/``fwhm_std``. Always None for the "Raw" row: it has no fit,
    so there is no sigma/gamma to compute the old formula from. Reporting
    surfaces show this only when a caller explicitly opts in — see
    ``build_summary_figure``'s and ``build_sample_results_xlsx``'s
    ``show_fwhm_v1`` parameter.
    """

    label: str
    n: int
    center_mean: float
    center_std: float
    intensity_mean: float
    intensity_std: float
    fwhm_mean: Optional[float]
    fwhm_std: Optional[float]
    fwhm_v1_mean: Optional[float] = None
    fwhm_v1_std: Optional[float] = None


class OpticalClassStat(NamedTuple):
    """One layer class's coverage and contrast, aggregated across a sample's
    segmented frames.

    The optical counterpart of `PeakStat`, and here for the same reason: the
    summary figure prints this table next to the Raman and PL ones, and `core`
    cannot import `modules.optical` to reach a `FrameResult`. The optical
    module builds these rows; the renderer only reads them.

    ``contrast_mean``/``contrast_std`` are None for the reference class, whose
    contrast is measured *against* it and so is not a number that exists. The
    renderer dashes that cell rather than printing a 0.0 that would read as a
    measurement — the same rule `PeakStat` uses for an unmeasurable width.

    """

    label: str
    coverage_mean: float
    coverage_std: float
    contrast_mean: Optional[float]
    contrast_std: Optional[float]
    n_frames: int


class SizePopulationStat(NamedTuple):
    """One size population's pooled stats -- the small or large half of a
    `DomainSizeStat` that cleared `domain_size.MIN_POPULATION_RATIO`.

    Same unit as the `DomainSizeStat` it lives on; there is no separate
    calibration flag here because a sample is calibrated or not as a whole.

    ``density_per_100um2`` is this population's own count over the *same*
    pooled area `DomainSizeStat.area_um2` carries, scaled to flakes per
    (100 um)^2 -- the two populations share one denominator (the sample's
    analyzed area), only their counts differ. `None` when the sample isn't
    calibrated, same as `DomainSizeStat`'s own density.
    """

    label: str  # "small" | "large"
    n: int
    diameter_median: float
    diameter_mean: float
    diameter_std: float
    density_per_100um2: Optional[float] = None


class DomainSizeStat(NamedTuple):
    """Pooled flake domain-size statistics across a sample's segmented
    frames, for the summary page's domain-size table.

    The domain-size counterpart of `OpticalClassStat`, here for the same
    reason: `core` cannot import `modules.optical.processing.domain_size` to
    reach its `DomainResult`, so that module builds this and the renderer
    only reads it.

    ``diameter_median``/``diameter_mean``/``diameter_std`` are in whatever
    unit ``unit`` names -- micrometres when a calibration was available for
    the sample's magnification, pixels when it was not. They are never a
    guessed micron figure standing in for a missing calibration: an
    uncalibrated sample reports real pixel measurements honestly labelled
    "px", not an invented "um".

    ``populations`` is `None` for the ordinary, single-population case --
    every existing caller and test keeps reading the pooled fields above
    unchanged. It holds exactly two `SizePopulationStat` (small, large) when
    `domain_size.split_size_populations` found a real ≥2x gap in the pooled
    diameters; a renderer that wants the split reads this, one that doesn't
    (or predates it) never has to know it exists.

    ``area_um2`` is the sample's total analyzed area -- every frame's own
    `DomainResult.area_um2` summed -- and ``density_per_100um2`` is
    ``(n_domains + EDGE_WEIGHT * n_edge) / area_um2 * domain_size.DENSITY_AREA_UM2``,
    the nucleation
    density scaled to flakes per (100 um)^2 rather than per um2 -- a rate
    over one um2 is a fraction far smaller than one for any real sample.
    Both `None` when the sample isn't calibrated: a density in 1/px² isn't
    comparable across magnifications, so it is omitted rather than invented,
    the same rule every other domain-size figure here already follows.

    ``n_edge`` counts domains touching a frame's analysed border: not sized
    (their size is cut off) and not in ``n_domains`` or any diameter field,
    but counted at one half each (``domain_size.EDGE_WEIGHT``) in
    ``density_per_100um2``.
    """

    n_domains: int
    n_frames: int
    diameter_median: float
    diameter_mean: float
    diameter_std: float
    unit: str
    calibrated: bool
    populations: Optional[Tuple[SizePopulationStat, SizePopulationStat]] = None
    area_um2: Optional[float] = None
    density_per_100um2: Optional[float] = None
    n_edge: int = 0
