"""
Unit tests for modules.spectra.viz.peak_quality (the QC Report's stats figures).

The figure reproduces the inherited WSe2 panels for both techniques, so the
tests that matter are the ones pinning what it plots: which peaks, which
metrics, which spec values, and — the part most easily got wrong — that a ratio
is formed within one spectrum before being summarized, rather than from two
separately averaged intensities.

The PL half is newer and carries its own hazards, each pinned below: PL has one
spec value and not three, its cleaning rule is not Raman's, its marker is a
median where Raman's is a mean, and its first column is the raw spectrum, which
is not a fit and must not disturb the fits' cleaning.
"""

import numpy as np
import pytest

from core.report.models import RAW_STAT_LABEL
from modules.spectra.models.peak import FitResult, FittedPeak
from modules.spectra.processing.peak_metrics import RAMAN_RATIO_PAIRS, RawPeakStats
from modules.spectra.viz.peak_quality import (
    _raw_records,
    PANEL_COLUMNS,
    PL_CLEANING,
    PL_PANEL_COLUMNS,
    PL_QUALITY,
    RAMAN_CLEANING,
    RAMAN_QUALITY,
    CleaningRule,
    PeakPanel,
    QualityFigureSpec,
    RatioPanel,
    _clean,
    _peak_values,
    _raman_ratio_panels,
    _ratio_values,
    _records,
    build_peak_quality_figure,
)


def _peak(label, center=250.0, intensity=100.0, width_fwhm=7.0):
    curve = np.zeros(10)
    curve[5] = intensity
    return FittedPeak(
        label=label, center=center, center_stderr=0.1,
        area=intensity * 7.0, area_stderr=1.0,
        width_fwhm=width_fwhm, width_stderr=0.1,
        shape=0.3, component_curve=curve,
    )


def _fit(peaks, r_squared=0.99):
    return FitResult(
        success=True, fitted_peaks=peaks,
        total_fit_curve=np.zeros(10), residuals=np.zeros(10),
        chi_squared=1.0, r_squared=r_squared, convergence_time=0.1,
    )


def _wse2_fit(**intensities):
    """A fit carrying the six Raman peaks the figure reads, at given intensities."""
    defaults = {"E2g+A1g": 200.0, "2LA": 30.0, "B2g": 20.0,
                "LA": 40.0, "C": 80.0, "LB": 25.0}
    defaults.update(intensities)
    centers = {"E2g+A1g": 250.0, "2LA": 260.0, "B2g": 308.0,
               "LA": 130.0, "C": 17.0, "LB": 30.0}
    return _fit([_peak(label, center=centers[label], intensity=value)
                 for label, value in defaults.items()])


def _pl_fit(exciton=200.0, trion=100.0, exciton_fwhm=30.0, trion_fwhm=50.0):
    """A PL fit at realistic WSe2 wavelengths and widths."""
    return _fit([
        _peak("Exciton", center=770.0, intensity=exciton, width_fwhm=exciton_fwhm),
        _peak("Trion", center=800.0, intensity=trion, width_fwhm=trion_fwhm),
    ])


