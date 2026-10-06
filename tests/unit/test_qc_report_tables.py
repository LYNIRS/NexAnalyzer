"""
The QC Report's tables: the OM frame rows and the workbook they land in.

What these guard is agreement. Each half of the report ships a summary and the
rows behind it — the OM class table over the per-frame table, the Summary sheet
over the per-technique sheets — and the whole point of shipping both is that a
reader can average the second and land on the first. A change that quietly
filtered one and not the other would leave both valid, both plausible, and
disagreeing; the round-trip tests below are what catch it.

Until v5.0.0 these guarded four CSV files written beside the figures. The QC
Report writes one workbook instead, and two of those CSVs were near-duplicates
of sheets it already carried, so the tests moved to the sheets rather than
following the files into retirement.

Intensity, never area: `test_intensity_is_the_curve_maximum_not_the_area`
holds the line CLAUDE.md draws.
"""

import io

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from core.report.models import PeakStat
from modules.optical.io.frame_tables import (
    CLASS_COLUMNS,
    frame_class_rows,
    threshold_columns,
    frame_class_stats,
    frame_point_rows,
)
from modules.optical.processing.contrast import FrameResult, class_labels
from modules.spectra.io.results_excel import TechniqueResults, build_sample_results_xlsx
from modules.spectra.models.peak import FittedPeak, FitResult
from modules.spectra.processing.peak_metrics import aggregate_fit_results, peak_intensity


# ------------------------------------------------------------------ fixtures
def _frame(point, percentages, contrast_below=-3.2, contrast_above=4.1,
           ref_label="2L"):
    """A FrameResult carrying only the fields the tables read.

    The image arrays are one pixel each: these tables never look at them, and a
    realistic frame would make the fixture about segmentation instead.
    """
    return FrameResult(
        name=f"50x-{point}",
        point=point,
        frame_type="circular",
        ref_label=ref_label,
        labels=class_labels(ref_label),
        percentages=percentages,
        contrast_below=contrast_below,
        contrast_above=contrast_above,
        components_below=14,
        components_above=9,
        original=np.zeros((1, 1, 3)),
        overlay=np.zeros((1, 1, 3)),
        valid=np.ones((1, 1), dtype=bool),
        hist_centers=np.array([0.0, 1.0]),
        hist_counts=np.array([1, 2]),
        mode=190.0,
        sigma_l=2.0,
        sigma_r=2.5,
        sigma_noise=1.8,
        threshold_low=182.0,
        threshold_high=197.0,
        shoulder=False,
    )


def _peak(label="E2g+A1g", center=250.0, area=1000.0, fwhm=7.0):
    return FittedPeak(
        label=label,
        center=center,
        center_stderr=0.03,
        area=area,
        area_stderr=5.0,
        width_fwhm=fwhm,
        width_stderr=0.1,
        shape=0.5,
        component_curve=np.zeros(3),
    )


def _fit(peaks, r_squared=0.99):
    return FitResult(
        success=True,
        fitted_peaks=peaks,
        total_fit_curve=np.zeros(3),
        residuals=np.zeros(3),
        chi_squared=1.5,
        r_squared=r_squared,
        convergence_time=0.2,
    )


class _Data:
    def __init__(self, x, y):
        self.X = np.asarray(x, float)
        self.Y = np.asarray(y, float)


class _Spectrum:
    """The bare shape `TechniqueResults.point_spectra` reads."""

    def __init__(self, fit_result, y=(0.0, 5.0, 0.0)):
        self.fit_result = fit_result
        self.processed_data = _Data(np.arange(len(y), dtype=float), y)


def _frames_table(frames):
    return pd.DataFrame(
        frame_class_rows(frames),
        columns=list(CLASS_COLUMNS),
    )


def _workbook(**kwargs):
    defaults = dict(
        sample_name="TSM260803", material_name="WSe2", report_date="2026-09-18",
    )
    defaults.update(kwargs)
    return load_workbook(io.BytesIO(build_sample_results_xlsx(**defaults)))


