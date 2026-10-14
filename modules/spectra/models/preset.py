"""
Data models for workflow preset system.

This module provides dataclasses for material-specific processing presets
that enable automated workflow execution.
"""

from dataclasses import dataclass, field, fields
from typing import Dict, List, Tuple, Optional
from .peak import MAX_PEAKS, PeakDefinition, link_errors
from .spectrum import DESPIKE_THRESHOLD_MAX, DESPIKE_THRESHOLD_MIN

#: Layers an optical block may be keyed by. The stored form, never the words
#: `contrast.layer_word` prints for them.
OPTICAL_LAYERS = ("1L", "2L")


def parse_exclusion_ranges(exclusion_str: Optional[str]) -> List[Tuple[float, float]]:
    """
    Parse exclusion ranges from string format.

    Parameters
    ----------
    exclusion_str : str or None
        Format: "min1-max1; min2-max2; ..."
        Example: "1200-1400; 2600-2800"

    Returns
    -------
    list of tuple
        List of (x_min, x_max) tuples

    Raises
    ------
    ValueError
        If format is invalid
    """
    if not exclusion_str:
        return []

    ranges = []
    for pair in exclusion_str.split(';'):
        pair = pair.strip()
        if not pair:
            continue

        parts = pair.split('-')
        if len(parts) != 2:
            raise ValueError(f"Invalid exclusion range format: '{pair}'. Expected 'min-max'")

        try:
            x_min = float(parts[0].strip())
            x_max = float(parts[1].strip())
        except ValueError:
            raise ValueError(f"Invalid numbers in exclusion range: '{pair}'")

        if x_min >= x_max:
            raise ValueError(f"Invalid exclusion range: x_min ({x_min}) must be < x_max ({x_max})")

        ranges.append((x_min, x_max))

    return ranges


