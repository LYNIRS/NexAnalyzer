"""
Voigt profile fitting using lmfit.

This module implements:
- Multi-peak Voigt fitting with constrained nonlinear optimization
- Auto-bounds calculation based on mode
- Peak auto-finding using scipy.signal
- Fit quality metrics (chi-squared, R²)

References
----------
- lmfit documentation: https://lmfit.github.io/lmfit-py/
- Voigt profile: convolution of Gaussian and Lorentzian line shapes
"""

import numpy as np
from scipy import signal
from lmfit import Parameters
from lmfit.models import VoigtModel
import time
from typing import Dict, List
from ..models.peak import MAX_PEAKS, PeakDefinition, FittedPeak, FitResult, link_errors


# FWHM of a unit-sigma Gaussian: 2*sqrt(2*ln 2). The bounds and initial
# guesses elsewhere in this module use the rounded 2.355; the difference is
# under 0.01% and they are left as they are so fits don't shift.
_GAUSSIAN_FWHM_PER_SIGMA = 2.3548200450309493


def voigt_fwhm(sigma: float, gamma: float) -> float:
    """
    Full width at half maximum of a Voigt profile.

    A Voigt is a Gaussian convolved with a Lorentzian, and its width depends on
    both: 2.355*sigma is the Gaussian contribution alone and understates the
    real width by 60-110% at the shapes this fitter converges to. There is no
    closed form, so this uses the Olivero & Longbothum approximation, accurate
    to ~0.02%:

        f_V ~= 0.5346*f_L + sqrt(0.2166*f_L^2 + f_G^2)

    with f_G = 2*sqrt(2 ln 2)*sigma and f_L = 2*gamma. Verified against the
    measured half-maximum width of the fitted component curves.
    """
    f_g = _GAUSSIAN_FWHM_PER_SIGMA * float(sigma)
    f_l = 2.0 * float(gamma)
    return 0.5346 * f_l + np.sqrt(0.2166 * f_l * f_l + f_g * f_g)


def _correlation(param, other_name: str):
    """lmfit's fitted correlation between `param` and `other_name`, or None."""
    correl = getattr(param, "correl", None)
    if not correl:
        return None
    return correl.get(other_name)


def voigt_fwhm_stderr(
    sigma: float, gamma: float, sigma_stderr: float, gamma_stderr: float, correlation=None
) -> float:
    """
    Standard error on `voigt_fwhm`, propagated from both width parameters.

    Taking 2.355*sigma_stderr — the previous behaviour — reports the
    uncertainty of the Gaussian component rather than of the width, and
    overstates it badly: sigma and gamma trade off against each other, so each
    is poorly determined on its own while their combination is not. That
    produced widths quoted as 2.07 +/- 133.29.

    Uses lmfit's fitted correlation between the two when it is available, which
    is what cancels most of that; without it the terms add in quadrature, which
    is an upper bound rather than a wrong answer.
    """
    f_g = _GAUSSIAN_FWHM_PER_SIGMA * float(sigma)
    f_l = 2.0 * float(gamma)
    root = np.sqrt(0.2166 * f_l * f_l + f_g * f_g)
    if root == 0:
        return 0.0

    # d(f_V)/d(sigma) and d(f_V)/d(gamma), via f_G and f_L.
    d_sigma = _GAUSSIAN_FWHM_PER_SIGMA * (f_g / root)
    d_gamma = 2.0 * (0.5346 + 0.2166 * f_l / root)

    variance = (d_sigma * sigma_stderr) ** 2 + (d_gamma * gamma_stderr) ** 2
    if correlation is not None:
        variance += 2.0 * d_sigma * d_gamma * correlation * sigma_stderr * gamma_stderr

    return float(np.sqrt(variance)) if variance > 0 else 0.0


#: hc in eV·nm: E [eV] = HC_EV_NM / λ [nm]. Used by energy-offset peak links.
HC_EV_NM = 1239.84193