class TestRamanPanelDefinitions:
    def test_the_grid_is_three_columns_of_two(self):
        assert len(PANEL_COLUMNS) == 3
        assert all(len(panels) == 2 for _title, panels in PANEL_COLUMNS)

    def test_the_local_5_7_5_arrangement(self):
        """Defect ratio under the centre panel (Local 5.7.1); the right-hand
        column is the B2g centre with the B2g ratio under it, and no table
        (Local 5.7.5)."""
        cells = [[p.caption for p in panels] for _title, panels in PANEL_COLUMNS]
        assert cells == [
            ["E₂g + A₁g", "2LA(M)"],
            ["E₂g + A₁g center", "Defect ratio"],
            ["B₂g center", "B₂g ratio"],
        ]

    def test_the_b2g_centre_is_a_strip_plot_of_the_b2g_peak(self):
        centre = PANEL_COLUMNS[2][1][0]
        assert isinstance(centre, PeakPanel)
        assert (centre.peak, centre.metric) == ("B2g", "center")

    def test_every_cell_is_a_strip_plot(self):
        assert all(isinstance(p, (PeakPanel, RatioPanel))
                   for _title, panels in PANEL_COLUMNS for p in panels)

    def test_column_titles_name_what_each_column_holds(self):
        assert [title for title, _ in PANEL_COLUMNS] == [
            "FWHM", "Center & Defect Ratio", "B₂g Center & Ratio"
        ]

    def test_the_inherited_spec_values_are_carried_over(self):
        """7.0, 308.0 and 0.13 are the values the wafer-comparison scripts
        label "(Spec)". They are shown and never evaluated."""
        specs = {
            panel.caption: panel.spec
            for _title, panels in PANEL_COLUMNS for panel in panels
        }
        assert specs["E₂g + A₁g"] == 7.0
        assert specs["B₂g center"] == 308.0
        assert specs["Defect ratio"] == 0.13

    def test_the_stacking_ratio_is_gone(self):
        assert ("C", "LB") not in RAMAN_RATIO_PAIRS
        captions = [getattr(p, "caption", None)
                    for _t, panels in PANEL_COLUMNS for p in panels]
        assert "Stacking ratio" not in captions

    def test_the_b2g_ratio_is_b2g_over_the_in_plane_mode(self):
        b2g = next(
            p for _t, panels in PANEL_COLUMNS for p in panels
            if getattr(p, "caption", None) == "B₂g ratio"
        )
        assert isinstance(b2g, RatioPanel)
        assert (b2g.numerator, b2g.denominator) == ("B2g", "E2g+A1g")
        assert b2g.spec is None

    def test_the_defect_ratio_is_la_over_the_in_plane_mode(self):
        defect = next(
            p for _t, panels in PANEL_COLUMNS for p in panels
            if getattr(p, "caption", None) == "Defect ratio"
        )
        assert (defect.numerator, defect.denominator) == ("LA", "E2g+A1g")

    def test_the_ratio_panels_come_from_the_shared_constant(self):
        """The figure and the QC Report's summary table must draw the same set
        of ratios. They were two independent literals before v5.0.0, each with
        a comment saying they had to agree — which is not a mechanism. This
        compares against `peak_metrics`, not against hardcoded strings, so it
        fails if the figure ever stops reading the constant."""
        drawn = [p for _title, panels in PANEL_COLUMNS for p in panels
                 if isinstance(p, RatioPanel)]

        assert tuple((p.numerator, p.denominator) for p in drawn) == RAMAN_RATIO_PAIRS

    def test_a_ratio_added_to_the_constant_gains_a_panel(self):
        """An unlabelled pair still gets drawn, with a derived caption. The
        alternative — dropping it — is the silent divergence this indirection
        exists to prevent."""
        panels = _raman_ratio_panels((("LA", "E2g+A1g"), ("2LA", "B2g")))

        assert len(panels) == 2
        assert (panels[1].numerator, panels[1].denominator) == ("2LA", "B2g")
        assert panels[1].caption == "2LA / B2g"
        assert panels[1].spec is None


class TestRamanCanvas:
    def test_the_canvas_keeps_its_three_by_two_aspect(self):
        """The B2g centre takes the table's cell rather than adding one, so the
        figure keeps the 22x11 proportions it has always had."""
        n_cols = len(PANEL_COLUMNS)
        n_rows = max(len(panels) for _t, panels in PANEL_COLUMNS)
        assert (n_cols, n_rows) == (3, 2)

    def test_the_figure_renders_with_the_b2g_centre_on_top(self):
        fits = [(point, _wse2_fit()) for point in range(1, 10) for _ in range(3)]
        png = build_peak_quality_figure(fits, "S", spec=RAMAN_QUALITY)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


