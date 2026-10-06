"""
The QC Report's numbers as one workbook: every point's fitted peaks, every
segmented frame, plus the summary the report's pages show.

Up to six sheets, two audiences:

- ``Summary`` is the summary page's tables — per-peak mean ± std for each
  technique, the intensity ratios, and any point that failed to fit — for
  reading.
- ``Raman`` and ``PL`` are tidy tables, one row per fitted peak per point,
  for pivoting and plotting. This is the detail the report only ever shows
  aggregated: the summary page says the E2g+A1g sat at 250.3 ± 0.4, the sheet
  says which point was the 0.4.
- ``OM_Stats`` and ``OM_Points`` are the same pair for the optical
  segmentation: three class rows, and the frame behind each of those averages.
- ``Domain_Size``, present only when domain-size analysis ran, is the pooled
  flake-diameter stats the summary page's domain table also shows — one row,
  or two (small, large) when the sample's domains split into size
  populations. See `DomainSizeStat` and `domain_size.split_size_populations`.

One workbook rather than a workbook plus four CSVs, which is what the two
merged pages wrote between them until v5.0.0. Two of those CSVs were
near-duplicates of sheets already here — the per-point fit table and the
per-peak aggregate — so retiring them removed a way for one file to disagree
with another about the same measurement.

Every point that fitted is here, including the ones the summary averages over
silently; every point that didn't is named in the excluded block rather than
just missing, because a workbook that drops nine rows without saying so reads
as a complete record of a sample that was never measured that way.

Intensity is the fitted curve's maximum, via
``peak_metrics.peak_intensity_and_stderr`` — never ``FittedPeak.area``. Same
quantity under the same name as the on-screen table, the master CSV and the
report's own tables. See peak_metrics for why those are two different numbers.

The optical import is the second `spectra -> optical` edge in the codebase and
is deliberate: a QC Report carries both techniques' numbers, and the
alternative is openpyxl in the page. `modules.optical.io.frame_tables` owns
the optical schema; this module only decides how Excel shows it.

Cell values are written unrounded. The display formats below only decide how
many decimals Excel *shows*, so a user who widens a column's format gets the
rest of the number instead of digits we already discarded.
"""

import os
from io import BytesIO
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font

from core.report.models import RAW_STAT_LABEL, DomainSizeStat, PeakStat
from modules.optical.io import frame_tables

from ..processing.peak_metrics import peak_intensity_and_stderr, raw_peak_stats

#: (median, MAD, n) — `peak_metrics.compute_peak_intensity_ratio`'s summary.
RatioSummary = Tuple[float, float, int]


class _Column(NamedTuple):
    """A column's heading, Excel number format, and width in characters."""

    header: str
    number_format: Optional[str] = None
    width: float = 12.0


class TechniqueResults(NamedTuple):
    """One technique's contribution to the workbook.

    ``stats`` is what the .pptx table shows, passed in rather than recomputed
    here, so the workbook cannot disagree with the deck written beside it —
    including PL's leading empirical row, which arrives as an ordinary
    `PeakStat` labelled `RAW_STAT_LABEL`.

    ``include_raw_row`` adds that same fit-free measurement per point on the
    data sheet. It is a flag, not an inference from ``label``, because which
    techniques want an empirical row is a reporting decision: PL's broad
    emission is read off the spectrum, Raman's modes are read off the fit.
    The master CSV draws the same line.
    """

    label: str
    point_spectra: Sequence[Tuple[int, object]] = ()
    stats: Optional[Sequence[PeakStat]] = None
    source_files: Optional[Dict[int, str]] = None
    errors: Sequence[Tuple[int, str]] = ()
    ratios: Sequence[Tuple[str, RatioSummary]] = ()
    include_raw_row: bool = False


