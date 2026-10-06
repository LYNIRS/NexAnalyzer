"""
Unit tests for core.report.summary_figure (the QC Report's composed pages).

Most of what follows is ported from `test_pptx_report.py`, deleted at v5.0.0
with the deck it tested. The four rules it pinned are properties of the
*report*, not of PowerPoint, and every one of them came from a real bug:

  - a ratio row carries median ± MAD in the Intensity column and dashes
    elsewhere, and sits below the peaks in the order given;
  - an unmeasurable width renders as an em dash, never as 0.0;
  - a caption never outgrows the one known to fit on a single line, because a
    wrapped caption overlaps the table below it;
  - a placeholder is blank — missing content is marked, not explained.

They are checked against `stats_table_content` and the layout helpers rather
than against pixels, which is the whole reason the row-building was pulled out
of the drawing.
"""

import matplotlib
import pytest

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from core.report.models import (  # noqa: E402
    RAW_STAT_LABEL,
    DomainSizeStat,
    OpticalClassStat,
    PeakStat,
    SizePopulationStat,
)
from core.report.summary_figure import (  # noqa: E402
    FIT_COLUMN_ASPECT_RATIO,
    FIT_GRID_COLUMNS,
    _add_domain_table,
    _add_fit_legend,
    _add_om_note,
    _add_placeholder,
    _add_title_bar,
    _fit_font_size,
    _fit_title_size,
    _FS_TITLE,
    _FS_TITLE_MIN,
    _TITLE_INSET,
    _TITLE_W,
    _hex_to_rgb,
    _text_width_pt,
    _stats_caption,
    _table_font,
    _table_height,
    build_fit_grid_figure,
    build_summary_figure,
    stats_table_content,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _stat(label="E2g+A1g", n=9, center=250.3, fwhm=6.9):
    return PeakStat(
        label=label, n=n,
        center_mean=center, center_std=0.4,
        intensity_mean=1234.5, intensity_std=88.1,
        fwhm_mean=fwhm, fwhm_std=0.3,
    )


def _classes():
    return [
        OpticalClassStat("Below 2L", 2.1, 0.4, -3.2, 0.5, 9),
        OpticalClassStat("Bilayer", 93.4, 1.2, None, None, 9),
        OpticalClassStat("Above 2L", 4.5, 0.8, 4.1, 0.6, 9),
    ]


class TestRatioRows:
    """Ported from TestBuildSampleReportPptx — the ratio rows the Raman table
    carries below its peaks."""

    def test_a_ratio_lands_in_the_intensity_column_as_median_and_mad(self):
        """They are ratios of intensities, so that is the column they belong
        in; center and FWHM do not apply and are dashed."""
        _headers, rows, _fracs, bold_from = stats_table_content(
            [_stat()], [("LA / E2g+A1g", (0.128, 0.011, 9))]
        )
        ratio_row = rows[bold_from]

        assert ratio_row[0] == "LA / E2g+A1g"
        assert ratio_row[1] == "—"
        assert ratio_row[2] == "0.128 ± 0.011"
        assert ratio_row[3] == "—"
        assert ratio_row[-1] == "9"

    def test_several_ratios_stack_below_the_peaks_in_order(self):
        _headers, rows, _fracs, bold_from = stats_table_content(
            [_stat(), _stat("2LA")],
            [("LA / E2g+A1g", (0.128, 0.011, 9)), ("C / LB", (3.2, 0.4, 9))],
        )

        assert bold_from == 2
        assert [row[0] for row in rows] == [
            "E2g+A1g", "2LA", "LA / E2g+A1g", "C / LB"
        ]

    def test_ratios_keep_three_decimals(self):
        """These run around 0.1, where two decimals would round away most of
        the point-to-point variation the rows exist to show."""
        _headers, rows, _fracs, bold_from = stats_table_content(
            [_stat()], [("LA / E2g+A1g", (0.1284, 0.0113, 9))]
        )

        assert rows[bold_from][2] == "0.128 ± 0.011"

    def test_no_ratios_adds_no_rows(self):
        _headers, rows, _fracs, bold_from = stats_table_content([_stat()], None)

        assert len(rows) == 1
        assert bold_from == 1

    def test_the_ratio_row_gains_a_dash_in_the_v1_column_too(self):
        _headers, rows, _fracs, bold_from = stats_table_content(
            [_stat()], [("LA / E2g+A1g", (0.128, 0.011, 9))], show_fwhm_v1=True
        )

        assert rows[bold_from] == ["LA / E2g+A1g", "—", "0.128 ± 0.011", "—", "—", "9"]


class TestUnmeasurableValues:
    def test_an_unmeasurable_raw_width_is_dashed_not_zero(self):
        """The empirical "Raw" row on a spectrum with no half-maximum crossing.
        A 0.0 there would read as a measurement of nothing."""
        raw = PeakStat(RAW_STAT_LABEL, 9, 770.0, 3.0, 5000.0, 200.0, None, None)

        _headers, rows, _fracs, _bold = stats_table_content([raw])

        assert rows[0][3] == "—"
        assert "0.0" not in rows[0][3]

    def test_an_absent_v1_width_is_dashed_too(self):
        _headers, rows, _fracs, _bold = stats_table_content(
            [_stat()], show_fwhm_v1=True
        )

        assert rows[0][4] == "—"


class TestColumns:
    def test_the_default_table_has_no_legacy_width_column(self):
        headers, _rows, fracs, _bold = stats_table_content([_stat()])

        assert headers == ["Peak", "Center", "Intensity", "FWHM", "n"]
        assert len(fracs) == len(headers)

    def test_the_legacy_column_is_inserted_after_the_real_one(self):
        headers, _rows, fracs, _bold = stats_table_content([_stat()], show_fwhm_v1=True)

        assert headers == ["Peak", "Center", "Intensity", "FWHM", "FWHM (v1)", "n"]
        assert headers.index("FWHM (v1)") == headers.index("FWHM") + 1
        assert len(fracs) == len(headers)

    def test_the_column_fractions_span_the_table(self):
        """They leave no gap and overrun no edge — the table is sized in
        fractions of its own rect."""
        for show in (False, True):
            _headers, _rows, fracs, _bold = stats_table_content([_stat()], show_fwhm_v1=show)
            assert sum(fracs) == pytest.approx(1.0)


class TestCaptions:
    def test_no_caption_outgrows_the_one_known_to_fit_on_one_line(self):
        """A wrapped caption overlaps the table below it. The ratios variant is
        the longest that has been checked to fit, so nothing may exceed it."""
        longest = _stats_caption(
            "Raman", [_stat()], has_ratios=True
        )
        variants = [
            _stats_caption("Raman", [_stat()], has_ratios=False),
            _stats_caption("PL", [_stat()], has_ratios=False),
            _stats_caption(
                "PL", [PeakStat(RAW_STAT_LABEL, 9, 770.0, 3.0, 5.0, 1.0, None, None)],
                has_ratios=False,
            ),
        ]

        for caption in variants:
            assert len(caption) <= len(longest), caption

    def test_a_table_led_by_the_empirical_row_is_not_called_a_fit_summary(self):
        raw = PeakStat(RAW_STAT_LABEL, 9, 770.0, 3.0, 5000.0, 200.0, None, None)

        caption = _stats_caption("PL", [raw, _stat("Exciton")], has_ratios=False)

        assert "fit summary" not in caption
        assert "empirical + fitted" in caption

    def test_a_purely_fitted_table_is_called_a_fit_summary(self):
        caption = _stats_caption("Raman", [_stat()], has_ratios=False)

        assert caption == "Raman fit summary (mean ± std)"


class TestTitleBar:
    """The header is one unclipped line at a fixed position, so until v5.3.1
    every fit-grid page drew its title off the right edge of the page and lost
    the tail of "— fitted spectra (9 points)". Nothing measured it, because the
    summary page's title — the one the layout was set up against — carries no
    subtitle and fits at full size.
    """

    #: What the drawing fits to: the rule's width less the same inset at each
    #: end, so a shrunk title stops short of the rule rather than touching it.
    FITTED = _TITLE_W - 2 * _TITLE_INSET
    #: What must never be exceeded: the text starts one inset in, so this is
    #: where the rule ends.
    LIMIT = _TITLE_W - _TITLE_INSET

    def _title(self, sample="HADH65", material="WSe2", subtitle=None):
        text = f"{sample}   |   Material: {material}   |   2026-09-21"
        if subtitle:
            text += f"   |   {subtitle}"
        return text

    @pytest.mark.parametrize("technique", ["Raman", "PL"])
    def test_a_fit_grid_title_fits_the_rule_it_sits_on(self, technique):
        text = self._title(subtitle=f"{technique} — fitted spectra (9 points)")

        size = _fit_title_size(text, self.FITTED, _FS_TITLE)

        assert _text_width_pt(text, size, True) / 72.0 <= self.LIMIT

    def test_the_subtitled_title_is_the_one_that_needed_shrinking(self):
        """Guards the fix against being read as unconditional: it is the
        subtitle that overflows, so the base size must genuinely not fit."""
        text = self._title(subtitle="Raman — fitted spectra (9 points)")

        assert _text_width_pt(text, _FS_TITLE, True) / 72.0 > self.LIMIT

    def test_the_summary_page_title_keeps_the_full_size(self):
        """It already fitted; shrinking it would be a silent redesign of the
        page this layout was built around."""
        assert _fit_title_size(self._title(), self.FITTED, _FS_TITLE) == _FS_TITLE

    def test_a_long_sample_name_shrinks_rather_than_overflowing(self):
        text = self._title(sample="QC_HADH67_RECHECK",
                           subtitle="Raman — fitted spectra (9 points)")

        size = _fit_title_size(text, self.FITTED, _FS_TITLE)

        assert size < _FS_TITLE
        assert _text_width_pt(text, size, True) / 72.0 <= self.LIMIT

    def test_the_shrink_stops_at_a_legible_floor(self):
        """Below this the header stops reading as a title, so a pathological
        string is left to overflow rather than set in 6pt."""
        text = self._title(sample="A" * 200,
                           subtitle="Raman — fitted spectra (9 points)")

        assert _fit_title_size(text, self.FITTED, _FS_TITLE) == _FS_TITLE_MIN

    def test_the_drawn_title_carries_the_fitted_size(self):
        """_fit_title_size can be right while _add_title_bar ignores it."""
        fig = plt.figure(figsize=(13.333, 7.5), dpi=100)
        try:
            _add_title_bar(fig, "HADH65", "WSe2", "2026-09-21",
                           subtitle="Raman — fitted spectra (9 points)")
            sizes = [t.get_fontsize() for t in fig.texts]
        finally:
            plt.close(fig)

        assert sizes and max(sizes) < _FS_TITLE


class TestPlaceholders:
    def test_missing_content_placeholders_have_no_text(self):
        """Deliberately blank rather than explaining why, so the report stays
        visually clean."""
        fig = plt.figure(figsize=(4, 4))
        try:
            _add_placeholder(fig, 0.5, 0.5, 2.0, 1.0)
            ax = fig.axes[-1]

            assert len(ax.texts) == 0
            assert len(ax.patches) == 1
        finally:
            plt.close(fig)


class TestTableGeometry:
    def test_the_font_steps_down_as_rows_are_added(self):
        assert _table_font(3) == 13
        assert _table_font(6) == 13
        assert _table_font(7) == 11
        assert _table_font(10) == 11
        assert _table_font(11) == 9

    def test_height_is_derived_from_the_row_count(self):
        """So a caller can stack tables without knowing how many ratio rows the
        one above ended up with."""
        assert _table_height(3) == pytest.approx(1.20)
        assert _table_height(5) == pytest.approx(1.80)
        assert _table_height(6) == pytest.approx(2.10)

    def test_a_tiny_table_keeps_a_floor(self):
        assert _table_height(1) == _table_height(2) == pytest.approx(0.90)

    def test_adding_a_ratio_row_pushes_the_table_below_it_down(self):
        """Hardcoded heights pushed the Raman table through the PL one as soon
        as a second ratio was added."""
        assert _table_height(6) > _table_height(5) > _table_height(3)


class TestColumnsNeverOverlap:
    """Centred text in a matplotlib axes is not clipped to anything, so a cell
    wider than its column simply draws across its neighbour.

    This shipped: a real WSe2 PL table printed "41022.4 ± 3980.31.4 ± 2.0" —
    the Intensity value running through the FWHM one — and every test passed,
    because they asserted PNG magic bytes. The PowerPoint table this replaced
    auto-shrank its own cell text; nothing here does unless asked.
    """

    def test_a_wide_cell_shrinks_the_font(self):
        headers = ["Peak", "Center", "Intensity", "FWHM", "n"]
        fracs = [0.30, 0.23, 0.23, 0.16, 0.08]
        narrow = [["Si", "520.0 ± 0.1", "1.2 ± 0.1", "3.1 ± 0.1", "9"]]
        wide = [["Raw", "771.4 ± 3.0", "50122.8 ± 4110.2", "31.4 ± 2.0", "225"]]

        assert _fit_font_size(headers, wide, fracs, 6.33, 13) < _fit_font_size(
            headers, narrow, fracs, 6.33, 13
        )

    def test_a_table_that_already_fits_keeps_its_font(self):
        headers = ["Peak", "Center", "Intensity", "FWHM", "n"]
        fracs = [0.30, 0.23, 0.23, 0.16, 0.08]
        rows = [["Si", "520.0 ± 0.1", "1.2 ± 0.1", "3.1 ± 0.1", "9"]]

        assert _fit_font_size(headers, rows, fracs, 6.33, 13) == 13

    def test_every_cell_ends_up_inside_its_own_column(self):
        """The property that actually matters, checked against the fitted size
        rather than against a font number."""
        headers = ["Peak", "Center", "Intensity", "FWHM", "n"]
        fracs = [0.30, 0.23, 0.23, 0.16, 0.08]
        rows = [
            ["Raw", "771.4 ± 3.0", "50122.8 ± 4110.2", "—", "225"],
            ["Exciton", "770.2 ± 2.1", "41022.4 ± 3980.1", "31.4 ± 2.0", "224"],
        ]
        table_w = 6.33

        size = _fit_font_size(headers, rows, fracs, table_w, 13)

        for row, bold in ((headers, True), *((r, False) for r in rows)):
            for value, frac in zip(row, fracs):
                assert _text_width_pt(value, size, bold) <= frac * table_w * 72.0, value

    def test_it_will_not_shrink_past_legibility(self):
        """An absurd value is a data problem, not a layout one; a 2pt table
        would hide it rather than surface it."""
        absurd = [["X", "1" * 400, "1", "1", "1"]]

        assert _fit_font_size(["a", "b", "c", "d", "e"], absurd,
                              [0.2] * 5, 6.33, 13) == pytest.approx(7.0)


class TestThresholdNote:
    """The page prints which threshold pair produced its OM table.

    Adaptive derives a different pair per wafer, so a saved page that does not
    name its pair cannot be reproduced from itself. The flag matters more: it
    means even the preset cut sits inside this wafer's noise, and a PNG
    outlives the on-screen warning that said so.
    """

    def _drawn(self, note):
        fig = plt.figure(figsize=(13.333, 7.5))
        _add_om_note(fig, note)
        texts = [t.get_text() for t in fig.texts]
        plt.close(fig)
        return texts

    def test_the_note_is_drawn(self):
        texts = self._drawn("preset contrast pair -6.00 / +4.25 %")

        assert any("-6.00 / +4.25" in t for t in texts)

    def test_no_note_draws_nothing(self):
        assert self._drawn(None) == []

    def test_the_page_renders_with_a_note(self):
        png = build_summary_figure(
            sample_name="HADH57", material_name="WSe2", report_date="2026-09-19",
            om_classes=_classes(),
            om_threshold_note="preset contrast pair -6.00 / +4.25 %",
        )

        assert png[:8] == _PNG_MAGIC


class TestHexParsing:
    def test_a_valid_hex_colour_round_trips(self):
        assert _hex_to_rgb("#804020") == pytest.approx((128 / 255, 64 / 255, 32 / 255))

    def test_anything_unparseable_falls_back_to_black(self):
        for bad in ("not-a-color", "blue", "", None, "#12"):
            assert _hex_to_rgb(bad) == (0.0, 0.0, 0.0)


class TestBuildSummaryFigure:
    def test_returns_png_bytes(self):
        png = build_summary_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            magnification_label="100x",
            raman_stats=[_stat(), _stat("2LA")],
            pl_stats=[_stat("Exciton", center=770.0)],
            raman_ratios=[("LA / E2g+A1g", (0.128, 0.011, 9))],
            om_classes=_classes(),
        )

        assert png[:8] == _PNG_MAGIC
        assert len(png) > 10_000

    def test_a_folder_with_nothing_in_it_still_renders(self):
        """Every panel a placeholder. The page's shape must not depend on its
        contents, or a half-empty report reads as a broken one."""
        png = build_summary_figure(
            sample_name="Empty", material_name="WSe2", report_date="2026-09-18",
        )

        assert png[:8] == _PNG_MAGIC

    def test_an_unanalysed_folder_omits_the_om_table_rather_than_drawing_zeros(self):
        no_table = build_summary_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            raman_stats=[_stat()],
        )
        with_table = build_summary_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            raman_stats=[_stat()], om_classes=_classes(),
        )

        assert no_table[:8] == with_table[:8] == _PNG_MAGIC
        assert no_table != with_table

    def test_a_bad_image_becomes_a_placeholder_not_an_exception(self):
        """A truncated or unreadable microscope frame must not take the whole
        report down with it."""
        png = build_summary_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            om_image_bytes={1: b"not an image"},
        )

        assert png[:8] == _PNG_MAGIC

    def test_domain_stat_none_still_renders(self):
        """No domain-size analysis ran (or found nothing) -- a placeholder
        box, not a missing page section."""
        png = build_summary_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            om_classes=_classes(),
        )

        assert png[:8] == _PNG_MAGIC

    def test_a_domain_stat_changes_the_page(self):
        no_table = build_summary_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            om_classes=_classes(),
        )
        with_table = build_summary_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            om_classes=_classes(), domain_stat=_domain_stat(),
        )

        assert no_table[:8] == with_table[:8] == _PNG_MAGIC
        assert no_table != with_table


