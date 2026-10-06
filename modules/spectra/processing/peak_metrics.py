"""
Metrics derived from a fit result: the per-peak numbers every reporting
surface displays, and their aggregation across a set of fits.

Two quantities, two names, used consistently everywhere:

- **intensity** — the fitted curve's maximum, via `peak_intensity_and_stderr`.
  This is what every reporting surface shows, because it's the number
  spectroscopists read off a plot: the on-screen Fit Results table, the
  exported CSVs, the QC Report's summary tables and its LA/E2g+A1g ratio
  all agree on it.
- **area** — the area under the peak, `FittedPeak.area`, which is intensity x
  FWHM x 1.064 for a Voigt. It is what lmfit solves for and what lmfit itself
  calls "amplitude", which is exactly why it is named `area` here.

The two diverge whenever peaks have unequal widths: WSe2's LA mode is ~6x
broader than E2g+A1g, so their area ratio is ~4.3x their intensity ratio. The
report used to show area under an "Amplitude" heading while the CSV used that
same heading for intensity, and the two surfaces disagreed.

Don't reintroduce that. "Amplitude" names neither quantity anywhere in the
codebase; a caller that wants the integrated quantity takes `FittedPeak.area`
and labels it "area".
"""

from typing import List, NamedTuple, Optional, Sequence, Tuple

import numpy as np

from ..models.peak import FitResult
from core.report.models import RAW_STAT_LABEL, PeakStat


def peak_intensity_and_stderr(peak) -> Tuple[float, float]:
    """
    A fitted peak's intensity and the standard error on that intensity.

    Intensity is the maximum of the fitted component curve, not
    `FittedPeak.area` (the area lmfit solves for). The reported stderr is
    `area_stderr` rescaled by the same intensity/area ratio — a linear
    approximation, but the only sensible one without re-propagating the
    covariance matrix.

    Falls back to the area and its stderr when no component curve was
    generated (e.g. a fit that converged without per-peak curves).
    """
    curve = getattr(peak, "component_curve", None)
    if curve is None or len(curve) == 0:
        return float(peak.area), float(peak.area_stderr)

    intensity = float(np.max(curve))
    if peak.area > 0:
        return intensity, float(peak.area_stderr) * (intensity / peak.area)
    return intensity, 0.0


def peak_intensity(peak) -> float:
    """A fitted peak's intensity — see peak_intensity_and_stderr()."""
    return peak_intensity_and_stderr(peak)[0]


class RawPeakStats(NamedTuple):
    """Fit-free stats read straight off a spectrum: the tallest point and the
    width of the spectrum at half that intensity. `fwhm` is None when the
    half-maximum crossing can't be measured (flat or non-positive signal)."""

    intensity: float
    center: float
    fwhm: Optional[float]


def raw_peak_stats(x: np.ndarray, y: np.ndarray) -> Optional[RawPeakStats]:
    """
    Measure the raw spectrum's dominant peak without reference to any fit.

    Used for PL, where the emission peak's raw intensity/position/width is
    reported alongside the fitted peaks — a sanity check on the fit and the
    number some instrument software quotes. Returns None for an empty
    spectrum.
    """
    if len(y) == 0:
        return None

    imax = int(np.argmax(y))
    intensity = float(y[imax])
    center = float(x[imax])

    fwhm: Optional[float] = None
    if intensity > 0:
        above = y >= intensity / 2.0
        if above.any():
            idxs = np.where(above)[0]
            fwhm = float(x[idxs[-1]] - x[idxs[0]])

    return RawPeakStats(intensity=intensity, center=center, fwhm=fwhm)


R_SQUARED_MIN = 0.5
"""Fits at or below this R-squared are dropped before anything is aggregated.

Inherited from the WSe2 analysis scripts, which gate every Raman and PL fit
this way before plotting or summarizing. A spectrum whose fit explains less
than half the variance is not a measurement of anything; averaging it in moves
a reported mean without moving `n`, so the number looks better-supported than
it is.
"""

IQR_FENCE = 1.5
"""Tukey fence multiplier for the per-point outlier cut. Also inherited."""

NEAR_ZERO_FRACTION = 0.02
"""A peak under this fraction of the strongest peak in its own fit was fitted to
zero: it still reports a centre and a width, but with no height those
parameters are free and describe nothing.

PL only. The QC Report's PL summary table (`aggregate_fit_results`) and its PL
quality figure (`peak_quality.PL_CLEANING`) both read this one value, so the
table's `n` and the figure's points cannot disagree. MoS2's A0 is the case that
needs it: on red-shifted wafers the trion carries the whole peak and A0 fits to
nothing on a third of the points.
"""


def iqr_mask(values: np.ndarray) -> np.ndarray:
    """
    A boolean mask keeping values inside 1.5x Tukey fences.

    Returns all-True when the interquartile range is zero, which is what makes
    this safe on small samples: a single measurement, or several identical
    ones, has no spread to judge an outlier against, so nothing is dropped.
    That is the property that leaves one-spectrum-per-point samples — every
    sample the app could fit before multi-spectrum parsing — numerically
    untouched by this filter.
    """
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return np.zeros(0, dtype=bool)
    q1, q3 = np.percentile(values, [25, 75])
    iqr = q3 - q1
    if iqr <= 0:
        return np.ones(values.size, dtype=bool)
    return (values >= q1 - IQR_FENCE * iqr) & (values <= q3 + IQR_FENCE * iqr)


