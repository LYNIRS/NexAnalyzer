"""Session state for the QC Report page.

Its own namespace, separate from the Spectra page's `files`/`current_file`
(that page's per-file model): a sample folder's spectra and nine microscope
frames don't fit a single-file-at-a-time shape.

This is the merge of `sample_report` and `qc_panel`, which were two dicts for
two pages reading the same folder until v5.0.0. They are one page now, so the
reason they were kept apart — either silently invalidating the other's results
— no longer applies.

A plain dict rather than a dataclass, matching the other state modules —
callers mutate it in place and Streamlit persists the object. It has no
migration story, so read any key added later with `.get()`.
"""

import streamlit as st

_KEY = "qc_report"


def initialize_qc_report_state() -> None:
    """Create st.session_state["qc_report"] if absent. Idempotent."""
    if _KEY not in st.session_state:
        st.session_state[_KEY] = {
            "folder": None,
            "scan": None,
            "magnification": None,
            "material": None,
            "reference_layer": "2L",
            # True relabels the reference class "Substrate" and the brighter
            # class as the named film -- see contrast.class_labels().
            "low_coverage": False,
            # None until resolved from report_settings.load_measure_domain_size.
            "measure_domain_size": None,
            # Which of classify()'s three classes to split into domains --
            # "below" | "reference" | "above"; see domain_size.TARGETS.
            "domain_target": "above",
            # None until resolved from report_settings.load_split_size_populations.
            # Gates domain_size.measure_domains()/domain_size_stat()'s own
            # detect_size_populations argument -- off by default since the
            # split has fired on frames with no real second population at all.
            "split_size_populations": None,
            # Everything below is derived and must appear in one of the
            # artifact tuples; see _SELECTIONS.
            "batch_result": None,
            "report_date": None,
            "summary_png": None,          # Figure 1, the overview page
            "xlsx_bytes": None,           # every table, one workbook
            "frames": None,               # list[contrast.FrameResult]
            "om_png": None,               # Figure 2, clean (for the report)
            "om_diagnostic_png": None,    # Figure 3, with the histogram row
            "optical_threshold_pair": None,  # preset (below, above), or None
            "optical_fingerprint": None,
            "domain_results": None,       # list[domain_size.DomainResult]
            "domain_stat": None,          # models.DomainSizeStat, or None
            "domain_grid_png": None,      # Figure 4, the domain-size grid
            "raman_stats": None,
            "raman_ratios": None,         # [(label, (median, mad, n))]
            "raman_grid_png": None,       # Figure 4, the nine fitted spectra
            "raman_stats_png": None,      # Figure 5, the quality panels
            "raman_errors": None,         # list[(point, message)] from fitting
            "raman_dropped": None,        # count gated out by R_SQUARED_MIN
            "raman_fitted": None,         # count that converged, gate aside
            "raman_fingerprint": None,
            "pl_stats": None,
            "pl_grid_png": None,          # Figure 6
            "pl_stats_png": None,         # Figure 7
            "pl_errors": None,
            "pl_dropped": None,
            "pl_fitted": None,
            "pl_fingerprint": None,
        }


def get_qc_report_state() -> dict:
    """Initialize (if needed) and return the QC Report state dict."""
    initialize_qc_report_state()
    return st.session_state[_KEY]


#: The operator's own choices. Everything else in the state dict is derived
#: from them and gets dropped by `reset_results`; a derived key missing from
#: the tuples below outlives the run that produced it, which is how a verdict
#: naming the previous material comes to sit under the new one's results.
_SELECTIONS = (
    "folder", "scan", "magnification", "material", "reference_layer",
    "low_coverage", "measure_domain_size", "domain_target", "split_size_populations",
)

#: Built from *every* input, so any change at all invalidates them. They are
#: also the cheap ones — the overview page is a second of matplotlib and the
#: workbook is ~50 ms of openpyxl — so there is nothing to protect here and
#: keeping a stale one would only let a report page name one material while the
#: figure beside it was drawn from another.
_COMPOSITE_ARTIFACTS = ("batch_result", "report_date", "summary_png", "xlsx_bytes")

#: Derived keys, grouped by the preset block whose settings produced them.
_OPTICAL_ARTIFACTS = (
    "frames", "om_png", "om_diagnostic_png", "optical_threshold_pair",
    "optical_fingerprint", "domain_results", "domain_stat", "domain_grid_png",
)
_RAMAN_ARTIFACTS = (
    "raman_stats", "raman_ratios", "raman_grid_png", "raman_stats_png",
    "raman_errors", "raman_dropped", "raman_fitted", "raman_fingerprint",
)
_PL_ARTIFACTS = (
    "pl_stats", "pl_grid_png", "pl_stats_png",
    "pl_errors", "pl_dropped", "pl_fitted", "pl_fingerprint",
)


def reset_results(state: dict) -> None:
    """Drop every derived artifact, keeping the operator's selections.

    Called whenever an input changes that invalidates *everything* — the
    folder, the material, the layer. The point of dropping them is that
    leaving stale derived keys behind means a figure from the previous sample
    stays on screen next to the new sample's name.

    For a preset edit that touches only one side, use `reset_optical_results`
    or `reset_spectra_results`: a Raman peak edit must not discard a 30-second
    OM segmentation.
    """
    reset_optical_results(state)
    reset_spectra_results(state)


def reset_optical_results(state: dict) -> None:
    """Drop the OM figures and the segmentation behind them."""
    for key in _OPTICAL_ARTIFACTS + _COMPOSITE_ARTIFACTS:
        state[key] = None


def reset_spectra_results(state: dict) -> None:
    """Drop both techniques' figures, statistics and fits.

    Raman and PL travel together, unlike optical, because one
    `run_sample_batch` call fits both: re-running to rebuild a Raman figure
    refits the PL spectra whether or not anything about PL changed. Splitting
    the reset would leave PL artifacts standing beside a `batch_result` that no
    longer produced them, which is a subtler version of the staleness these
    resets exist to prevent. The per-technique fingerprints are still kept
    apart so the page can name which block moved.
    """
    for key in _RAMAN_ARTIFACTS + _PL_ARTIFACTS + _COMPOSITE_ARTIFACTS:
        state[key] = None