#: Reference-peak starting points for a linked fit, as fractions of the
#: reference's centre window. The preset's own start is always tried first.
LINKED_START_FRACTIONS = (0.125, 0.375, 0.625, 0.875)


def linked_center(reference_center: float, energy_offset_meV: float) -> float:
    """Wavelength (nm) lying `energy_offset_meV` from `reference_center` (nm) in energy."""
    return HC_EV_NM / (HC_EV_NM / reference_center + energy_offset_meV / 1000.0)


def _voigt_height_expr(prefix: str) -> str:
    """lmfit VoigtModel's own height expression: the component's maximum, i.e. its intensity."""
    return (f"({prefix}amplitude/(max(1e-15, {prefix}sigma*sqrt(2*pi))))"
            f"*real(wofz((1j*{prefix}gamma)/(max(1e-15, {prefix}sigma*sqrt(2)))))")


def _area_per_height_expr(prefix: str) -> str:
    """Inverse of `_voigt_height_expr` at fixed widths: area = height × this."""
    return (f"(max(1e-15, {prefix}sigma*sqrt(2*pi)))"
            f"/real(wofz((1j*{prefix}gamma)/(max(1e-15, {prefix}sigma*sqrt(2)))))")


def _resolve_links(peak_table: List[PeakDefinition], mode: str) -> Dict[int, int]:
    """Index of each linked peak -> index of its reference. Raises on a bad link."""
    labels = [p.label for p in peak_table]
    errors = link_errors(
        labels,
        [(p.linked_to, p.energy_offset_meV, p.max_intensity_ratio) for p in peak_table],
        mode,
    )
    if errors:
        raise ValueError("Invalid peak links: " + "; ".join(errors))
    return {i: labels.index(p.linked_to) for i, p in enumerate(peak_table) if p.linked_to is not None}


def _multi_starts(peak_table: List[PeakDefinition], links: Dict[int, int]) -> List[Dict[int, float]]:
    """Centre overrides to fit from: just the preset's start unless peaks are linked."""
    starts = [{}]
    references = sorted(set(links.values()))
    for frac in (LINKED_START_FRACTIONS if references else ()):
        override = {}
        for j in references:
            lo, hi = peak_table[j].center_min, peak_table[j].center_max
            if hi > lo:
                override[j] = lo + frac * (hi - lo)
        if override:
            starts.append(override)
    return starts


def _apply_links(params: Parameters, peak_table: List[PeakDefinition], links: Dict[int, int]) -> None:
    """
    Tie each linked peak to its reference with lmfit expressions.

    A constrained parameter is still clipped to its own [min, max], so the
    linked peak's own centre window and area ceiling are lifted first: its
    position comes from the reference, and its intensity is bounded by the
    ratio times the reference's (itself bounded).

    The intensity cap is on the Voigts' analytic heights. The reported
    intensity (`peak_metrics.peak_intensity`) is each component's maximum on
    the data grid, so at the cap the reported ratio can exceed it by the grid
    sampling -- ~1e-5 relative at PL's 0.2 nm steps.
    """
    for i, j in links.items():
        peak, p, ref = peak_table[i], f"p{i}_", f"p{j}_"
        if peak.energy_offset_meV is not None:
            params[f"{p}center"].set(min=-np.inf, max=np.inf, vary=True)
            params[f"{p}center"].set(
                expr=f"{HC_EV_NM!r}/({HC_EV_NM!r}/{ref}center + {peak.energy_offset_meV / 1000.0!r})")
        if peak.max_intensity_ratio is not None:
            cap = float(peak.max_intensity_ratio)
            params.add(f"{p}intensity_ratio", value=0.5 * cap, min=0.0, max=cap)
            params[f"{p}amplitude"].set(min=-np.inf, max=np.inf)
            params[f"{p}amplitude"].set(
                expr=f"{p}intensity_ratio*{_voigt_height_expr(ref)}*{_area_per_height_expr(p)}")


