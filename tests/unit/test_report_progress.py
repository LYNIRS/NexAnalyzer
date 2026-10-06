"""Unit tests for core.report.progress.

The bug this guards against is a bar that lies: reaching 100% while three
quarters of the work is still ahead, going backwards, or leaving Streamlit to
raise on a fraction a hair outside [0, 1].
"""

import pytest

from core.report.progress import (
    BASELINE_FIT_SPECTRA,
    STAGES,
    ReportProgress,
    Stage,
    build,
    stages_for,
)


def _recorder():
    seen = []
    return seen, lambda fraction, message: seen.append((fraction, message))


def _progress(stages=None):
    seen, sink = _recorder()
    return seen, ReportProgress(stages if stages is not None else list(STAGES), sink)


class TestMeasuredWeights:
    def test_the_full_chain_sums_to_one_plus_the_unmeasured_placeholder(self):
        """The measured ~64 s chain's shares still sum to 1 on their own --
        `ReportProgress` renormalizes whatever it is handed, so this isn't
        load-bearing for a run, but it is what keeps the docstring's
        percentages of that 64 s meaningful. `domain_size` is deliberately
        outside that budget (see its module docstring): an unmeasured
        placeholder added on top, not a share carved out of it, so it adds its
        own 0.390 rather than participating in the sum-to-one."""
        measured = sum(s.weight for s in STAGES if s.key != "domain_size")
        assert measured == pytest.approx(1.0)

        domain_size = next(s for s in STAGES if s.key == "domain_size")
        assert sum(s.weight for s in STAGES) == pytest.approx(1.0 + domain_size.weight)

    def test_no_stage_is_weightless_and_none_dominates_entirely(self):
        for stage in STAGES:
            assert 0.0 < stage.weight < 0.5, stage

    def test_segmentation_is_the_longest_stage(self):
        """~30 s of the ~64 s run. A bar that doesn't say so sits nearly still
        for half a minute, which is the "has it died?" this module exists to
        answer."""
        longest = max(STAGES, key=lambda s: s.weight)

        assert longest.key == "optical_segmentation"

    def test_the_weights_are_not_uniform(self):
        """A uniform bar would sit at 50% with 78% of the time still to run --
        the same lie in a different shape."""
        weights = {s.weight for s in STAGES}
        assert len(weights) > 1

    def test_fitting_is_not_the_whole_bar(self):
        """The original bug: fitting had a bar and everything after it did
        not, so the visible progress ended at ~21% of the real work."""
        fit = next(s for s in STAGES if s.key == "fit")
        assert fit.weight < 0.3