def _domain_stat(unit="µm", calibrated=True):
    return DomainSizeStat(
        n_domains=42, n_frames=9,
        diameter_median=3.1, diameter_mean=3.3, diameter_std=0.6,
        unit=unit, calibrated=calibrated,
    )


class TestAddDomainTable:
    """`_add_domain_table` owns the placeholder-vs-table choice and the
    uncalibrated note -- checked directly rather than only through a rendered
    page, matching `TestThresholdNote`'s own pattern for its sibling caption."""

    def _drawn_texts(self, stat, class_label=None, include_table_cells=False):
        fig = plt.figure(figsize=(13.333, 7.5))
        _add_domain_table(fig, stat, left=0.1, top=0.5, w=0.3, h=0.1,
                          class_label=class_label)
        # `_text` (captions/notes) draws onto `fig` directly and lands in
        # `fig.texts`; `_draw_table`'s own cells draw onto the table's own
        # child Axes instead, so reading table content needs `ax.texts` too.
        texts = [t.get_text() for t in fig.texts]
        if include_table_cells:
            texts += [t.get_text() for ax in fig.axes for t in ax.texts]
        plt.close(fig)
        return texts

    def test_none_draws_a_placeholder_not_a_table(self):
        texts = self._drawn_texts(None)

        assert not any("Domains (n)" in t for t in texts)

    def test_zero_domains_draws_a_placeholder_too(self):
        stat = DomainSizeStat(
            n_domains=0, n_frames=0,
            diameter_median=0.0, diameter_mean=0.0, diameter_std=0.0,
            unit="µm", calibrated=True,
        )
        texts = self._drawn_texts(stat)

        assert not any("Domains (n)" in t for t in texts)

    def test_calibrated_stat_omits_the_pixel_note(self):
        texts = self._drawn_texts(_domain_stat(calibrated=True))

        assert not any("pixels" in t for t in texts)

    def test_uncalibrated_stat_shows_the_pixel_note(self):
        texts = self._drawn_texts(_domain_stat(unit="px", calibrated=False))

        assert any("pixels" in t for t in texts)

    def test_class_label_is_appended_to_the_caption(self):
        texts = self._drawn_texts(_domain_stat(), class_label="Bilayer")

        assert any("Bilayer" in t for t in texts)

    def test_no_class_label_leaves_the_plain_caption(self):
        texts = self._drawn_texts(_domain_stat(), class_label=None)

        assert any(t == "Domain size (equivalent circular diameter)" for t in texts)

    def test_an_unsplit_stat_has_no_size_column(self):
        texts = self._drawn_texts(_domain_stat(), include_table_cells=True)
        assert not any(t == "Size" for t in texts)

    def test_a_split_stat_draws_both_populations(self):
        stat = _domain_stat()._replace(
            populations=(
                SizePopulationStat("small", 745, 10.22, 12.13, 5.58),
                SizePopulationStat("large", 57, 66.44, 70.83, 17.72),
            ),
        )
        texts = self._drawn_texts(stat, include_table_cells=True)

        assert any("Small" in t for t in texts)
        assert any("Large" in t for t in texts)
        assert any("745" in t for t in texts)
        assert any("57" in t for t in texts)
        # The pooled single-row numbers must not appear instead.
        assert not any(t == "42" for t in texts)

    def test_calibrated_stat_shows_a_density_value(self):
        stat = _domain_stat()._replace(density_per_100um2=150.0)
        texts = self._drawn_texts(stat, include_table_cells=True)

        assert any("Density" in t for t in texts)
        assert any("150" in t for t in texts)

    def test_uncalibrated_stat_shows_a_dash_for_density(self):
        stat = _domain_stat(unit="px", calibrated=False)
        assert stat.density_per_100um2 is None
        texts = self._drawn_texts(stat, include_table_cells=True)

        assert any(t == "—" for t in texts)

    def test_split_stat_shows_both_populations_densities(self):
        stat = _domain_stat()._replace(
            populations=(
                SizePopulationStat("small", 745, 10.22, 12.13, 5.58, density_per_100um2=2960.0),
                SizePopulationStat("large", 57, 66.44, 70.83, 17.72, density_per_100um2=35.9),
            ),
        )
        texts = self._drawn_texts(stat, include_table_cells=True)

        assert any("2960.0" in t for t in texts)
        assert any("35.9" in t for t in texts)