# One row per fitted peak per point. Mode isn't a column: the sheet name is
# the mode, which is what keeps these tables pivotable as they stand.
#
# `show_fwhm_v1` inserts FWHM_v1/FWHM_v1_Stderr right after FWHM/FWHM_Stderr
# -- the pre-v3.4.0 Gaussian-only width, see FittedPeak.width_fwhm_v1. Off by
# default: every other reporting surface shows only the correct width.
def _point_columns(show_fwhm_v1: bool) -> Sequence[_Column]:
    columns = [
        _Column("Point", "0", 7),
        _Column("Source_File", None, 24),
        _Column("Peak_Label", None, 14),
        _Column("Center", "0.00", 11),
        _Column("Center_Stderr", "0.000", 14),
        _Column("Intensity", "0.00", 12),
        _Column("Intensity_Stderr", "0.000", 17),
        _Column("FWHM", "0.00", 10),
        _Column("FWHM_Stderr", "0.000", 13),
    ]
    if show_fwhm_v1:
        columns += [
            _Column("FWHM_v1", "0.00", 10),
            _Column("FWHM_v1_Stderr", "0.000", 13),
        ]
    columns += [
        _Column("Shape", "0.00", 8),
        _Column("R_Squared", "0.0000", 11),
        _Column("Chi_Squared", "General", 13),
        _Column("Convergence_Time_s", "0.000", 19),
    ]
    return columns


# Mean and std as separate numeric columns, not one "250.3 ± 0.4" string: the
# whole point of a workbook is that the next person can compute with it.
def _summary_columns(show_fwhm_v1: bool) -> Sequence[_Column]:
    columns = [
        _Column("Peak", None, 16),
        _Column("N", "0", 5),
        _Column("Center_Mean", "0.00", 13),
        _Column("Center_Std", "0.000", 12),
        _Column("Intensity_Mean", "0.00", 15),
        _Column("Intensity_Std", "0.000", 14),
        _Column("FWHM_Mean", "0.00", 12),
        _Column("FWHM_Std", "0.000", 11),
    ]
    if show_fwhm_v1:
        columns += [
            _Column("FWHM_v1_Mean", "0.00", 13),
            _Column("FWHM_v1_Std", "0.000", 12),
        ]
    return columns

_RATIO_COLUMNS: Sequence[_Column] = (
    _Column("Ratio", None, 22),
    _Column("N", "0", 5),
    _Column("Median", "0.000", 11),
    _Column("MAD", "0.000", 11),
)

_EXCLUDED_COLUMNS: Sequence[_Column] = (
    _Column("Technique", None, 12),
    _Column("Point", "0", 7),
    _Column("Error", None, 60),
)

# How Excel shows each optical column. Keyed by the header names
# `frame_tables` declares, so the schema has one owner and adding a column
# there cannot silently produce an unformatted one here — it just picks up the
# text default until someone gives it a format.
_OPTICAL_FORMATS = {
    "Point": ("0", 7),
    "Frame": (None, 22),
    "Frame_Type": (None, 12),
    "Reference_Layer": (None, 16),
    "Below_Coverage_pct": ("0.00", 20),
    "Reference_Coverage_pct": ("0.00", 22),
    "Above_Coverage_pct": ("0.00", 20),
    "Below_Contrast_pct": ("0.00", 19),
    "Above_Contrast_pct": ("0.00", 19),
    "Below_Components": ("0", 18),
    "Above_Components": ("0", 18),
    "Green_Mode": ("0.0", 12),
    "Sigma_Left": ("0.000", 12),
    "Sigma_Right": ("0.000", 13),
    "Sigma_Noise": ("0.000", 13),
    "Threshold_Low": ("0.0", 14),
    "Threshold_High": ("0.0", 15),
    "Shoulder": ("0.0", 10),
    "Threshold_Mode": (None, 15),
    "Step_Below_pct": ("0.000", 15),
    "Step_Above_pct": ("0.000", 15),
    "Status_Below": (None, 15),
    "Status_Above": (None, 15),
    "Class": (None, 16),
    "Position": (None, 11),
    "N_Frames": ("0", 10),
    "N_Noise_Limited": ("0", 17),
    "Coverage_Mean_pct": ("0.00", 18),
    "Coverage_Std_pct": ("0.000", 17),
    "Contrast_Mean_pct": ("0.00", 18),
    "Contrast_Std_pct": ("0.000", 17),
    "Threshold_Below_pct": ("0.000", 20),
    "Threshold_Above_pct": ("0.000", 20),
    "Threshold_Base_Below_pct": ("0.000", 24),
    "Threshold_Base_Above_pct": ("0.000", 24),
    "Threshold_Below_Source": (None, 22),
    "Threshold_Above_Source": (None, 22),
    "Noise_Sigma_pct": ("0.000", 16),
}