class TestStagesFor:
    def test_a_raman_only_sample_drops_the_pl_stage(self):
        keys = [s.key for s in stages_for(has_raman=True, has_pl=False, has_optical=True)]

        assert "raman_figures" in keys
        assert "pl_figures" not in keys

    def test_a_pl_only_sample_drops_the_raman_stage(self):
        keys = [s.key for s in stages_for(has_raman=False, has_pl=True, has_optical=True)]

        assert "pl_figures" in keys
        assert "raman_figures" not in keys

    def test_no_spectra_at_all_drops_the_fitting_stage(self):
        """An images-only folder still produces a report."""
        keys = [s.key for s in stages_for(has_raman=False, has_pl=False, has_optical=True)]

        assert "fit" not in keys
        assert "optical_images" in keys
        assert "compose" in keys

    def test_a_sample_with_no_optical_images_drops_every_optical_stage(self):
        keys = [s.key for s in stages_for(has_raman=True, has_pl=True, has_optical=False)]

        assert "optical_images" not in keys
        assert "optical_segmentation" not in keys
        assert "om_figures" not in keys

    def test_images_without_a_segmentation_still_get_loaded(self):
        """A folder can have frames with nothing to segment them against — no
        material picked. The summary page's grid still wants them."""
        keys = [s.key for s in stages_for(
            has_raman=True, has_pl=True, has_optical=True, has_segmentation=False)]

        assert "optical_images" in keys
        assert "optical_segmentation" not in keys
        assert "om_figures" not in keys

    def test_domain_size_measurement_is_opt_in(self):
        """The operator's own choice, not a fact about the sample -- off must
        drop the stage even when everything it needs (frames, a segmentation)
        is otherwise present."""
        keys = [s.key for s in stages_for(
            has_raman=True, has_pl=True, has_optical=True, has_domain_size=False)]

        assert "domain_size" not in keys
        assert "optical_segmentation" in keys

    def test_domain_size_measurement_defaults_to_on(self):
        """A caller that hasn't been updated for the flag keeps today's
        behaviour rather than silently losing the stage."""
        keys = [s.key for s in stages_for(has_raman=True, has_pl=True, has_optical=True)]

        assert "domain_size" in keys

    def test_domain_size_measurement_still_needs_a_segmentation(self):
        """Opting in cannot conjure a `hi` mask out of a segmentation that
        never ran."""
        keys = [s.key for s in stages_for(
            has_raman=True, has_pl=True, has_optical=True,
            has_segmentation=False, has_domain_size=True)]

        assert "domain_size" not in keys

    def test_stages_keep_execution_order(self):
        keys = [s.key for s in stages_for(has_raman=True, has_pl=True,
                                          has_optical=True)]

        assert keys == [s.key for s in STAGES]

    def test_composing_the_report_is_never_dropped(self):
        """It is the one stage every run performs."""
        keys = [s.key for s in stages_for(has_raman=False, has_pl=False, has_optical=False)]

        assert "compose" in keys

    def test_the_most_minimal_run_is_just_the_compose_stage(self):
        keys = [s.key for s in stages_for(
            has_raman=False, has_pl=False, has_optical=False, has_segmentation=False)]

        assert keys == ["compose"]


class TestSubsetsStillSpanTheWholeBar:
    def test_a_partial_run_still_reaches_one(self):
        """Weights renormalize, so a Raman-only sample does not stop at 60%."""
        stages = stages_for(has_raman=True, has_pl=False, has_optical=False)
        seen, progress = _progress(stages)

        for key in progress.stage_keys:
            progress.complete(key)

        assert seen[-1][0] == pytest.approx(1.0)

    def test_a_single_stage_run_spans_zero_to_one(self):
        seen, progress = _progress([Stage("only", "Only", 0.3)])

        progress.start("only")
        progress.complete("only")

        assert seen[0][0] == pytest.approx(0.0)
        assert seen[-1][0] == pytest.approx(1.0)

    def test_dropping_a_stage_reallocates_its_share(self):
        """The remaining stages must grow, or the bar stalls where the dropped
        stage would have been."""
        full = ReportProgress(list(STAGES), lambda f, m: None)
        partial = ReportProgress(
            stages_for(has_raman=True, has_pl=False, has_optical=True), lambda f, m: None
        )

        assert partial._share["fit"] > full._share["fit"]


class TestFractionsAreSafeForStreamlit:
    def test_never_exceeds_one_even_when_overshooting_a_stage(self):
        """Streamlit raises StreamlitAPIException outside [0.0, 1.0]."""
        seen, progress = _progress()

        progress.tick("compose", 99, 3)

        assert all(0.0 <= f <= 1.0 for f, _ in seen)
        assert seen[-1][0] == pytest.approx(1.0)

    def test_never_goes_below_zero(self):
        seen, progress = _progress()

        progress.tick("fit", -5, 9)

        assert all(0.0 <= f <= 1.0 for f, _ in seen)

    def test_a_zero_total_does_not_divide_by_zero(self):
        """A technique with no points must not raise; the stage is just done."""
        seen, progress = _progress()

        progress.tick("fit", 0, 0)

        assert seen[-1][0] == pytest.approx(
            progress._offset["fit"] + progress._share["fit"]
        )

    def test_every_emitted_fraction_is_a_float_in_range(self):
        seen, progress = _progress()
        progress.start("fit")
        progress.tick("fit", 5, 9)
        progress.complete("fit")
        progress.start("raman_figures")
        progress.finish()

        for fraction, _ in seen:
            assert isinstance(fraction, float)
            assert 0.0 <= fraction <= 1.0