def filter_fits_by_quality(
    fits_by_point: Sequence[Tuple[int, FitResult]],
    r_squared_min: float = R_SQUARED_MIN,
) -> List[Tuple[int, FitResult]]:
    """
    Drop fits whose R-squared is at or below `r_squared_min`, preserving order.

    Kept a separate, named step rather than folded into the aggregation so the
    gate is visible at the call site and so every downstream summary — the
    stats tables, the intensity ratios, the quality figure — is demonstrably
    working from the same surviving set.
    """
    return [(point, fit) for point, fit in fits_by_point if fit.r_squared > r_squared_min]


def _clean_pool(by_point: dict) -> np.ndarray:
    """Concatenate every point's IQR-surviving values into one pooled array."""
    kept = [np.asarray(v, float)[iqr_mask(v)] for v in by_point.values() if len(v)]
    return np.concatenate(kept) if kept else np.zeros(0)


def _mean_std(pooled: np.ndarray) -> Tuple[float, float]:
    if pooled.size == 0:
        return 0.0, 0.0
    if pooled.size == 1:
        return float(pooled[0]), 0.0
    return float(np.mean(pooled)), float(np.std(pooled, ddof=1))


def aggregate_fit_results(
    fits_by_point: Sequence[Tuple[int, FitResult]],
    relative_intensity_floor: Optional[float] = None,
) -> List[PeakStat]:
    """
    Group fitted peaks by label across `(point, fit_result)` pairs and compute
    mean/std/n of center, intensity, and width_fwhm for each label.

    Intensity, not `FittedPeak.area` — see the module docstring.

    Takes pairs rather than bare fits because outlier removal is **per point**:
    a multi-spectrum file contributes many fits at one grid position, and the
    spread that matters is within a position, not across the wafer. Cleaning
    across the whole sample would mistake real position-to-position variation
    for noise and delete it — which is exactly the variation the report exists
    to show.

    Values outside 1.5x Tukey fences within their own point are excluded from
    each metric independently, since a fit can be sound in center and wild in
    width. `n` counts the fits contributing that peak, before that per-metric
    cut, so it reports measurements taken rather than a different number for
    every column.

    Label order follows first-seen order. Standard deviation uses ddof=1 when
    more than one value survives, else 0.0. Callers should pass only successful
    fits, already gated by `filter_fits_by_quality`; neither `FitResult.success`
    nor R-squared is re-checked here.

    `relative_intensity_floor` (the QC Report passes `NEAR_ZERO_FRACTION` for
    PL) skips a peak whose intensity is under that fraction of the strongest
    peak in the same fit. It is skipped whole -- centre, width, intensity and
    `n` -- so `n` counts the points where the peak was found, and the intensity
    mean is over those points.
    """
    metrics: dict = {}
    counts: dict = {}
    order: List[str] = []

    for point, fit_result in fits_by_point:
        floor = None
        if relative_intensity_floor is not None and fit_result.fitted_peaks:
            floor = relative_intensity_floor * max(
                peak_intensity(p) for p in fit_result.fitted_peaks
            )
        for peak in fit_result.fitted_peaks:
            if floor is not None and peak_intensity(peak) < floor:
                continue
            label = peak.label
            if label not in metrics:
                metrics[label] = {"center": {}, "intensity": {}, "fwhm": {}, "fwhm_v1": {}}
                counts[label] = 0
                order.append(label)
            counts[label] += 1
            for key, value in (
                ("center", peak.center),
                ("intensity", peak_intensity(peak)),
                ("fwhm", peak.width_fwhm),
            ):
                metrics[label][key].setdefault(point, []).append(value)
            # None for a FittedPeak built outside fit_voigt_peaks -- excluded
            # rather than pooled as 0.0, same as the "Raw" row's unmeasurable
            # fwhm.
            if peak.width_fwhm_v1 is not None:
                metrics[label]["fwhm_v1"].setdefault(point, []).append(peak.width_fwhm_v1)

    stats = []
    for label in order:
        center_mean, center_std = _mean_std(_clean_pool(metrics[label]["center"]))
        intensity_mean, intensity_std = _mean_std(_clean_pool(metrics[label]["intensity"]))
        fwhm_mean, fwhm_std = _mean_std(_clean_pool(metrics[label]["fwhm"]))
        if metrics[label]["fwhm_v1"]:
            fwhm_v1_mean, fwhm_v1_std = _mean_std(_clean_pool(metrics[label]["fwhm_v1"]))
        else:
            fwhm_v1_mean, fwhm_v1_std = None, None
        stats.append(PeakStat(
            label=label,
            n=counts[label],
            center_mean=center_mean,
            center_std=center_std,
            intensity_mean=intensity_mean,
            intensity_std=intensity_std,
            fwhm_mean=fwhm_mean,
            fwhm_std=fwhm_std,
            fwhm_v1_mean=fwhm_v1_mean,
            fwhm_v1_std=fwhm_v1_std,
        ))

    return stats