def _constraint_text(index: int, peak: PeakDefinition, params) -> str:
    """Plain-language statement of a linked peak's constraints, for display."""
    if peak.linked_to is None:
        return ""
    parts = []
    if peak.energy_offset_meV is not None:
        sign = "−" if peak.energy_offset_meV < 0 else "+"
        parts.append(f"E = {peak.linked_to} {sign} {abs(peak.energy_offset_meV):g} meV")
    if peak.max_intensity_ratio is not None:
        text = f"I ≤ {peak.max_intensity_ratio:g} × {peak.linked_to}"
        ratio = params.get(f"p{index}_intensity_ratio")
        # lmfit leaves a bounded parameter ~1e-5 inside its bound, not on it.
        if ratio is not None and ratio.value >= peak.max_intensity_ratio * (1 - 1e-4):
            text += " (at cap)"
        parts.append(text)
    return "; ".join(parts)


def _build_params(x, y, peak_table, center_starts) -> Parameters:
    """
    lmfit parameters for every peak, with bounds and data-derived starts.

    `center_starts` maps a peak's index to a starting centre that replaces the
    preset's, for the multi-start of a linked fit; an empty dict is the
    preset's own start for every peak.
    """
    params = Parameters()

    for i, peak in enumerate(peak_table):
        prefix = f"p{i}_"
        # Convert FWHM to sigma/gamma using shape parameter
        # Shape: 0 = pure Gaussian, 1 = pure Lorentzian, 0.5 = equal contribution
        # The split is linear in shape across the whole range; this used to be
        # written as an if/else on shape < 0.5 whose two arms were identical,
        # which read as though the halves were treated differently.
        shape = peak.shape
        frac_gaussian = 1.0 - shape
        frac_lorentzian = shape

        # Distribute FWHM according to shape
        # Gaussian FWHM = 2.355 * sigma, Lorentzian FWHM = 2 * gamma
        sigma_guess = (peak.width_fwhm * frac_gaussian) / 2.355
        gamma_guess = (peak.width_fwhm * frac_lorentzian) / 2.0

        # Ensure non-zero values (minimum bounds)
        sigma_min = peak.width_min / (2 * 2.355)
        gamma_min = peak.width_min / 4.0
        sigma_guess = max(sigma_guess, sigma_min)
        gamma_guess = max(gamma_guess, gamma_min)

        # The starting center must lie inside its own bounds, which it doesn't
        # when the preset places the peak outside the spectrum's range and
        # calculate_auto_bounds has pinned it to the edge.
        start = center_starts.get(i, peak.center)
        center_guess = min(max(start, peak.center_min), peak.center_max)

        # Auto-estimate the peak intensity from the data at center_guess.
        # Position + FWHM come from the preset (material properties); intensity
        # depends on measurement conditions, so we always init from the data.
        idx = int(np.argmin(np.abs(x - center_guess)))
        intensity_guess = max(float(y[idx]), 1e-6)
        fwhm_eff = peak.width_fwhm
        # lmfit's "amplitude" is the area, so both the guess and the ceiling
        # convert from intensity: area = intensity x FWHM x 1.064 for a Voigt.
        area_guess = intensity_guess * fwhm_eff * 1.064
        area_max = peak.intensity_max * fwhm_eff * 1.064

        # Add parameters with bounds
        if peak.center_min == peak.center_max:
            # calculate_auto_bounds pinned it: the preset places this peak
            # outside the spectrum, so the edge is the only position open to
            # it. lmfit rejects min == max, so say "fixed" rather than passing
            # a degenerate range -- and note it swaps min/max silently when
            # they arrive inverted, which is how an off-range peak used to come
            # back at a position belonging to neither bound.
            params.add(f"{prefix}center", value=center_guess, vary=False)
        else:
            params.add(f"{prefix}center", value=center_guess,
                       min=peak.center_min, max=peak.center_max)
        params.add(f"{prefix}amplitude", value=area_guess,
                   min=0, max=area_max)
        params.add(f"{prefix}sigma", value=sigma_guess,
                   min=peak.width_min / (2 * 2.355), max=peak.width_max / (2 * 2.355))
        params.add(f"{prefix}gamma", value=gamma_guess,
                   min=peak.width_min / 4.0, max=peak.width_max / 4.0)

    return params