class TestTheBarNeverGoesBackwards:
    def test_monotonic_across_a_whole_run(self):
        seen, progress = _progress()

        for key in progress.stage_keys:
            progress.start(key)
            for done in range(1, 4):
                progress.tick(key, done, 3)
            progress.complete(key)
        progress.finish()

        fractions = [f for f, _ in seen]
        assert fractions == sorted(fractions)

    def test_starting_a_later_stage_cannot_undercut_earlier_ticks(self):
        seen, progress = _progress()

        progress.complete("fit")
        high = seen[-1][0]
        progress.start("raman_figures")

        assert seen[-1][0] >= high

    def test_a_stale_low_tick_does_not_rewind_the_bar(self):
        """Out-of-order reports from a callback must not make the bar retreat,
        which reads as a restart."""
        seen, progress = _progress()

        progress.tick("fit", 9, 9)
        high = seen[-1][0]
        progress.tick("fit", 1, 9)

        assert seen[-1][0] == pytest.approx(high)


class TestMessages:
    def test_start_names_the_stage_currently_running(self):
        """It announces before the work, so the label is the thing in flight,
        not the thing just finished."""
        seen, progress = _progress()

        progress.start("optical_segmentation")

        assert "Segmenting optical frames" in seen[-1][1]

    def test_a_tick_carries_its_count(self):
        seen, progress = _progress()

        progress.tick("optical_images", 4, 9)

        assert "4/9" in seen[-1][1]

    def test_a_count_beyond_the_total_is_not_shown_as_such(self):
        seen, progress = _progress()

        progress.tick("optical_images", 12, 9)

        assert "12/9" not in seen[-1][1]
        assert "9/9" in seen[-1][1]

    def test_detail_overrides_the_count(self):
        seen, progress = _progress()

        progress.start("compose", detail="summary page")

        assert "summary page" in seen[-1][1]

    def test_finish_reports_completion(self):
        seen, progress = _progress()

        progress.finish()

        assert seen[-1] == (1.0, "Report generated")


