"""
Sidebar UI component for the Spectra page: mode selection, file upload,
and results export.

This module provides the render_sidebar() function that displays, in order:
- Material Presets: a dropdown sourced from src/presets/materials.json
  (edited on the "Material Presets" page — see pages/material_presets.py)
  plus the Run Auto-Workflow / Run All Files buttons
- Quick Export (PNG/HTML/CSV) and Batch Export (master CSV)
- Reset to Raw
- Load Spectra (file upload widget) and Loaded Files (file selector)
- View Options (plot layer visibility), last in the sidebar
"""

import streamlit as st
from ..models.spectrum import SpectrumFile, ProcessingSettings
from ..processing.parser import parse_spectrum_multi, detect_mode_from_filename
from ..processing.axis_calibration import (
    DEADBAND as AXIS_DEADBAND,
    axis_note,
    calibrate_raman_session,
)
from core.io.report_settings import load_correct_raman_axis, save_correct_raman_axis
from core.io.export import (
    export_figure_png,
    export_figure_html,
    create_filename,
    prompt_save_path,
    prompt_save_folder,
)
from ..io.results_csv import export_fit_params_csv, export_master_csv
from ..io.preset_store import load_presets
from ..utils.preset_staleness import technique_fingerprint
from ..viz.fit_plot import plot_composite
from .session_state import (
    initialize_session_state,
    add_spectrum_file,
    remove_spectrum_file,
    clear_all_files,
    set_mode,
    get_current_spectrum,
    set_view_options,
    clear_pinned_view_options,
    pin_view_option,
    pinned_view_options,
)


@st.cache_data(max_entries=16, show_spinner=False)
def _export_png_cached(fig, width: int, height: int, scale: float) -> bytes:
    """`export_figure_png`, memoized on the figure itself.

    st.download_button needs its bytes at render time, so this rasterization
    used to run on EVERY rerun of the Spectra page — measured at 1316 ms, paid
    for every checkbox toggle and file switch, whether or not anyone ever
    clicked Download.

    `fig` is deliberately NOT underscore-prefixed. Streamlit hashes a plotly
    Figure by content (measured 4.3 ms for a 2000-point composite), so the
    figure *is* the cache key, and it cannot go stale: change the data, the fit,
    a peak label, a peak color or even a palette constant and the hash moves.
    A hand-rolled fingerprint was tried first and had five holes, one of them
    reachable — it hashed no module constants, so restyling `palette.py` served
    the old PNG under an identical key until the process restarted.

    max_entries bounds it: the default is unbounded, and each PNG is ~165-300 KB.
    show_spinner=False because the default would render "Running
    _export_png_cached(...)" inside the narrow Quick Export column.

    Note that a raise is not cached, so a broken kaleido install still re-pays
    the failing render every rerun — an error path, not a slow path.
    """
    return export_figure_png(fig, width=width, height=height, scale=scale)


def _describe_detected_modes(files) -> None:
    """Show what the loaded filenames said their technique was, and warn if some
    didn't say.

    Technique used to be a property of the selected preset, so it was always
    stated. It now comes from the filename, and `detect_mode_from_filename`
    returns None for a name it doesn't recognise -- whereupon the loader falls
    back to "Raman". That fallback is a guess, and a guess that nothing on
    screen distinguishes from a reading is worse than no reading at all.

    `SpectrumFile.auto_detected` already records which files were guessed at.
    """
    if not files:
        return

    counts = {"Raman": 0, "PL": 0}
    undetected = []
    for name, spectrum in files.items():
        if getattr(spectrum, "auto_detected", False):
            counts[spectrum.mode] = counts.get(spectrum.mode, 0) + 1
        else:
            undetected.append(name)

    parts = [f"{n} {mode}" for mode, n in counts.items() if n]
    if undetected:
        parts.append(f"{len(undetected)} undetected → Raman")
    if not parts:
        return

    line = ", ".join(parts)
    if undetected:
        st.warning(
            f"**Detected from filenames**: {line}. "
            f"Rename to RM_*/Raman_*/PL_* so the technique is read rather than "
            f"assumed.",
            icon="⚠️",
        )
    else:
        st.caption(f"**Detected from filenames**: {line}")


