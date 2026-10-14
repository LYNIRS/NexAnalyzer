"""
Raman-shift axis calibration against the laser line.

A Raman shift is measured from the laser, so the Rayleigh line belongs at
exactly 0 cm⁻¹. Instruments drift from session to session: the IMEC-Nov maps
put it at -3.9, -1.9 and +1.7 cm⁻¹, which moved the same bilayer WSe2's
E2g+A1g from 247.6 to 253.1 cm⁻¹ — a spread that was all instrument. Shifted
so each map's own laser line sits at 0, the six wafers agree within 0.7 cm⁻¹.

What this does, per file (a map of many spectra sharing one X):

1. Average the spectra, take the peaks within ±`SEARCH_HALF_WIDTH` of 0 most
   prominent first, and fit a Gaussian plus a constant within
   ±`FIT_HALF_WIDTH` of each until one passes step 2. One fit per file, not per spectrum: the
   offset belongs to the session, and a single spectrum's line can be weak or
   clipped where the average is not.
2. Believe the fit only if it looks like a laser line — data reaching below
   `MIN_AXIS_START`, a width inside `FWHM_RANGE`, a position inside
   ±`MAX_OFFSET`, a height well above the fit's residual. Anything else is
   `not found` and the axis is left exactly as read: a filtered or truncated
   export must not invent a correction.
3. Shift X by minus the fitted position, unless it is within `DEADBAND` of 0
   — a third of a typical 0.87 cm⁻¹ detector step, so a freshly calibrated
   instrument reads its own numbers back unchanged rather than moved by a few
   hundredths from fit to fit.

4. Within one load of several files (a sample's maps), the offset is the
   session's: a file with no believable line, or one more than
   `SESSION_TOLERANCE` from the others' median, is shifted by that median
   and marked `from_session` — see `calibrate_raman_session`.

The fitted position is recorded whether or not it was applied, so a session
that drifts after calibration shows up as a number rather than hiding in the
data.

A constant shift is the right correction whether the error sits in the
laser's wavelength or the detector's calibration: across 0-300 cm⁻¹ either
moves every peak by the same amount to within ~0.05 cm⁻¹. It fixes the zero,
not the scale — a scale error needs a second reference line (Si 520.7), which
this deliberately does not use.

Raman only. A PL axis is a wavelength, with no line at zero to anchor it.
"""

import warnings
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit
from scipy.signal import find_peaks

from ..models.spectrum import LaserCalibration, SpectrumData

SEARCH_HALF_WIDTH = 25.0  # cm⁻¹ either side of 0 searched for the line's maximum
FIT_HALF_WIDTH = 5.0      # cm⁻¹ either side of that maximum actually fitted
MIN_AXIS_START = -10.0    # the data must reach below this to hold a whole line
FWHM_RANGE = (1.5, 4.0)   # cm⁻¹; the IMEC-Nov lines are 1.9-2.5
MAX_OFFSET = 10.0         # cm⁻¹; beyond this it is not drift, it is something else
DEADBAND = 0.3            # cm⁻¹; smaller fitted offsets are not applied
# Line height over the fit's residual. Low on purpose: the filter's shoulders
# sit inside the fit window and put the 54 IMEC-Nov maps anywhere from 4 to 51.
# A window holding no line at all fits at ~1-2.
_MIN_SIGNAL_TO_RESIDUAL = 3.0
# Peaks of the search window tried, most prominent first. A handful covers the
# line and the filter's shoulders; a window with no line has nothing to find.
_MAX_CANDIDATES = 5
# cm⁻¹; how far a file's line may sit from its session's median and still be
# its own. The IMEC-Nov and Samsung-Oct sessions span 0.04-0.19 within one load.
SESSION_TOLERANCE = 0.5


def _gaussian(x, amplitude, center, sigma, offset):
    return amplitude * np.exp(-0.5 * ((x - center) / sigma) ** 2) + offset