def _optical_columns(headers: Sequence[str]) -> Sequence[_Column]:
    return tuple(
        _Column(header, *_OPTICAL_FORMATS.get(header, (None, 14)))
        for header in headers
    )

_HEADING_FONT = Font(bold=True)
_TITLE_FONT = Font(bold=True, size=14)


def _point_rows(technique: TechniqueResults, show_fwhm_v1: bool) -> List[list]:
    """`technique`'s data-sheet rows, ordered by point then by fit order."""
    source_files = technique.source_files or {}
    rows: List[list] = []

    for point, spectrum in sorted(technique.point_spectra, key=lambda item: item[0]):
        source = os.path.basename(source_files.get(point, "")) or None
        fit = spectrum.fit_result

        if technique.include_raw_row:
            raw = raw_peak_stats(spectrum.processed_data.X, spectrum.processed_data.Y)
            # `raw` is None only for an empty spectrum — no row at all then.
            # `raw.fwhm` is None whenever there's no half-maximum crossing (a
            # flat or non-positive signal), and that cell is left empty rather
            # than filled with a 0.0 that would read as a measured width. The
            # intensity is still reported: 0 counts is a measurement.
            if raw is not None:
                row = [
                    point, source, RAW_STAT_LABEL,
                    raw.center, None, raw.intensity, None, raw.fwhm, None,
                ]
                if show_fwhm_v1:
                    # No fit, so there is no sigma/gamma the v1 formula could
                    # come from.
                    row += [None, None]
                row += [None, None, None, None]
                rows.append(row)

        for peak in (fit.fitted_peaks if fit else []):
            intensity, intensity_stderr = peak_intensity_and_stderr(peak)
            row = [
                point, source, peak.label,
                peak.center, peak.center_stderr,
                intensity, intensity_stderr,
                peak.width_fwhm, peak.width_stderr,
            ]
            if show_fwhm_v1:
                # No stderr computed for the deprecated formula -- left
                # blank rather than fabricated.
                row += [peak.width_fwhm_v1, None]
            row += [peak.shape, fit.r_squared, fit.chi_squared, fit.convergence_time]
            rows.append(row)

    return rows


def _summary_rows(stats: Optional[Sequence[PeakStat]], show_fwhm_v1: bool) -> List[list]:
    rows = []
    for stat in (stats or []):
        row = [
            stat.label, stat.n,
            stat.center_mean, stat.center_std,
            stat.intensity_mean, stat.intensity_std,
            # None for a row whose width genuinely couldn't be measured — the
            # same cell the .pptx dashes out.
            stat.fwhm_mean, stat.fwhm_std,
        ]
        if show_fwhm_v1:
            row += [stat.fwhm_v1_mean, stat.fwhm_v1_std]
        rows.append(row)
    return rows


def _summary_caption(technique: TechniqueResults) -> str:
    """Mirrors the .pptx caption rule: a table led by the empirical row is not
    a "fit summary"."""
    has_empirical = any(stat.label == RAW_STAT_LABEL for stat in (technique.stats or []))
    kind = (
        "summary (empirical + fitted, mean ± std)" if has_empirical
        else "fit summary (mean ± std)"
    )
    return f"{technique.label} {kind}"


def _write_table(
    ws, columns: Sequence[_Column], rows: Sequence[Sequence], start_row: int = 1
) -> int:
    """Write a header row and `rows` beneath it; return the next free row."""
    for col_index, column in enumerate(columns, start=1):
        cell = ws.cell(row=start_row, column=col_index, value=column.header)
        cell.font = _HEADING_FONT

    for row_offset, row in enumerate(rows, start=1):
        for col_index, (column, value) in enumerate(zip(columns, row), start=1):
            cell = ws.cell(row=start_row + row_offset, column=col_index, value=value)
            # Guarded on the value's type: a format applied to the empty cell of
            # an unmeasured width would make Excel render it as 0.00.
            if column.number_format and isinstance(value, (int, float)):
                cell.number_format = column.number_format

    return start_row + len(rows) + 1