def aggregate_raw_peak_stats(spectra) -> Optional[PeakStat]:
    """
    The empirical PL measurement, aggregated across a sample's points: the
    tallest point of each processed spectrum, with no fit involved.

    Returned as a `PeakStat` labelled "Raw" so it drops straight into the
    report's summary table alongside the fitted peaks. This is the same
    fit-free quantity the on-screen Fit Results table and the master CSV
    already show as a "Raw" row; the three now agree.

    Read off `processed_data` — after de-spiking and baseline correction, the
    same layer the fit sees — so its intensity is comparable to the fitted
    intensities in the rows below it, not inflated by a baseline offset.

    `fwhm_mean`/`fwhm_std` are None when no point yielded a half-maximum
    crossing (`raw_peak_stats` returns None there). Averaging over the points
    that did measure would report a width from a subset while `n` claimed the
    full count, and defaulting to 0.0 would print a fake measurement, so the
    cell is dashed out instead.

    Returns None when no spectrum yields a raw peak at all.
    """
    intensities: List[float] = []
    centers: List[float] = []
    fwhms: List[float] = []

    for spectrum in spectra:
        data = getattr(spectrum, "processed_data", None)
        if data is None:
            continue
        raw = raw_peak_stats(data.X, data.Y)
        if raw is None:
            continue
        intensities.append(raw.intensity)
        centers.append(raw.center)
        if raw.fwhm is not None:
            fwhms.append(raw.fwhm)

    n = len(intensities)
    if n == 0:
        return None

    ddof = 1 if n > 1 else 0
    fwhm_mean = float(np.mean(fwhms)) if len(fwhms) == n else None
    if fwhm_mean is None:
        fwhm_std = None
    else:
        fwhm_std = float(np.std(fwhms, ddof=ddof)) if n > 1 else 0.0

    return PeakStat(
        label=RAW_STAT_LABEL,
        n=n,
        center_mean=float(np.mean(centers)),
        center_std=float(np.std(centers, ddof=ddof)) if n > 1 else 0.0,
        intensity_mean=float(np.mean(intensities)),
        intensity_std=float(np.std(intensities, ddof=ddof)) if n > 1 else 0.0,
        fwhm_mean=fwhm_mean,
        fwhm_std=fwhm_std,
    )


RAMAN_RATIO_PAIRS: Tuple[Tuple[str, str], ...] = (("LA", "E2g+A1g"), ("C", "LB"))
"""The WSe2 bilayer quality indicators, as (numerator, denominator) labels.

- ``LA / E2g+A1g`` — the defect ratio: disorder relative to the in-plane mode.
- ``C / LB`` — the stacking ratio: the shear mode against the layer-breathing
  mode. Both are interlayer vibrations, so their ratio speaks directly to how
  the two layers sit on each other, which is what a bilayer wafer is judged on.

One constant, because two surfaces read it: the QC Report's Raman summary table
and the Raman quality figure's ratio panels. They were separate literals until
v5.0.0 — one in the page, one in the figure module — with a comment in each
saying they had to agree, which is not a mechanism. A figure and a table
disagreeing about which ratios matter is the exact failure that comment feared.

B2g / E2g+A1g was reported until v3.10.0 and was dropped in favour of the
stacking ratio. Any pair whose labels aren't both present in a fit is dropped
automatically (see `compute_peak_intensity_ratio`), so this list is safe for
materials that don't define these peaks.
"""


def compute_peak_intensity_ratio(
    fit_results: List[FitResult], numerator_label: str, denominator_label: str
) -> Optional[Tuple[float, float, int]]:
    """
    The per-point peak-intensity ratio `numerator_label` / `denominator_label`
    (e.g. "LA" / "E2g+A1g" for WSe2 Raman), summarized across `fit_results`.

    The ratio is formed per point first, then summarized — not
    mean(numerator) / mean(denominator) — so it describes the actual
    point-to-point ratio rather than a ratio of two separately averaged
    numbers. On a 9-point grid the two agree to well under a percent, but only
    the per-point form has a meaningful spread attached.

    Summarized by **median and median absolute deviation**, not mean and
    standard deviation: one badly fitted point moves a 9-point mean noticeably,
    and a ratio of two fitted quantities is exactly where that happens.

    Intensities, not `FittedPeak.area` — see the module docstring; for peaks
    of unequal width the two ratios differ by a large factor.

    Points missing either peak (or with a zero denominator) are skipped.
    Returns (median, mad, n), or None if no point has both peaks.
    """
    ratios = []
    for fit_result in fit_results:
        intensities = {peak.label: peak_intensity(peak) for peak in fit_result.fitted_peaks}
        numerator = intensities.get(numerator_label)
        denominator = intensities.get(denominator_label)
        if numerator is not None and denominator:
            ratios.append(numerator / denominator)

    if not ratios:
        return None

    values = np.asarray(ratios, dtype=float)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return (median, mad, len(ratios))
