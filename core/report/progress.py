"""
Weighted, staged progress for a long report build.

Report generation is a chain of stages with wildly different costs, and the
user's real question during it is "is this working, or has it died?". A bar
that reaches 100% and disappears while three quarters of the work is still
to come answers that question wrongly, which is what this exists to fix.

The weights below are **carried over from two measured sets**, not freshly
measured on the merged pipeline. The report half was measured on a real
9-point sample (``Example/HADG06``: 9 Raman + 9 PL 2000-point spectra, 9x
2240x1680 BMP optical images) totalling 24.5 s:

    fit 5.28s | Raman fit columns 3.98s | PL fit columns 4.66s
    optical images 5.37s | .pptx 2.09s | preview 3.17s

The segmentation half comes from the QC Panel's own documented timings: about
30 s to segment nine frames, and about 5 s for each of the two OM renders. The
.pptx and PowerPoint-preview stages are gone at v5.0.0 and their time is
replaced by the summary page and workbook, which together cost about a second.

Combining the two gives roughly 64 s, dominated by segmentation at ~47%.
**This is owed a re-measurement on the merged page** — the two sets came off
different samples on different days, and only their ratio to each other is
guesswork. What is not guesswork is the shape: segmentation is far and away
the longest stage, and a bar that doesn't say so is the lie this module exists
to prevent.

`domain_size`'s weight is a third kind of number: not measured at all, unlike
its two siblings above. Domain-size analysis re-runs a segmentation-equivalent
`analyse_array()` pass per frame (this codebase's actual, current cost) plus
watershed marker-finding on top, so its weight is set equal to
`optical_segmentation`'s own — "at least as expensive as segmentation, plausibly
more" — as an explicit placeholder pending a real measurement, not a rounded
guess dressed up as one. Replace it the first time this stage's real wall time
is known.

Don't flatten them to equal shares. Equal weights would put the bar at 50%
when 78% of the time is still ahead, which is the same lie in a different
shape. If the pipeline changes materially, re-measure rather than estimate.

Aggregation (stats, legends, ratios) is deliberately absent: it takes 4 ms,
so a visible step for it would flicker past and buy nothing.

This module is intentionally free of Streamlit. It computes fractions and
labels; the caller decides what to draw. That keeps the arithmetic — the part
with the off-by-one and the divide-by-zero in it — testable without a
browser or a script runner.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

# fraction in [0.0, 1.0], human-readable message
ProgressSink = Callable[[float, str], None]


@dataclass(frozen=True)
class Stage:
    """One phase of the build, and its measured share of the total wall time.

    ``weight`` is relative: `ReportProgress` renormalizes whatever set of
    stages it is handed, so a run with no PL files stays honest without the
    caller doing arithmetic.
    """

    key: str
    label: str
    weight: float


#: The full chain, in execution order, with weights. A caller keeps only the
#: stages its run will actually perform.
STAGES: Sequence[Stage] = (
    # Renormalised in v5.5.0, when the 0.078 the threshold derivation held was
    # given back to the stages proportionally rather than to any one of them:
    # the measured run is the same run, minus a pass that no longer happens.
    # `domain_size` (local addition) weighs the same as segmentation, the ratio
    # it was measured at before the merge; weights are relative, so adding it
    # needs no renormalisation of the rest.
    Stage("optical_images", "Loading optical images", 0.090),
    Stage("optical_segmentation", "Segmenting optical frames", 0.423),
    Stage("om_figures", "Rendering OM figures", 0.168),
    Stage("domain_size", "Measuring domain size", 0.423),
    Stage("fit", "Fitting spectra", 0.089),
    Stage("raman_figures", "Rendering Raman figures", 0.101),
    Stage("pl_figures", "Rendering PL figures", 0.112),
    Stage("compose", "Composing the report", 0.017),
)


BASELINE_FIT_SPECTRA = 18
"""Spectra fitted by the sample the stage weights were measured on.