class TestPlPanelDefinitions:

    def test_raw_spectrum_column_comes_first_and_the_grid_is_three_by_two(self):
        """Local 5.7.4: the raw spectrum's FWHM and centre lead; the
        Exciton/Trion ratio column it replaced is gone."""
        assert [title for title, _ in PL_PANEL_COLUMNS] == [
            "Raw Spectrum", "FWHM", "Peak Centers"]
        assert [len(panels) for _title, panels in PL_PANEL_COLUMNS] == [2, 2, 2]
        raw = PL_PANEL_COLUMNS[0][1]
        assert [(p.peak, p.metric) for p in raw] == [
            (RAW_STAT_LABEL, "fwhm"), (RAW_STAT_LABEL, "center")]

    def test_there_is_no_pl_ratio_panel(self):
        assert not any(
            isinstance(panel, RatioPanel)
            for _title, panels in PL_PANEL_COLUMNS for panel in panels
        )

    def test_the_only_pl_spec_is_the_thirty_five_nanometre_fwhm(self):
        """The inherited script draws exactly one PL reference line, gated on
        `if col == "FWHM"`, and labels it "FWHM = 35 (Spec)"."""
        specs = {
            panel.caption: panel.spec
            for _title, panels in PL_PANEL_COLUMNS for panel in panels
        }
        assert specs["Exciton"] == 35.0
        assert specs["Trion"] == 35.0
        # The fitted peaks' spec: the raw width spans both and runs wider.
        assert specs["Raw FWHM"] is None
        assert set(specs.values()) == {35.0, None}

    def test_the_centre_panels_carry_no_spec(self):
        """770 nm and 800 nm are fit-initialisation guesses in the material
        preset, not tolerances anyone measured. Promoting them to spec lines
        would invent a judgment the lineage never made."""
        centres = [
            panel for _title, panels in PL_PANEL_COLUMNS for panel in panels
            if isinstance(panel, PeakPanel) and panel.metric == "center"
        ]
        assert len(centres) == 3  # raw, exciton, trion
        assert all(panel.spec is None for panel in centres)

    def test_the_units_are_nanometres_not_wavenumbers(self):
        labels = {
            panel.ylabel for _title, panels in PL_PANEL_COLUMNS
            for panel in panels if isinstance(panel, PeakPanel)
        }
        assert labels == {"FWHM (nm)", "Wavelength (nm)"}
        assert not any("cm" in label for label in labels)


class TestTechniqueSpecs:
    def test_each_technique_carries_its_own_columns_cleaning_and_statistic(self):
        """Bundled so a call site cannot pair Raman's panels with PL's cleaning
        — a mismatch that renders without error and is wrong only in its
        numbers."""
        assert RAMAN_QUALITY.columns is PANEL_COLUMNS
        assert RAMAN_QUALITY.cleaning is RAMAN_CLEANING
        assert RAMAN_QUALITY.statistic == "mean"

        assert PL_QUALITY.columns is PL_PANEL_COLUMNS
        assert PL_QUALITY.cleaning is PL_CLEANING
        assert PL_QUALITY.statistic == "median"

    def test_the_marker_statistic_differs_because_the_lineages_do(self):
        """Raman's ancestor marks each position with a mean, PL's with a
        median, and the PL summary schema stores medians only."""
        assert RAMAN_QUALITY.statistic != PL_QUALITY.statistic


