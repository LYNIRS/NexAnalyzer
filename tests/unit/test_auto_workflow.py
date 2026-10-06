"""Unit tests for modules.spectra.processing.auto_workflow.

execute_auto_workflow() writes st.session_state, but only ever by assigning to
keys -- which works outside a script run -- so it *is* callable here, and the
re-run tests below do exactly that. The "needs AppTest" note that used to sit
here was why the non-idempotence fixed in v5.4.0 went uncovered for so long.
"""

import numpy as np

from modules.spectra.processing.auto_workflow import (
    execute_auto_workflow,
    format_workflow_summary,
    get_workflow_suggestions,
)
from modules.spectra.models.preset import (
    MaterialPreset,
    PeakTemplate,
    TechniquePreset,
)
from modules.spectra.models.peak import FittedPeak, FitResult
from modules.spectra.models.spectrum import (
    ProcessingSettings,
    SpectrumData,
    SpectrumFile,
)


def _make_preset(x_range_enabled=False):
    return MaterialPreset(
        material_name="Silicon",
        enabled=True,
        raman=TechniquePreset(
            x_range_enabled=x_range_enabled,
            x_min=100.0 if x_range_enabled else None,
            x_max=900.0 if x_range_enabled else None,
            despike_threshold=6.0,
            baseline_algorithm="Polynomial",
            baseline_degree=5,
            baseline_lambda=None,
            baseline_p=None,
            peak_templates=[
                PeakTemplate(peak_label="Si", center=520.0, center_tolerance=3.0,
                             width_fwhm=8.0, shape=0.2, color="#2ca02c")
            ],
        ),
    )


def _make_fit_result():
    return FitResult(
        success=True,
        fitted_peaks=[
            FittedPeak(label="Si", center=520.0, center_stderr=0.1, area=1000.0,
                       area_stderr=5.0, width_fwhm=8.0, width_stderr=0.2, shape=0.2,
                       component_curve=None, color="#2ca02c")
        ],
        total_fit_curve=None,
        residuals=None,
        chi_squared=1.23,
        r_squared=0.987,
        convergence_time=0.05,
    )


class TestFormatWorkflowSummary:
    def test_success_summary_includes_key_facts(self):
        preset = _make_preset(x_range_enabled=True)
        result = {"success": True, "fit_result": _make_fit_result()}

        summary = format_workflow_summary(result, preset, "Raman")

        assert "Silicon" in summary
        assert "Raman" in summary
        assert "0.9870" in summary
        assert "100.0 - 900.0" in summary

    def test_failure_summary_includes_stage_and_error(self):
        preset = _make_preset()
        result = {"success": False, "stage_completed": "baseline", "error_message": "boom"}

        summary = format_workflow_summary(result, preset, "Raman")

        assert "baseline" in summary
        assert "boom" in summary


class TestGetWorkflowSuggestions:
    def test_known_stage_returns_specific_suggestions(self):
        suggestion = get_workflow_suggestions("despike", "some error")
        assert "despike_threshold" in suggestion

    def test_unknown_stage_returns_generic_suggestion(self):
        suggestion = get_workflow_suggestions("unknown_stage", "some error")
        assert suggestion == "Try manual workflow to diagnose the issue."


class TestSummaryWithoutTheRequestedBlock:
    def test_a_material_with_no_pl_settings_says_so(self):
        """format_workflow_summary is reachable with a technique the material
        has no block for, and must not raise there."""
        summary = format_workflow_summary(
            {"success": True, "fit_result": _make_fit_result()},
            _make_preset(), "PL",
        )

        assert "No PL settings" in summary
        assert "Silicon" in summary


# --------------------------------------------------------------------------
# Re-running the workflow (v5.4.0)
#
# Every test here is a regression guard for one cause: stage 2 reads
# `processed_data`, so before execute_auto_workflow reset to raw, a second run
# processed the first run's output. An edited preset was then applied to the
# residual of the old one instead of to the measurement, which is how a
# changed preset could look like it had no effect.
# --------------------------------------------------------------------------