class TestSubCallbackMatchesSampleBatch:
    def test_adapts_the_label_done_total_signature(self):
        """sample_batch.ProgressCallback is Callable[[str, int, int], None]."""
        seen, progress = _progress()

        callback = progress.sub_callback("fit")
        callback("Raman", 3, 9)

        assert "Raman 3/9" in seen[-1][1]
        assert 0.0 < seen[-1][0] < 1.0

    def test_per_point_reports_move_the_bar_within_the_stage(self):
        seen, progress = _progress()
        callback = progress.sub_callback("fit")

        callback("Raman", 1, 9)
        first = seen[-1][0]
        callback("Raman", 9, 9)

        assert seen[-1][0] > first

    def test_both_techniques_move_the_bar_within_the_one_fitting_stage(self):
        """run_sample_batch runs Raman then PL, restarting its count for each.
        Without a combined denominator Raman's 9/9 fills the stage, PL's 1/9
        computes lower, the monotonic guard pins it, and the bar sits still
        through the entire second technique."""
        seen, progress = _progress()
        callback = progress.sub_callback("fit", totals={"Raman": 9, "PL": 9})

        for i in range(1, 10):
            callback("Raman", i, 9)
        after_raman = seen[-1][0]
        pl_fractions = []
        for i in range(1, 10):
            callback("PL", i, 9)
            pl_fractions.append(seen[-1][0])

        fit_start = progress._offset["fit"]
        fit_end = fit_start + progress._share["fit"]
        assert after_raman == pytest.approx(fit_start + progress._share["fit"] / 2)
        assert len(set(pl_fractions)) == 9, "PL points did not move the bar"
        assert pl_fractions == sorted(pl_fractions)
        assert pl_fractions[-1] == pytest.approx(fit_end)

    def test_raman_alone_only_reaches_half_the_stage_when_pl_is_expected(self):
        """The denominator is the combined total, so finishing one technique
        must not claim the whole stage."""
        seen, progress = _progress()
        callback = progress.sub_callback("fit", totals={"Raman": 9, "PL": 9})

        callback("Raman", 9, 9)

        assert seen[-1][0] < progress._offset["fit"] + progress._share["fit"]

    def test_without_totals_a_single_label_still_spans_the_stage(self):
        seen, progress = _progress()
        callback = progress.sub_callback("fit")

        callback("Raman", 9, 9)

        assert seen[-1][0] == pytest.approx(
            progress._offset["fit"] + progress._share["fit"]
        )

    def test_a_stale_report_for_one_label_does_not_shrink_the_sum(self):
        seen, progress = _progress()
        callback = progress.sub_callback("fit", totals={"Raman": 9, "PL": 9})

        callback("Raman", 9, 9)
        callback("PL", 5, 9)
        high = seen[-1][0]
        callback("Raman", 2, 9)  # a stale, lower report for a finished label

        assert seen[-1][0] == pytest.approx(high)

    def test_a_zero_total_from_the_callback_is_survivable(self):
        seen, progress = _progress()

        progress.sub_callback("fit")("Raman", 0, 0)

        assert 0.0 <= seen[-1][0] <= 1.0


class TestMisuseIsLoud:
    def test_an_unknown_stage_key_raises(self):
        """A typo must not silently report nothing."""
        _seen, progress = _progress()

        with pytest.raises(KeyError):
            progress.start("figures")

    def test_a_stage_dropped_from_this_run_raises(self):
        _seen, progress = _progress(
            stages_for(has_raman=True, has_pl=False, has_optical=True)
        )

        with pytest.raises(KeyError):
            progress.start("pl_figures")

    def test_sub_callback_for_an_inactive_stage_raises_immediately(self):
        """Not on first call, when the work is already underway."""
        _seen, progress = _progress(stages_for(
            has_raman=False, has_pl=False, has_optical=True))

        with pytest.raises(KeyError):
            progress.sub_callback("fit")

    def test_no_stages_is_rejected(self):
        with pytest.raises(ValueError):
            ReportProgress([], lambda f, m: None)

    def test_weightless_stages_are_rejected(self):
        with pytest.raises(ValueError):
            ReportProgress([Stage("a", "A", 0.0)], lambda f, m: None)


class TestBuild:
    def test_returns_a_reporter_for_a_normal_run(self):
        _seen, sink = _recorder()
        progress = build(sink, has_raman=True, has_pl=True, has_optical=True)

        assert progress is not None
        assert progress.stage_keys == [s.key for s in STAGES]

    def test_an_images_only_run_still_gets_a_reporter(self):
        _seen, sink = _recorder()
        progress = build(sink, has_raman=False, has_pl=False, has_optical=True)

        assert progress is not None
        assert "fit" not in progress.stage_keys

    def test_the_sink_receives_what_the_reporter_emits(self):
        seen, sink = _recorder()
        progress = build(sink, has_raman=True, has_pl=True, has_optical=True)

        progress.start("optical_images")

        assert seen == [(0.0, "Loading optical images...")]