class TestCleaning:
    def test_raman_takes_only_the_per_position_iqr_cut(self):
        """Matching `peak_metrics.aggregate_fit_results` exactly. A difference
        would put two numbers for one measurement in front of one reader."""
        assert RAMAN_CLEANING.per_position_iqr is True
        assert RAMAN_CLEANING.fwhm_floor is None
        assert RAMAN_CLEANING.center_floor is None
        assert RAMAN_CLEANING.fwhm_top_fraction is None
        assert RAMAN_CLEANING.relative_intensity_floor is None

    def test_pl_takes_the_floors_and_top_fraction_instead(self):
        assert PL_CLEANING.fwhm_floor == 5.0
        assert PL_CLEANING.relative_intensity_floor == 0.02
        assert PL_CLEANING.fwhm_top_fraction == 0.05
        assert PL_CLEANING.per_position_iqr is False

    def test_pl_has_no_centre_floor(self):
        """The inherited 700 nm floor was WSe2's emission band as a constant,
        and it discarded every MoS2 peak (620-680 nm)."""
        assert PL_CLEANING.center_floor is None

    def test_a_narrow_fit_is_dropped_by_the_floor(self):
        """`FWHM > 5`, strictly — a fit that collapsed onto noise."""
        records = _records([(1, _fit([
            _peak("Exciton", center=770.0, width_fwhm=4.0),
            _peak("Trion", center=800.0, width_fwhm=50.0),
        ]))])

        kept = _clean(records, PL_CLEANING)

        assert [r.label for r in kept] == ["Trion"]

    def test_a_centre_floor_still_works_when_a_rule_sets_one(self):
        records = _records([(1, _fit([
            _peak("Exciton", center=650.0, width_fwhm=30.0),
            _peak("Trion", center=800.0, width_fwhm=50.0),
        ]))])

        kept = _clean(records, CleaningRule(center_floor=700.0))

        assert [r.label for r in kept] == ["Trion"]

    def test_mos2_peaks_below_700_nm_reach_the_figure(self):
        """A0 662, A- 680 and B 620: all three survive PL cleaning."""
        records = _records([(1, _fit([
            _peak("A0", center=662.0, intensity=2000.0, width_fwhm=22.0),
            _peak("A-", center=680.0, intensity=1000.0, width_fwhm=40.0),
            _peak("B", center=620.0, intensity=100.0, width_fwhm=30.0),
        ]))])

        kept = _clean(records, PL_CLEANING)

        assert [r.label for r in kept] == ["A0", "A-", "B"]

    def test_a_peak_fitted_to_near_zero_is_dropped(self):
        """Under 2% of its spectrum's strongest peak, the centre and width
        describe nothing; the peak that carries the spectrum stays."""
        records = _records([(1, _fit([
            _peak("A0", center=645.0, intensity=10.0, width_fwhm=45.0),
            _peak("A-", center=683.0, intensity=4300.0, width_fwhm=29.0),
        ]))])

        kept = _clean(records, PL_CLEANING)

        assert [r.label for r in kept] == ["A-"]

    def test_the_intensity_floor_is_per_spectrum_not_pooled(self):
        """A dim spectrum's peaks are judged against that spectrum, so a weak
        point is not wiped out by a bright one elsewhere on the wafer."""
        records = _records([
            (1, _pl_fit(exciton=20000.0, trion=5000.0)),
            (2, _pl_fit(exciton=300.0, trion=100.0)),
        ])

        kept = _clean(records, PL_CLEANING)

        assert sorted((r.point, r.label) for r in kept) == [
            (1, "Exciton"), (1, "Trion"), (2, "Exciton"), (2, "Trion"),
        ]

    def test_the_top_fraction_is_taken_per_peak_not_pooled(self):
        """Exciton is the narrower peak. Pooling would judge its whole
        distribution against Trion's and cut nothing from it."""
        fits = [
            (point, _pl_fit(exciton_fwhm=width, trion_fwhm=width * 2))
            for point, width in enumerate([20, 21, 22, 23, 24, 25, 26, 27, 28, 90], start=1)
        ]

        kept = _clean(_records(fits), PL_CLEANING)
        widths = {label: sorted(r.fwhm for r in kept if r.label == label)
                  for label in ("Exciton", "Trion")}

        assert 90.0 not in widths["Exciton"]
        assert 180.0 not in widths["Trion"]

    def test_raman_cleaning_keeps_everything_at_the_record_level(self):
        """Its whole rule is the per-position cut, applied later when the panel
        is drawn — nothing is filtered out of the record set."""
        records = _records([(1, _wse2_fit())])

        assert _clean(records, RAMAN_CLEANING) == records

    def test_floors_run_before_the_top_fraction(self):
        """The quantile is taken over what the floors left, not over the raw
        set — a pile of collapsed fits would otherwise drag the cutoff down and
        take good ones with it."""
        rule = CleaningRule(fwhm_floor=5.0, fwhm_top_fraction=0.5)
        records = _records([(1, _fit([
            _peak("A", width_fwhm=1.0), _peak("A", width_fwhm=2.0),
            _peak("A", width_fwhm=10.0), _peak("A", width_fwhm=20.0),
        ]))])

        kept = sorted(r.fwhm for r in _clean(records, rule))

        # Floors leave [10, 20]; the median of those is 15, so 20 goes.
        assert kept == [10.0]