#: Fingerprint of the preset block each file was last fitted with, keyed by
#: filename. Session state rather than a `SpectrumFile` field: which preset a
#: fit came from is a UI affordance, not part of the measurement, and keeping
#: it here leaves the model's serialization alone.
_FIT_PRESET_KEY = "fit_preset_fingerprints"


def _remember_fit_preset(spectrum, preset) -> None:
    """Record which preset block `spectrum` was just fitted with."""
    fingerprints = st.session_state.setdefault(_FIT_PRESET_KEY, {})
    fingerprints[spectrum.filename] = (
        preset.material_name, technique_fingerprint(preset, spectrum.mode)
    )


def _warn_if_preset_moved(spectrum, preset) -> None:
    """Say so when the selected preset no longer matches the displayed fit.

    The QC Report has compared preset fingerprints since v5.0.0 and tells the
    operator to re-run; the Spectra page compared nothing, so editing a preset
    and coming back left a fit on screen that no longer matched the settings
    beside it, with nothing to say so. `fit_stale` covers despike and baseline
    only -- and nothing rendered it either.

    A warning, not an automatic refit: fitting is the operator's action, and
    the previous fit stays exportable until they take it.
    """
    if spectrum is None or not spectrum.fit_done:
        return
    remembered = st.session_state.get(_FIT_PRESET_KEY, {}).get(spectrum.filename)
    if remembered is None:
        return  # fitted before this ran, or by hand; nothing to compare against

    material, fingerprint = remembered
    if material == preset.material_name and \
            fingerprint == technique_fingerprint(preset, spectrum.mode):
        return

    made_with = (
        f"**{material}**'s earlier settings"
        if material == preset.material_name
        else f"**{material}**, not **{preset.material_name}**"
    )
    st.warning(
        f"The fit shown was made with {made_with}. Run the auto-workflow "
        f"again to apply the current preset.",
        icon="⚠️",
    )