def _synthetic_raman():
    """Two Voigt-ish peaks on a sloping, offset background, plus fixed noise.

    Offset deliberately non-zero: it is what makes a skipped baseline
    distinguishable from an applied one.
    """
    x = np.linspace(330.0, 460.0, 1300)

    def gaussian(center, fwhm, height):
        sigma = fwhm / 2.355
        return height * np.exp(-0.5 * ((x - center) / sigma) ** 2)

    y = (gaussian(383.0, 7.0, 100.0) + gaussian(408.0, 9.0, 140.0)
         + 0.02 * (x - 330.0) + 30.0)
    y = y + np.random.default_rng(0).normal(0.0, 0.4, x.size)
    return x, y


def _make_spectrum():
    x, y = _synthetic_raman()
    return SpectrumFile(
        filename="RM_sample.txt",
        mode="Raman",
        original_data=SpectrumData(X=x.copy(), Y=y.copy()),
        raw_data=SpectrumData(X=x.copy(), Y=y.copy()),
        processed_data=SpectrumData(X=x.copy(), Y=y.copy()),
        source_dir=".",
        processing_settings=ProcessingSettings(),
        peak_table=[],
        fit_result=None,
    )


def _mos2_like(**overrides):
    """A two-peak ALS preset shaped like the committed MoS2 entry."""
    block = TechniquePreset(
        x_range_enabled=False,
        x_min=None,
        x_max=None,
        despike_threshold=8.0,
        baseline_algorithm="ALS",
        baseline_degree=None,
        baseline_lambda=50000.0,
        baseline_p=0.01,
        peak_templates=[
            PeakTemplate(peak_label="E2g", center=383.0, center_tolerance=5.0,
                         width_fwhm=10.0, shape=0.3, color="#d62728"),
            PeakTemplate(peak_label="A1g", center=408.0, center_tolerance=5.0,
                         width_fwhm=12.0, shape=0.3, color="#9467bd"),
        ],
    )
    for field, value in overrides.items():
        setattr(block, field, value)
    return MaterialPreset(material_name="MoS2", enabled=True, raman=block)


class TestReRunningIsIdempotent:
    def test_running_the_same_preset_twice_gives_the_same_numbers(self):
        """The bug in its purest form: no edit at all, and the answer moved.

        Before the fix the second run despiked an already-despiked array and
        ALS-baselined an already-baselined one, drifting the fitted areas by a
        few percent per run.
        """
        spectrum = _make_spectrum()
        preset = _mos2_like()

        execute_auto_workflow(spectrum, preset)
        first = [peak.area for peak in spectrum.fit_result.fitted_peaks]
        first_y = spectrum.processed_data.Y.copy()

        execute_auto_workflow(spectrum, preset)
        second = [peak.area for peak in spectrum.fit_result.fitted_peaks]

        assert first == second
        assert np.array_equal(first_y, spectrum.processed_data.Y)

    def test_a_re_run_after_an_edit_matches_a_fresh_run_of_the_edit(self):
        """The operator's actual question: does re-running give me the same
        thing as loading the file again would?"""
        edited = _mos2_like(baseline_lambda=1000.0)

        reused = _make_spectrum()
        execute_auto_workflow(reused, _mos2_like())
        execute_auto_workflow(reused, edited)

        fresh = _make_spectrum()
        execute_auto_workflow(fresh, edited)

        assert np.array_equal(reused.processed_data.Y, fresh.processed_data.Y)
        assert ([p.area for p in reused.fit_result.fitted_peaks]
                == [p.area for p in fresh.fit_result.fitted_peaks])