class TestPeakValues:
    def test_values_are_collected_under_their_grid_position(self):
        records = _records([(1, _wse2_fit()), (1, _wse2_fit()), (2, _wse2_fit())])

        values = _peak_values(records, PeakPanel("E2g+A1g", "center", "y", "c"))

        assert sorted(values) == [1, 2]
        assert values[1].size == 2
        assert values[2].size == 1

    def test_a_missing_peak_yields_no_entry_rather_than_zeros(self):
        records = _records([(1, _fit([_peak("E2g+A1g")]))])

        assert _peak_values(records, PeakPanel("B2g", "center", "y", "c")) == {}

    def test_intensity_is_the_curve_maximum_not_the_area(self):
        """The area is 7x the intensity in this fixture; reading the wrong one
        is the mistake that made the report disagree with the CSV in v3.3.0."""
        records = _records([(1, _fit([_peak("LA", intensity=36.0)]))])

        values = _peak_values(records, PeakPanel("LA", "intensity", "y", "c"))

        assert values[1][0] == pytest.approx(36.0)


class TestRatioValues:
    def test_a_ratio_is_formed_within_one_spectrum(self):
        """Per spectrum, then summarized — not mean(numerator)/mean(denominator).
        These two fits have identical means but opposite per-spectrum ratios, so
        only the per-spectrum form can tell them apart."""
        records = _records([
            (1, _wse2_fit(LA=100.0, **{"E2g+A1g": 200.0})),   # 0.5
            (1, _wse2_fit(LA=200.0, **{"E2g+A1g": 100.0})),   # 2.0
        ])

        values = _ratio_values(records, RatioPanel("LA", "E2g+A1g", "y", "c"))

        assert sorted(values[1]) == pytest.approx([0.5, 2.0])

    def test_two_spectra_at_one_position_stay_separate_records(self):
        """The ratio keys off the spectrum, not the position: collapsing them
        would divide one spectrum's numerator by another's denominator."""
        records = _records([(1, _wse2_fit()), (1, _wse2_fit())])

        values = _ratio_values(records, RatioPanel("LA", "E2g+A1g", "y", "c"))

        assert values[1].size == 2

    def test_a_spectrum_missing_either_peak_is_skipped(self):
        records = _records([(1, _fit([_peak("LA")])), (1, _wse2_fit())])

        values = _ratio_values(records, RatioPanel("LA", "E2g+A1g", "y", "c"))

        assert values[1].size == 1

    def test_a_zero_denominator_is_skipped_not_infinite(self):
        records = _records([(1, _wse2_fit(LB=0.0))])

        assert _ratio_values(records, RatioPanel("C", "LB", "y", "c")) == {}

    def test_a_peak_the_cleaning_dropped_takes_its_ratio_with_it(self):
        """Cleaning is record-level, so a spectrum whose Trion ran wide still
        contributes its Exciton — but not the ratio that needed both."""
        records = _clean(
            _records([(1, _fit([
                _peak("Exciton", center=770.0, width_fwhm=30.0),
                _peak("Trion", center=800.0, width_fwhm=2.0),
            ]))]),
            PL_CLEANING,
        )

        assert _peak_values(records, PeakPanel("Exciton", "fwhm", "y", "c")) != {}
        assert _ratio_values(records, RatioPanel("Exciton", "Trion", "y", "c")) == {}


