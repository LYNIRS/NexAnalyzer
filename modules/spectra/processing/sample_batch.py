"""
Per-sample batch-fit orchestration for the QC Report feature: loads
and fits every discovered Raman/PL file against its technique's preset,
reusing execute_auto_workflow() per file (same as "Run All Files" on the
Spectra page, just headless and grouped by point index).
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..models.preset import MaterialPreset
from core.report.models import SampleScan
from ..models.spectrum import LaserCalibration, ProcessingSettings, SpectrumFile
from .auto_workflow import execute_auto_workflow
from .axis_calibration import calibrate_raman_session
from .parser import parse_spectrum_multi

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class SampleBatchResult:
    raman_spectra: List[Tuple[int, SpectrumFile]] = field(default_factory=list)
    pl_spectra: List[Tuple[int, SpectrumFile]] = field(default_factory=list)
    raman_errors: List[Tuple[int, str]] = field(default_factory=list)
    pl_errors: List[Tuple[int, str]] = field(default_factory=list)
    #: One laser-line calibration per Raman *file* (point), every file that
    #: parsed — including ones whose fits all failed, since the axis was still
    #: checked. See `axis_calibration.axis_note`.
    raman_calibrations: Dict[int, LaserCalibration] = field(default_factory=dict)


def _run_one_technique(
    files_by_point: Dict[int, str],
    mode: str,
    preset: Optional[MaterialPreset],
    out_spectra: List[Tuple[int, SpectrumFile]],
    out_errors: List[Tuple[int, str]],
    progress_callback: Optional[ProgressCallback],
    correct_raman_axis: bool = True,
    out_calibrations: Optional[Dict[int, LaserCalibration]] = None,
) -> None:
    """Load + fit every spectrum in `files_by_point` against `preset`.

    One file may hold many spectra. A measurement point's file is commonly a
    map — one X column plus N intensity columns, 25 or 100 of them — and every
    column is fitted, so a point contributes N entries to `out_spectra` under
    the same point index. Grouping by that index is what lets the aggregation
    tell within-point spread from position-to-position variation.

    A `preset` of None skips the technique entirely (no entries added to
    either output list, callback never invoked). A parse or fit failure is
    recorded in `out_errors` and excluded from `out_spectra`; this never
    raises.

    Raman files are calibrated against their laser line as they are parsed —
    measured always, applied when `correct_raman_axis` — and each file's
    calibration lands in `out_calibrations` and on its spectra.
    """
    if preset is None:
        return

    # Parse every file before fitting any of it, so the progress total counts
    # spectra rather than files. A bar driven by file count advances once per
    # 25 fits and reads as frozen through nearly all the work — the same defect
    # fixed in v3.8.1, one level down.
    parsed: List[Tuple[int, list]] = []
    for point in sorted(files_by_point.keys()):
        try:
            parsed.append((point, parse_spectrum_multi(files_by_point[point])))
        except Exception as e:
            out_errors.append((point, str(e)))

    # One session: the sample's maps share an offset, so a map whose own
    # line is missing or off takes the others' (see calibrate_raman_session).
    calibrations: Dict[int, Optional[LaserCalibration]] = {point: None for point, _ in parsed}
    if mode == "Raman" and parsed:
        results = calibrate_raman_session([spectra for _, spectra in parsed], correct_raman_axis)
        parsed = [(point, spectra) for (point, _), (spectra, _) in zip(parsed, results)]
        calibrations = {point: cal for (point, _), (_, cal) in zip(parsed, results)}
        if out_calibrations is not None:
            out_calibrations.update(calibrations)

    total = sum(len(spectra) for _, spectra in parsed)
    if total == 0:
        # Every file failed to parse. Still report once, or the stage never
        # reaches its share and the overall bar stalls short of done.
        if progress_callback is not None and files_by_point:
            progress_callback(mode, 1, 1)
        return

    done = 0
    for point, spectra in parsed:
        multi = len(spectra) > 1
        for index, spectrum_data in enumerate(spectra, start=1):
            # "Raman_1__3" is the 3rd spectrum at point 1, matching the naming
            # in the inherited analysis CSVs. Single-spectrum files keep the
            # plain "Raman_1" they have always had.
            filename = f"{mode}_{point}__{index}" if multi else f"{mode}_{point}"
            try:
                spectrum_file = SpectrumFile(
                    filename=filename,
                    mode=mode,
                    original_data=spectrum_data,
                    raw_data=spectrum_data,
                    processed_data=spectrum_data,
                    processing_settings=ProcessingSettings(),
                    laser_calibration=calibrations[point],
                )
                result = execute_auto_workflow(spectrum_file, preset)
                if result["success"]:
                    out_spectra.append((point, spectrum_file))
                else:
                    out_errors.append((point, result.get("error_message", "Unknown error")))
            except Exception as e:
                out_errors.append((point, str(e)))

            done += 1
            if progress_callback is not None:
                progress_callback(mode, done, total)


def run_sample_batch(
    scan: SampleScan,
    raman_preset: Optional[MaterialPreset],
    pl_preset: Optional[MaterialPreset],
    progress_callback: Optional[ProgressCallback] = None,
    correct_raman_axis: bool = True,
) -> SampleBatchResult:
    """
    Fit every discovered Raman/PL file in `scan` against its respective
    preset. A preset of None for a technique skips it entirely. A parse or
    fit failure for one point is recorded in the matching `*_errors` list
    and excluded from `*_spectra`; this never raises.

    `correct_raman_axis` shifts each Raman file so its laser line sits at
    0 cm⁻¹ (see `axis_calibration`); the line is measured either way.
    """
    result = SampleBatchResult()

    _run_one_technique(
        scan.raman_files, "Raman", raman_preset,
        result.raman_spectra, result.raman_errors, progress_callback,
        correct_raman_axis=correct_raman_axis,
        out_calibrations=result.raman_calibrations,
    )
    _run_one_technique(
        scan.pl_files, "PL", pl_preset,
        result.pl_spectra, result.pl_errors, progress_callback
    )

    return result