@dataclass
class PeakTemplate:
    """
    Peak template with center tolerance for flexible fitting.

    This is used in presets to define initial peak guesses with
    flexible center position bounds (center ± tolerance).

    The three link fields tie this peak to another of the same block -- e.g.
    a trion 30 meV below its exciton and never brighter than it. They default
    to None, and an unlinked template serializes exactly as it did before they
    existed: `preset_staleness` hashes `to_dict()`, so writing three nulls into
    every template would have marked every existing fit stale. See
    `peak.link_errors` for the rules.
    """
    peak_label: str
    center: float
    center_tolerance: float  # ± tolerance for center bounds
    width_fwhm: float
    shape: float  # Voigt mixing: 0=Gaussian, 1=Lorentzian
    color: str  # Hex color for plotting
    linked_to: Optional[str] = None  # label of the reference peak
    energy_offset_meV: Optional[float] = None  # this peak's E minus the reference's
    max_intensity_ratio: Optional[float] = None  # intensity ≤ ratio × reference's

    _LINK_FIELDS = ("linked_to", "energy_offset_meV", "max_intensity_ratio")

    def link(self) -> tuple:
        """(linked_to, energy_offset_meV, max_intensity_ratio), for `link_errors`."""
        return tuple(getattr(self, f) for f in self._LINK_FIELDS)

    def to_peak_definition(
        self,
        mode: str,
        x_range: Tuple[float, float],
        y_max: float,
        spectral_resolution: float
    ) -> PeakDefinition:
        """
        Convert template to PeakDefinition with auto-calculated bounds.

        The template's `center_tolerance` is carried onto the PeakDefinition
        rather than baked into its bounds, because `fit_voigt_peaks` re-runs
        `calculate_auto_bounds` and would otherwise overwrite them with the mode
        default.

        Parameters
        ----------
        mode : str
            "Raman" or "PL" (affects default bounds calculation)
        x_range : tuple
            (x_min, x_max) of spectrum
        y_max : float
            Maximum Y value of spectrum
        spectral_resolution : float
            Median spacing between X points

        Returns
        -------
        PeakDefinition
            Configured peak definition ready for fitting
        """
        peak = PeakDefinition(
            center=self.center,
            # Placeholder: PeakDefinition.intensity is never consulted by the
            # fitter (auto-estimated from data at fit time; see PeakDefinition's
            # own docstring), so PeakTemplate has no intensity field of its own.
            intensity=1.0,
            width_fwhm=self.width_fwhm,
            label=self.peak_label,
            shape=self.shape,
            color=self.color,
            center_tolerance=self.center_tolerance,
            linked_to=self.linked_to,
            energy_offset_meV=self.energy_offset_meV,
            max_intensity_ratio=self.max_intensity_ratio,
        )

        # Calculate auto-bounds using existing logic. This reads
        # center_tolerance, so the bounds it writes are the template's.
        peak.calculate_auto_bounds(mode, x_range, y_max, spectral_resolution)

        return peak

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export. Link fields only when set."""
        out = {
            "peak_label": self.peak_label,
            "center": self.center,
            "center_tolerance": self.center_tolerance,
            "width_fwhm": self.width_fwhm,
            "shape": self.shape,
            "color": self.color,
        }
        out.update({f: getattr(self, f) for f in self._LINK_FIELDS if getattr(self, f) is not None})
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "PeakTemplate":
        """Deserialize from dictionary."""
        return cls(**data)


@dataclass
class TechniquePreset:
    """Processing settings and peak templates for one technique of a material.

    This is everything the old flat `MaterialPreset` held except the fields
    that identify the material rather than the measurement (`material_name`,
    `enabled`, `description`) and `mode`, which is no longer stored at all: a
    block's technique is the name of the slot it sits in.

    Every field is defaulted, so a block can be created empty and filled in by
    the editor.
    """

    # X-range settings
    x_range_enabled: bool = False
    x_min: Optional[float] = None
    x_max: Optional[float] = None

    # De-spiking settings
    despike_threshold: float = 8.0

    # Baseline settings
    baseline_algorithm: str = "None (Skip)"  # "Polynomial", "ALS", "None (Skip)"
    baseline_degree: Optional[int] = None    # For Polynomial
    baseline_lambda: Optional[float] = None  # For ALS
    baseline_p: Optional[float] = None       # For ALS

    # Peak templates
    peak_templates: List[PeakTemplate] = field(default_factory=list)

    #: Fit iteration budget (lmfit's `max_nfev`). Per technique, not per
    #: material: a PL doublet on a noisy background and a two-peak Raman
    #: spectrum converge at very different speeds, and the block is where every
    #: other fit setting already lives. Until v5.2.0 this was a sidebar slider
    #: living in session state, which meant the number that produced a fit was
    #: never written down anywhere -- not in the preset, not in the QC report,
    #: not in the CSV -- so a run could not be reproduced from its preset alone.
    max_iterations: int = 2000

    # Semicolon-separated ranges (e.g., "1200-1400; 2600-2800")
    exclusion_ranges: Optional[str] = None

    def validate(self, mode: Optional[str] = None) -> List[str]:
        """
        Error messages for this block alone (empty list if valid).

        The caller prefixes each with the technique name, since a material
        reports all of its blocks' errors together. `mode` is that technique
        ("Raman"/"PL"), when known: a peak link's energy offset is PL-only.
        """
        errors = []

        # Despike threshold
        if not (DESPIKE_THRESHOLD_MIN <= self.despike_threshold <= DESPIKE_THRESHOLD_MAX):
            errors.append(
                f"despike_threshold {self.despike_threshold} out of range "
                f"[{DESPIKE_THRESHOLD_MIN}, {DESPIKE_THRESHOLD_MAX}]"
            )

        # Fit iteration budget
        if not (500 <= self.max_iterations <= 20000):
            errors.append(
                f"max_iterations {self.max_iterations} out of range [500, 20000]"
            )

        # Baseline algorithm
        valid_algos = ["Polynomial", "ALS", "None (Skip)"]
        if self.baseline_algorithm not in valid_algos:
            errors.append(
                f"Invalid baseline_algorithm: {self.baseline_algorithm} "
                f"(must be one of {valid_algos})"
            )

        # Algorithm-specific validation
        if self.baseline_algorithm == "Polynomial":
            if self.baseline_degree is None or not (1 <= self.baseline_degree <= 10):
                errors.append(
                    f"Polynomial algorithm requires baseline_degree in [1, 10], "
                    f"got {self.baseline_degree}"
                )

        if self.baseline_algorithm == "ALS":
            if self.baseline_lambda is None or not (1000 <= self.baseline_lambda <= 1000000):
                errors.append(
                    f"ALS algorithm requires baseline_lambda in [1e3, 1e6], "
                    f"got {self.baseline_lambda}"
                )
            if self.baseline_p is None or not (0.001 <= self.baseline_p <= 0.1):
                errors.append(
                    f"ALS algorithm requires baseline_p in [0.001, 0.1], "
                    f"got {self.baseline_p}"
                )

        # X-range validation
        if self.x_range_enabled:
            if self.x_min is None or self.x_max is None:
                errors.append("x_range_enabled=True requires both x_min and x_max")
            elif self.x_min >= self.x_max:
                errors.append(f"x_min ({self.x_min}) must be < x_max ({self.x_max})")

        # Peak templates validation
        if len(self.peak_templates) == 0:
            errors.append("At least one peak template required")
        if len(self.peak_templates) > MAX_PEAKS:
            errors.append(
                f"Maximum {MAX_PEAKS} peaks allowed (got {len(self.peak_templates)})"
            )

        # Validate individual peak templates
        for i, template in enumerate(self.peak_templates):
            if template.center_tolerance <= 0:
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): center_tolerance must be > 0"
                )
            if template.width_fwhm <= 0:
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): width_fwhm must be > 0"
                )
            if not (0.0 <= template.shape <= 1.0):
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): shape must be in [0.0, 1.0]"
                )
            # Validate hex color format
            if not (template.color.startswith('#') and len(template.color) == 7):
                errors.append(
                    f"Peak {i+1} ({template.peak_label}): color must be hex format #RRGGBB"
                )

        errors.extend(link_errors(
            [t.peak_label for t in self.peak_templates],
            [t.link() for t in self.peak_templates],
            mode,
        ))

        return errors

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "x_range_enabled": self.x_range_enabled,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "despike_threshold": self.despike_threshold,
            "baseline_algorithm": self.baseline_algorithm,
            "baseline_degree": self.baseline_degree,
            "baseline_lambda": self.baseline_lambda,
            "baseline_p": self.baseline_p,
            "peak_templates": [t.to_dict() for t in self.peak_templates],
            "exclusion_ranges": self.exclusion_ranges,
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TechniquePreset":
        """Deserialize from dictionary."""
        data = dict(data)
        data["peak_templates"] = [
            PeakTemplate.from_dict(t) for t in data.get("peak_templates", [])
        ]
        return _construct(cls, data, what="technique block")


@dataclass
class OpticalParams:
    """
    Optical-contrast tuning for one layer of one material.

    Every field is `None` by default, meaning "use contrast.py's own default".
    That is deliberate: the numbers live in exactly one place, and a material
    with no optical block produces `analyse_frame(**{})`, which is the vendored
    algorithm untouched.

    `mask_margin` and `ff_divisor` are **not** circular-only, despite
    docs/OM_Contrast_Algo.md quoting circular figures for both. Whether a frame
    is circular is decided per image at runtime by `contrast.is_circular`, so a
    preset has no way to address one frame type; an override applies to both,
    and naming it otherwise would promise a precision that cannot exist.

    `ff_divisor` is a **divisor**, not a sigma: the flat-field blur runs at
    max(H, W) / ff_divisor, so a larger number means a *smaller* sigma.

    `marker_margin_sigma`, `marker_min_distance`, and `mask_closing_radius`
    are not part of `_KWARGS` and never reach `analyse_frame`/`classify()` --
    they tune `domain_size.measure_domains()`'s watershed pass instead,
    domain-size-only settings layered on top of the same segmentation. Kept
    on this dataclass rather than a new one because they are still per
    material and layer (a thin, faint 1L flake and a bolder 2L flake can need
    different marker sensitivity, spacing, or edge closing), which is exactly
    what this dataclass already exists to carry.
    """

    nsigma: Optional[float] = None
    minpx: Optional[int] = None
    mask_margin: Optional[float] = None
    ff_divisor: Optional[float] = None
    abs_threshold_below: Optional[float] = None
    abs_threshold_above: Optional[float] = None
    #: How many sigma_noise past a class's own boundary a watershed marker
    #: must clear -- lower catches weaker, flatter domains at the risk of
    #: more noise-driven markers. `None` means
    #: `domain_size.MARKER_MARGIN_SIGMA` (0.5). Not passed to `analyse_frame`
    #: (as_kwargs skips it): it never reaches `classify()`, only
    #: `measure_domains()`.
    marker_margin_sigma: Optional[float] = None
    #: Minimum pixel separation between two accepted watershed markers. `None`
    #: means `domain_size.DEFAULT_MARKER_MIN_DISTANCE` (8), the value the
    #: "above" path was validated against for VBBF46 -- raise it for a
    #: material whose flakes show internal reflection highlights bright
    #: enough to be watershed-split into several markers (and so several
    #: domains) instead of staying one. Not passed to `analyse_frame` either:
    #: like `marker_margin_sigma`, it only tunes `measure_domains()`.
    marker_min_distance: Optional[int] = None
    #: Morphological-closing radius (pixels) applied to the domain-size target
    #: mask before connected-component/marker/watershed. `None` means
    #: `domain_size.DEFAULT_MASK_CLOSING_RADIUS` (0, off) -- the mask is used
    #: exactly as `classify()` produced it. Raise it for a material whose
    #: flakes' ragged, noisy real edges are leaving small fringe fragments
    #: disconnected from the flake's main body, each counted as its own
    #: domain sitting right at or inside the real flake's footprint. Not
    #: passed to `analyse_frame` either: like the two fields above, it only
    #: tunes `measure_domains()`. Too large a radius starts merging
    #: genuinely separate close-together small flakes into one.
    mask_closing_radius: Optional[int] = None
    #: Field name -> the `contrast.analyse_frame` keyword it sets.
    _KWARGS = {
        "nsigma": "nsigma",
        "minpx": "minpx",
        "mask_margin": "margin",
        "ff_divisor": "ff_divisor",
    }

    #: Set together or not at all; they become one `abs_threshold` pair.
    _ABS_FIELDS = ("abs_threshold_below", "abs_threshold_above")

    def as_kwargs(self) -> dict:
        """
        Only the fields that are set, keyed as `analyse_frame` wants them.

        An unset field is omitted rather than passed as None, so the defaults
        stay written down once, in contrast.py.

        The two `abs_threshold_*` fields collapse into `analyse_frame`'s single
        `abs_threshold=(below, above)` argument. They are stored apart because a
        preset editor wants two number boxes, and passed together because the
        algorithm wants one pair. Setting them switches thresholding from
        `nsigma` to a fixed contrast; `nsigma` is then unused but still stored,
        so a preset can be switched back without losing its old value.
        """
        out = {
            keyword: getattr(self, name)
            for name, keyword in self._KWARGS.items()
            if getattr(self, name) is not None
        }
        below, above = (getattr(self, f) for f in self._ABS_FIELDS)
        if below is not None and above is not None:
            out["abs_threshold"] = (below, above)
        return out

    def validate(self) -> List[str]:
        """Error messages for this block alone (empty list if valid)."""
        errors = []
        if self.nsigma is not None and not (1.0 <= self.nsigma <= 10.0):
            errors.append(f"nsigma {self.nsigma} out of range [1.0, 10.0]")
        if self.minpx is not None and self.minpx < 1:
            errors.append(f"minpx must be >= 1 (got {self.minpx})")
        if self.mask_margin is not None and not (0.0 <= self.mask_margin < 0.5):
            errors.append(f"mask_margin {self.mask_margin} out of range [0.0, 0.5)")
        if self.ff_divisor is not None and not (1.0 <= self.ff_divisor <= 64.0):
            errors.append(f"ff_divisor {self.ff_divisor} out of range [1.0, 64.0]")
        below, above = (getattr(self, f) for f in self._ABS_FIELDS)
        if (below is None) != (above is None):
            errors.append(
                "abs_threshold_below and abs_threshold_above must be set together "
                "or left unset; one alone does not describe a segmentation"
            )
        for name in self._ABS_FIELDS:
            value = getattr(self, name)
            if value is not None and not (0.5 <= value <= 50.0):
                errors.append(f"{name} {value} out of range [0.5, 50.0] %")
        if self.marker_margin_sigma is not None and not (0.0 <= self.marker_margin_sigma <= 5.0):
            errors.append(
                f"marker_margin_sigma {self.marker_margin_sigma} out of range [0.0, 5.0]"
            )
        if self.marker_min_distance is not None and not (1 <= self.marker_min_distance <= 200):
            errors.append(
                f"marker_min_distance {self.marker_min_distance} out of range [1, 200]"
            )
        if self.mask_closing_radius is not None and not (0 <= self.mask_closing_radius <= 10):
            errors.append(
                f"mask_closing_radius {self.mask_closing_radius} out of range [0, 10]"
            )
        return errors

    def to_dict(self) -> dict:
        """Only the fields that are set; absent means "algorithm default"."""
        return {
            name: getattr(self, name)
            for name in tuple(self._KWARGS) + self._ABS_FIELDS
            + ("marker_margin_sigma", "marker_min_distance", "mask_closing_radius")
            if getattr(self, name) is not None
        }

    #: Keys that older presets carry and this version no longer honours.
    #: Dropped rather than rejected: a stored preset written by v4.6-v5.4 is
    #: still a valid preset, it just names a method that no longer exists.
    _RETIRED = ("adaptive_threshold",)

    @classmethod
    def from_dict(cls, data: dict) -> "OpticalParams":
        """Deserialize from dictionary, ignoring retired keys."""
        data = {k: v for k, v in data.items() if k not in cls._RETIRED}
        return _construct(cls, data, what="optical block")


def _construct(cls, data: dict, what: str):
    """
    `cls(**data)`, but naming the offending key when one is unknown.

    A bare ``TypeError: __init__() got an unexpected keyword argument 'mode'``
    says nothing about which entry in materials.json to go and fix, which is
    the only thing the reader needs to know.
    """
    allowed = {f.name for f in fields(cls)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise TypeError(
            f"Unknown {what} field(s) {unknown}; expected any of {sorted(allowed)}"
        )
    return cls(**data)


@dataclass
class MaterialPreset:
    """
    Everything the app knows about one material.

    Keyed by material name alone. Technique is no longer part of a preset's
    identity -- it comes from the filename, via
    `parser.detect_mode_from_filename` -- so one material holds a `raman`
    block, a `pl` block, or both, and either may be absent.

    `optical` splits a level further, by layer ("1L", "2L"), because optical
    contrast against SiO2/Si genuinely differs between a monolayer and a
    bilayer, while a Raman or PL spectrum does not: the signal says which peaks
    it has. Layers are stored as "1L"/"2L", never as the words
    `contrast.layer_word` prints for them -- that helper is one-way
    presentation and passes unknown values through, so keying persisted JSON by
    its output would orphan every stored block the day the wording changed, and
    would let the file express a "Trilayer" the UI can never select.
    """

    material_name: str
    enabled: bool = True
    description: str = ""

    raman: Optional[TechniquePreset] = None
    pl: Optional[TechniquePreset] = None
    optical: Dict[str, OpticalParams] = field(default_factory=dict)

    #: Technique name -> the attribute holding its block.
    _BLOCKS = {"Raman": "raman", "PL": "pl"}

    def block_for(self, mode: str) -> Optional[TechniquePreset]:
        """The block for `mode` ("Raman"/"PL"), or None if there isn't one."""
        attribute = self._BLOCKS.get(mode)
        return getattr(self, attribute) if attribute else None

    def optical_for(self, layer: str) -> "OpticalParams":
        """
        Tuning for `layer`, or an all-defaults block when that layer is untuned.

        Never None: an untuned layer is a legitimate state that runs the
        algorithm's own defaults, and the page says which it is using rather
        than refusing to run.
        """
        return self.optical.get(layer) or OpticalParams()

    def validate(self) -> List[str]:
        """Error messages across every block (empty list if valid)."""
        errors = []

        if self.raman is None and self.pl is None and not self.optical:
            errors.append(
                f"'{self.material_name}' has no Raman, PL or optical settings; "
                f"a preset with no blocks does nothing"
            )

        for mode, attribute in self._BLOCKS.items():
            block = getattr(self, attribute)
            if block is not None:
                errors.extend(f"{mode}: {e}" for e in block.validate(mode))

        for layer, params in self.optical.items():
            if layer not in OPTICAL_LAYERS:
                # Reported, not dropped. The loader keeps unknown layers so that
                # "I loaded the file" never quietly means "I deleted part of it";
                # flagging it here is what makes the choice visible.
                errors.append(
                    f"Optical: unknown layer '{layer}' (expected one of "
                    f"{list(OPTICAL_LAYERS)}); it is preserved but unused"
                )
            errors.extend(f"Optical {layer}: {e}" for e in params.validate())

        return errors

    def to_dict(self) -> dict:
        """
        Serialize to dictionary for JSON export.

        Absent blocks are **omitted**, not written as null: "this material has
        no PL" and "this material has an empty PL block" are different states,
        and only omission round-trips the first one.
        """
        out: dict = {
            "material_name": self.material_name,
            "enabled": self.enabled,
            "description": self.description,
        }
        if self.raman is not None:
            out["raman"] = self.raman.to_dict()
        if self.pl is not None:
            out["pl"] = self.pl.to_dict()
        if self.optical:
            out["optical"] = {
                layer: params.to_dict() for layer, params in self.optical.items()
            }
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "MaterialPreset":
        """
        Deserialize from dictionary.

        Nested blocks are reconstructed explicitly: `cls(**data)` would hand
        `raman` a raw dict, and every later attribute access would fail
        somewhere far away from the entry that caused it.
        """
        data = dict(data)
        for attribute in cls._BLOCKS.values():
            if data.get(attribute) is not None:
                data[attribute] = TechniquePreset.from_dict(data[attribute])
            else:
                data.pop(attribute, None)
        if data.get("optical") is not None:
            data["optical"] = {
                layer: OpticalParams.from_dict(params)
                for layer, params in data["optical"].items()
            }
        else:
            data.pop("optical", None)
        return _construct(cls, data, what=f"material '{data.get('material_name')}'")