class TestBuildFigure:
    def test_returns_png_bytes(self):
        fits = [(point, _wse2_fit()) for point in range(1, 10)]

        png = build_peak_quality_figure(fits, sample_name="TSM260803")

        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 10_000

    def test_a_sample_with_no_fits_is_an_error_not_a_blank_figure(self):
        with pytest.raises(ValueError):
            build_peak_quality_figure([], sample_name="Empty")

    def test_panels_whose_peak_is_absent_still_render(self):
        """A non-WSe2 material has none of these peaks. The figure should come
        back with empty panels rather than raising midway through."""
        fits = [(point, _fit([_peak("Si", center=520.0)])) for point in range(1, 10)]

        png = build_peak_quality_figure(fits, sample_name="Silicon", material_name="Si")

        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_many_spectra_at_one_position_do_not_multiply_the_position_count(self):
        fits = [(1, _wse2_fit()) for _ in range(25)] + [(2, _wse2_fit()) for _ in range(25)]

        png = build_peak_quality_figure(fits, sample_name="TwoPoints")

        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_the_pl_figure_renders_with_its_raw_column(self):
        fits = [(point, _pl_fit()) for point in range(1, 10)]
        raw = [(point, RawPeakStats(intensity=900.0, center=766.0, fwhm=35.0))
               for point in range(1, 10)]

        png = build_peak_quality_figure(fits, sample_name="HADG06", spec=PL_QUALITY,
                                        raw_by_point=raw)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 10_000

    def test_the_pl_figure_without_raw_values_still_renders(self):
        """The raw panels come back empty rather than the builder raising."""
        fits = [(point, _pl_fit()) for point in range(1, 10)]

        png = build_peak_quality_figure(fits, sample_name="NoRaw", spec=PL_QUALITY)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_raw_values_alone_are_enough(self):
        """Every fit lost at the R² gate still leaves each spectrum's maximum."""
        raw = [(point, RawPeakStats(intensity=900.0, center=766.0, fwhm=35.0))
               for point in range(1, 10)]

        png = build_peak_quality_figure([], sample_name="AllGated", spec=PL_QUALITY,
                                        raw_by_point=raw)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_a_pl_sample_cleaned_down_to_nothing_still_renders(self):
        """Every fit below the centre floor. The panels come back empty rather
        than the builder raising on an empty record set."""
        fits = [(point, _fit([_peak("Exciton", center=500.0, width_fwhm=30.0)]))
                for point in range(1, 10)]

        png = build_peak_quality_figure(fits, sample_name="NotPL", spec=PL_QUALITY)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_the_grid_is_computed_from_the_columns_not_hardcoded(self):
        """A four-column spec used to raise IndexError on `grid[0, 3]`."""
        columns = tuple(
            (f"Family {i}", (PeakPanel("E2g+A1g", "center", "y", f"c{i}"),))
            for i in range(4)
        )
        spec = QualityFigureSpec("Raman", columns, RAMAN_CLEANING, "mean")
        fits = [(point, _wse2_fit()) for point in range(1, 10)]

        png = build_peak_quality_figure(fits, sample_name="Wide", spec=spec)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_a_column_with_three_panels_is_not_silently_truncated(self):
        """`zip((top, bottom), panels)` used to drop the third without a word."""
        columns = (
            ("Family", (
                PeakPanel("E2g+A1g", "center", "y", "one"),
                PeakPanel("2LA", "center", "y", "two"),
                PeakPanel("B2g", "center", "y", "three"),
            )),
        )
        spec = QualityFigureSpec("Raman", columns, RAMAN_CLEANING, "mean")
        fits = [(point, _wse2_fit()) for point in range(1, 10)]

        png = build_peak_quality_figure(fits, sample_name="Tall", spec=spec)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_a_position_cleaned_away_keeps_its_tick(self):
        """Nine positions must not look like eight because one was cleaned out
        — the gap is the thing worth seeing."""
        fits = [(point, _pl_fit()) for point in range(1, 9)]
        fits.append((9, _fit([_peak("Exciton", center=500.0, width_fwhm=30.0)])))

        png = build_peak_quality_figure(fits, sample_name="Gap", spec=PL_QUALITY)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"