def _sheet_frame(wb, title):
    rows = list(wb[title].iter_rows(values_only=True))
    return pd.DataFrame(rows[1:], columns=rows[0])


def _summary_block(wb, heading):
    """The header and body rows of one titled block on the Summary sheet.

    The sheet stacks several tables under bold headings rather than being one
    rectangle, so a block is read by finding its heading and stopping at the
    blank row that ends it.
    """
    rows = list(wb["Summary"].iter_rows(values_only=True))
    start = next(i for i, row in enumerate(rows) if row and row[0] == heading)
    header = rows[start + 1]
    body = []
    for row in rows[start + 2:]:
        if not row or row[0] is None:
            break
        body.append(row)
    return pd.DataFrame(body, columns=header)


# ------------------------------------------------------------- OM class table
class TestFrameClassStats:
    def test_one_row_per_class_in_label_order(self):
        frames = [_frame(p, (10.0, 70.0, 20.0)) for p in (1, 2, 3)]

        df = _frames_table(frames)

        assert list(df["Class"]) == ["Below 2L", "Bilayer", "Above 2L"]
        assert list(df["Position"]) == ["Below", "Reference", "Above"]
        assert list(df["N_Frames"]) == [3, 3, 3]

    def test_position_is_stable_across_reference_layers(self):
        """`Class` follows the layer, `Position` does not — which is what lets
        a 1L sample's table be stacked on a 2L sample's."""
        monolayer = [_frame(1, (10.0, 70.0, 20.0), ref_label="1L")]

        df = _frames_table(monolayer)

        assert list(df["Class"]) == ["Below 1L", "Monolayer", "Above 1L"]
        assert list(df["Position"]) == ["Below", "Reference", "Above"]

    def test_coverage_mean_and_std_match_the_frames(self):
        frames = [
            _frame(1, (10.0, 70.0, 20.0)),
            _frame(2, (14.0, 66.0, 20.0)),
        ]

        df = _frames_table(frames).set_index("Position")

        assert df.loc["Below", "Coverage_Mean_pct"] == pytest.approx(12.0)
        # ddof=1: two frames, one degree of freedom.
        assert df.loc["Below", "Coverage_Std_pct"] == pytest.approx(
            np.std([10.0, 14.0], ddof=1)
        )

    def test_a_single_frame_reports_zero_spread_not_nan(self):
        df = _frames_table([_frame(1, (10.0, 70.0, 20.0))])

        assert list(df["Coverage_Std_pct"]) == [0.0, 0.0, 0.0]

    def test_the_reference_class_has_no_contrast_of_its_own(self):
        """Contrast is measured relative to the reference film, so the
        reference row's cell is None rather than a 0.0 that reads as flat."""
        stats = {entry.label: entry for entry in frame_class_stats(
            [_frame(1, (10.0, 70.0, 20.0))]
        )}

        assert stats["Bilayer"].contrast_mean is None
        assert stats["Bilayer"].contrast_std is None
        assert stats["Below 2L"].contrast_mean == pytest.approx(-3.2)

    def test_no_frames_yields_no_rows_rather_than_a_table_of_zeros(self):
        assert frame_class_stats([]) == []

    def test_a_frame_without_the_class_does_not_turn_the_contrast_nan(self):
        """A frame with no above-class pixels records nan; the wafer's figure
        is taken over the frames that have the class (SU261001 printed
        "nan ± nan")."""
        stats = {entry.label: entry for entry in frame_class_stats([
            _frame(1, (1.0, 99.0, 0.0), contrast_above=float("nan")),
            _frame(2, (1.0, 98.0, 1.0), contrast_above=4.0),
            _frame(3, (1.0, 98.0, 1.0), contrast_above=6.0),
        ])}

        assert stats["Above 2L"].contrast_mean == pytest.approx(5.0)
        assert stats["Above 2L"].contrast_std == pytest.approx(np.std([4.0, 6.0], ddof=1))

    def test_a_class_absent_from_every_frame_has_no_contrast(self):
        """Empty, not nan and not 0.0: there is nothing to measure."""
        stats = {entry.label: entry for entry in frame_class_stats([
            _frame(1, (1.0, 99.0, 0.0), contrast_above=float("nan")),
            _frame(2, (1.0, 99.0, 0.0), contrast_above=float("nan")),
        ])}

        assert stats["Above 2L"].contrast_mean is None
        assert stats["Above 2L"].contrast_std is None
        assert stats["Below 2L"].contrast_mean == pytest.approx(-3.2)
        assert frame_class_rows([]) == []

    def _points(self, frames):
        from modules.optical.io.frame_tables import POINT_COLUMNS

        return pd.DataFrame(frame_point_rows(frames), columns=list(POINT_COLUMNS))

    def test_one_row_per_frame_sorted_by_point(self):
        frames = [_frame(p, (10.0, 70.0, 20.0)) for p in (3, 1, 2)]

        df = self._points(frames)

        assert list(df["Point"]) == [1, 2, 3]
        assert list(df["Frame"]) == ["50x-1", "50x-2", "50x-3"]

    def test_coverages_are_a_partition(self):
        row = self._points([_frame(1, (11.5, 69.3, 19.2))]).iloc[0]

        total = (row["Below_Coverage_pct"] + row["Reference_Coverage_pct"]
                 + row["Above_Coverage_pct"])
        assert total == pytest.approx(100.0)

    def test_the_threshold_landmarks_travel_with_the_row(self):
        """The diagnostic PNG's histogram marks, as numbers: a frame that
        segmented badly is legible here without opening the image."""
        row = self._points([_frame(1, (10.0, 70.0, 20.0))]).iloc[0]

        assert row["Green_Mode"] == pytest.approx(190.0)
        assert row["Threshold_Low"] == pytest.approx(182.0)
        assert row["Threshold_High"] == pytest.approx(197.0)
        assert row["Sigma_Noise"] == pytest.approx(1.8)

    def test_the_points_table_averages_to_the_class_table(self):
        """The reason both ship. If one ever starts filtering rows the other
        keeps, this is what fails."""
        frames = [
            _frame(1, (10.0, 70.0, 20.0)),
            _frame(2, (14.0, 66.0, 20.0)),
            _frame(3, (12.0, 68.0, 20.0)),
        ]

        points = self._points(frames)
        stats = _frames_table(frames).set_index("Position")

        for position, column in (
            ("Below", "Below_Coverage_pct"),
            ("Reference", "Reference_Coverage_pct"),
            ("Above", "Above_Coverage_pct"),
        ):
            assert stats.loc[position, "Coverage_Mean_pct"] == pytest.approx(
                points[column].mean()
            )
            assert stats.loc[position, "Coverage_Std_pct"] == pytest.approx(
                points[column].std(ddof=1)
            )

    def test_no_frames_yields_no_rows(self):
        assert frame_point_rows([]) == []