def fit_voigt_peaks(
    x: np.ndarray,
    y: np.ndarray,
    peak_table: List[PeakDefinition],
    mode: str = "Raman",
    max_iterations: int = 2000
) -> FitResult:
    """
    Fit multiple Voigt profiles to spectrum data.

    Parameters
    ----------
    x : np.ndarray
        Wavenumber (cm⁻¹) or wavelength (nm).
    y : np.ndarray
        Baseline-corrected intensity.
    peak_table : List[PeakDefinition]
        User-defined peak guesses with bounds.
    mode : str, default="Raman"
        "Raman" or "PL" (affects auto-bounds if not manually set).

    Returns
    -------
    FitResult
        Fitting results with fitted_peaks, quality metrics, and error handling.

    Raises
    ------
    ValueError
        If peak_table is empty or has more than MAX_PEAKS peaks.

    Notes
    -----
    Algorithm (FR-031 to FR-037):
    - Uses lmfit VoigtModel (Gaussian + Lorentzian convolution)
    - Levenberg-Marquardt optimizer (method='leastsq')
    - Auto-bounds from PeakDefinition, honouring its center_tolerance when set
    - Returns actionable error messages on convergence failure

    Voigt parameters:
    - center: peak position
    - amplitude: lmfit's name for the area — peak intensity × width
    - sigma: Gaussian width (FWHM = 2.355 * sigma)
    - gamma: Lorentzian width (FWHM = 2 * gamma)

    Examples
    --------
    >>> peak_defs = [
    ...     PeakDefinition(center=1350, intensity=5000, width_fwhm=50),
    ...     PeakDefinition(center=1580, intensity=8000, width_fwhm=60)
    ... ]
    >>> result = fit_voigt_peaks(x, y, peak_defs, mode="Raman")
    >>> if result.success:
    ...     print(f"R² = {result.r_squared:.4f}")
    """
    # Validation
    if len(peak_table) == 0:
        raise ValueError("peak_table must have at least 1 peak")

    if len(peak_table) > MAX_PEAKS:
        raise ValueError(f"peak_table must have <= {MAX_PEAKS} peaks (got {len(peak_table)})")

    if len(x) != len(y):
        raise ValueError(f"x and y must have same length (got {len(x)} vs {len(y)})")

    # (Re)calculate bounds against this spectrum's own range and resolution.
    # Unconditional by design: it is what keeps the bounds consistent with the
    # current center and width. A peak that carries its own center_tolerance —
    # every peak built from a preset does — keeps that tolerance here.
    x_range = (x.min(), x.max())
    y_max = y.max()
    spectral_resolution = np.median(np.abs(np.diff(x)))

    for peak in peak_table:
        peak.calculate_auto_bounds(mode, x_range, y_max, spectral_resolution)

    links = _resolve_links(peak_table, mode)

    # Build composite model (sum of Voigt models)
    composite_model = None
    for i in range(len(peak_table)):
        voigt = VoigtModel(prefix=f"p{i}_")
        composite_model = voigt if composite_model is None else composite_model + voigt

    # Fit using Levenberg-Marquardt with increased tolerance. A linked model
    # can have more than one minimum -- on a single-line WSe2 PL spectrum the
    # trion either takes the main line or goes to zero, depending on where the
    # exciton starts -- so a linked fit is run from several reference starts
    # and the lowest chi-squared kept. An unlinked fit runs once, as before.
    start_time = time.time()
    result = None
    error_message = ""
    for center_starts in _multi_starts(peak_table, links):
        params = _build_params(x, y, peak_table, center_starts)
        _apply_links(params, peak_table, links)
        try:
            trial = composite_model.fit(
                y, params, x=x,
                method='leastsq',
                max_nfev=max_iterations,
                fit_kws={'ftol': 1e-6, 'xtol': 1e-6}  # Increased tolerance for stability
            )
        except Exception as e:
            error_message = f"Fitting error: {str(e)}"
            continue

        # Accept fit even if error bars couldn't be estimated (common with tight fits)
        # result.success may be False if uncertainties couldn't be calculated, but fit is still valid
        if not trial.success and "Tolerance seems to be too small" not in str(trial.message):
            error_message = (
                f"Fitting failed to converge ({trial.message}). "
                f"Try adjusting initial guesses or reducing number of peaks."
            )
            continue
        if result is None or trial.chisqr < result.chisqr:
            result = trial
    convergence_time = time.time() - start_time

    if result is None:
        return FitResult(
            success=False,
            fitted_peaks=[],
            total_fit_curve=np.zeros_like(y),
            residuals=y.copy(),
            chi_squared=np.sum(y**2),
            r_squared=0.0,
            convergence_time=convergence_time,
            error_message=error_message,
        )

    # Extract fitted parameters
    fitted_peaks = []
    total_fit = result.best_fit

    # Helper functions to safely extract parameter values and errors
    # (handles both Parameter objects and raw floats)
    def get_param_value(param):
        """Extract value from Parameter object or float."""
        return param.value if hasattr(param, 'value') else float(param)

    def get_param_stderr(param):
        """Extract stderr from Parameter object (returns 0.0 if unavailable)."""
        if hasattr(param, 'stderr'):
            return param.stderr or 0.0
        return 0.0

    for i, peak in enumerate(peak_table):
        prefix = f"p{i}_"

        try:
            # Get fitted values - robust to both Parameter objects and raw floats
            center_fit = get_param_value(result.params[f"{prefix}center"])
            center_stderr = get_param_stderr(result.params[f"{prefix}center"])
            # lmfit's "amplitude" parameter is the integrated area under the peak.
            area_fit = get_param_value(result.params[f"{prefix}amplitude"])
            area_stderr = get_param_stderr(result.params[f"{prefix}amplitude"])
            sigma_fit = get_param_value(result.params[f"{prefix}sigma"])
            sigma_stderr = get_param_stderr(result.params[f"{prefix}sigma"])
            gamma_fit = get_param_value(result.params[f"{prefix}gamma"])
        except AttributeError as e:
            # Debug: print parameter type if extraction fails
            param_key = f"{prefix}center"
            param_obj = result.params.get(param_key)
            raise AttributeError(
                f"Parameter extraction failed for {param_key}. "
                f"Type: {type(param_obj)}, "
                f"Has 'value': {hasattr(param_obj, 'value')}, "
                f"Original error: {e}"
            )

        # A Voigt's width comes from both of its components, so 2.355*sigma
        # is only the Gaussian half of it (see voigt_fwhm).
        gamma_stderr = get_param_stderr(result.params[f"{prefix}gamma"])
        sigma_gamma_correl = _correlation(result.params[f"{prefix}sigma"], f"{prefix}gamma")
        width_fwhm_fit = voigt_fwhm(sigma_fit, gamma_fit)
        width_stderr = voigt_fwhm_stderr(
            sigma_fit, gamma_fit, sigma_stderr, gamma_stderr, sigma_gamma_correl
        )
        # The pre-v3.4.0 value, kept only for the "FWHM (v1)" comparison
        # column -- see FittedPeak.width_fwhm_v1.
        width_fwhm_v1_fit = _GAUSSIAN_FWHM_PER_SIGMA * sigma_fit

        # Calculate shape parameter (Lorentzian fraction)
        # shape = gamma / (gamma + sigma)
        shape_fit = gamma_fit / (gamma_fit + sigma_fit)

        # Evaluate component curve
        voigt_component = VoigtModel(prefix=prefix)
        # Pass fitted parameters directly as keyword arguments
        component_curve = voigt_component.eval(
            x=x,
            **{
                f"{prefix}center": center_fit,
                f"{prefix}amplitude": area_fit,
                f"{prefix}sigma": sigma_fit,
                f"{prefix}gamma": gamma_fit
            }
        )

        fitted_peaks.append(FittedPeak(
            label=peak.label,
            center=center_fit,
            center_stderr=center_stderr,
            area=area_fit,
            area_stderr=area_stderr,
            width_fwhm=width_fwhm_fit,
            width_stderr=width_stderr,
            shape=shape_fit,
            component_curve=component_curve,
            color=peak.color,  # Copy color from PeakDefinition
            width_fwhm_v1=width_fwhm_v1_fit,
            constraint=_constraint_text(i, peak, result.params),
        ))

    # Calculate quality metrics
    residuals = result.residual
    chi_squared = result.chisqr

    # R-squared
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y - np.mean(y))**2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return FitResult(
        success=True,
        fitted_peaks=fitted_peaks,
        total_fit_curve=total_fit,
        residuals=residuals,
        chi_squared=chi_squared,
        r_squared=r_squared,
        convergence_time=convergence_time,
        error_message=""
    )


