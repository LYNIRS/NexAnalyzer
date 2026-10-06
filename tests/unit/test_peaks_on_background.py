"""The "Peaks on Background" view (v5.13.0).

The fit is made on baseline-corrected data, but the spectrum as measured has its
peaks sitting on a background. This view adds the subtracted baseline back under
the data, the total fit and every component, and draws the baseline itself, so
the peaks are seen where they actually are. The residuals stay about zero.

That only works if the baseline is kept, and kept in the scale it was
subtracted in: before v5.13.0 the auto-workflow discarded it.
"""

import numpy as np
import pytest
import streamlit as st

from modules.spectra.models.spectrum import SpectrumData, SpectrumFile
from modules.spectra.viz import live_plot
from modules.spectra.viz.fit_plot import BACKGROUND_COLOR, plot_composite


def _traces(fig):
    return {t.name: t for t in fig.data}


class _Peak:
    def __init__(self, label, curve):
        self.label = label
        self.color = "#123456"
        self.component_curve = curve


class _Fit:
    def __init__(self, x):
        self.success = True
        peak = np.exp(-((x - 5.0) ** 2))
        self.fitted_peaks = [_Peak("P", peak)]
        self.total_fit_curve = peak
        self.residuals = np.zeros_like(x)


class TestQuickExport:
    x = np.linspace(0.0, 10.0, 50)

    def _fig(self, background):
        fit = _Fit(self.x)
        return plot_composite(self.x, fit.total_fit_curve, fit, background=background)

    def test_every_fitted_layer_sits_on_the_background(self):
        background = 100.0 + self.x
        fit = _Fit(self.x)
        traces = _traces(self._fig(background))

        np.testing.assert_allclose(traces["Background"].y, background)
        np.testing.assert_allclose(traces["Data"].y, fit.total_fit_curve + background)
        np.testing.assert_allclose(traces["Total Fit"].y, fit.total_fit_curve + background)
        np.testing.assert_allclose(traces["P"].y, fit.total_fit_curve + background)
        assert traces["Background"].line.color == BACKGROUND_COLOR

    def test_residuals_stay_about_zero(self):
        fig = self._fig(100.0 + self.x)
        residuals = [t for t in fig.data if t.name == "Residuals"][0]
        np.testing.assert_allclose(residuals.y, 0.0)

    def test_without_a_background_nothing_moves(self):
        fit = _Fit(self.x)
        traces = _traces(self._fig(None))

        assert "Background" not in traces
        np.testing.assert_allclose(traces["Data"].y, fit.total_fit_curve)

    def test_a_mismatched_background_falls_back_instead_of_raising(self):
        assert "Background" not in _traces(self._fig(np.ones(3)))


class TestSpectraPage:
    @pytest.fixture(autouse=True)
    def clean_session(self):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        yield

    def _spectrum(self, background):
        x = np.linspace(0.0, 10.0, 200)
        fit = _Fit(x)
        spectrum = SpectrumFile(
            filename="a.txt", mode="Raman",
            original_data=SpectrumData(X=x, Y=fit.total_fit_curve),
            raw_data=SpectrumData(X=x, Y=fit.total_fit_curve),
            processed_data=SpectrumData(X=x, Y=fit.total_fit_curve),
            fit_result=fit, fit_done=True, baseline_done=True,
            background=background,
        )
        spectrum.processing_settings.baseline_applied = True
        return spectrum

    def _config(self, on):
        return {"baseline_corrected": True, "fit_total": True, "components": True,
                "residuals": True, "on_background": on}

    def test_the_layers_are_lifted_and_relabelled(self):
        spectrum = self._spectrum(50.0 + np.zeros(200))
        traces = _traces(live_plot.create_unified_figure(spectrum, self._config(True)))

        assert "Baseline-corrected" not in traces
        np.testing.assert_allclose(traces["Data"].y, spectrum.processed_data.Y + 50.0)
        np.testing.assert_allclose(traces["Fit Total"].y, spectrum.fit_result.total_fit_curve + 50.0)
        np.testing.assert_allclose(traces["P"].y, spectrum.fit_result.total_fit_curve + 50.0)
        np.testing.assert_allclose(traces["Background"].y, 50.0)
        np.testing.assert_allclose(traces["Residuals"].y, 0.0)

    def test_toggled_off_it_is_the_baseline_corrected_view(self):
        spectrum = self._spectrum(50.0 + np.zeros(200))
        traces = _traces(live_plot.create_unified_figure(spectrum, self._config(False)))

        assert "Background" not in traces
        np.testing.assert_allclose(traces["Baseline-corrected"].y, spectrum.processed_data.Y)

    def test_a_file_fitted_before_the_background_was_kept_falls_back(self):
        traces = _traces(live_plot.create_unified_figure(self._spectrum(None), self._config(True)))

        assert "Background" not in traces
        assert "Baseline-corrected" in traces


class TestTheBackgroundIsKept:
    def test_it_survives_a_save_and_load(self):
        x = np.linspace(0.0, 10.0, 200)
        spectrum = SpectrumFile(
            filename="a.txt", mode="Raman",
            original_data=SpectrumData(X=x, Y=x), raw_data=SpectrumData(X=x, Y=x),
            processed_data=SpectrumData(X=x, Y=x), background=x * 2,
        )

        loaded = SpectrumFile.from_dict(spectrum.to_dict())
        np.testing.assert_allclose(loaded.background, x * 2)

    def test_an_old_project_loads_without_one(self):
        x = np.linspace(0.0, 10.0, 200)
        spectrum = SpectrumFile(
            filename="a.txt", mode="Raman",
            original_data=SpectrumData(X=x, Y=x), raw_data=SpectrumData(X=x, Y=x),
            processed_data=SpectrumData(X=x, Y=x),
        )
        data = spectrum.to_dict()
        del data["background"]

        assert SpectrumFile.from_dict(data).background is None

    def test_reset_clears_it(self):
        x = np.linspace(0.0, 10.0, 200)
        spectrum = SpectrumFile(
            filename="a.txt", mode="Raman",
            original_data=SpectrumData(X=x, Y=x), raw_data=SpectrumData(X=x, Y=x),
            processed_data=SpectrumData(X=x, Y=x), background=x,
        )
        spectrum.reset_to_raw()
        assert spectrum.background is None


class TestResidualPanel:
    """Residuals sit in their own panel below the spectrum, not on its axis."""

    def _fig(self, residuals):
        spectrum = TestSpectraPage()._spectrum(50.0 + np.zeros(200))
        config = TestSpectraPage()._config(True)
        config["residuals"] = residuals
        return live_plot.create_unified_figure(spectrum, config)

    def test_residuals_get_their_own_lower_axis(self):
        traces = _traces(self._fig(True))

        assert traces["Residuals"].yaxis == "y2"
        assert traces["Data"].yaxis in (None, "y")
        assert traces["Fit Total"].xaxis in (None, "x")

    def test_without_residuals_there_is_one_panel(self):
        fig = self._fig(False)

        assert "Residuals" not in _traces(fig)
        assert "yaxis2" not in fig.layout.to_plotly_json()