# -------------------------------------------------------------- the workbook
class TestOpticalSheets:
    def test_both_optical_sheets_are_written(self):
        wb = _workbook(optical_frames=[_frame(p, (10.0, 70.0, 20.0)) for p in (1, 2)])

        assert "OM_Stats" in wb.sheetnames
        assert "OM_Points" in wb.sheetnames

    def test_a_run_without_segmentation_writes_neither(self):
        """An empty sheet named OM_Stats claims a segmentation that never ran."""
        wb = _workbook()

        assert "OM_Stats" not in wb.sheetnames
        assert "OM_Points" not in wb.sheetnames

    def test_the_sheets_carry_the_rows_the_builders_produced(self):
        frames = [_frame(p, (10.0, 70.0, 20.0)) for p in (1, 2, 3)]
        wb = _workbook(optical_frames=frames)

        stats = _sheet_frame(wb, "OM_Stats")
        points = _sheet_frame(wb, "OM_Points")

        assert list(stats["Class"]) == ["Below 2L", "Bilayer", "Above 2L"]
        assert list(points["Point"]) == [1, 2, 3]

    def test_the_reference_contrast_cell_stays_empty(self):
        wb = _workbook(optical_frames=[_frame(1, (10.0, 70.0, 20.0))])

        stats = _sheet_frame(wb, "OM_Stats").set_index("Position")

        # Empty, not 0.0 — the reference film is what contrast is measured
        # against, so its own contrast is not a number that exists.
        assert pd.isna(stats.loc["Reference", "Contrast_Mean_pct"])
        assert stats.loc["Below", "Contrast_Mean_pct"] == pytest.approx(-3.2)


