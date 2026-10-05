"""The View Options are the user's answer, not a derived one.

The reported bug had two halves, and both came from the same design: the six
`show_*` keys are one global set, but four separate paths *wrote* to them as if
they were state derived from the current file's processing stage —

  - `_update_visibility_for_file()`, on every file switch;
  - the auto-workflow's four stages;
  - the sidebar, after a single run and after a batch run;
  - `compute_default_visibility()`, the fallback inside the plot builder.

So a checkbox the user set by hand survived exactly until the next file was
selected ("when I change the data it turns back to the original setting"), and
the four writers did not even agree with each other about components at the fit
stage, so which fitted file showed them depended on which path ran last.

`set_view_options()` is now the single automatic writer, and it skips any key
the user pinned by toggling it.
"""

import pytest
import streamlit as st

from modules.spectra.ui.session_state import (
    VIEW_OPTION_KEYS,
    clear_pinned_view_options,
    initialize_session_state,
    pin_view_option,
    pinned_view_options,
    set_view_options,
)
from modules.spectra.viz.live_plot import (
    _update_visibility_for_file,
    compute_default_visibility,
    sync_pending_view_options,
)


@pytest.fixture(autouse=True)
def clean_session():
    """A fresh session per test; st.session_state is process-global."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    initialize_session_state()
    yield


class _Fit:
    def __init__(self, success=True):
        self.success = success


class _Spectrum:
    """The attributes the visibility logic actually reads."""

    def __init__(self, despike_done=False, baseline_done=False, fit_done=False):
        self.despike_done = despike_done
        self.baseline_done = baseline_done
        self.fit_done = fit_done
        self.fit_result = _Fit() if fit_done else None


class TestPinningSurvivesAFileSwitch:
    """The headline symptom: changing the data reverted the user's choice."""

    def test_an_untouched_option_still_follows_the_stage(self):
        _update_visibility_for_file(_Spectrum(fit_done=True))
        assert st.session_state["show_components"] is True

        _update_visibility_for_file(_Spectrum())
        assert st.session_state["show_components"] is False

    def test_a_pinned_option_survives_switching_to_an_unfitted_file(self):
        st.session_state["show_components"] = True
        pin_view_option("show_components")

        # The switch that used to wipe it.
        _update_visibility_for_file(_Spectrum())

        assert st.session_state["show_components"] is True

    def test_a_pinned_option_survives_switching_to_a_fitted_file(self):
        """Pinning OFF has to stick too — otherwise the fit stage turns every
        layer back on and the user can never keep a plot uncluttered."""
        st.session_state["show_residuals"] = False
        pin_view_option("show_residuals")

        _update_visibility_for_file(_Spectrum(fit_done=True))

        assert st.session_state["show_residuals"] is False

    def test_pinning_one_option_does_not_freeze_the_others(self):
        st.session_state["show_components"] = True
        pin_view_option("show_components")

        _update_visibility_for_file(_Spectrum(fit_done=True))
        assert st.session_state["show_corrected"] is True

        _update_visibility_for_file(_Spectrum())
        assert st.session_state["show_corrected"] is False
        assert st.session_state["show_components"] is True

    def test_many_switches_do_not_erode_the_pin(self):
        st.session_state["show_raw"] = True
        pin_view_option("show_raw")

        for _ in range(10):
            _update_visibility_for_file(_Spectrum(fit_done=True))
            _update_visibility_for_file(_Spectrum(baseline_done=True))

        assert st.session_state["show_raw"] is True


