"""
Fit-results CSV export: one row per fitted peak.

Two shapes, keyed to what the caller is holding:

- `export_fit_params_csv()` — the current spectrum only (sidebar Quick Export).
- `export_master_csv()` — every fitted file, plus provenance columns
  (auto-detected mode, X-range crop, Raman laser line and axis shift,
  convergence time) and, for PL, a leading
  "Raw" row per file. This is the batch/archival table.

Both report Intensity — the fitted curve's maximum, via
`peak_metrics.peak_intensity_and_stderr` — not `FittedPeak.area`. See that
module for the two quantities and their names.

There were two more until v5.0.0: `export_point_fits_csv` and
`export_peak_stats_csv`, written beside the QC Panel's figures. The QC Report
now writes one workbook instead of a scatter of CSVs, and those two tables
were near-duplicates of sheets it already carried — the per-point table of its
technique sheet, the per-peak aggregate of its Summary sheet. Two files
claiming the same numbers is one more chance for them to disagree.
"""

from typing import Dict, List

import pandas as pd

from ..processing.peak_metrics import peak_intensity_and_stderr, raw_peak_stats

# Columns shared by both CSVs, in order.
_PEAK_COLUMNS = [
    "Peak_Label", "Center", "Center_Stderr", "Intensity", "Intensity_Stderr",
    "FWHM", "FWHM_Stderr", "Shape", "R_Squared", "Chi_Squared",
]


def _peak_row(filename: str, spectrum, peak) -> dict:
    """One fitted peak's row: identity, then the shared measurement columns."""
    intensity, intensity_stderr = peak_intensity_and_stderr(peak)
    return {
        "Filename": filename,
        "Mode": spectrum.mode,
        "Peak_Label": peak.label,
        "Center": peak.center,
        "Center_Stderr": peak.center_stderr,
        "Intensity": intensity,
        "Intensity_Stderr": intensity_stderr,
        "FWHM": peak.width_fwhm,
        "FWHM_Stderr": peak.width_stderr,
        "Shape": peak.shape,
        "R_Squared": spectrum.fit_result.r_squared,
        "Chi_Squared": spectrum.fit_result.chi_squared,
    }


def _provenance(spectrum) -> dict:
    """Master-CSV-only columns recording how the spectrum was processed.

    Laser_Line_cm1 is where the file's laser line was found on the axis as
    read, blank when none was (or the file was never checked, or is PL);
    Axis_Shift_cm1 is what was added to the axis before fitting — 0 when the
    line was found but not applied. Without them a corrected and an
    uncorrected export cannot be told apart, and they differ by up to 4 cm⁻¹.
    """
    calibration = getattr(spectrum, "laser_calibration", None)
    return {
        "Auto_Detected": spectrum.auto_detected,
        "X_Range_Limited": spectrum.x_range_enabled,
        "X_Min": spectrum.x_min if spectrum.x_range_enabled else "",
        "X_Max": spectrum.x_max if spectrum.x_range_enabled else "",
        "Laser_Line_cm1": (calibration.laser_line
                           if calibration is not None and calibration.laser_line is not None
                           else ""),
        "Axis_Shift_cm1": calibration.shift if calibration is not None else "",
    }


def _raw_row(filename: str, spectrum) -> dict:
    """PL-only leading row holding the unfitted spectrum's peak stats."""
    stats = raw_peak_stats(spectrum.processed_data.X, spectrum.processed_data.Y)
    row = {
        "Filename": filename,
        "Mode": spectrum.mode,
        **_provenance(spectrum),
        "Peak_Label": "Raw",
        "Center": stats.center,
        "Center_Stderr": "",
        "Intensity": stats.intensity,
        "Intensity_Stderr": "",
        "FWHM": stats.fwhm if stats.fwhm is not None else "",
        "FWHM_Stderr": "",
        "Shape": "",
        "R_Squared": "",
        "Chi_Squared": "",
        "Convergence_Time_s": "",
    }
    return row


def export_fit_params_csv(spectrum) -> str:
    """
    The current spectrum's fitted peak parameters as CSV.

    One row per fitted peak; no provenance columns and no PL Raw row (that's
    `export_master_csv`'s archival format).
    """
    rows = [
        _peak_row(spectrum.filename, spectrum, peak)
        for peak in spectrum.fit_result.fitted_peaks
    ]
    return pd.DataFrame(rows).to_csv(index=False)


def export_master_csv(files: Dict) -> str:
    """
    Every successfully fitted file's peaks in one table.

    Parameters
    ----------
    files : Dict[str, SpectrumFile]
        Mapping of filename -> SpectrumFile (i.e. st.session_state['files']).

    Returns
    -------
    str
        CSV content. A comment line if nothing has been fitted yet, so the
        file is never silently empty.
    """
    rows: List[dict] = []

    for filename, spectrum in files.items():
        if spectrum.fit_result is None or not spectrum.fit_result.success:
            continue

        if spectrum.mode == "PL" and len(spectrum.processed_data.Y) > 0:
            rows.append(_raw_row(filename, spectrum))

        for peak in spectrum.fit_result.fitted_peaks:
            rows.append({
                **_peak_row(filename, spectrum, peak),
                **_provenance(spectrum),
                "Convergence_Time_s": spectrum.fit_result.convergence_time,
            })

    if not rows:
        return "# No fit results to export\n"

    # Reindex so provenance columns land in a stable position even though the
    # Raw row and peak rows build their dicts in different orders.
    column_order = [
        "Filename", "Mode", "Auto_Detected", "X_Range_Limited", "X_Min", "X_Max",
        "Laser_Line_cm1", "Axis_Shift_cm1",
        *_PEAK_COLUMNS, "Convergence_Time_s",
    ]
    return pd.DataFrame(rows)[column_order].to_csv(index=False)