class TestSubCallbackAdaptiveTotals:
    """`totals` is an estimate the caller makes from its file count. A
    multi-spectrum file reports one tick per spectrum, so the estimate can be
    wrong by 25x and the denominator has to catch up."""

    def test_a_label_reporting_more_than_expected_raises_the_denominator(self):
        seen, progress = _progress()
        callback = progress.sub_callback("fit", totals={"Raman": 9, "PL": 0})

        callback("Raman", 225, 225)

        assert seen[-1][0] == pytest.approx(
            progress._offset["fit"] + progress._share["fit"]
        )

    def test_overshooting_does_not_peg_the_bar_early(self):
        """Without the correction, Raman's 25th of 225 ticks reads 25/9 — well
        past full — and the bar freezes there for the remaining 200 fits."""
        seen, progress = _progress()
        callback = progress.sub_callback("fit", totals={"Raman": 9, "PL": 0})

        fractions = []
        for done in (25, 75, 150, 225):
            callback("Raman", done, 225)
            fractions.append(seen[-1][0])

        assert fractions == sorted(fractions)
        assert len(set(fractions)) == 4, "the bar stopped moving"
        assert fractions[0] < progress._offset["fit"] + progress._share["fit"] / 2

    def test_a_label_that_has_not_reported_keeps_its_reserved_slice(self):
        seen, progress = _progress()
        callback = progress.sub_callback("fit", totals={"Raman": 9, "PL": 9})

        callback("Raman", 225, 225)

        # 225 of (225 + 9): nearly the whole stage, but PL still owns its share.
        assert seen[-1][0] < progress._offset["fit"] + progress._share["fit"]


class TestFitWeightScalesWithSpectrumCount:
    """The fit stage is the only one whose cost tracks spectra per point.

    Segmenting frames, rendering figures and composing the report cost the same
    whether a grid point holds one spectrum or a hundred. Measured on
    TSM260803 (25 spectra per file) fitting took 77 % of the wall clock against
    a declared 21.5 %, so the bar crawled through the first fifth and then
    jumped to done.
    """

    def _fit_share(self, **kwargs):
        stages = stages_for(has_raman=True, has_pl=True, has_optical=True, **kwargs)
        total = sum(s.weight for s in stages)
        return next(s.weight for s in stages if s.key == "fit") / total

    def test_the_baseline_sample_is_unchanged(self):
        """18 spectra is what the weights were measured on, so passing it
        explicitly must match passing nothing at all."""
        assert self._fit_share(fit_spectra=BASELINE_FIT_SPECTRA) == pytest.approx(
            self._fit_share()
        )

    def test_more_spectra_claim_more_of_the_bar(self):
        assert self._fit_share(fit_spectra=450) > self._fit_share(fit_spectra=18) * 3

    def test_a_real_multi_spectrum_sample_claims_most_of_the_bar(self):
        """TSM260803: 9 files x 25 spectra, Raman only. Fitting measured at
        0.77 of the *old* report's total; on the merged page it shares the run
        with ~40 s of segmentation and OM rendering, so its honest share is
        lower. It must still be the largest stage — that is the property the
        scaling exists to preserve."""
        stages = stages_for(has_raman=True, has_pl=False, has_optical=True,
                            fit_spectra=225)
        total = sum(s.weight for s in stages)
        weights = {s.key: s.weight for s in stages}
        fit = weights["fit"] / total

        assert 0.45 < fit < 0.7
        assert weights["fit"] > weights["optical_segmentation"]

    def test_zero_or_none_falls_back_to_the_unscaled_weights(self):
        assert self._fit_share(fit_spectra=0) == pytest.approx(self._fit_share())
        assert self._fit_share(fit_spectra=None) == pytest.approx(self._fit_share())

    def test_scaling_does_not_disturb_the_other_stages_relative_order(self):
        stages = stages_for(has_raman=True, has_pl=True, has_optical=True,
                            fit_spectra=450)
        others = {s.key: s.weight for s in stages if s.key != "fit"}

        assert others["optical_segmentation"] > others["om_figures"]
        assert others["om_figures"] > others["pl_figures"] > others["raman_figures"]
        assert others["raman_figures"] > others["optical_images"] > others["compose"]
