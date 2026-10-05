"""
Session state management utilities for Streamlit.

This module provides functions to:
- Initialize session state on first load
- Access and update session state safely
- Manage multiple spectrum files
"""

import streamlit as st


def initialize_session_state():
    """
    Initialize session state on first app load.

    Creates the following keys in st.session_state:
    - mode: str ("Raman" or "PL")
    - files: dict (filename -> SpectrumFile mapping)
    - current_file: Optional[str] (selected filename)
    - plot_width_preset: str (v2.1+, FR-14: "Compact" | "Standard" | "Wide" | "Full")
    - selected_preset: MaterialPreset or None (currently selected preset on the Spectra page)
    - show_raw / show_despiked / show_corrected / show_fit / show_components /
      show_residuals: bool (View Options plot-layer visibility)

    This function is idempotent (safe to call multiple times).
    """
    if "mode" not in st.session_state:
        st.session_state["mode"] = "Raman"

    if "files" not in st.session_state:
        st.session_state["files"] = {}

    if "current_file" not in st.session_state:
        st.session_state["current_file"] = None

    # v2.1+ (FR-14): Plot width control
    if "plot_width_preset" not in st.session_state:
        st.session_state["plot_width_preset"] = "Full"  # Default: 100% width

    # Material preset system (embedded/JSON-backed as of v2.11.0)
    if "selected_preset" not in st.session_state:
        st.session_state["selected_preset"] = None

    # View Options defaults. Set here (rather than via the checkboxes'
    # `value=` parameter) so the checkboxes never pass `value=` themselves —
    # otherwise Streamlit warns whenever this state was already written
    # earlier in the same run (e.g. by sync_pending_file_switch()).
    # The starting set is the fitted-spectrum view (v5.12.0; raw-only before),
    # the one a finished fit derives anyway.
    for _key, _on in VIEW_OPTION_DEFAULTS.items():
        if _key not in st.session_state:
            st.session_state[_key] = _on


VIEW_OPTION_KEYS = (
    "show_raw",
    "show_despiked",
    "show_corrected",
    "show_fit",
    "show_components",
    "show_residuals",
)

#: Where the View Options start in a new session: the fitted-spectrum view.
VIEW_OPTION_DEFAULTS = {
    "show_raw": False,
    "show_despiked": False,
    "show_corrected": True,
    "show_fit": True,
    "show_components": True,
    "show_residuals": True,
}

# Session key holding the set of View Options the user has set by hand.
VIEW_OPTIONS_PINNED_KEY = "_view_options_pinned"


def pinned_view_options() -> set:
    """The View Options the user has toggled by hand this session.

    A pinned key is the user's answer, not a derived one, so the stage-driven
    defaults leave it alone. Before v5.7.1 nothing tracked this: the file
    switch, the auto-workflow and the batch run each rewrote all six keys, so
    a toggle survived only until the next file was selected.
    """
    pinned = st.session_state.get(VIEW_OPTIONS_PINNED_KEY)
    if not isinstance(pinned, set):
        pinned = set()
        st.session_state[VIEW_OPTIONS_PINNED_KEY] = pinned
    return pinned


def pin_view_option(key: str) -> None:
    """Mark a View Options checkbox as the user's own answer.

    Runs as the checkbox's on_change callback.
    """
    pinned_view_options().add(key)


def clear_pinned_view_options() -> None:
    """Forget every hand-set View Option, so the layers follow the data again."""
    st.session_state[VIEW_OPTIONS_PINNED_KEY] = set()


def set_view_options(values: dict, *, respect_pinned: bool = True) -> None:
    """Write stage-derived View Options, skipping any the user pinned.

    Every automatic writer of the show_* keys goes through here, so one place
    knows that a hand-set checkbox outranks a stage default. Callers must run
    before the sidebar instantiates those checkboxes: Streamlit forbids
    writing a widget's key after the widget exists in the same script run.
    """
    pinned = pinned_view_options() if respect_pinned else set()
    for key, value in values.items():
        if key in pinned:
            continue
        st.session_state[key] = value


def get_current_spectrum():
    """
    Get the currently selected SpectrumFile.

    Returns
    -------
    SpectrumFile or None
        Current spectrum if one is selected, else None.

    Examples
    --------
    >>> spectrum = get_current_spectrum()
    >>> if spectrum:
    ...     print(f"Loaded: {spectrum.filename}")
    """
    current_file = st.session_state.get("current_file")
    if current_file is None:
        return None

    files = st.session_state.get("files", {})
    return files.get(current_file)


def add_spectrum_file(spectrum_file):
    """
    Add a SpectrumFile to session state.

    Parameters
    ----------
    spectrum_file : SpectrumFile
        Spectrum file to add.

    Notes
    -----
    If a file with the same filename exists, it will be overwritten.
    The added file automatically becomes the current selection.
    """
    if "files" not in st.session_state:
        st.session_state["files"] = {}

    st.session_state["files"][spectrum_file.filename] = spectrum_file
    st.session_state["current_file"] = spectrum_file.filename


def remove_spectrum_file(filename: str):
    """
    Remove a SpectrumFile from session state.

    Parameters
    ----------
    filename : str
        Filename to remove.

    Notes
    -----
    If the removed file was the current selection, current_file is set to None
    (or to the first remaining file if any exist).
    """
    if filename not in st.session_state.get("files", {}):
        return

    del st.session_state["files"][filename]

    # Update current_file if the removed file was selected
    if st.session_state.get("current_file") == filename:
        remaining_files = list(st.session_state["files"].keys())
        st.session_state["current_file"] = remaining_files[0] if remaining_files else None


def clear_all_files():
    """
    Remove all SpectrumFile objects from session state.

    This is useful for "New Project" functionality.
    """
    st.session_state["files"] = {}
    st.session_state["current_file"] = None
def set_mode(mode: str):
    """
    Set spectroscopy mode.

    Parameters
    ----------
    mode : str
        "Raman" or "PL"

    Raises
    ------
    ValueError
        If mode is not "Raman" or "PL"
    """
    if mode not in ["Raman", "PL"]:
        raise ValueError(f"mode must be 'Raman' or 'PL' (got {mode})")

    st.session_state["mode"] = mode