def find_laser_line(x: np.ndarray, y: np.ndarray) -> Optional[Tuple[float, float]]:
    """(position, FWHM) of the laser line in one spectrum, or None.

    None whenever the line can't be believed — see the module docstring for
    the tests. Never raises.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size == 0 or x.min() > MIN_AXIS_START:
        return None
    search = np.abs(x) <= SEARCH_HALF_WIDTH
    if not search.any():
        return None
    xs, ys = x[search], y[search]
    # Candidates are the window's peaks, most prominent first, and the first
    # that passes every test wins. Not simply the tallest point: the notch
    # filter's shoulder can outgrow a weak line — on QC_KRX261001 a shoulder
    # at +10 cm⁻¹ stood over a +4.7 line in four maps of nine.
    peaks, props = find_peaks(ys, prominence=0)
    order = np.argsort(props["prominences"])[::-1] if peaks.size else []
    candidates = [xs[peaks[i]] for i in order] or [xs[np.argmax(ys)]]
    for peak in candidates[:_MAX_CANDIDATES]:
        found = _fit_line(x, y, peak)
        if found is not None:
            return found
    return None


def _fit_line(x: np.ndarray, y: np.ndarray, peak: float) -> Optional[Tuple[float, float]]:
    """Fit one candidate close in, or None if it isn't a believable laser line.

    Fitted ±`FIT_HALF_WIDTH` around the candidate rather than across the whole
    search window: the filter's shoulders ~6 cm⁻¹ out would pull a wide fit.
    """
    window = np.abs(x - peak) <= FIT_HALF_WIDTH
    if window.sum() < 6:
        return None
    xw, yw = x[window], y[window]
    guess = [np.ptp(yw), peak, 1.0, float(np.min(yw))]
    try:
        # A candidate that is no line at all often leaves the covariance
        # undefined; the tests below reject it, the warning adds nothing.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            params, _ = curve_fit(_gaussian, xw, yw, p0=guess, maxfev=5000)
    except (RuntimeError, ValueError):
        return None

    amplitude, center, sigma, _ = params
    fwhm = abs(sigma) * 2.0 * np.sqrt(2.0 * np.log(2.0))
    residual = np.std(yw - _gaussian(xw, *params))
    if not (np.isfinite(center) and np.isfinite(fwhm)):
        return None
    if amplitude <= 0 or amplitude < _MIN_SIGNAL_TO_RESIDUAL * max(residual, 1e-12):
        return None
    if not (FWHM_RANGE[0] <= fwhm <= FWHM_RANGE[1]) or abs(center) > MAX_OFFSET:
        return None
    return float(center), float(fwhm)


def _measure(spectra: Sequence[SpectrumData]) -> Optional[Tuple[float, float]]:
    """The laser line of one file's spectra, fitted on their average."""
    if not spectra:
        return None
    x = spectra[0].X
    if all(s.X.shape == x.shape and np.array_equal(s.X, x) for s in spectra):
        mean_y = np.mean([s.Y for s in spectra], axis=0)
    else:  # not one map after all; trust the first column alone
        mean_y = spectra[0].Y
    return find_laser_line(x, mean_y)


def _session_line(lines: Sequence[float], n_files: int) -> Optional[float]:
    """The line most of a load's files agree on, or None.

    Needs at least two lines, from at least half the files, and at least half
    the files within `SESSION_TOLERANCE` of their median — a majority, so a
    session of mostly misses or of disagreeing lines lends nothing.
    """
    if len(lines) < 2 or 2 * len(lines) < n_files:
        return None
    median = float(np.median(lines))
    agreeing = [line for line in lines if abs(line - median) <= SESSION_TOLERANCE]
    if 2 * len(agreeing) < n_files:
        return None
    return float(np.median(agreeing))