class TestSetViewOptions:
    def test_it_writes_every_unpinned_key(self):
        set_view_options({"show_fit": True, "show_components": True})
        assert st.session_state["show_fit"] is True
        assert st.session_state["show_components"] is True

    def test_it_skips_pinned_keys(self):
        st.session_state["show_fit"] = False
        pin_view_option("show_fit")

        set_view_options({"show_fit": True, "show_components": True})

        assert st.session_state["show_fit"] is False
        assert st.session_state["show_components"] is True

    def test_respect_pinned_false_is_an_explicit_override(self):
        st.session_state["show_fit"] = False
        pin_view_option("show_fit")

        set_view_options({"show_fit": True}, respect_pinned=False)

        assert st.session_state["show_fit"] is True

    def test_clearing_lets_the_layers_follow_the_data_again(self):
        st.session_state["show_components"] = True
        pin_view_option("show_components")
        clear_pinned_view_options()

        assert pinned_view_options() == set()

        _update_visibility_for_file(_Spectrum())
        assert st.session_state["show_components"] is False

    def test_pinned_set_survives_a_missing_key(self):
        """pinned_view_options() has to self-heal: session state is restored
        from disk in some paths, and a non-set value there must not raise."""
        st.session_state["_view_options_pinned"] = None
        pin_view_option("show_fit")
        assert "show_fit" in pinned_view_options()


class TestTheWritersAgreeAtTheFitStage:
    """The second half of the bug: four writers, two answers for components."""

    @staticmethod
    def _stage_view(spectrum):
        _update_visibility_for_file(spectrum)
        return {k: st.session_state[k] for k in VIEW_OPTION_KEYS}

    def test_compute_default_matches_update_visibility_for_a_fitted_file(self):
        spectrum = _Spectrum(fit_done=True)
        fallback = compute_default_visibility(spectrum)
        stage = self._stage_view(spectrum)

        # The two vocabularies for the same layers.
        assert fallback["components"] == stage["show_components"] is True
        assert fallback["residuals"] == stage["show_residuals"] is True
        assert fallback["fit_total"] == stage["show_fit"] is True
        assert fallback["baseline_corrected"] == stage["show_corrected"] is True

    def test_the_fallback_names_residuals(self):
        """It omitted the key entirely, so the residuals layer could only ever
        be off on the fallback path, whatever the file's stage."""
        assert "residuals" in compute_default_visibility(_Spectrum())

    def test_an_unfitted_file_draws_no_fit_layers(self):
        vis = compute_default_visibility(_Spectrum())
        assert vis["fit_total"] is False
        assert vis["components"] is False
        assert vis["residuals"] is False


class TestTheResetIsDeferredToTheNextRun:
    """"Follow the data again" sits BELOW the checkboxes it resets.

    Streamlit raises StreamlitAPIException if a widget's key is written after
    that widget was instantiated in the same script run, so the button only
    records the intent; the page applies it before the sidebar on the next run,
    exactly as the file switch already does.
    """

    def test_a_pending_reset_re_derives_from_the_current_file(self):
        st.session_state["files"] = {"a.txt": _Spectrum(fit_done=True)}
        st.session_state["current_file"] = "a.txt"
        st.session_state["show_components"] = False
        st.session_state["_pending_view_options_reset"] = True

        sync_pending_view_options()

        assert st.session_state["show_components"] is True
        assert "_pending_view_options_reset" not in st.session_state

    def test_it_is_a_no_op_without_a_pending_reset(self):
        st.session_state["files"] = {"a.txt": _Spectrum(fit_done=True)}
        st.session_state["current_file"] = "a.txt"
        st.session_state["show_components"] = False

        sync_pending_view_options()

        assert st.session_state["show_components"] is False

    def test_it_survives_a_reset_with_no_file_loaded(self):
        st.session_state["_pending_view_options_reset"] = True
        sync_pending_view_options()  # must not raise
        assert "_pending_view_options_reset" not in st.session_state


class TestStartingViewOptions:
    def test_a_new_session_starts_on_the_fitted_spectrum_view(self):
        """Since v5.12.0 the View Options open as a finished fit shows them:
        baseline-corrected, fit, components and residuals; no raw/de-spiked."""
        assert {k: st.session_state[k] for k in VIEW_OPTION_KEYS} == {
            "show_raw": False,
            "show_despiked": False,
            "show_corrected": True,
            "show_fit": True,
            "show_components": True,
            "show_residuals": True,
        }