Nine Raman plus nine PL files, one spectrum each. `fit`'s 0.215 is that
sample's share; a run fitting more spectra scales it up from here.
"""


def stages_for(
    *, has_raman: bool, has_pl: bool, has_optical: bool,
    has_segmentation: bool = True,
    has_domain_size: bool = True,
    fit_spectra: Optional[int] = None,
) -> List[Stage]:
    """The stages a run will actually perform, in order.

    A sample with no PL files must not reserve 19% of the bar for PL figures;
    it would sit still through a stage that never runs. Dropping the stage and
    letting the remaining weights renormalize keeps the bar proportional to
    the work in front of it.

    `fit_spectra` is how many spectra the run will fit, and it matters because
    the fit stage is the only one that scales with it: rendering figures,
    loading images and assembling the deck cost the same whether a grid point
    holds one spectrum or a hundred. Measured on TSM260803, whose files hold 25
    spectra each, fitting took 77% of the wall clock against a declared 21.5% —
    so the bar crawled through the first fifth and then jumped. Scaling the
    weight by the spectrum count keeps one weight set honest for both shapes.
    Leave it None for the single-spectrum-per-point case the weights were
    measured on.

    `has_domain_size` is the operator's own opt-in choice, not a fact about
    the sample the way `has_optical`/`has_segmentation` are -- default True
    so a caller that hasn't been updated for the flag keeps today's behavior.
    """
    keep = {
        "optical_images": has_optical,
        # A folder can have images without a segmentation to run — no material
        # picked, or a preset with no optical block the operator declined. The
        # summary page's grid still wants the frames loaded.
        "optical_segmentation": has_optical and has_segmentation,
        "om_figures": has_optical and has_segmentation,
        "domain_size": has_optical and has_segmentation and has_domain_size,
        "fit": has_raman or has_pl,
        "raman_figures": has_raman,
        "pl_figures": has_pl,
        "compose": True,
    }
    scale = 1.0
    if fit_spectra and fit_spectra > 0:
        scale = fit_spectra / BASELINE_FIT_SPECTRA
    return [
        Stage(stage.key, stage.label,
              stage.weight * scale if stage.key == "fit" else stage.weight)
        for stage in STAGES if keep.get(stage.key, False)
    ]


class ReportProgress:
    """Drives a `ProgressSink` through a weighted sequence of stages.

    Usage is `start()` before a stage's work and, where the stage has
    countable sub-steps, `tick()` as they complete::

        progress.start("optical_images")
        for i, path in enumerate(paths, start=1):
            load(path)
            progress.tick("optical_images", i, len(paths))

    `start()` announcing *before* the work is the point: the label has to name
    the thing currently running, not the thing that just finished.
    """

    def __init__(self, stages: Sequence[Stage], sink: ProgressSink) -> None:
        if not stages:
            raise ValueError("stages must not be empty")

        total_weight = sum(stage.weight for stage in stages)
        if total_weight <= 0:
            raise ValueError(f"stage weights must sum to > 0 (got {total_weight})")

        self._stages: List[Stage] = list(stages)
        self._sink = sink

        # Renormalized so any subset of STAGES still spans exactly 0..1, and
        # cumulative offsets precomputed so `tick` is pure arithmetic.
        self._share: Dict[str, float] = {}
        self._offset: Dict[str, float] = {}
        running = 0.0
        for stage in self._stages:
            share = stage.weight / total_weight
            self._share[stage.key] = share
            self._offset[stage.key] = running
            running += share

        self._labels: Dict[str, str] = {s.key: s.label for s in self._stages}
        self._highest = 0.0  # the bar must never appear to go backwards

    @property
    def stage_keys(self) -> List[str]:
        return [stage.key for stage in self._stages]

    def _emit(self, fraction: float, message: str) -> None:
        # Streamlit raises if a progress value leaves [0.0, 1.0], and rounding
        # a sum of six renormalized floats can land a hair outside it.
        fraction = min(1.0, max(0.0, fraction))
        # Monotonic: a later stage's start must not undercut an earlier
        # stage's completed ticks, however the weights round.
        self._highest = max(self._highest, fraction)
        self._sink(self._highest, message)

    def _require(self, key: str) -> None:
        if key not in self._share:
            raise KeyError(
                f"unknown or inactive stage {key!r}; active stages are {self.stage_keys}"
            )

    def start(self, key: str, detail: str = "") -> None:
        """Announce a stage, before its work runs."""
        self._require(key)
        self._emit(self._offset[key], self.message(key, detail=detail))

    def tick(self, key: str, done: int, total: int, detail: str = "") -> None:
        """Report `done` of `total` sub-steps completed within a stage.

        A `total` of 0 means nothing to do, so the stage reads as complete
        rather than raising on the division.
        """
        self._require(key)
        completed = 1.0 if total <= 0 else min(1.0, max(0.0, done / total))
        fraction = self._offset[key] + completed * self._share[key]
        counted = detail or (f"{min(done, total)}/{total}" if total > 0 else "")
        self._emit(fraction, self.message(key, detail=counted))

    def complete(self, key: str, detail: str = "") -> None:
        """Mark a stage fully done without claiming the next one has begun."""
        self._require(key)
        self._emit(
            self._offset[key] + self._share[key], self.message(key, detail=detail)
        )

    def finish(self, message: str = "Report generated") -> None:
        self._emit(1.0, message)

    def message(self, key: str, detail: str = "") -> str:
        label = self._labels.get(key, key)
        return f"{label} ({detail})" if detail else f"{label}..."

    def sub_callback(
        self, key: str, totals: Optional[Dict[str, int]] = None
    ) -> Callable[[str, int, int], None]:
        """An adapter matching `sample_batch.ProgressCallback`.

        That callback is `(label, done, total)` and already fires once per
        fitted point, so the fitting stage gets per-point resolution for free
        rather than sitting still for its share of the bar.

        `totals` maps each label to the number of spectra it is expected to
        report — a floor the callback raises if a label reports more, since a
        caller counting files cannot know how many spectra each file holds.
        It matters more than it looks: `run_sample_batch` runs the techniques in
        sequence and restarts its count for each, so Raman reporting 9/9 would
        fill the whole stage and PL would then report 1/9 — a lower fraction,
        which the monotonic guard pins in place. The bar would sit still
        through the entire second technique. Summing against a fixed combined
        denominator is what makes both halves visible. Pass None only when a
        single label reports.
        """
        self._require(key)
        expected: Dict[str, int] = dict(totals) if totals else {}
        seen: Dict[str, int] = {}

        def _callback(label: str, done: int, total: int) -> None:
            # max(): a stale lower report must not rewind this label's count.
            seen[label] = max(seen.get(label, 0), done)
            # `totals` is a floor, not a promise. Callers estimate it from the
            # file count, but a multi-spectrum file reports one tick per
            # spectrum, so a label can overshoot its estimate 25-fold and pin
            # the bar at its share for the rest of the run. Raising a label's
            # denominator to what it actually reports keeps the fraction
            # honest, while labels that have not started yet keep their
            # estimate and so keep their slice reserved.
            if total > expected.get(label, 0):
                expected[label] = total
            combined = sum(expected.values())
            if combined > 0:
                self.tick(
                    key, sum(seen.values()), combined,
                    detail=f"{label} {min(done, total)}/{total}",
                )
            else:
                self.tick(key, done, total, detail=f"{label} {min(done, total)}/{total}")

        return _callback


def build(
    sink: ProgressSink,
    *,
    has_raman: bool,
    has_pl: bool,
    has_optical: bool,
    has_segmentation: bool = True,
    has_domain_size: bool = True,
    fit_spectra: Optional[int] = None,
) -> Optional[ReportProgress]:
    """A `ReportProgress` for a run with these techniques, or None if there is
    nothing to report on.

    Pass `fit_spectra` when a sample's files hold more than one spectrum each —
    see `stages_for`.
    """
    stages = stages_for(
        has_raman=has_raman,
        has_pl=has_pl,
        has_optical=has_optical,
        has_segmentation=has_segmentation,
        has_domain_size=has_domain_size,
        fit_spectra=fit_spectra,
    )
    if not stages:
        return None
    return ReportProgress(stages, sink)