def auto_find_peaks(
    x: np.ndarray,
    y: np.ndarray,
    mode: str = "Raman",
    min_peaks: int = 2,
    max_peaks: int = 5,
    prominence_threshold: float = 0.05
) -> List[PeakDefinition]:
    """
    Automatically find peaks in spectrum using scipy.signal.find_peaks.

    Parameters
    ----------
    x : np.ndarray
        Wavenumber (cm⁻¹) or wavelength (nm).
    y : np.ndarray
        Baseline-corrected intensity.
    mode : str, default="Raman"
        "Raman" or "PL" (used for auto-bounds).
    min_peaks : int, default=2
        Minimum number of peaks to find.
    max_peaks : int, default=5
        Maximum number of peaks to find.
    prominence_threshold : float, default=0.05
        Minimum peak prominence (fraction of max intensity).

    Returns
    -------
    peak_table : List[PeakDefinition]
        Automatically generated peak definitions.

    Notes
    -----
    Algorithm (FR-027):
    - Uses scipy.signal.find_peaks with prominence threshold
    - Estimates FWHM from peak widths_half_height
    - Returns top N peaks by prominence (N = max_peaks)
    - Default colors from Plotly palette

    Examples
    --------
    >>> peak_table = auto_find_peaks(x, y, mode="Raman", max_peaks=5)
    >>> print(f"Found {len(peak_table)} peaks")
    """
    if len(y) < 10:
        return []

    # Calculate prominence threshold
    y_max = y.max()
    min_prominence = prominence_threshold * y_max
    x_range = (x.min(), x.max())

    # Find peaks with width detection
    peak_indices, properties = signal.find_peaks(
        y,
        prominence=min_prominence,
        width=2,  # Minimum width in data points
        rel_height=0.5  # Measure width at half-maximum
    )

    if len(peak_indices) == 0:
        return []

    # Sort by prominence (descending)
    prominences = properties['prominences']
    sorted_indices = np.argsort(prominences)[::-1]

    # Take top N peaks by prominence, capped at max_peaks.
    # min_peaks can't manufacture peaks that don't exist in sorted_indices,
    # so it has no effect here beyond what the slice below already does.
    n_peaks = min(len(sorted_indices), max_peaks)
    top_indices = sorted_indices[:n_peaks]

    # Median spectral resolution
    dx = np.median(np.abs(np.diff(x)))

    # Create PeakDefinition objects
    peak_table = []
    default_colors = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
    ]

    for i, idx in enumerate(top_indices):
        peak_idx = peak_indices[idx]

        # Peak center
        center = x[peak_idx]

        # Peak intensity
        intensity = y[peak_idx]

        # Estimate FWHM from widths (improved with curvature-based fallback)
        if 'widths' in properties and len(properties['widths']) > idx:
            # Primary method: use scipy's width_half_height
            width_points = properties['widths'][idx]
            width_fwhm = width_points * dx
        else:
            # Improved fallback: estimate from peak curvature
            if peak_idx > 1 and peak_idx < len(y) - 2:
                # Calculate 2nd derivative at peak: y''(x) ≈ (y[i-1] - 2*y[i] + y[i+1]) / dx²
                d2y = (y[peak_idx - 1] - 2 * y[peak_idx] + y[peak_idx + 1]) / (dx ** 2)

                if d2y < 0:  # Concave down (valid peak)
                    # For Gaussian: y''(peak) = -intensity / σ²
                    # σ ≈ sqrt(intensity / |y''|)
                    # FWHM ≈ 2.355 × σ
                    sigma_est = np.sqrt(intensity / abs(d2y))
                    width_fwhm = 2.355 * sigma_est

                    # Sanity check: FWHM should be reasonable
                    x_span = x_range[1] - x_range[0]
                    if width_fwhm < dx or width_fwhm > 0.5 * x_span:
                        # Unreasonable estimate, use conservative fallback
                        width_fwhm = 10 * dx
                else:
                    # Not a concave-down peak (edge case), use conservative fallback
                    width_fwhm = 10 * dx
            else:
                # Peak too close to edge, use conservative fallback
                width_fwhm = 10 * dx

        # Color
        color = default_colors[i % len(default_colors)]

        # Label
        label = f"Peak {i+1}"

        peak_table.append(PeakDefinition(
            center=center,
            intensity=intensity,
            width_fwhm=width_fwhm,
            label=label,
            color=color,
            shape=0.5  # Equal Gaussian/Lorentzian
        ))

    # Sort by center position (left to right)
    peak_table.sort(key=lambda p: p.center)

    # Renumber labels
    for i, peak in enumerate(peak_table):
        peak.label = f"Peak {i+1}"

    return peak_table