def _set_widths(ws, columns: Sequence[_Column]) -> None:
    for col_index, column in enumerate(columns, start=1):
        letter = ws.cell(row=1, column=col_index).column_letter
        ws.column_dimensions[letter].width = column.width


def _add_data_sheet(wb: Workbook, technique: TechniqueResults, show_fwhm_v1: bool) -> None:
    """One technique's per-point sheet, or nothing when it produced no fits —
    an empty sheet named "PL" claims a technique that was never measured."""
    rows = _point_rows(technique, show_fwhm_v1)
    if not rows:
        return

    columns = _point_columns(show_fwhm_v1)
    ws = wb.create_sheet(title=technique.label)
    _write_table(ws, columns, rows)
    _set_widths(ws, columns)
    # The header stays put while scrolling 9 points x n peaks, and the filter
    # is how you pull one peak's row out of every point in two clicks.
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _add_optical_sheets(wb: Workbook, frames: Sequence,
                        threshold_pair=None) -> None:
    """``OM_Stats`` and ``OM_Points``, or nothing when no frame segmented.

    Two sheets rather than one, matching the spectra pair: the three-row class
    table is what a reader wants, the per-frame table is what a plotter wants,
    and interleaving them would serve neither.
    """
    if not frames:
        return

    for title, headers, rows in (
        ("OM_Stats", frame_tables.CLASS_COLUMNS,
         frame_tables.frame_class_rows(frames, threshold_pair)),
        ("OM_Points", frame_tables.POINT_COLUMNS, frame_tables.frame_point_rows(frames)),
    ):
        if not rows:
            continue
        columns = _optical_columns(headers)
        ws = wb.create_sheet(title=title)
        _write_table(ws, columns, rows)
        _set_widths(ws, columns)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions


def _domain_columns(split: bool) -> Sequence[_Column]:
    columns = [_Column("Population", None, 12)] if split else []
    columns += [
        _Column("Domains_N", "0", 12),
        _Column("Frames_N", "0", 10),
        _Column("Diameter_Median", "0.00", 16),
        _Column("Diameter_Mean", "0.00", 15),
        _Column("Diameter_Std", "0.00", 14),
        _Column("Unit", None, 8),
        _Column("Density_per_100um2", "0.00", 18),
    ]
    return columns


def _add_domain_sheet(
    wb: Workbook, stat: Optional[DomainSizeStat], class_label: Optional[str] = None
) -> None:
    """``Domain_Size``, or nothing when domain-size analysis did not run (no
    OM frames, or the checkbox was off) -- same convention as `_add_optical_sheets`.

    Tidy, numeric columns rather than the PDF table's formatted "mean ± std"
    string, matching this module's own stated split: the report page shows
    the read-friendly number, the workbook carries the plottable one. One row
    when the sample's domains didn't split into two size populations, two
    (small, large) when `domain_size.split_size_populations` found a ≥2x gap
    in the pooled diameters -- see `DomainSizeStat.populations`.
    """
    if stat is None or stat.n_domains == 0:
        return

    ws = wb.create_sheet(title="Domain_Size")
    start_row = 1
    if class_label:
        caption = "Domain size (equivalent circular diameter)"
        ws.cell(row=1, column=1, value=f"{caption} — {class_label}").font = _HEADING_FONT
        start_row = 3

    columns = _domain_columns(split=bool(stat.populations))
    if stat.populations:
        rows = [
            [pop.label.capitalize(), pop.n, stat.n_frames,
             pop.diameter_median, pop.diameter_mean, pop.diameter_std, stat.unit,
             pop.density_per_100um2]
            for pop in stat.populations
        ]
    else:
        rows = [[
            stat.n_domains, stat.n_frames,
            stat.diameter_median, stat.diameter_mean, stat.diameter_std, stat.unit,
            stat.density_per_100um2,
        ]]
    _write_table(ws, columns, rows, start_row=start_row)
    _set_widths(ws, columns)
    ws.freeze_panes = f"A{start_row + 1}"