class TestFitGridLegend:
    """The legend is the only thing telling a reader which colour is Data,
    which is Total Fit and which is each peak — the column images deliberately
    carry none of their own. Ported from the deleted test_pptx_report.py, which
    pinned this with seven tests; the new suite had none, so the whole function
    body could have been deleted with no test failing.
    """

    ENTRIES = [("Data", "#800080"), ("Total Fit", "#000000"), ("Exciton", "#d62728")]

    def _drawn(self, entries):
        fig = plt.figure(figsize=(13.333, 7.5))
        _add_fit_legend(fig, entries)
        labels = [t.get_text() for t in fig.texts]
        swatches = [a for a in fig.artists if isinstance(a, Rectangle)]
        colors = [a.get_facecolor()[:3] for a in swatches]
        plt.close(fig)
        return labels, swatches, colors

    def test_every_entry_is_labelled_exactly_once(self):
        labels, _swatches, _colors = self._drawn(self.ENTRIES)

        assert labels == ["Data", "Total Fit", "Exciton"]

    def test_every_entry_gets_a_swatch(self):
        _labels, swatches, _colors = self._drawn(self.ENTRIES)

        assert len(swatches) == len(self.ENTRIES)

    def test_each_swatch_carries_its_entrys_own_colour(self):
        """The bug this guards: a swatch painted from a literal, or from a
        colour the parser fell back to black on, tells the reader the wrong
        line is the one they are looking at."""
        _labels, _swatches, colors = self._drawn(self.ENTRIES)

        for (label, hex_color), drawn in zip(self.ENTRIES, colors):
            # abs, not rel: matplotlib stores channels at float32 precision, so
            # a near-zero channel's relative error is large and meaningless.
            assert tuple(drawn) == pytest.approx(_hex_to_rgb(hex_color), abs=1e-4), label

    def test_an_unparseable_colour_falls_back_to_black_rather_than_raising(self):
        """`FittedPeak.color` is never validated. matplotlib would accept a CSS
        name happily — the strict parser is the only thing that notices, which
        is why palette.py states its colours as hex."""
        _labels, swatches, colors = self._drawn([("Mystery", "not-a-color")])

        assert len(swatches) == 1
        assert tuple(colors[0]) == pytest.approx((0.0, 0.0, 0.0), abs=1e-4)

    def test_no_entries_draws_no_legend_at_all(self):
        for empty in (None, []):
            labels, swatches, _colors = self._drawn(empty)
            assert labels == []
            assert swatches == []


class TestBuildFitGridFigure:
    def test_returns_png_bytes(self):
        png = build_fit_grid_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            technique="Raman", column_images={},
            legend=[("Data", "#800080"), ("Total Fit", "#000000")],
            x_label="Raman Shift (cm⁻¹)", y_label="Normalized intensity",
        )

        assert png[:8] == _PNG_MAGIC

    def test_a_missing_column_is_a_placeholder_not_a_shifted_grid(self):
        """The other columns keep their usual positions, so the page still
        reads as a 3x3 grid with a hole in it."""
        png = build_fit_grid_figure(
            sample_name="HADG06", material_name="WSe2", report_date="2026-09-18",
            technique="PL", column_images={},
        )

        assert png[:8] == _PNG_MAGIC

    def test_the_column_aspect_ratio_matches_the_slot_it_fills(self):
        """Callers rasterize their column figures at this ratio; if it drifts
        from the layout the images letterbox inside their columns."""
        assert 0 < FIT_COLUMN_ASPECT_RATIO < 1
        assert FIT_GRID_COLUMNS == 3