class TestTechniqueSheets:
    def _raman(self, pairs):
        return TechniqueResults(
            label="Raman",
            point_spectra=[(point, _Spectrum(fit)) for point, fit in pairs],
            stats=aggregate_fit_results(pairs),
            source_files={point: f"Raman_{point}.txt" for point, _ in pairs},
        )

    def test_one_row_per_peak_per_fit(self):
        pairs = [
            (1, _fit([_peak("E2g+A1g"), _peak("2LA", center=260.0)])),
            (2, _fit([_peak("E2g+A1g"), _peak("2LA", center=260.0)])),
        ]
        wb = _workbook(techniques=[self._raman(pairs)])

        df = _sheet_frame(wb, "Raman")

        assert len(df) == 4
        assert sorted(df["Peak_Label"].unique()) == ["2LA", "E2g+A1g"]

    def test_intensity_is_the_curve_maximum_not_the_area(self):
        """CLAUDE.md's line. The two differ by ~FWHM x 1.064, and shipping the
        area under an "Intensity" heading is what made v3.3.0's report disagree
        with its own CSV."""
        peak = _peak(area=1000.0, fwhm=7.0)
        wb = _workbook(techniques=[self._raman([(1, _fit([peak]))])])

        df = _sheet_frame(wb, "Raman")

        assert df.iloc[0]["Intensity"] == pytest.approx(peak_intensity(peak))
        assert df.iloc[0]["Intensity"] != pytest.approx(peak.area)
        assert "Area" not in df.columns

    def test_the_fits_r_squared_lands_on_every_one_of_its_rows(self):
        fit = _fit([_peak(), _peak("2LA")], r_squared=0.87)
        wb = _workbook(techniques=[self._raman([(1, fit)])])

        df = _sheet_frame(wb, "Raman")

        assert list(df["R_Squared"]) == [pytest.approx(0.87)] * 2

    def test_a_technique_with_no_fits_gets_no_sheet(self):
        wb = _workbook(techniques=[TechniqueResults(label="PL")])

        assert "PL" not in wb.sheetnames

    def test_both_techniques_get_their_own_sheet(self):
        raman = self._raman([(1, _fit([_peak()]))])
        pl = TechniqueResults(
            label="PL",
            point_spectra=[(1, _Spectrum(_fit([_peak("Exciton", center=770.0)])))],
            stats=aggregate_fit_results([(1, _fit([_peak("Exciton", center=770.0)]))]),
        )
        wb = _workbook(techniques=[raman, pl])

        assert "Raman" in wb.sheetnames
        assert "PL" in wb.sheetnames


