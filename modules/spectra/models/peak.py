"""
Data models for peak definitions and fit results.

This module defines:
- PeakDefinition: User-defined peak guess with auto-calculated bounds
- FittedPeak: Fitted parameters with standard errors
- FitResult: Complete fitting results with quality metrics
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence
import numpy as np

#: Most peaks one fit may carry -- the preset block, the fit and its result all
#: check against this. Raised from 10 at v5.9.0 for `WSe2 extended` (20 peaks).
MAX_PEAKS = 20


def link_errors(
    labels: Sequence[str],
    links: Sequence[tuple],
    mode: Optional[str] = None,
) -> List[str]:
    """
    What is wrong with a set of peak links (empty list if nothing).

    A peak may be *linked* to one other peak of the same fit, its reference,
    named by label. The link carries up to two rules, applied by
    `fitting.fit_voigt_peaks`:

    - `energy_offset_meV`: the peak's centre is the reference's centre moved by
      that many meV, through E = hc/λ -- negative means lower energy (longer
      wavelength). Defined on a wavelength axis, so PL only.
    - `max_intensity_ratio`: the peak's intensity (its maximum, as everywhere
      in this app) is at most that multiple of the reference's.

    One check for both callers -- `TechniquePreset.validate` and the fitter --
    so a preset the editor accepts is one the fitter will run.

    Parameters
    ----------
    labels : sequence of str
        Every peak's label, in fit order.
    links : sequence of (linked_to, energy_offset_meV, max_intensity_ratio)
        One tuple per peak, aligned with `labels`; all three None when unlinked.
    mode : str, optional
        "Raman" or "PL". When given, an energy offset outside PL is reported.
    """
    errors = []
    for label, (target, offset, ratio) in zip(labels, links):
        name = f"'{label}'"
        if target is None:
            if offset is not None or ratio is not None:
                errors.append(f"{name}: ΔE and max intensity ratio need 'linked to' a reference peak")
            continue
        if target == label:
            errors.append(f"{name} cannot be linked to itself")
            continue
        matches = [i for i, other in enumerate(labels) if other == target]
        if len(matches) != 1:
            errors.append(
                f"{name} is linked to '{target}', which must be the label of exactly "
                f"one other peak (found {len(matches)})"
            )
            continue
        if links[matches[0]][0] is not None:
            # One level only: a chain would let a peak's position drift through
            # two offsets, and a cycle would have nothing free at all.
            errors.append(f"{name} is linked to '{target}', which is itself linked; link to a free peak")
        if offset is None and ratio is None:
            errors.append(f"{name} is linked to '{target}' but sets neither ΔE nor max intensity ratio")
        if offset is not None and (not np.isfinite(offset) or offset == 0):
            errors.append(f"{name}: ΔE must be a non-zero number of meV (got {offset})")
        if offset is not None and mode is not None and mode != "PL":
            errors.append(f"{name}: ΔE is an energy offset on a wavelength axis and applies to PL only")
        if ratio is not None and not (np.isfinite(ratio) and ratio > 0):
            errors.append(f"{name}: max intensity ratio must be > 0 (got {ratio})")
    return errors


@dataclass
class PeakDefinition:
    """
    User-defined peak guess with fitting bounds.

    Attributes
    ----------
    center : float
        Peak center position (cm⁻¹ or nm).
    intensity : float
        Placeholder field; required > 0 for backward-compatible serialization.
        The runtime initial guess is auto-estimated from data at fit time, and
        ``intensity_max`` is derived from ``y_max`` (5× max intensity), so this
        value is not consulted by the fitter. Optional in the Excel preset
        format (defaults to 1.0 when absent).
    width_fwhm : float
        Full-width-at-half-maximum (cm⁻¹ or nm, must be > 0).
    label : str
        User-defined label (e.g., 'D-Band'), max 50 characters.
    shape : float
        Voigt mixing parameter (0=Gaussian, 1=Lorentzian), range [0, 1].
    color : str
        Hex color for plot component (e.g., '#1f77b4').
    center_tolerance : Optional[float]
        Half-width of the allowed center range, in the same units as `center`.
        When set — presets set it — `calculate_auto_bounds` uses it instead of
        the mode default, every time it runs. Leave it None for a peak that has
        no opinion and should take the mode's tolerance.
    center_min : Optional[float]
        Lower bound for center. Derived, not input: `calculate_auto_bounds`
        overwrites it on every call from `center` and `center_tolerance`.
    center_max : Optional[float]
        Upper bound for center. Derived; see `center_min`.
    width_min : Optional[float]
        Minimum FWHM (auto-calculated if None).
    width_max : Optional[float]
        Maximum FWHM (auto-calculated if None).
    intensity_max : Optional[float]
        Maximum peak intensity (auto-calculated if None). An intensity, not an
        area: `fitting.py` multiplies it by FWHM × 1.064 before handing it to
        lmfit, whose "amplitude" parameter is the integrated area (see
        ``FittedPeak.area``).
    linked_to : Optional[str]
        Label of this peak's reference peak, or None for a free peak. See
        `link_errors` for the rules a link may carry.
    energy_offset_meV : Optional[float]
        This peak's energy minus the reference's, in meV (linked peaks only).
        When set, the centre is computed from the reference and this peak's
        own `center_tolerance` window is not used.
    max_intensity_ratio : Optional[float]
        Ceiling on this peak's intensity as a multiple of the reference's
        (linked peaks only).
    """

    center: float
    intensity: float
    width_fwhm: float
    label: str = ""
    shape: float = 0.5
    color: str = "#1f77b4"
    center_tolerance: Optional[float] = None
    center_min: Optional[float] = None
    center_max: Optional[float] = None
    width_min: Optional[float] = None
    width_max: Optional[float] = None
    intensity_max: Optional[float] = None
    linked_to: Optional[str] = None
    energy_offset_meV: Optional[float] = None
    max_intensity_ratio: Optional[float] = None

    def __post_init__(self):
        """Validate attributes."""
        if self.intensity <= 0:
            raise ValueError(f"intensity must be > 0 (got {self.intensity})")

        if self.width_fwhm <= 0:
            raise ValueError(f"width_fwhm must be > 0 (got {self.width_fwhm})")

        if len(self.label) > 50:
            raise ValueError(f"label must be <= 50 characters (got {len(self.label)})")

        if not (0.0 <= self.shape <= 1.0):
            raise ValueError(f"shape must be in [0, 1] (got {self.shape})")

        # Validate hex color format
        if not (self.color.startswith('#') and len(self.color) == 7):
            raise ValueError(f"color must be hex format #RRGGBB (got {self.color})")

    def calculate_auto_bounds(
        self,
        mode: str,
        x_range: tuple[float, float],
        y_max: float,
        spectral_resolution: float
    ):
        """
        Calculate auto-bounds based on mode and data characteristics.

        Parameters
        ----------
        mode : str
            'Raman' or 'PL' (affects center tolerance).
        x_range : tuple[float, float]
            (min, max) of X data range.
        y_max : float
            Maximum Y value in data.
        spectral_resolution : float
            Median step size in X (used for width_min).

        Notes
        -----
        Auto-bounds logic (FR-029):
        - Raman: center ± 5 cm⁻¹ — unless `center_tolerance` is set
        - PL: center ± 30 nm — unless `center_tolerance` is set
        - width_min: 2-3 × spectral_resolution
        - width_max: 50% of X range
        - intensity_max: 5 × max(Y)

        This runs on every fit, and it is the *only* thing that writes
        `center_min`/`center_max` — which is why a preset's tolerance has to be
        carried on `center_tolerance` rather than written into the bounds by the
        caller. A caller that set the bounds itself would have them silently
        replaced with the mode default here. That was the v3.10.0 behaviour and
        it cost WSe₂ 5 of its 7 tolerances; LA railed at its ±5 wall.
        """
        # Center bounds. The preset's tolerance wins when it has one: center is
        # a material property, so a preset that says ±10 means ±10 (CLAUDE.md,
        # "the preset owns position and width").
        if self.center_tolerance is not None:
            center_tolerance = self.center_tolerance
        elif mode == "Raman":
            # Raman: at least 5 cm⁻¹ or 5% of FWHM, whichever is larger
            center_tolerance = max(5.0, 0.05 * self.width_fwhm)
        else:  # PL
            # PL: at least 30 nm or 10% of FWHM, whichever is larger
            center_tolerance = max(30.0, 0.10 * self.width_fwhm)

        # Always recalculate center bounds to ensure they include the current center
        # (important when user manually edits peak positions)
        self.center_min = max(x_range[0], self.center - center_tolerance)
        self.center_max = min(x_range[1], self.center + center_tolerance)

        # Clamping to the data range inverts the window when the peak sits
        # outside the data entirely -- WSe2's "center" template is at 0 while
        # its own x-range crop starts at 6, which produced min=6.674 > max=3.
        # lmfit takes an inverted bound without complaint and returns a center
        # outside both, so the reported position was meaningless rather than
        # merely wrong. Pin to the nearest edge instead: the peak can only be
        # at the boundary, and a pinned value (zero spread) is legible as such.
        if self.center_min > self.center_max:
            self.center_min = self.center_max = (
                x_range[0] if self.center < x_range[0] else x_range[1]
            )

        # Width bounds (adaptive: allow 0.5× to 3× initial guess)
        # Always recalculate to ensure bounds match current width_fwhm
        # At least 2.5× spectral resolution, or 50% of initial guess
        self.width_min = max(2.5 * spectral_resolution, 0.5 * self.width_fwhm)
        # At most 50% of X range, or 3× initial guess (prevents runaway fitting)
        self.width_max = min(0.5 * (x_range[1] - x_range[0]), 3.0 * self.width_fwhm)

        # Intensity ceiling (wider range for uncertain peaks)
        if self.intensity_max is None:
            # Allow up to 5× max intensity (accounts for sharp peaks above baseline)
            self.intensity_max = 5.0 * y_max

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "label": self.label,
            "center": self.center,
            "intensity": self.intensity,
            "width_fwhm": self.width_fwhm,
            "shape": self.shape,
            "color": self.color,
            "center_tolerance": self.center_tolerance,
            "center_min": self.center_min,
            "center_max": self.center_max,
            "width_min": self.width_min,
            "width_max": self.width_max,
            "intensity_max": self.intensity_max,
            "linked_to": self.linked_to,
            "energy_offset_meV": self.energy_offset_meV,
            "max_intensity_ratio": self.max_intensity_ratio,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PeakDefinition":
        """Deserialize from dictionary."""
        return cls(**data)


@dataclass
class FittedPeak:
    """
    Fitted peak parameters with standard errors.

    Attributes
    ----------
    label : str
        Copied from PeakDefinition.
    center : float
        Fitted peak center (cm⁻¹ or nm).
    center_stderr : float
        Standard error in center from lmfit covariance.
    area : float
        The area under the peak, which is the quantity lmfit solves for and
        calls "amplitude". Deliberately *not* named amplitude here: the UI, the
        CSV export and the QC Report all report peak *intensity* — the
        curve's maximum, see peak_metrics.peak_intensity — and the two differ
        by roughly FWHM x 1.064.
    area_stderr : float
        Standard error in area.
    width_fwhm : float
        Fitted FWHM (cm⁻¹ or nm) -- the Voigt width, from both sigma and
        gamma. See `fitting.voigt_fwhm`.
    width_stderr : float
        Standard error in FWHM.
    shape : float
        Fitted Voigt mixing (0-1).
    component_curve : np.ndarray
        This peak's contribution to total fit (same length as X).
    color : str
        Peak color for plotting (hex #RRGGBB), copied from PeakDefinition.
    constraint : str
        How a linked peak was tied to its reference, e.g.
        "E = Exciton − 30 meV; I ≤ 1 × Exciton (at cap)". Empty for a free
        peak. A centre set by an energy link is computed, not measured, and
        this is what says so wherever the peak is shown.
    width_fwhm_v1 : Optional[float]
        `2.355 * sigma` -- the Gaussian-only width the app reported before
        v3.4.0, which silently dropped gamma and understated every width by
        59-108%. Kept only so the on-screen table and, optionally, the
        QC Report can show it next to the correct value for comparison;
        nothing else in the app should read this field. None for a
        `FittedPeak` built outside `fitting.fit_voigt_peaks` (there is no
        sigma/gamma to compute it from).
    """

    label: str
    center: float
    center_stderr: float
    area: float
    area_stderr: float
    width_fwhm: float
    width_stderr: float
    shape: float
    component_curve: np.ndarray
    color: str = "#1f77b4"  # Default color
    width_fwhm_v1: Optional[float] = None
    constraint: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "label": self.label,
            "center": self.center,
            "center_stderr": self.center_stderr,
            "area": self.area,
            "area_stderr": self.area_stderr,
            "width_fwhm": self.width_fwhm,
            "width_stderr": self.width_stderr,
            "shape": self.shape,
            "component_curve": self.component_curve.tolist() if self.component_curve is not None else None,
            "color": self.color,
            "constraint": self.constraint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FittedPeak":
        """Deserialize from dictionary."""
        component_curve = np.array(data["component_curve"]) if data.get("component_curve") else None
        return cls(
            label=data["label"],
            center=data["center"],
            center_stderr=data["center_stderr"],
            area=data["area"],
            area_stderr=data["area_stderr"],
            width_fwhm=data["width_fwhm"],
            width_stderr=data["width_stderr"],
            shape=data["shape"],
            component_curve=component_curve,
            color=data.get("color", "#1f77b4"),  # Default color if not present
            constraint=data.get("constraint", ""),
        )


@dataclass
class FitResult:
    """
    Complete fitting results with quality metrics.

    Attributes
    ----------
    success : bool
        True if Levenberg-Marquardt solver converged.
    fitted_peaks : list[FittedPeak]
        Fitted parameters for each peak (1 to MAX_PEAKS).
    total_fit_curve : np.ndarray
        Sum of all fitted peaks (same length as X).
    residuals : np.ndarray
        Y_data - total_fit_curve.
    chi_squared : float
        Sum of squared residuals.
    r_squared : float
        Coefficient of determination, in (-inf, 1]. Negative means the fit
        explains the data worse than its own mean does -- see __post_init__.
    convergence_time : float
        Fitting time in seconds.
    error_message : str
        Empty if success=True, else actionable suggestion.
    """

    success: bool
    fitted_peaks: list[FittedPeak]
    total_fit_curve: np.ndarray
    residuals: np.ndarray
    chi_squared: float
    r_squared: float
    convergence_time: float
    error_message: str = ""

    def __post_init__(self):
        """Validate attributes."""
        # Allow empty fitted_peaks only if success=False
        if self.success and not (1 <= len(self.fitted_peaks) <= MAX_PEAKS):
            raise ValueError(f"fitted_peaks must have 1-{MAX_PEAKS} peaks when success=True (got {len(self.fitted_peaks)})")

        # R-squared is 1 - SS_res/SS_tot, which is unbounded below: any model
        # that fits worse than a horizontal line through the mean scores
        # negative. That is a *poor fit*, not an invalid one, and it is exactly
        # what a preset aimed at the wrong material produces -- MoS2's 383/408
        # peaks on WSe2 data land in empty spectrum, the areas collapse to
        # ~zero and R-squared settles near -0.11. Rejecting it here turned that
        # into "Peak fitting failed: r_squared must be in [0, 1]", an internal
        # invariant surfacing as a user-facing error that blamed the initial
        # guesses. Worse, it was inconsistent: the Silicon preset on the same
        # data scores +0.00008 and passed validation silently. Both are the
        # same mistake and both belong to peak_metrics.R_SQUARED_MIN, the gate
        # built to drop them. Only > 1 is genuinely impossible.
        if self.r_squared > 1.0:
            raise ValueError(f"r_squared must be <= 1 (got {self.r_squared})")

        if self.chi_squared < 0:
            raise ValueError(f"chi_squared must be >= 0 (got {self.chi_squared})")

        if self.convergence_time < 0:
            raise ValueError(f"convergence_time must be >= 0 (got {self.convergence_time})")

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "success": self.success,
            "fitted_peaks": [p.to_dict() for p in self.fitted_peaks],
            "total_fit_curve": self.total_fit_curve.tolist() if self.total_fit_curve is not None else None,
            "residuals": self.residuals.tolist() if self.residuals is not None else None,
            "chi_squared": self.chi_squared,
            "r_squared": self.r_squared,
            "convergence_time": self.convergence_time,
            "error_message": self.error_message
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FitResult":
        """Deserialize from dictionary."""
        return cls(
            success=data["success"],
            fitted_peaks=[FittedPeak.from_dict(p) for p in data["fitted_peaks"]],
            total_fit_curve=np.array(data["total_fit_curve"]) if data.get("total_fit_curve") else None,
            residuals=np.array(data["residuals"]) if data.get("residuals") else None,
            chi_squared=data["chi_squared"],
            r_squared=data["r_squared"],
            convergence_time=data["convergence_time"],
            error_message=data.get("error_message", "")
        )