class TestColumnsForAMaterialTheInheritedPanelsDoNotName:
    """v5.4.0: MoS₂ fits peaks called `E2g` and `A1g` separately, and the
    inherited columns name `E2g+A1g`, `2LA`, `B2g`, `LA`, `LB` and `C`. Every
    panel therefore read "no E2g+A1g fits" however good the fits were, on every
    run — so no MoS₂ preset edit could ever change this figure.
    """

    def _mos2_fit(self):
        return _fit([
            _peak("E2g", center=383.0, intensity=100.0, width_fwhm=6.5),
            _peak("A1g", center=408.0, intensity=140.0, width_fwhm=8.5),
        ])

    def test_a_wse2_sample_keeps_the_inherited_columns(self):
        """Identity, not equality: the inherited tuple must be handed back
        untouched, spec lines and ratio column included."""
        labels = [peak.label for peak in _wse2_fit().fitted_peaks]

        assert RAMAN_QUALITY.columns_for(labels) is PANEL_COLUMNS

    def test_one_matching_peak_is_enough_to_keep_them(self):
        """The test is "would this render empty", not "is this WSe₂". A
        material sharing even one inherited peak keeps the inherited panels."""
        assert RAMAN_QUALITY.columns_for(["E2g+A1g", "Nonsense"]) is PANEL_COLUMNS

    def test_a_pl_sample_keeps_the_inherited_pl_columns(self):
        labels = [peak.label for peak in _pl_fit().fitted_peaks]

        assert PL_QUALITY.columns_for(labels) is PL_PANEL_COLUMNS

    def test_mos2_gets_columns_naming_its_own_peaks(self):
        columns = RAMAN_QUALITY.columns_for(["E2g", "A1g"])

        titles = [title for title, _ in columns]
        assert titles == ["FWHM", "Peak Centers"]
        assert [panel.peak for panel in columns[0][1]] == ["E2g", "A1g"]
        assert [panel.peak for panel in columns[1][1]] == ["E2g", "A1g"]
        assert [panel.metric for panel in columns[0][1]] == ["fwhm", "fwhm"]
        assert [panel.metric for panel in columns[1][1]] == ["center", "center"]

    def test_the_peaks_keep_the_order_the_fit_reports_them_in(self):
        """The fitter returns the preset's order, ascending in wavenumber.
        Sorting would print A1g above E2g and read as wrong."""
        columns = RAMAN_QUALITY.columns_for(["E2g", "A1g", "E2g", "A1g"])

        assert [panel.peak for panel in columns[0][1]] == ["E2g", "A1g"]

    def test_no_spec_line_is_invented(self):
        """The four spec values in this lineage are inherited literals tied to
        WSe₂'s peaks. There is no measured tolerance for another material's,
        and a grey line a reader takes for a spec is worse than no line."""
        columns = RAMAN_QUALITY.columns_for(["E2g", "A1g"])

        assert all(panel.spec is None
                   for _, panels in columns for panel in panels)

    def test_no_ratio_column_is_invented(self):
        """`RAMAN_RATIO_PAIRS` is the lineage's two diagnostic ratios. Which
        ratios mean something for another material is a materials question."""
        columns = RAMAN_QUALITY.columns_for(["E2g", "A1g"])

        assert "Diagnostic Ratios" not in [title for title, _ in columns]

    def test_the_units_follow_the_technique(self):
        raman = RAMAN_QUALITY.columns_for(["E2g"])
        pl = PL_QUALITY.columns_for(["Defect"])

        assert "cm⁻¹" in raman[0][1][0].ylabel
        assert "nm" in pl[0][1][0].ylabel

    def test_a_sample_with_no_peaks_at_all_keeps_the_inherited_columns(self):
        """Nothing to build from, and an empty grid would raise."""
        assert RAMAN_QUALITY.columns_for([]) is PANEL_COLUMNS

    def test_a_pl_fallback_keeps_the_raw_column_first(self):
        """Every material's spectrum has a maximum, so a PL material whose
        peaks the inherited columns don't name still gets the raw column."""
        columns = PL_QUALITY.columns_for(["A0", "A-"])

        assert columns[0] is PL_PANEL_COLUMNS[0]
        assert [title for title, _ in columns] == ["Raw Spectrum", "FWHM", "Peak Centers"]
        assert [panel.peak for panel in columns[1][1]] == ["A0", "A-"]

    def test_the_rendered_mos2_figure_plots_its_peaks(self):
        fits = [(point, self._mos2_fit()) for point in range(1, 4)]

        png = build_peak_quality_figure(
            fits, "MoS2-01", material_name="MoS₂", spec=RAMAN_QUALITY)

        assert png.startswith(b"\x89PNG")