def calibrate_raman_session(
    files: Sequence[Sequence[SpectrumData]], enabled: bool = True
) -> List[Tuple[List[SpectrumData], LaserCalibration]]:
    """Shift each file's spectra so the session's laser line sits at 0 cm⁻¹.

    `files` are the files of one load — a sample's nine maps — each the list
    `parse_spectrum_multi` returns. Every file's line is fitted on its own
    first; the offset is the session's, though, so a file whose line was not
    found, or lands more than `SESSION_TOLERANCE` from the median of the
    others, takes that median instead (`from_session`). That is the filter
    shoulder case: on QC_KRX261001 four maps of nine had a shoulder taller
    than the line, which no single-map test separates cleanly. A file alone,
    or a session with no majority, keeps exactly its own result.

    With `enabled` False every line is still measured and reported, but
    nothing moves.
    """
    files = [list(spectra) for spectra in files]
    measured = [_measure(spectra) for spectra in files]
    session = _session_line([m[0] for m in measured if m is not None], len(files))

    out = []
    for spectra, found in zip(files, measured):
        line, fwhm = found if found is not None else (None, None)
        if line is not None and (session is None or abs(line - session) <= SESSION_TOLERANCE):
            used, from_session = line, False
        elif session is not None:
            used, from_session = session, True
        else:
            used, from_session = None, False

        if used is None or not enabled or abs(used) < DEADBAND:
            out.append((spectra, LaserCalibration(line, fwhm, 0.0)))
            continue
        shift = -used
        shifted = [SpectrumData(X=s.X + shift, Y=s.Y) for s in spectra]
        out.append((shifted, LaserCalibration(line, fwhm, shift, from_session)))
    return out


def calibrate_raman_spectra(
    spectra: Sequence[SpectrumData], enabled: bool = True
) -> Tuple[List[SpectrumData], LaserCalibration]:
    """Shift one file's spectra so its laser line sits at 0 cm⁻¹.

    `spectra` are the columns of one file, as `parse_spectrum_multi` returns
    them: one shared X. The line is fitted on their average. With `enabled`
    False the line is still measured and reported, but nothing moves. A file
    on its own has no session to fall back on; loaders holding several files
    use `calibrate_raman_session`.
    """
    spectra = list(spectra)
    if not spectra:
        return spectra, LaserCalibration(None, None, 0.0)
    return calibrate_raman_session([spectra], enabled)[0]


def axis_note(calibrations: Sequence[Optional[LaserCalibration]]) -> Optional[str]:
    """One line saying what happened to a run's Raman axis, for a report.

    One entry per *file*, not per spectrum — a map's 36 spectra share one
    calibration. None entries (spectra loaded without one, e.g. from an old
    project) count as "not checked". Returns None when there is nothing to
    summarize.
    """
    calibrations = list(calibrations)
    if not calibrations:
        return None
    checked = [c for c in calibrations if c is not None]
    found = [c for c in checked if c.laser_line is not None]
    applied = [c for c in checked if c.applied]
    own = [c for c in applied if not c.from_session]
    borrowed = [c for c in applied if c.from_session]
    n = len(calibrations)

    def _span(values):
        lo, hi = min(values), max(values)
        return f"{lo:+.2f}" if abs(hi - lo) < 0.005 else f"{lo:+.2f} to {hi:+.2f}"

    if applied:
        text = "Raman axis shifted to laser line"
        if len(applied) < n:
            text += f" on {len(applied)} of {n} files"
        if own:
            text += f": laser at {_span([c.laser_line for c in own])} cm⁻¹ moved to 0"
        if borrowed:
            text += (f"; {len(borrowed)} file(s) without a matching line of their own "
                     f"took the session's line at {_span([-c.shift for c in borrowed])} cm⁻¹")
        if len(applied) < n:
            text += "; the rest uncorrected"
        return text
    if found:
        return (f"Raman axis uncorrected; laser line at "
                f"{_span([c.laser_line for c in found])} cm⁻¹")
    if checked:
        return "Raman axis uncorrected; no laser line found"
    return None