def render_sidebar():
    """
    Render sidebar with mode toggle, file upload, and results export.

    This function should be called from app.py within a `with st.sidebar:` block.

    Notes
    -----
    - Mode toggle affects axis labels and fitting bounds
    - File upload supports multiple files (batch loading)
    - File selector dropdown shows all loaded files
    """
    # Initialize session state
    initialize_session_state()

    st.header("Settings")

    # Material Presets (embedded/JSON-backed as of v2.11.0 — add or edit
    # materials on the "Material Presets" page). As of v4.0.0 a preset is
    # keyed by material alone and each file's own mode picks the technique
    # block, so nothing here rewrites a loaded file's mode.
    st.subheader("Material Presets")

    presets = load_presets()
    files = st.session_state.get("files", {})
    current_file = st.session_state.get("current_file")
    current_spectrum = files[current_file] if (files and current_file) else None

    if presets:
        preset_keys = sorted(key for key, p in presets.items() if p.enabled)

        selected_key = st.selectbox(
            "Select Material",
            options=[None] + preset_keys,
            index=0,
            format_func=lambda k: "(None)" if k is None else k,
            help="Materials configured on the Material Presets page (Raman + PL)"
        )

        if selected_key is not None:
            preset = presets[selected_key]
            st.session_state['selected_preset'] = preset

            # No mode sync. Until v4.0.0 this loop rewrote *every* loaded
            # file's mode to the preset's, Raman files included, which is how a
            # PL preset could silently relabel a folder of Raman spectra. Each
            # file's mode now comes from its own filename and stays there.
            _describe_detected_modes(files)

            # Display preset info, per technique present.
            for _mode in ("Raman", "PL"):
                _block = preset.block_for(_mode)
                if _block is None:
                    continue
                st.caption(
                    f"**{_mode}**: {_block.baseline_algorithm} baseline, "
                    f"{len(_block.peak_templates)} peaks"
                )
            if not preset.block_for("Raman") and not preset.block_for("PL"):
                st.caption("No Raman or PL settings on this material.")
            if preset.description:
                st.caption(f"**Notes**: {preset.description}")

            if current_spectrum is None:
                st.caption("Load spectrum files below to enable Auto-Workflow")

            # Immediately above the Run button, which is the thing to press
            # about it.
            _warn_if_preset_moved(current_spectrum, preset)

            # No max-iterations control here. As of v5.2.0 the fit iteration
            # budget is a field of the technique block, edited on the Material
            # Presets page alongside the baseline and peak settings it belongs
            # with, so a preset states everything its fit does.

            # Run Auto-Workflow button (requires a loaded file)
            run_clicked = st.button(
                "🚀 Run Auto-Workflow", type="primary", width="stretch",
                disabled=current_spectrum is None
            )
            if run_clicked and current_spectrum is not None:
                # Execute auto-workflow immediately (single-click behavior)
                from ..processing.auto_workflow import execute_auto_workflow, format_workflow_summary, get_workflow_suggestions

                with st.spinner("🚀 Executing automated workflow..."):
                    result = execute_auto_workflow(current_spectrum, preset)

                if result["success"]:
                    # Show success message with summary
                    summary = format_workflow_summary(result, preset, current_spectrum.mode)
                    st.success(summary)

                    _remember_fit_preset(current_spectrum, preset)

                    # Update view options to show fit results, unless the
                    # user pinned these checkboxes by hand.
                    set_view_options({'show_fit': True, 'show_components': True,
                                      'show_on_background': True})

                    # Auto-expand export section
                    st.session_state['expanded_section'] = 'export'

                else:
                    # Show error message with suggestions
                    st.error(format_workflow_summary(result, preset, current_spectrum.mode))

                    # Show contextual suggestions
                    if result['stage_completed']:
                        suggestions = get_workflow_suggestions(
                            result['stage_completed'],
                            result['error_message']
                        )
                        st.info(suggestions)

                    # Expand section where failure occurred
                    stage_to_section = {
                        'x_range': 'processing_range',
                        'despike': 'despiking',
                        'baseline': 'baseline',
                        'fitting': 'peak_fitting'
                    }
                    failed_section = stage_to_section.get(result['stage_completed'], 'processing_range')
                    st.session_state['expanded_section'] = failed_section

                # Trigger rerun to update UI
                st.rerun()

            # Batch auto-workflow button (only if multiple files loaded)
            if len(files) > 1:
                if st.button("🚀 Run All Files", type="secondary", width="stretch",
                             help="Run auto-workflow on all loaded files"):
                    from ..processing.auto_workflow import execute_auto_workflow

                    # Skip files whose technique was guessed rather than read.
                    # Fitting a PL spectrum against Raman settings produces a
                    # confident-looking wrong answer, and in a batch of 25 no
                    # one would notice which one it was.
                    file_items = [
                        item for item in files.items()
                        if getattr(item[1], "auto_detected", False)
                    ]
                    skipped = [
                        name for name, spec in files.items()
                        if not getattr(spec, "auto_detected", False)
                    ]
                    if skipped:
                        st.warning(
                            f"Skipping {len(skipped)} file(s) whose technique the "
                            f"filename doesn't state: {', '.join(skipped[:5])}"
                            + (" ..." if len(skipped) > 5 else "")
                            + ". Run them individually, or rename them RM_*/PL_*."
                        )
                    total = len(file_items)
                    success_count = 0
                    failed_files = []


                    if total == 0:
                        st.error(
                            "No loaded file states its technique in its "
                            "filename, so there is nothing safe to batch-fit."
                        )
                    # One widget carrying one number: the old pair reported the
                    # same index as "now starting" (label) and "this many
                    # finished" (bar), so they could never be reconciled.
                    with st.status(f"Fitting {total} files...", expanded=True) as status:
                        progress_bar = st.progress(0.0, text="Starting...")
                        for idx, (filename, spectrum) in enumerate(file_items):
                            # idx, not idx + 1: the fraction is work *finished*,
                            # while the label names the file now running. The
                            # old code advanced the bar before doing the work,
                            # so it read 100% throughout the final — usually
                            # slowest — fit and was then torn down.
                            progress_bar.progress(
                                idx / total, text=f"Fitting {idx + 1}/{total}: {filename}"
                            )
                            result = execute_auto_workflow(spectrum, preset)
                            if result["success"]:
                                success_count += 1
                                _remember_fit_preset(spectrum, preset)
                            else:
                                failed_files.append(
                                    (filename, result.get("error_message") or "Unknown error")
                                )

                        progress_bar.progress(1.0, text=f"{success_count}/{total} fitted")
                        status.update(
                            label=f"{success_count}/{total} files fitted",
                            state="complete",
                            expanded=False,
                        )

                    if total == 0:
                        pass  # already reported above
                    elif success_count == total:
                        st.success(f"All {total} files processed successfully!")
                    elif success_count > 0:
                        st.warning(f"{success_count}/{total} files succeeded. {len(failed_files)} failed.")
                    else:
                        st.error("All files failed processing.")

                    # Outside the status — st.status is expander-like, and
                    # Streamlit forbids nesting expanders. Expanded, because a
                    # collapsed one could never be opened: clicking it reruns
                    # the script with this button False, so the whole block
                    # (and the expander) ceases to exist.
                    if failed_files:
                        with st.expander(f"{len(failed_files)} file(s) failed", expanded=True):
                            for fname, err in failed_files:
                                st.error(f"**{fname}**: {err}")

                    set_view_options({'show_fit': True, 'show_components': True,
                                      'show_on_background': True})
                    st.session_state['expanded_section'] = 'export'
                    st.session_state['despike_preview'] = None
                    st.session_state['baseline_preview'] = None
                    st.session_state['despike_preview_toggle'] = False
                    st.session_state['baseline_preview_toggle'] = False
                    # No st.rerun() here. It used to discard everything above —
                    # the success/warning summary and the whole list of files
                    # that failed — one frame after drawing it, so a 12-file
                    # batch ended with no record of what happened. Writing
                    # show_fit/show_components is safe without it because their
                    # checkboxes are instantiated further down this same
                    # function, so the values land before the widgets exist
                    # (the same ordering sync_pending_file_switch relies on).

        else:
            st.session_state['selected_preset'] = None

    else:
        st.caption("No materials configured yet.")
        st.caption("Add one on the **Material Presets** page (top of sidebar).")

    st.markdown("---")

    # ------------------------------------------------------------------
    # Export, View Options, and Reset (moved from the former right-hand
    # control panel in v2.10.0 so the whole workflow lives in the sidebar)
    # ------------------------------------------------------------------
    export_spectrum = get_current_spectrum()

    if export_spectrum is None:
        st.caption("Load a file below to get started.")
    elif not export_spectrum.fit_done:
        st.caption("Run Auto-Workflow to enable export.")
    else:
        st.subheader("Quick Export")

        try:
            export_fig = plot_composite(
                x=export_spectrum.processed_data.X,
                y_data=export_spectrum.processed_data.Y,
                fit_result=export_spectrum.fit_result,
                mode=export_spectrum.mode,
                title=f"{export_spectrum.filename} - Fit Results",
                # Follow View Options rather than hardcoding both on: the
                # exported figure is the same picture the user just tuned
                # on screen, and shipping a PNG that disagrees with the
                # plot above it is the bug this fixes.
                show_components=st.session_state.get("show_components", True),
                show_residuals=st.session_state.get("show_residuals", True),
                background=(export_spectrum.background
                            if st.session_state.get("show_on_background", True) else None),
            )
        except Exception as e:
            st.error(f"Failed to prepare export: {e}")
            export_fig = None

        if export_fig is not None:
            exp_col1, exp_col2, exp_col3 = st.columns(3)

            with exp_col1:
                st.caption("📷 Static Image")
                try:
                    png_bytes = _export_png_cached(export_fig, width=1200, height=600, scale=2.0)
                    filename_png = create_filename(export_spectrum.filename, "fit", "png")
                    st.download_button(
                        label="Download PNG",
                        data=png_bytes,
                        file_name=filename_png,
                        mime="image/png",
                        width="stretch"
                    )
                except RuntimeError:
                    st.error("PNG export requires kaleido")
                    st.caption("Install: `pip install kaleido`")

            with exp_col2:
                st.caption("🌐 Interactive Plot")
                try:
                    html_string = export_figure_html(export_fig)
                    filename_html = create_filename(export_spectrum.filename, "fit", "html")
                    st.download_button(
                        label="Download HTML",
                        data=html_string,
                        file_name=filename_html,
                        mime="text/html",
                        width="stretch"
                    )
                except Exception as e:
                    st.error(f"HTML export failed: {e}")

            with exp_col3:
                st.caption("📊 Fit Parameters")
                csv_quick = export_fit_params_csv(export_spectrum)
                filename_csv_quick = create_filename(export_spectrum.filename, "fit_params", "csv")

                st.download_button(
                    label="Download CSV",
                    data=csv_quick,
                    file_name=filename_csv_quick,
                    mime="text/csv",
                    width="stretch"
                )

        st.markdown("---")

        export_files = st.session_state.get('files', {})
        fitted_files = [f for f in export_files.values() if f.fit_done and f.fit_result and f.fit_result.success]

        if len(fitted_files) > 1:
            st.subheader("Batch Export")
            st.caption(f"Export data from all {len(fitted_files)} fitted files")

            try:
                master_csv = export_master_csv(export_files)

                batch_col1, batch_col2 = st.columns([2, 1])

                with batch_col1:
                    # Default the Save-As dialog to the current file's source
                    # folder, falling back to the last-browsed folder, then the OS default.
                    default_dir = getattr(export_spectrum, 'source_dir', None) \
                        or st.session_state.get('last_picked_dir', '')
                    default_name = create_filename("nexanalyzer", "master_results", "csv")

                    save_clicked = st.button(
                        "💾 Save Master CSV to folder",
                        help="Open a Save-As dialog (pre-pointed at your raw-data folder) "
                             "and type a filename",
                        width="stretch"
                    )

                with batch_col2:
                    st.metric("Files", len(fitted_files))

                if save_clicked:
                    try:
                        save_path = prompt_save_path(
                            default_dir=default_dir,
                            default_filename=default_name,
                            title="Save Master CSV"
                        )
                        if save_path:
                            with open(save_path, "w", newline="", encoding="utf-8") as f:
                                f.write(master_csv)
                            st.success(f"✅ Saved to {save_path}")
                        # save_path is None => user cancelled => no-op
                    except Exception as e:
                        st.error(f"❌ Failed to save master CSV: {e}")

                st.caption("**Master CSV includes:** Filename, Mode, Peak_Label, Center, Intensity, FWHM, Shape, R², χ², and standard errors")

            except Exception as e:
                st.error(f"Failed to generate master CSV: {e}")

            # Own try/except, independent of the master-CSV one above: a
            # broken kaleido install must not also take down the CSV export,
            # and vice versa. Also computes its own default_dir rather than
            # reusing the master-CSV block's -- that block may have raised
            # before ever assigning it.
            try:
                import os  # local, like this function's other os.path uses

                img_default_dir = getattr(export_spectrum, 'source_dir', None) \
                    or st.session_state.get('last_picked_dir', '')
                img_col1, img_col2 = st.columns([2, 1])

                with img_col1:
                    save_images_clicked = st.button(
                        "📷 Save PNGs to folder",
                        help="Open a folder dialog (pre-pointed at your raw-data "
                             "folder) and save one fit-results PNG per fitted file",
                        width="stretch"
                    )

                with img_col2:
                    st.metric("Files", len(fitted_files))

                if save_images_clicked:
                    image_dir = prompt_save_folder(
                        default_dir=img_default_dir,
                        title="Select Folder for Batch PNG Export",
                    )
                    if image_dir:
                        progress = st.progress(0.0, text="Exporting images…")
                        saved = 0
                        failed = []
                        for i, spectrum in enumerate(fitted_files, start=1):
                            try:
                                fig = plot_composite(
                                    x=spectrum.processed_data.X,
                                    y_data=spectrum.processed_data.Y,
                                    fit_result=spectrum.fit_result,
                                    mode=spectrum.mode,
                                    title=f"{spectrum.filename} - Fit Results",
                                    # Same View Options as Quick Export's single
                                    # PNG, so a batch export matches what the
                                    # operator is currently looking at.
                                    show_components=st.session_state.get(
                                        "show_components", True
                                    ),
                                    show_residuals=st.session_state.get(
                                        "show_residuals", True
                                    ),
                                )
                                png_bytes = export_figure_png(
                                    fig, width=1200, height=600, scale=2.0
                                )
                                out_path = os.path.join(
                                    image_dir,
                                    create_filename(spectrum.filename, "fit", "png"),
                                )
                                with open(out_path, "wb") as out_f:
                                    out_f.write(png_bytes)
                                saved += 1
                            except RuntimeError:
                                # kaleido missing -- every remaining export
                                # would fail identically, so stop rather than
                                # repeat the same error N times.
                                st.error("❌ PNG export requires kaleido")
                                st.caption("Install: `pip install kaleido`")
                                failed = None
                                break
                            except Exception as e:
                                failed.append((spectrum.filename, str(e)))
                            progress.progress(
                                i / len(fitted_files),
                                text=f"Exporting images… ({i}/{len(fitted_files)})",
                            )
                        progress.empty()
                        if saved:
                            st.success(f"✅ Saved {saved} PNG(s) to {image_dir}")
                        if failed:
                            with st.expander(f"⚠️ {len(failed)} file(s) failed"):
                                for name, err in failed:
                                    st.caption(f"{name}: {err}")
                    # image_dir is None => user cancelled => no-op

            except Exception as e:
                st.error(f"Failed to prepare batch image export: {e}")

        elif len(fitted_files) == 1:
            st.caption("Run Auto-Workflow on multiple files to enable batch export")

    st.markdown("---")

    # Reset to Raw
    if export_spectrum is not None:
        if st.button("🔄 Reset to Raw", help="Clear all processing and start over"):
            export_spectrum.reset_to_raw()
            st.success("✅ Reset to raw data")
            st.rerun()

    st.markdown("---")

    # Load spectra
    st.subheader("Load Spectra")

    # Persisted and shared with the QC Report, so both pages put one file on
    # one axis. Applied at load: files already in the list keep the axis they
    # were loaded with.
    correct_raman_axis = st.checkbox(
        "Correct Raman axis to laser line",
        value=load_correct_raman_axis(),
        help="Shift each Raman file so its laser line sits at 0 cm⁻¹. The line is "
             "measured either way; offsets under "
             f"{AXIS_DEADBAND:g} cm⁻¹ are not applied. Takes effect for files "
             "loaded after the change.",
    )
    if correct_raman_axis != load_correct_raman_axis():
        save_correct_raman_axis(correct_raman_axis)

    # Remember the directory of the last picked file for the next dialog's initialdir
    if 'last_picked_dir' not in st.session_state:
        st.session_state['last_picked_dir'] = ""

    # Browse spectrum files button (multi-select)
    if st.button("Browse Spectrum Files", width="stretch"):
        try:
            import subprocess
            import sys
            import os
            import tempfile
            import json

            # NOTE: askopenfilenames returns a Tcl tuple of paths. Bare print()
            # would mangle paths containing spaces/commas/unicode, so we serialize
            # via json.dumps and parse with json.loads on this side.
            dialog_script = """
import tkinter as tk
from tkinter import filedialog
import sys
import json

root = tk.Tk()
root.withdraw()
root.wm_attributes('-topmost', 1)

initial_dir = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
selected_files = filedialog.askopenfilenames(
    title="Select Spectrum Files",
    initialdir=initial_dir,
    filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
)

root.destroy()
print(json.dumps(list(selected_files)))
"""

            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as f:
                f.write(dialog_script)
                script_path = f.name

            initial_dir = st.session_state.get('last_picked_dir', '')
            result = subprocess.run(
                [sys.executable, script_path, initial_dir],
                capture_output=True,
                text=True,
                timeout=120
            )

            os.unlink(script_path)

            raw = result.stdout.strip()
            picked = json.loads(raw) if raw else []

            if picked:
                st.session_state['pending_files_to_load'] = picked
                st.session_state['last_picked_dir'] = os.path.dirname(picked[0])
                st.rerun()
            # cancel / empty selection -> no-op, no rerun

        except Exception as e:
            st.error(f"❌ Failed to open file browser: {e}")

    # Process the pending-files queue populated by the Browse button on the previous rerun
    files_to_load = st.session_state.pop('pending_files_to_load', None)
    if files_to_load:
        import os

        loaded_count = 0
        skipped_count = 0

        # Parse everything first, so the Raman files picked together can be
        # calibrated as one session: they share an offset, and a file whose
        # own laser line is missing or off takes the others'.
        loaded = []  # (filename, source_dir, spectra, mode, auto_detected)
        for file_path in files_to_load:
            filename = os.path.basename(file_path)
            source_dir = os.path.dirname(file_path)  # v2.7: remember source folder for exports
            try:
                # Parse spectrum (returns list; len > 1 for multi-Y files)
                spectra = parse_spectrum_multi(file_path)
            except Exception as e:
                st.error(f"❌ Failed to load {filename}: {e}")
                continue

            # v2.1+ (FR-12): Auto-detect mode from filename
            detected_mode = detect_mode_from_filename(filename)
            if detected_mode is not None:
                set_mode(detected_mode)
            loaded.append((filename, source_dir, spectra, detected_mode or "Raman",
                           detected_mode is not None))

        calibrations = [None] * len(loaded)
        raman = [i for i, item in enumerate(loaded) if item[3] == "Raman"]
        if raman:
            results = calibrate_raman_session([loaded[i][2] for i in raman], correct_raman_axis)
            for i, (spectra, calibration) in zip(raman, results):
                loaded[i] = loaded[i][:2] + (spectra,) + loaded[i][3:]
                calibrations[i] = calibration

        for (filename, source_dir, spectra, file_mode, auto_detected), calibration in zip(
            loaded, calibrations
        ):
            try:
                # Build per-spectrum entries. For multi-Y files, suffix the
                # filename with __1, __2, … so each Y column appears as a
                # separate file entry in the sidebar list.
                base, ext = os.path.splitext(filename)
                n = len(spectra)
                for i, spectrum_data in enumerate(spectra, start=1):
                    entry_name = filename if n == 1 else f"{base}__{i}{ext}"

                    if entry_name in st.session_state.get("files", {}):
                        skipped_count += 1
                        continue

                    spectrum_file = SpectrumFile(
                        filename=entry_name,
                        mode=file_mode,
                        original_data=spectrum_data,
                        raw_data=spectrum_data,
                        processed_data=spectrum_data,
                        source_dir=source_dir,  # v2.7: folder the .txt came from
                        processing_settings=ProcessingSettings(),
                        auto_detected=auto_detected,
                        laser_calibration=calibration,
                    )
                    add_spectrum_file(spectrum_file)
                    loaded_count += 1

            except Exception as e:
                st.error(f"❌ Failed to load {filename}: {e}")

        # Show summary message
        if loaded_count > 0:
            st.success(f"✅ Loaded {loaded_count} file(s)")
            if skipped_count > 0:
                st.info(f"ℹ️ Skipped {skipped_count} already-loaded file(s)")
            st.rerun()
        elif skipped_count > 0:
            st.info(f"ℹ️ All {skipped_count} file(s) already loaded")

    st.markdown("---")

    # File selector
    files = st.session_state.get("files", {})
    if files:
        st.subheader("Loaded Files")

        filenames = list(files.keys())
        current_file = st.session_state.get("current_file")

        # Default to first file if none selected
        if current_file is None or current_file not in filenames:
            st.session_state["current_file"] = filenames[0]
            current_file = filenames[0]

        # File info (selection handled by navigation bar in plot area)
        spectrum = files[current_file]
        st.caption(f"**Mode**: {spectrum.mode}")
        st.caption(f"**Points**: {len(spectrum.raw_data.X)}")
        st.caption(
            f"**Range**: {spectrum.raw_data.X.min():.2f} - {spectrum.raw_data.X.max():.2f}"
        )
        if spectrum.mode == "Raman":
            st.caption(f"**Axis**: {axis_note([spectrum.laser_calibration]) or 'not checked'}")

        # Remove-file / delete-all buttons (side by side)
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Remove File", key=f"remove_{current_file}"):
                remove_spectrum_file(current_file)
                st.rerun()
        with col_b:
            if st.button("🗑️ Delete All", key="delete_all_files",
                         help="Remove all loaded files"):
                clear_all_files()
                st.rerun()

    else:
        st.info("Upload .txt files to begin")

    st.markdown("---")

    # View Options (last item in the sidebar)
    with st.expander("🔍 View Options", expanded=False):
        st.markdown("**Plot Layer Visibility**")

        # No value= here: defaults come from initialize_session_state(), and
        # sync_pending_file_switch() (run before the sidebar) may also have
        # just written these — passing value= as well would make Streamlit
        # warn that the widget's state was set through both paths.
        #
        # on_change pins the key: from then on the checkbox is the user's
        # answer, and the stage-derived defaults (file switch, auto-workflow,
        # batch) stop overwriting it. Before v5.7.1 every one of those paths
        # reset all six, so a toggle survived only until the next file.
        st.checkbox("Show Raw", key="show_raw",
                   on_change=pin_view_option, args=("show_raw",),
                   help="Show raw data (before any processing)")

        st.checkbox("Show De-spiked", key="show_despiked",
                   on_change=pin_view_option, args=("show_despiked",),
                   help="Show data after spike removal")

        st.checkbox("Show Baseline-corrected", key="show_corrected",
                   on_change=pin_view_option, args=("show_corrected",),
                   help="Show data after baseline correction")

        st.markdown("---")
        st.markdown("**Peak Fitting Display**")

        st.checkbox("Show Fit", key="show_fit",
                   on_change=pin_view_option, args=("show_fit",),
                   help="Show total fitted curve")

        st.checkbox("Show Components", key="show_components",
                   on_change=pin_view_option, args=("show_components",),
                   help="Show individual peak components")

        st.checkbox("Show Residuals", key="show_residuals",
                   on_change=pin_view_option, args=("show_residuals",),
                   help="Show fit residuals (Baseline-corrected minus Fit Total)")

        st.checkbox("Peaks on Background", key="show_on_background",
                   on_change=pin_view_option, args=("show_on_background",),
                   help="Draw the data, fit and components on top of the "
                        "subtracted baseline (gray), instead of on zero. "
                        "Residuals stay about zero. Quick Export follows.")

        st.caption(
            "Toggle plot layers on/off without reprocessing. A layer you set "
            "here stays set when you switch files or re-run the workflow."
        )

        if pinned_view_options():
            if st.button("Follow the data again", width="stretch",
                         help="Drop your manual choices and let each file's "
                              "processing stage pick the layers again"):
                # Un-pin here, but re-derive on the NEXT run: this button sits
                # below the checkboxes, and Streamlit raises
                # StreamlitAPIException if show_* is written after its widget
                # was instantiated in the same run. The flag is consumed by
                # sync_pending_view_options(), which the page calls before the
                # sidebar — the same ordering sync_pending_file_switch() uses.
                clear_pinned_view_options()
                st.session_state['_pending_view_options_reset'] = True
                st.rerun()