class TestSummarySheet:
    def test_the_superseded_gaussian_width_stays_out_by_default(self):
        """FWHM_v1 under-reports every width by 59-108%. It is an opt-in
        comparison, not something a saved file carries unlabelled."""
        stats = [PeakStat("E2g+A1g", 9, 250.0, 0.4, 1200.0, 30.0, 7.0, 0.2,
                          fwhm_v1_mean=3.0, fwhm_v1_std=0.1)]
        wb = _workbook(techniques=[TechniqueResults(label="Raman", stats=stats)])

        block = _summary_block(wb, "Raman fit summary (mean ± std)")

        assert not [c for c in block.columns if "v1" in str(c).lower()]

    def test_an_unmeasurable_width_is_blank_not_zero(self):
        """The empirical "Raw" row on a spectrum with no half-maximum crossing.
        A 0.0 there would read as a measurement of nothing."""
        stats = [PeakStat("Raw", 9, 750.0, 3.0, 5000.0, 200.0, None, None)]
        wb = _workbook(techniques=[TechniqueResults(label="PL", stats=stats)])

        block = _summary_block(wb, "PL summary (empirical + fitted, mean ± std)")

        assert block.iloc[0]["FWHM_Mean"] is None
        assert block.iloc[0]["FWHM_Std"] is None

    def test_the_points_sheet_aggregates_to_the_summary_block(self):
        """Same round-trip as the OM pair, through the real aggregation: the
        Summary block must be the technique sheet summarised, not a parallel
        computation that can drift from it."""
        pairs = [
            (1, _fit([_peak(center=250.0, fwhm=7.0)])),
            (2, _fit([_peak(center=250.6, fwhm=7.2)])),
            (3, _fit([_peak(center=250.2, fwhm=6.9)])),
        ]
        wb = _workbook(techniques=[TechniqueResults(
            label="Raman",
            point_spectra=[(point, _Spectrum(fit)) for point, fit in pairs],
            stats=aggregate_fit_results(pairs),
        )])

        points = _sheet_frame(wb, "Raman")
        summary = _summary_block(wb, "Raman fit summary (mean ± std)")

        assert summary.iloc[0]["N"] == len(points)
        assert summary.iloc[0]["Center_Mean"] == pytest.approx(points["Center"].mean())
        assert summary.iloc[0]["FWHM_Mean"] == pytest.approx(points["FWHM"].mean())
        assert summary.iloc[0]["Intensity_Mean"] == pytest.approx(
            points["Intensity"].mean()
        )

    def test_a_sample_where_nothing_fitted_still_gets_a_summary(self):
        """The metadata and the excluded list are then the whole record, which
        beats an absent file."""
        wb = _workbook(techniques=[TechniqueResults(
            label="Raman", errors=[(1, "did not converge")],
        )])

        assert "Summary" in wb.sheetnames
        assert wb.sheetnames[0] == "Summary"


class TestThresholdProvenance:
    """One preset pair segments every wafer, but a preset can be edited, so a
    sheet that does not carry the pair cannot be told apart from one produced
    before the edit. "Which pair ran" is not metadata, it is part of the
    measurement.
    """

    def test_the_pair_lands_on_every_class_row(self):
        frames = [_frame(p, (10.0, 70.0, 20.0)) for p in (1, 2)]

        df = pd.DataFrame(frame_class_rows(frames, (6.0, 4.25)),
                          columns=list(CLASS_COLUMNS))

        assert list(df["Threshold_Below_pct"]) == [6.0] * 3
        assert list(df["Threshold_Above_pct"]) == [4.25] * 3

    def test_an_nsigma_run_has_no_pair_to_name(self):
        """No absolute pair means no contrast cut exists to record — blank,
        rather than a zero that would read as a measurement."""
        assert threshold_columns(None) == [None, None]

    def test_the_pair_reaches_the_workbook(self):
        frames = [_frame(p, (10.0, 70.0, 20.0)) for p in (1, 2)]
        wb = _workbook(optical_frames=frames, optical_threshold_pair=(6.0, 4.25))

        stats = _sheet_frame(wb, "OM_Stats")

        assert stats.iloc[0]["Threshold_Below_pct"] == pytest.approx(6.0)
        assert stats.iloc[0]["Threshold_Above_pct"] == pytest.approx(4.25)

    def test_an_nsigma_run_reaches_the_workbook_too(self):
        frames = [_frame(p, (10.0, 70.0, 20.0)) for p in (1, 2)]
        wb = _workbook(optical_frames=frames)

        stats = _sheet_frame(wb, "OM_Stats")

        assert stats["Threshold_Below_pct"].isna().all()