class TestAnEditedPresetReachesTheRefit:
    def test_widening_the_x_range_restores_the_discarded_points(self):
        """X-range used to be a one-way ratchet: stage 1 overwrote `raw_data`
        with the crop, so the next run cropped the crop and the widened
        window could never come back."""
        spectrum = _make_spectrum()

        execute_auto_workflow(spectrum, _mos2_like(
            x_range_enabled=True, x_min=370.0, x_max=420.0))
        assert spectrum.raw_data.X.min() >= 370.0
        assert spectrum.raw_data.X.max() <= 420.0

        execute_auto_workflow(spectrum, _mos2_like(
            x_range_enabled=True, x_min=340.0, x_max=450.0))

        assert spectrum.raw_data.X.min() < 341.0
        assert spectrum.raw_data.X.max() > 449.0

    def test_switching_the_baseline_to_skip_puts_the_baseline_back(self):
        """"None (Skip)" declines to subtract a *new* baseline. It cannot
        un-subtract the previous run's, so without the reset the old ALS
        correction simply stayed."""
        spectrum = _make_spectrum()
        execute_auto_workflow(spectrum, _mos2_like())
        corrected_floor = float(spectrum.processed_data.Y.min())

        execute_auto_workflow(
            spectrum, _mos2_like(baseline_algorithm="None (Skip)"))

        fresh = _make_spectrum()
        execute_auto_workflow(
            fresh, _mos2_like(baseline_algorithm="None (Skip)"))

        assert np.array_equal(spectrum.processed_data.Y, fresh.processed_data.Y)
        # The ~30-count offset is back, so this is visibly not the corrected run.
        assert float(spectrum.processed_data.Y.min()) > corrected_floor + 20.0

    def test_an_edited_peak_centre_moves_the_fitted_centre(self):
        spectrum = _make_spectrum()
        execute_auto_workflow(spectrum, _mos2_like())
        before = spectrum.fit_result.fitted_peaks[0].center

        shifted = _mos2_like()
        shifted.raman.peak_templates[0].center = 395.0
        execute_auto_workflow(spectrum, shifted)
        after = spectrum.fit_result.fitted_peaks[0].center

        assert abs(after - before) > 1.0


class TestTheResetDoesNotDiscardTheMeasurement:
    def test_original_data_survives_a_cropping_run(self):
        spectrum = _make_spectrum()
        original = spectrum.original_data.X.copy()

        execute_auto_workflow(spectrum, _mos2_like(
            x_range_enabled=True, x_min=370.0, x_max=420.0))

        assert np.array_equal(spectrum.original_data.X, original)


class TestAFailedRunDoesNotDiscardTheOldFit:
    def test_a_material_with_no_block_for_this_technique_leaves_the_fit_alone(self):
        """The reset must happen after the block check, not before. Picking the
        wrong material in the dropdown cannot run, and wiping a good fit on the
        way to saying so would make that mistake destructive."""
        spectrum = _make_spectrum()
        execute_auto_workflow(spectrum, _mos2_like())
        assert spectrum.fit_done
        areas = [peak.area for peak in spectrum.fit_result.fitted_peaks]

        pl_only = MaterialPreset(material_name="OpticalOnly", enabled=True)
        result = execute_auto_workflow(spectrum, pl_only)

        assert result["success"] is False
        assert "no Raman settings" in result["error_message"]
        assert spectrum.fit_done
        assert [peak.area for peak in spectrum.fit_result.fitted_peaks] == areas


class TestTheBaselineIsKept:
    """The "Peaks on Background" view (v5.13.0) adds the baseline back under
    the fit; that is only the measured spectrum if the stored baseline is the
    one subtracted, in the scale it was subtracted in."""

    def test_corrected_plus_background_is_the_despiked_spectrum(self):
        from modules.spectra.processing.despiking import remove_spikes

        spectrum = _make_spectrum()
        preset = _mos2_like()
        despiked, _ = remove_spikes(spectrum.raw_data.Y, threshold=preset.raman.despike_threshold)

        execute_auto_workflow(spectrum, preset)

        np.testing.assert_allclose(spectrum.processed_data.Y + spectrum.background, despiked)

    def test_skipping_the_baseline_leaves_none(self):
        spectrum = _make_spectrum()
        execute_auto_workflow(spectrum, _mos2_like())
        execute_auto_workflow(spectrum, _mos2_like(baseline_algorithm="None (Skip)"))

        assert spectrum.background is None