def _add_summary_sheet(
    wb: Workbook,
    sample_name: str,
    material_name: str,
    report_date: str,
    techniques: Sequence[TechniqueResults],
    show_fwhm_v1: bool,
) -> None:
    """The reading sheet: identity block, then each technique's summary table
    and ratios, then whatever was excluded."""
    ws = wb.create_sheet(title="Summary", index=0)
    summary_columns = _summary_columns(show_fwhm_v1)

    ws["A1"] = "NexAnalyzer sample results"
    ws["A1"].font = _TITLE_FONT

    row = 3
    for label, value in (
        ("Sample", sample_name),
        ("Material", material_name),
        ("Date", report_date),
    ):
        ws.cell(row=row, column=1, value=label).font = _HEADING_FONT
        ws.cell(row=row, column=2, value=value)
        row += 1
    row += 1

    for technique in techniques:
        if technique.stats:
            ws.cell(row=row, column=1, value=_summary_caption(technique)).font = _HEADING_FONT
            row = _write_table(
                ws, summary_columns, _summary_rows(technique.stats, show_fwhm_v1), start_row=row + 1
            )
            row += 1

        if technique.ratios:
            ws.cell(
                row=row, column=1,
                value=f"{technique.label} intensity ratios (median ± MAD)",
            ).font = _HEADING_FONT
            ratio_rows = [
                [label, n, median, mad] for label, (median, mad, n) in technique.ratios
            ]
            row = _write_table(ws, _RATIO_COLUMNS, ratio_rows, start_row=row + 1)
            row += 1

    excluded = [
        [technique.label, point, message]
        for technique in techniques
        for point, message in technique.errors
    ]
    if excluded:
        ws.cell(
            row=row, column=1,
            value=f"Excluded — {len(excluded)} point(s) failed to fit and are absent above",
        ).font = _HEADING_FONT
        _write_table(ws, _EXCLUDED_COLUMNS, excluded, start_row=row + 1)

    # Widths from the summary table: the blocks share the sheet's columns, and
    # it is the one whose headings are long enough to need them.
    _set_widths(ws, summary_columns)


def build_sample_results_xlsx(
    *,
    sample_name: str,
    material_name: str,
    report_date: str,
    techniques: Sequence[TechniqueResults] = (),
    optical_frames: Sequence = (),
    optical_threshold_pair=None,
    domain_stat: Optional[DomainSizeStat] = None,
    domain_class_label: Optional[str] = None,
    show_fwhm_v1: bool = False,
) -> bytes:
    """
    The sample's results as .xlsx bytes: a `Summary` sheet, one per-point sheet
    for each technique that produced fits, the two optical sheets when the
    segmentation ran, and a `Domain_Size` sheet when domain-size analysis ran.

    The identity arguments mirror the summary figure's, so the workbook and the
    report pages written beside it name the same sample, material and date.

    `optical_frames` is the `FrameResult` list the OM figures were drawn from.
    Passed rather than re-segmented, so a sheet cannot describe a different
    segmentation than the PNG saved next to it. `optical_threshold_pair` is
    the preset's `(below, above)` contrast pair, or None for an `nsigma` run;
    it lands on every `OM_Stats` row.

    `domain_stat`/`domain_class_label` mirror `build_summary_figure`'s own
    arguments of the same name -- the pooled `DomainSizeStat`, and the OM
    class its domains were split from. None (no domain-size analysis ran)
    omits the `Domain_Size` sheet entirely rather than writing an empty one.

    `show_fwhm_v1` adds FWHM_v1/FWHM_v1_Stderr to each per-point sheet and
    FWHM_v1_Mean/FWHM_v1_Std to the Summary sheet — the pre-v3.4.0
    Gaussian-only width, kept only for this opt-in comparison. Off by default,
    since every other reporting surface shows only the correct width.

    Returns
    -------
    bytes
        A workbook ready to write to disk. It always carries a Summary sheet,
        even for a sample where nothing fitted — the metadata and the excluded
        list are then the whole record, which beats an absent file.
    """
    wb = Workbook()
    # A fresh Workbook arrives with one unnamed sheet; the real ones are
    # created with their titles below.
    wb.remove(wb.active)

    _add_summary_sheet(wb, sample_name, material_name, report_date, techniques, show_fwhm_v1)
    for technique in techniques:
        _add_data_sheet(wb, technique, show_fwhm_v1)
    _add_optical_sheets(wb, optical_frames, optical_threshold_pair)
    _add_domain_sheet(wb, domain_stat, domain_class_label)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