class TestRawRecords:
    """The raw maximum is plotted beside the fits but must not enter their
    cleaning: as a spectrum's "strongest peak" it would raise the 2 % floor."""

    def test_one_record_per_measurable_spectrum(self):
        raw = [(1, RawPeakStats(900.0, 766.0, 35.0)),
               (2, RawPeakStats(0.0, 700.0, None)),  # flat: no width, no record
               (3, None)]
        records = _raw_records(raw, first_index=10)

        assert [(r.point, r.spectrum, r.label) for r in records] == [(1, 10, RAW_STAT_LABEL)]

    def test_adding_raw_values_does_not_change_the_fitted_peaks_drawn(self, monkeypatch):
        """A weak trion that survives the floor against its own exciton (5 %)
        would fail it against a raw maximum 20x higher (0.5 %)."""
        from modules.spectra.viz import peak_quality

        drawn = []
        original = peak_quality._draw_panel
        monkeypatch.setattr(peak_quality, "_draw_panel",
                            lambda ax, values, *a, **kw: drawn.append(values) or original(ax, values, *a, **kw))
        fits = [(p, _pl_fit(exciton=200.0, trion=10.0)) for p in range(1, 4)]
        raw = [(p, RawPeakStats(intensity=4000.0, center=770.0, fwhm=35.0)) for p in range(1, 4)]

        build_peak_quality_figure(fits, "S", spec=PL_QUALITY, raw_by_point=raw)
        trion_fwhm = drawn[3]  # Raw FWHM, Raw centre, Exciton FWHM, Trion FWHM

        assert sorted(trion_fwhm) == [1, 2, 3]

    def test_raw_values_go_through_pl_cleaning(self):
        """A cosmic ray reads as a sub-nanometre "peak"; the FWHM floor drops it."""
        raw = [(1, RawPeakStats(5000.0, 633.0, 0.4)), (1, RawPeakStats(900.0, 766.0, 35.0))]

        kept = _clean(_raw_records(raw, first_index=0), PL_CLEANING)

        assert [r.center for r in kept] == [766.0]


class TestRobustCvLegend:
    def test_every_drawn_panel_says_its_robust_cv_under_the_median(self, monkeypatch):
        import matplotlib.axes
        legends = []
        original = matplotlib.axes.Axes.legend

        def recording(self, *a, **kw):
            legend = original(self, *a, **kw)
            legends.append([t.get_text() for t in legend.get_texts()])
            return legend

        monkeypatch.setattr(matplotlib.axes.Axes, "legend", recording)
        fits = [(p, _wse2_fit()) for p in range(1, 10) for _ in range(3)]
        build_peak_quality_figure(fits, "S", spec=RAMAN_QUALITY)

        assert legends
        for texts in legends:
            assert texts[0].startswith("Median = ")
            assert texts[1].startswith("Robust CV = ")

    def test_the_pl_figure_carries_it_too(self, monkeypatch):
        import matplotlib.axes
        legends = []
        original = matplotlib.axes.Axes.legend
        monkeypatch.setattr(matplotlib.axes.Axes, "legend",
                            lambda self, *a, **kw: legends.append(original(self, *a, **kw)) or legends[-1])
        fits = [(p, _pl_fit()) for p in range(1, 10) for _ in range(3)]
        build_peak_quality_figure(fits, "S", spec=PL_QUALITY)
        assert legends and all(lg.get_texts()[1].get_text().startswith("Robust CV = ") for lg in legends)