def detect_overlapping_peaks(
    peak_table: List[PeakDefinition],
    merge_threshold: float = 2.0
) -> List[str]:
    """
    Detect peaks that are too close and may cause fitting issues.

    Parameters
    ----------
    peak_table : List[PeakDefinition]
        List of peaks (should be sorted by center position).
    merge_threshold : float, default=2.0
        Warn if centers are closer than this × average FWHM.

    Returns
    -------
    warnings : List[str]
        List of warning messages for overlapping peaks.

    Notes
    -----
    Peaks closer than 2× FWHM often:
    - Collapse into a single peak during fitting
    - Cause poor convergence
    - Result in low R² values

    Example
    -------
    >>> warnings = detect_overlapping_peaks(peak_table, merge_threshold=2.0)
    >>> for warning in warnings:
    ...     print(warning)
    """
    warnings = []

    # Sort by center position
    sorted_peaks = sorted(peak_table, key=lambda p: p.center)

    for i in range(len(sorted_peaks) - 1):
        p1 = sorted_peaks[i]
        p2 = sorted_peaks[i + 1]

        distance = abs(p2.center - p1.center)
        avg_fwhm = (p1.width_fwhm + p2.width_fwhm) / 2

        if distance < merge_threshold * avg_fwhm:
            warnings.append(
                f"⚠️ Peaks '{p1.label}' and '{p2.label}' are very close "
                f"({distance:.1f} < {merge_threshold}×FWHM={merge_threshold*avg_fwhm:.1f}). "
                f"Consider merging into a single peak or refining guesses."
            )

    return warnings
