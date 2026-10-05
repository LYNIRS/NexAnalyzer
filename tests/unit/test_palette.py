"""The on-screen plot and the exported figures must style shared traces alike.

A Sample Report that doesn't look like the spectrum it came from is the bug
these tests exist to prevent: the two plotters each used to pick their own
colors, and drifted into a black dashed fit on screen versus a solid orange one
in the .pptx.
"""

import numpy as np

from core.report.summary_figure import _hex_to_rgb
from modules.spectra.viz import fit_plot, live_plot, palette


class _Peak:
    def __init__(self, label="Exciton", color="#123456", curve=None):
        self.label = label
        self.color = color
        self.component_curve = curve if curve is not None else np.array([0.0, 1.0, 0.0])


class _Fit:
    success = True

    def __init__(self):
        self.fitted_peaks = [_Peak()]
        self.total_fit_curve = np.array([0.0, 1.0, 0.0])
        self.residuals = np.array([0.0, 0.0, 0.0])


def _traces_by_name(fig):
    return {t.name: t for t in fig.data if t.name}


class TestBothPlottersShareOnePalette:
    def test_neither_plotter_hardcodes_a_shared_trace_color(self):
        """The colors live in palette.py; a literal in either plotter is how
        they drifted apart before."""
        for module in (fit_plot, live_plot):
            source = open(module.__file__, encoding="utf-8").read()
            for stray in ('color="blue"', "color='blue'", 'color="#1f77b4"',
                          'color="#ff7f0e"', 'color="#d62728"', 'color="green"',
                          'color="black"'):
                assert stray not in source, f"{module.__name__} hardcodes {stray}"

    def test_the_exported_figure_draws_the_palette_colors(self):
        x = np.array([0.0, 1.0, 2.0])
        fig = fit_plot.plot_composite(
            x, np.array([0.0, 1.0, 0.0]), _Fit(), mode="Raman",
            show_components=True, show_residuals=True,
        )
        traces = _traces_by_name(fig)

        assert traces["Data"].marker.color == palette.PROCESSED_COLOR
        assert traces["Total Fit"].line.color == palette.FIT_TOTAL_COLOR
        assert traces["Total Fit"].line.dash == palette.FIT_TOTAL_DASH
        assert traces["Residuals"].marker.color == palette.RESIDUAL_COLOR

    def test_the_total_fit_is_solid_black_and_the_components_are_colored(self):
        """The two lines sit on top of each other for most of their length; since
        v5.12.0 both are solid, so the fit's black is what tells them apart."""
        x = np.array([0.0, 1.0, 2.0])
        fig = fit_plot.plot_composite(
            x, np.array([0.0, 1.0, 0.0]), _Fit(), mode="Raman", show_components=True,
        )
        traces = _traces_by_name(fig)

        assert traces["Total Fit"].line.dash == "solid"
        assert traces["Total Fit"].line.color == "#000000"
        assert traces["Exciton"].line.dash == palette.COMPONENT_DASH
        assert palette.COMPONENT_DASH is None  # i.e. solid
        assert traces["Exciton"].opacity == palette.COMPONENT_OPACITY

    def test_the_legend_swatches_survive_the_report_color_parser(self):
        """`_hex_to_rgb` falls back to black for anything it can't read, so a
        CSS name here would plot in color and draw a black swatch beside it.

        matplotlib would accept a CSS name happily, which is precisely why this
        test matters: the strict parser is the only thing that notices, and it
        draws the summary page's legend."""
        for label, color in fit_plot.fit_legend_entries([_Fit()]):
            assert color.startswith("#") and len(color) == 7, (label, color)
            assert _hex_to_rgb(color) == _hex_to_rgb(color)  # parses without raising

        data_entry = dict(fit_plot.fit_legend_entries([_Fit()]))["Data"]
        assert _hex_to_rgb(data_entry) != _hex_to_rgb("not-a-color"), \
            "Data's swatch fell back to black, so it no longer matches its line"

    def test_the_palette_colors_are_the_css_names_the_screen_used(self):
        """Hex, not names — but the same colors the Spectra page always drew."""
        assert palette.PROCESSED_COLOR == "#800080"  # purple
        assert palette.RAW_COLOR == "#0000FF"        # blue
        assert palette.FIT_TOTAL_COLOR == "#000000"  # black
        assert palette.RESIDUAL_COLOR == "#008000"   # green

    def test_a_component_keeps_its_own_preset_color(self):
        """Only the style is shared; the color comes from the Material Preset so
        the same peak is the same color in every point's panel."""
        x = np.array([0.0, 1.0, 2.0])
        fig = fit_plot.plot_composite(
            x, np.array([0.0, 1.0, 0.0]), _Fit(), mode="Raman", show_components=True,
        )

        assert _traces_by_name(fig)["Exciton"].line.color == "#123456"


class TestPaletteIsOnlyForSharedTraces:
    def test_the_screen_only_layers_are_not_in_the_palette(self):
        """De-spiked and the live previews belong to the Spectra page alone;
        the report never draws them. The baseline-corrected series is the one
        exception, and it is in the palette precisely because both draw it."""
        for name in ("DESPIKED_COLOR", "BASELINE_COLOR", "PREVIEW_COLOR"):
            assert not hasattr(palette, name)


class TestTheFittedSeriesIsOneColorEverywhere:
    """The report's only data trace and the Spectra page's "Baseline-corrected"
    layer are the same numbers — `processed_data`, after de-spiking and baseline
    removal. Drawing them in two colors is how a report stops looking like the
    spectrum it came from, and painting the data in a preset's own peak color
    (WSe2 gives C and center #3276EC, blue) is how the preset colors stop
    reading as preset colors.
    """

    def test_the_report_draws_the_processed_series_not_the_raw_one(self):
        x = np.array([0.0, 1.0, 2.0])
        fig = fit_plot.plot_composite(
            x, np.array([0.0, 1.0, 0.0]), _Fit(), mode="Raman",
        )

        assert _traces_by_name(fig)["Data"].marker.color == palette.PROCESSED_COLOR
        assert _traces_by_name(fig)["Data"].marker.color != palette.RAW_COLOR

    def test_the_grid_panels_draw_it_too(self):
        """plot_fit_column is what pages 2 and 3 of the .pptx are made of."""
        x = np.array([0.0, 1.0, 2.0])
        fig = fit_plot.plot_fit_column([(1, x, np.array([0.0, 1.0, 0.0]), _Fit())], mode="PL")

        assert _traces_by_name(fig)["Data"].marker.color == palette.PROCESSED_COLOR

    def test_the_screen_sets_it_on_the_marker_that_actually_renders(self):
        """The layer is drawn `mode="markers"`; a color on `line` alone only
        reaches the dots as a Plotly fallback, so it was one default away from
        silently becoming a cycled color."""
        source = open(live_plot.__file__, encoding="utf-8").read()
        corrected = source[source.index('name="Baseline-corrected"'):]
        marker_line = corrected[:corrected.index("))")]

        assert "marker=dict(color=PROCESSED_COLOR)" in marker_line

    def test_the_components_are_drawn_over_the_total_fit_not_under_it(self):
        """Pages 2 and 3 exist to show the preset-colored components; the black
        total fit must not be the heaviest line on top of them."""
        x = np.array([0.0, 1.0, 2.0])
        fig = fit_plot.plot_fit_column([(1, x, np.array([0.0, 1.0, 0.0]), _Fit())], mode="Raman")
        traces = _traces_by_name(fig)

        assert traces["Exciton"].line.width > traces["Total Fit"].line.width
