"""
QC Report: one sample folder in, eight figures and a workbook out.

Pick a folder holding a 9-point Raman/PL/OM measurement grid, fit every
spectrum against one material's presets, segment every frame, and produce:

  1 Summary            overview page — OM grid, its class table, Raman and PL
                       fit-summary tables, and the pooled domain-size table
  2 OM                 layer segmentation across the grid positions
  3 OM diagnostic      the same, plus the green-channel histograms showing
                       where each threshold landed
  4 Domain size        flake domains split out of one OM class -- below,
                       reference, or above the reference layer, operator's
                       choice -- and measured, in microns where the
                       magnification has a calibration set (Material Presets
                       page), in pixels otherwise. Opt-in — off by default,
                       roughly doubling optical analysis time when on
  5 Raman              the nine fitted Raman spectra
  6 Raman stats        fitted FWHM, peak centres and diagnostic ratios across
                       the positions, with the inherited spec lines drawn
  7 PL                 the nine fitted PL spectra
  8 PL stats           the same panels for PL

plus one .xlsx carrying every number behind them.

This is the merge of the Sample Report and QC Panel pages (v5.0.0). They read
the same folder, ran the same scan and fit, and each produced half of what an
operator wanted — so running both meant picking the same folder twice and
fitting the same spectra twice. The .pptx the Sample Report used to build is
gone with them, along with the PowerPoint COM rendering that rasterized it:
the eight PNGs and the workbook are the deliverable now, and nothing here
needs PowerPoint installed.

**One run.** One folder pick, one `run_sample_batch`, and every artifact
derived from that single result — so no two figures can describe different
fits of the same sample.

**Auto-detect.** A technique with no data is skipped rather than drawn empty,
and the inventory above the Run button says what was found before anything
runs. Two different absences get two different messages: a folder with no PL
files is a naming problem, a material with no PL block is a preset problem,
and they are fixed in different places.

**The threshold pair comes from the preset, and only from the preset.** Every
wafer is segmented on the same `abs_threshold` pair, so two wafers' coverage
numbers are measured with the same ruler and can be compared. The pair is
printed on screen, written under the summary page's OM table, and carried on
every `OM_Stats` row — a page that does not name its pair cannot be reproduced
from itself.

The segmentation's other tunables — `nsigma`, `minpx`, the mask margin and the
flat-field divisor — come from the material preset's optical block for the
chosen layer, so a figure's numbers are reproducible from the sample folder
plus the committed preset. Tune them on the Material Presets page, not per
run; docs/OM_Contrast_Algo.md says when each is worth moving. A layer with no
block of its own runs the algorithm's own defaults, and the page says so.
"""

import io
import os
from datetime import date

import streamlit as st
from PIL import Image

from core.io.export import export_figure_png, prompt_save_path
from core.io.folder_picker import prompt_folder_path
from core.io.om_calibration import get_um_per_px, load_om_calibration
from core.io.report_settings import (
    load_default_material,
    load_measure_domain_size,
    load_split_size_populations,
    save_default_material,
    save_measure_domain_size,
    save_split_size_populations,
)
from core.report.progress import build as build_progress
from core.report.summary_figure import (
    FIT_COLUMN_ASPECT_RATIO,
    FIT_GRID_COLUMNS,
    build_fit_grid_figure,
    build_summary_figure,
)
from modules.optical.io.frame_tables import frame_class_stats
from modules.optical.processing.contrast import (
    REFERENCE_CHOICES,
    analyse_frame,
    class_labels,
    class_summary,
    layer_word,
)
from modules.optical.processing.domain_size import TARGETS, domain_size_stat, measure_domains
from modules.optical.ui.qc_report_state import (
    get_qc_report_state,
    reset_optical_results,
    reset_results,
    reset_spectra_results,
)
from modules.optical.viz.domain_grid import build_domain_grid_figure
from modules.optical.viz.om_grid import build_om_grid_figure
from modules.spectra.io.preset_store import load_presets
from modules.spectra.io.results_excel import TechniqueResults, build_sample_results_xlsx
from modules.spectra.processing.parser import count_spectra
from modules.spectra.processing.peak_metrics import (
    NEAR_ZERO_FRACTION,
    R_SQUARED_MIN,
    RAMAN_RATIO_PAIRS,
    aggregate_fit_results,
    aggregate_raw_peak_stats,
    compute_peak_intensity_ratio,
    filter_fits_by_quality,
)
from modules.spectra.processing.sample_batch import run_sample_batch
from modules.spectra.processing.sample_scanner import default_magnification, scan_sample_folder
from modules.spectra.utils.preset_staleness import optical_fingerprint, technique_fingerprint
from modules.spectra.viz.fit_plot import (
    axis_label,
    fit_legend_entries,
    peak_normalization_scale,
    plot_fit_column,
    shared_axis_ranges,
    y_axis_title,
)
from modules.spectra.viz.peak_quality import (
    PL_QUALITY,
    RAMAN_QUALITY,
    build_peak_quality_figure,
)

#: The saved file set, in the order the report is meant to be read. The numeric
#: prefix is load-bearing: alphabetical order in a file browser puts "Summary"
#: last and interleaves the two techniques, so a colleague handed the folder
#: would meet the figures in an order nobody chose.
_ARTIFACTS = (
    (1, "Summary", "summary_png"),
    (2, "OM", "om_png"),
    (3, "OM_diagnostic", "om_diagnostic_png"),
    (4, "Domain_size", "domain_grid_png"),
    (5, "Raman", "raman_grid_png"),
    (6, "Raman_stats", "raman_stats_png"),
    (7, "PL", "pl_grid_png"),
    (8, "PL_stats", "pl_stats_png"),
)

st.title("🔬 QC Report")
st.markdown(
    "Eight figures and one workbook from a sample folder's 9-point "
    "Raman + PL + OM measurement grid. Anything without data is skipped."
)

state = get_qc_report_state()

# ---------------------------------------------------------------- Folder pick
st.subheader("1. Sample Folder")
col_pick, col_path = st.columns([1, 3])
with col_pick:
    pick_clicked = st.button("Select Sample Folder", width="stretch")
with col_path:
    st.caption(state["folder"] or "No folder selected yet.")

if pick_clicked:
    try:
        picked = prompt_folder_path(default_dir=state["folder"] or "")
    except Exception as e:
        st.error(f"Failed to open folder browser: {e}")
        picked = None

    if picked:
        state["folder"] = picked
        state["scan"] = scan_sample_folder(picked)
        state["magnification"] = default_magnification(state["scan"])
        reset_results(state)
        st.rerun()

scan = state["scan"]

if scan is not None:
    n_images = sum(len(v) for v in scan.image_files.values())
    st.success(
        f"**{scan.sample_name}** — {len(scan.raman_files)} Raman, "
        f"{len(scan.pl_files)} PL, {n_images} image file(s)"
    )

    if scan.ignored_files:
        with st.expander(f"{len(scan.ignored_files)} file(s) ignored (didn't match naming pattern)"):
            st.write(", ".join(scan.ignored_files))

    # -------------------------------------------------------- Magnification
    st.subheader("2. OM Magnification")
    magnifications = scan.magnifications()
    if magnifications:
        current_mag = state["magnification"] if state["magnification"] in magnifications else magnifications[0]
        chosen_mag = st.selectbox(
            "Magnification group", options=magnifications, index=magnifications.index(current_mag)
        )
        if chosen_mag != state["magnification"]:
            # Every optical artifact — and the summary page, whose grid caption
            # names the magnification — was built from the other group's nine
            # frames. Without this the page keeps showing and saving 50x images
            # under a heading that says 100x.
            state["magnification"] = chosen_mag
            reset_optical_results(state)
    else:
        st.caption("No OM images found in this folder.")

    # ---------------------------------------------------------------- Layer
    st.subheader("3. Layer")
    reference = st.selectbox(
        "The film being measured",
        options=REFERENCE_CHOICES,
        index=REFERENCE_CHOICES.index(state.get("reference_layer", "2L")),
        # layer_word() is presentation only; "1L"/"2L" is what gets stored and
        # what keys the preset's optical block.
        format_func=lambda r: f"{r} — {layer_word(r).lower()} film",
    )
    if reference != state.get("reference_layer"):
        state["reference_layer"] = reference
        reset_optical_results(state)

    # classify() always treats the frame's most common pixel value as "the
    # reference" -- reference (above) never reaches the threshold math, it
    # only names the result. For a sparse-nucleation sample, bare substrate
    # is that majority, not the film, so the classes come out mislabelled
    # unless this says so.
    low_coverage = st.checkbox(
        f"Low coverage — most of the frame is substrate, not "
        f"{layer_word(reference).lower()}",
        value=state["low_coverage"],
        help="Relabels the reference class \"Substrate\" and the brighter "
             "class as the named film itself, instead of \"Above "
             f"{reference}\" over what would really be substrate. Still two "
             "populations underneath -- doesn't separate a thicker layer "
             "from the named one if both sit above substrate in the same "
             "frame.",
    )
    if low_coverage != state["low_coverage"]:
        state["low_coverage"] = low_coverage
        reset_optical_results(state)

    # Worth stating plainly: with identical tuning the segmentation is the same
    # either way, so a wrong choice here relabels every class by one layer with
    # no visible symptom in the figure.
    if low_coverage:
        st.caption(
            f"Classes will be labelled **Below Substrate**, **Substrate**, "
            f"**{layer_word(reference)}** — the algorithm's reference is "
            f"still the frame's majority population, now interpreted as "
            f"substrate rather than {layer_word(reference).lower()}."
        )
    else:
        st.caption(
            f"Classes will be labelled **Below {reference}**, "
            f"**{layer_word(reference)}**, **Above {reference}**. "
            "The algorithm cannot infer this — a wrong choice mislabels every class "
            "by one layer, and selects a different tuning block."
        )

    # ------------------------------------------------------------- Material
    st.subheader("4. Material")
    presets = load_presets()
    materials = sorted(name for name, p in presets.items() if p.enabled)

    if not materials:
        st.warning("No materials configured yet. Add one on the **Material Presets** page.")
    else:
        default_material = load_default_material()
        current_material = state["material"] if state["material"] in materials else (
            default_material if default_material in materials else materials[0]
        )
        material_selection = st.selectbox(
            "Material", options=materials, index=materials.index(current_material)
        )
        if material_selection != state["material"]:
            state["material"] = material_selection
            save_default_material(material_selection)
            reset_results(state)

        selected = presets.get(state["material"])
        raman_preset = selected if (selected and selected.block_for("Raman")) else None
        pl_preset = selected if (selected and selected.block_for("PL")) else None

        # Optical tuning for the chosen layer. Never None: an untuned layer
        # runs contrast.py's own defaults, which is a legitimate state and one
        # the operator should be told about rather than blocked by.
        optical = selected.optical_for(reference) if selected else None
        if selected is not None:
            settings = optical.as_kwargs()
            if reference in selected.optical and settings:
                st.caption(
                    f"Optical tuning from **{state['material']} / {reference}**: "
                    + ", ".join(f"`{k}`={v}" for k, v in sorted(settings.items()))
                )
            else:
                st.caption(
                    f"**{state['material']}** has no optical tuning for "
                    f"{layer_word(reference).lower()} films — running the "
                    f"algorithm's own defaults. Add a block on the "
                    f"**Material Presets** page to tune it."
                )

        # Domain-size measurement re-runs a segmentation-equivalent pass per
        # frame plus watershed splitting on top -- progress.py's own weighting
        # rates it as costing as much as segmentation itself, so it is opt-in
        # rather than always-on, and remembered like the material choice above.
        default_measure_domain_size = load_measure_domain_size()
        measure_domain_size = st.checkbox(
            "Measure domain size (adds a watershed segmentation pass, "
            "roughly doubling optical analysis time)",
            value=(
                state["measure_domain_size"]
                if state["measure_domain_size"] is not None
                else default_measure_domain_size
            ),
        )
        if measure_domain_size != state["measure_domain_size"]:
            state["measure_domain_size"] = measure_domain_size
            save_measure_domain_size(measure_domain_size)
            reset_optical_results(state)

        # Which of classify()'s three classes counts as "a domain" -- the
        # same three the OM class table already names, so the wording here
        # always matches it and updates automatically if the reference layer
        # changes. Shares that reference layer rather than an independent
        # one: a second choice of layer would mean a second, full
        # segmentation-equivalent pass just for domain size.
        domain_target_label = dict(
            zip(TARGETS, class_labels(reference, state["low_coverage"]))
        )
        if measure_domain_size:
            domain_target = st.radio(
                "Which class is the domain?",
                options=TARGETS,
                index=TARGETS.index(state["domain_target"]),
                format_func=lambda t: domain_target_label[t],
                horizontal=True,
            )
            if domain_target != state["domain_target"]:
                state["domain_target"] = domain_target
                reset_optical_results(state)

            # Off by default and remembered per installation, like domain-size
            # measurement itself -- the split has fired on frames with no real
            # second population at all (pure background texture), so an
            # operator who hasn't asked for it should see the plain pooled
            # measurement, not a split that may not be real.
            default_split_populations = load_split_size_populations()
            split_size_populations = st.checkbox(
                "Split into small/large size populations",
                value=(
                    state["split_size_populations"]
                    if state["split_size_populations"] is not None
                    else default_split_populations
                ),
                help="Tests whether the domains split into a small and a "
                     "large size population (at least a 2x gap in a KDE of "
                     "the diameters), rather than reporting one pooled mean.",
            )
            if split_size_populations != state["split_size_populations"]:
                state["split_size_populations"] = split_size_populations
                save_split_size_populations(split_size_populations)
                reset_optical_results(state)

        # Domain-size calibration is keyed by magnification alone, not by
        # material/layer like the optical tuning above — a 50x objective has
        # the same field of view regardless of what film is under it.
        um_per_px = get_um_per_px(load_om_calibration(), state["magnification"])
        if measure_domain_size and state["magnification"]:
            if um_per_px is not None:
                st.caption(
                    f"Domain size calibration for **{state['magnification']}**: "
                    f"`{um_per_px:g}` µm/px."
                )
            else:
                st.caption(
                    f"No µm/px calibration set for **{state['magnification']}** — "
                    f"domain sizes will be reported in pixels. Set one on the "
                    f"**Material Presets** page."
                )

        # Drop only what a preset edit invalidates. Editing a Raman peak must
        # not discard a 30-second OM segmentation, and vice versa.
        _optical_now = optical_fingerprint(selected, reference)
        _raman_now = technique_fingerprint(selected, "Raman")
        _pl_now = technique_fingerprint(selected, "PL")
        if state.get("om_png") and state.get("optical_fingerprint") != _optical_now:
            reset_optical_results(state)
            st.info("The optical settings changed — re-run to rebuild the OM figures.")
        # Named separately so the message points at the block that moved, even
        # though both techniques re-fit together: one batch call produces them.
        #
        # Gated on the fingerprint, not on a figure. A run against the wrong
        # material fits everything and loses it all at the quality gate, so it
        # has no stats figure — but it does have a fitted-spectra grid, a
        # summary page and a workbook. Keying the check on the figure meant
        # exactly that run, the one whose whole point is that the operator goes
        # and fixes the preset, never noticed the fix.
        _stale = []
        if state.get("raman_fingerprint") is not None and state["raman_fingerprint"] != _raman_now:
            _stale.append("Raman")
        if state.get("pl_fingerprint") is not None and state["pl_fingerprint"] != _pl_now:
            _stale.append("PL")
        if _stale:
            reset_spectra_results(state)
            st.info(
                f"The {' and '.join(_stale)} settings changed — re-run to rebuild "
                "the fitted figures. Both techniques re-fit together: one batch "
                "pass produces them."
            )

        # --------------------------------------------------------- Inventory
        st.subheader("5. Run")
        om_paths = {
            point: path
            for point, path in scan.image_files.get(state["magnification"] or "", {}).items()
            if 1 <= point <= 9
        }

        # Said before the run, not after: a folder whose PL files are named
        # `PL1.txt` instead of `PL_1.txt` scans as a sample with no PL, and is
        # indistinguishable from one that genuinely has none once the report is
        # built. The two causes below get two different sentences because they
        # are fixed in two different places.
        st.markdown(
            f"**Found:** {len(om_paths)} OM "
            f"({state['magnification'] or 'no magnification'}) · "
            f"{len(scan.raman_files)} Raman · {len(scan.pl_files)} PL"
        )
        for label, files, preset in (
            ("Raman", scan.raman_files, raman_preset),
            ("PL", scan.pl_files, pl_preset),
        ):
            if not files:
                st.caption(
                    f"↳ No {label} files in this folder — the {label} figures and "
                    f"sheet will be skipped. Check the file naming if you expected some."
                )
            elif preset is None:
                st.caption(
                    f"↳ **{state['material']}** defines no {label} peaks, so its "
                    f"{len(files)} {label} file(s) will not be fitted. Add a "
                    f"{label} block on the **Material Presets** page."
                )
        if not om_paths:
            st.caption(
                "↳ No OM images for this magnification — the OM figures and sheets "
                "will be skipped, and the summary page's image grid will be empty."
            )

        show_fwhm_v1 = st.checkbox(
            "Include legacy FWHM (v1) column",
            value=False,
            help="Adds the pre-v3.4.0 Gaussian-only FWHM (2.355 x sigma) next to the "
                 "correct Voigt FWHM, in both the summary tables and the .xlsx sheets. "
                 "The v1 value understated real peak widths and is included here only "
                 "for comparison against older reports.",
        )

        will_fit_raman = bool(scan.raman_files and raman_preset)
        will_fit_pl = bool(scan.pl_files and pl_preset)
        has_segmentation = bool(om_paths) and selected is not None
        has_content = bool(om_paths) or will_fit_raman or will_fit_pl

        if not has_content:
            st.warning(
                "Nothing to analyse: this folder yielded no OM images for the "
                "selected magnification, and no spectra this material can fit."
            )

        if st.button("🚀 Generate QC Report", type="primary", width="stretch",
                     disabled=not has_content):
            reset_results(state)
            try:
                # One status for the whole build. The `with` form is
                # load-bearing: on the way out it resolves the status to
                # complete, or to error if the body raised. A bare handle would
                # leave it spinning forever on a failure, which is exactly the
                # "did it hang?" the progress is here to answer.
                with st.status("Generating QC report...", expanded=True) as status:
                    progress_bar = st.progress(0.0, text="Starting...")

                    def _report(fraction, message):
                        progress_bar.progress(fraction, text=message)
                        status.update(label=message)

                    # Counting columns costs ~2 ms per file and is what keeps the
                    # bar proportional: a folder whose files hold 25 spectra each
                    # spends most of the run fitting, against the share the stage
                    # weights reserve for a one-spectrum-per-point sample.
                    fit_files = (
                        (list(scan.raman_files.values()) if will_fit_raman else [])
                        + (list(scan.pl_files.values()) if will_fit_pl else [])
                    )
                    progress = build_progress(
                        _report,
                        has_raman=will_fit_raman,
                        has_pl=will_fit_pl,
                        has_optical=bool(om_paths),
                        has_segmentation=has_segmentation,
                        has_domain_size=state["measure_domain_size"],
                        fit_spectra=sum(count_spectra(path) for path in fit_files),
                    )

                    report_date = date.today().isoformat()
                    state["report_date"] = report_date

                    # -------------------------------------------- OM frames
                    # Per image, not per batch: these are ~0.6 s each for a real
                    # 2240x1680 microscope frame, and if the folder lives on
                    # OneDrive a cloud-only file has to be downloaded first,
                    # which no estimate can predict. Ticking keeps the bar moving
                    # through it either way.
                    om_png_bytes = {}
                    if om_paths:
                        progress.start("optical_images", detail=f"0/{len(om_paths)}")
                        for done, (point, path) in enumerate(sorted(om_paths.items()), start=1):
                            try:
                                with Image.open(path) as im:
                                    buf = io.BytesIO()
                                    im.convert("RGB").save(buf, format="PNG")
                                om_png_bytes[point] = buf.getvalue()
                            except Exception as e:
                                st.warning(f"Could not load image for point {point}: {e}")
                            progress.tick("optical_images", done, len(om_paths))

                    # ---------------------------------------- Segmentation
                    if has_segmentation:
                        # Only the fields the preset actually sets; everything
                        # else keeps contrast.py's default.
                        om_kwargs = optical.as_kwargs()

                        # One ruler for every wafer. Recorded on the state so
                        # the summary page and the workbook can both name the
                        # cut their numbers came from.
                        state["optical_threshold_pair"] = om_kwargs.get(
                            "abs_threshold")

                        progress.start("optical_segmentation", detail=f"0/{len(om_paths)}")
                        frames = []
                        for done, point in enumerate(sorted(om_paths), start=1):
                            # Per frame, like the loader above. One corrupt,
                            # truncated, locked or cloud-only image used to
                            # raise through every later stage and discard the
                            # whole run — including the Raman and PL halves,
                            # which have nothing to do with it. Before the
                            # merge that only cost the QC Panel's two images;
                            # now it would cost all eight figures and the
                            # workbook, so a bad frame drops out and the rest
                            # of the sample still reports.
                            try:
                                frames.append(analyse_frame(
                                    om_paths[point], point=point,
                                    ref_label=state["reference_layer"],
                                    name=f"{state['magnification']}-{point}",
                                    low_coverage=state["low_coverage"],
                                    **om_kwargs,
                                ))
                            except Exception as e:
                                st.warning(f"Could not segment point {point}: {e}")
                            progress.tick("optical_segmentation", done, len(om_paths))
                        state["frames"] = frames

                        # `build_om_grid_figure` raises on an empty list, and a
                        # figure of nine blanks would claim a segmentation that
                        # never happened.
                        if frames:
                            # Two renders off one segmentation. The analysis is
                            # already spent; each render is about 5 s.
                            progress.start("om_figures", detail="clean copy")
                            state["om_png"] = build_om_grid_figure(
                                frames, sample_name=scan.sample_name,
                                magnification=state["magnification"],
                                show_histograms=False,
                            )
                            progress.tick("om_figures", 1, 2, detail="diagnostic copy")
                            state["om_diagnostic_png"] = build_om_grid_figure(
                                frames, sample_name=scan.sample_name,
                                magnification=state["magnification"],
                                show_histograms=True,
                            )
                            state["optical_fingerprint"] = _optical_now
                        else:
                            st.warning(
                                "No optical frame could be segmented — the OM "
                                "figures and sheets are omitted."
                            )
                        progress.complete("om_figures")

                        # --------------------------------------- Domain size
                        # Same `om_kwargs` the segmentation above used: the
                        # mask this re-derives is the same population the OM
                        # figure's overlay already shows, and measuring it
                        # with different tuning would make the two figures
                        # disagree about the same frames.
                        if frames and state["measure_domain_size"]:
                            progress.start("domain_size", detail=f"0/{len(frames)}")
                            domain_results = []
                            for done, frame in enumerate(frames, start=1):
                                try:
                                    domain_results.append(measure_domains(
                                        frame, um_per_px=um_per_px,
                                        target=state["domain_target"],
                                        marker_margin_sigma=(
                                            optical.marker_margin_sigma if optical else None
                                        ),
                                        marker_min_distance=(
                                            optical.marker_min_distance if optical else None
                                        ),
                                        mask_closing_radius=(
                                            optical.mask_closing_radius if optical else None
                                        ),
                                        detect_size_populations=state["split_size_populations"],
                                        **om_kwargs,
                                    ))
                                except Exception as e:
                                    st.warning(
                                        f"Could not measure domains for point "
                                        f"{frame.point}: {e}"
                                    )
                                progress.tick("domain_size", done, len(frames))
                            state["domain_results"] = domain_results
                            state["domain_stat"] = domain_size_stat(
                                domain_results,
                                detect_size_populations=state["split_size_populations"],
                            )
                            if domain_results:
                                state["domain_grid_png"] = build_domain_grid_figure(
                                    frames, domain_results,
                                    sample_name=scan.sample_name,
                                    magnification=state["magnification"],
                                    class_label=dict(zip(
                                        TARGETS, class_labels(reference, state["low_coverage"])
                                    ))[state["domain_target"]],
                                    detect_size_populations=bool(
                                        state["split_size_populations"]
                                    ),
                                )
                            progress.complete("domain_size")

                    # ---------------------------------------------- Fitting
                    has_spectra = will_fit_raman or will_fit_pl
                    if has_spectra:
                        progress.start("fit")
                    batch_result = run_sample_batch(
                        scan,
                        raman_preset if will_fit_raman else None,
                        pl_preset if will_fit_pl else None,
                        progress_callback=progress.sub_callback(
                            "fit",
                            # Both techniques share the one fitting stage, and
                            # each counts from 1 again, so the stage needs their
                            # combined total or the bar stalls through the second.
                            totals={
                                "Raman": len(scan.raman_files) if will_fit_raman else 0,
                                "PL": len(scan.pl_files) if will_fit_pl else 0,
                            },
                        ) if has_spectra else None,
                    )
                    if has_spectra:
                        progress.complete("fit")
                    state["batch_result"] = batch_result

                    # Stamped on *attempt*, not on success. A run that fitted
                    # everything and lost it all at the quality gate still
                    # produced artifacts from these preset blocks, and is the
                    # run most likely to be followed by a preset edit — so it
                    # is the one that most needs the staleness check to fire.
                    if will_fit_raman:
                        state["raman_fingerprint"] = _raman_now
                    if will_fit_pl:
                        state["pl_fingerprint"] = _pl_now

                    # ------------------------------------- Fitted-spectra grids
                    # Same traces as the Spectra page's fit results (data + total
                    # fit + peak components), minus the residuals strip, which is
                    # unreadable at this size and only steals height from the
                    # spectrum. Rendered at the report page's column aspect ratio
                    # so each image fills its column instead of being letterboxed.
                    column_width_px = 1000
                    column_height_px = round(column_width_px / FIT_COLUMN_ASPECT_RATIO)

                    def _build_fit_columns(point_spectra, mode, stage_key):
                        """One image per grid column, each holding that column's
                        three points on a single shared X-axis: column 0 is
                        points 1/4/7, column 1 is 2/5/8, column 2 is 3/6/9.

                        Reports per column, because each column is one kaleido
                        rasterization costing ~1.3 s and there is no progress to
                        be had inside one."""
                        # The grid shows one panel per grid point, but a
                        # multi-spectrum file contributes many fits at the same
                        # point. Show each point's best-fitting spectrum:
                        # dict(point_spectra) would keep whichever happened to be
                        # parsed last, which is arbitrary and silently so.
                        def _quality(spectrum):
                            return spectrum.fit_result.r_squared if spectrum.fit_result else -1.0

                        by_point = {}
                        for point, spectrum in point_spectra:
                            best = by_point.get(point)
                            if best is None or _quality(spectrum) > _quality(best):
                                by_point[point] = spectrum

                        # Ranges are computed from the normalized series, because
                        # that is what actually gets drawn — every point divided
                        # by its own tallest peak, so each panel's peak lands at 1.
                        normalized = []
                        for s in by_point.values():
                            scale = peak_normalization_scale(s.processed_data.Y, s.fit_result)
                            curve = s.fit_result.total_fit_curve if s.fit_result else None
                            normalized.append((
                                s.processed_data.X,
                                s.processed_data.Y / scale,
                                None if curve is None else curve / scale,
                            ))
                        x_range, y_range = shared_axis_ranges(normalized)

                        images = {}
                        progress.start(stage_key, detail=f"0/{FIT_GRID_COLUMNS}")
                        for col in range(FIT_GRID_COLUMNS):
                            column_points = [
                                (
                                    point,
                                    by_point[point].processed_data.X,
                                    by_point[point].processed_data.Y,
                                    by_point[point].fit_result,
                                )
                                for point in (col + 1, col + 4, col + 7)
                                if point in by_point
                            ]
                            if not column_points:
                                continue  # placeholder column; nothing to render

                            fig = plot_fit_column(
                                column_points, mode=mode, show_components=True,
                                x_range=x_range, y_range=y_range, normalize=True,
                            )
                            images[col] = export_figure_png(
                                fig, width=column_width_px, height=column_height_px, scale=2.0
                            )
                            progress.tick(stage_key, col + 1, FIT_GRID_COLUMNS)
                        return images

                    # One quality gate, applied once, feeding every summary
                    # below — the stats tables, the ratios, the quality figures
                    # and the .xlsx — so no two surfaces can be working from a
                    # different set of fits. Pairs, not bare fits: outlier
                    # removal is per grid point.
                    raman_pairs = filter_fits_by_quality(
                        [(point, s.fit_result) for point, s in batch_result.raman_spectra]
                    )
                    pl_pairs = filter_fits_by_quality(
                        [(point, s.fit_result) for point, s in batch_result.pl_spectra]
                    )

                    state["raman_errors"] = batch_result.raman_errors
                    state["pl_errors"] = batch_result.pl_errors
                    # Both counts, not just the survivors: what the gate removed
                    # is reported below and cannot be recovered from the stats.
                    state["raman_fitted"] = len(batch_result.raman_spectra)
                    state["raman_dropped"] = len(batch_result.raman_spectra) - len(raman_pairs)
                    state["pl_fitted"] = len(batch_result.pl_spectra)
                    state["pl_dropped"] = len(batch_result.pl_spectra) - len(pl_pairs)

                    if batch_result.raman_spectra:
                        raman_columns = _build_fit_columns(
                            batch_result.raman_spectra, "Raman", "raman_figures"
                        )
                        state["raman_grid_png"] = build_fit_grid_figure(
                            sample_name=scan.sample_name,
                            material_name=state["material"],
                            report_date=report_date,
                            technique="Raman",
                            column_images=raman_columns,
                            legend=fit_legend_entries(
                                [s.fit_result for _, s in batch_result.raman_spectra]
                            ),
                            x_label=axis_label("Raman"),
                            y_label=y_axis_title(normalized=True),
                        )
                    if raman_pairs:
                        state["raman_stats"] = aggregate_fit_results(raman_pairs)
                        state["raman_stats_png"] = build_peak_quality_figure(
                            raman_pairs, sample_name=scan.sample_name,
                            material_name=state["material"], spec=RAMAN_QUALITY,
                        )
                        raman_fits = [fit for _, fit in raman_pairs]
                        state["raman_ratios"] = [
                            (f"{numerator} / {denominator}", ratio)
                            for numerator, denominator in RAMAN_RATIO_PAIRS
                            for ratio in [compute_peak_intensity_ratio(
                                raman_fits, numerator, denominator
                            )]
                            if ratio is not None
                        ]

                    if batch_result.pl_spectra:
                        pl_columns = _build_fit_columns(
                            batch_result.pl_spectra, "PL", "pl_figures"
                        )
                        state["pl_grid_png"] = build_fit_grid_figure(
                            sample_name=scan.sample_name,
                            material_name=state["material"],
                            report_date=report_date,
                            technique="PL",
                            column_images=pl_columns,
                            legend=fit_legend_entries(
                                [s.fit_result for _, s in batch_result.pl_spectra]
                            ),
                            x_label=axis_label("PL"),
                            y_label=y_axis_title(normalized=True),
                        )
                        # PL leads with the empirical measurement — the tallest
                        # point of each processed spectrum, no fit involved —
                        # then the fitted peaks. The same "Raw" row the on-screen
                        # table and the master CSV already show.
                        # The PL quality figure's near-zero rule, so the table's
                        # n and the figure's points count the same peaks.
                        pl_stats = aggregate_fit_results(
                            pl_pairs, relative_intensity_floor=NEAR_ZERO_FRACTION
                        ) if pl_pairs else []
                        raw_stat = aggregate_raw_peak_stats(
                            [s for _, s in batch_result.pl_spectra]
                        )
                        state["pl_stats"] = ([raw_stat] + pl_stats) if raw_stat else pl_stats
                    if pl_pairs:
                        state["pl_stats_png"] = build_peak_quality_figure(
                            pl_pairs, sample_name=scan.sample_name,
                            material_name=state["material"], spec=PL_QUALITY,
                        )

                    # ------------------------------------- Summary + workbook
                    progress.start("compose", detail="summary page")
                    state["summary_png"] = build_summary_figure(
                        sample_name=scan.sample_name,
                        material_name=state["material"],
                        report_date=report_date,
                        magnification_label=state["magnification"],
                        om_image_bytes=om_png_bytes,
                        om_classes=frame_class_stats(state["frames"] or []),
                        om_threshold_note=(
                            "preset contrast pair -{:.2f} / +{:.2f} %".format(
                                *state["optical_threshold_pair"])
                            if state["optical_threshold_pair"]
                            else ("preset nsigma threshold"
                                  if state["frames"] else None)
                        ),
                        raman_stats=state["raman_stats"],
                        pl_stats=state["pl_stats"],
                        raman_ratios=state["raman_ratios"],
                        domain_stat=state["domain_stat"],
                        domain_class_label=(
                            dict(zip(TARGETS, class_labels(
                                state["reference_layer"], state["low_coverage"]
                            )))[state["domain_target"]]
                            if state["measure_domain_size"] else None
                        ),
                        show_fwhm_v1=show_fwhm_v1,
                    )

                    # Built from the very stats the summary page was built from,
                    # and the very frames the OM figures were drawn from, so the
                    # workbook cannot disagree with the images beside it.
                    progress.tick("compose", 1, 2, detail="workbook")
                    techniques = []
                    if batch_result.raman_spectra:
                        techniques.append(TechniqueResults(
                            label="Raman",
                            point_spectra=batch_result.raman_spectra,
                            stats=state["raman_stats"],
                            source_files=scan.raman_files,
                            errors=batch_result.raman_errors,
                            ratios=state["raman_ratios"] or (),
                        ))
                    if batch_result.pl_spectra:
                        techniques.append(TechniqueResults(
                            label="PL",
                            point_spectra=batch_result.pl_spectra,
                            stats=state["pl_stats"],
                            source_files=scan.pl_files,
                            errors=batch_result.pl_errors,
                            # The empirical row, per point, matching the one the
                            # summary page's PL table leads with.
                            include_raw_row=True,
                        ))
                    if techniques or state["frames"]:
                        state["xlsx_bytes"] = build_sample_results_xlsx(
                            sample_name=scan.sample_name,
                            material_name=state["material"],
                            report_date=report_date,
                            techniques=techniques,
                            optical_frames=state["frames"] or (),
                            optical_threshold_pair=state["optical_threshold_pair"],
                            domain_stat=state["domain_stat"],
                            domain_class_label=(
                                dict(zip(
                                    TARGETS, class_labels(reference, state["low_coverage"])
                                ))[state["domain_target"]]
                                if state["domain_stat"] else None
                            ),
                            show_fwhm_v1=show_fwhm_v1,
                        )
                    progress.complete("compose")
                    progress.finish(f"QC report generated for {scan.sample_name}.")

                    status.update(
                        label=f"QC report generated for {scan.sample_name}.",
                        state="complete",
                        expanded=False,
                    )
            except Exception as e:
                st.error(f"Report generation failed: {e}")


# ---------------------------------------------------------------- Results
def _render_gate_verdict(technique: str, dropped_key: str, fitted_key: str,
                         figure_key: str) -> None:
    """Say what the quality gate did to one technique, on every rerun.

    Spectra dropped here are not fit failures, so they never reach the "failed
    to fit" list; they just leave, and `n` comes out smaller. When *every*
    spectrum is dropped there is no figure either, and a count on its own reads
    as a malfunction rather than as the verdict it is. The usual cause of a
    wholesale drop is a preset aimed at another material — MoS2's 383/408 cm-1
    peaks on WSe2 data converge onto empty spectrum and score about -0.11
    apiece — so name the selected material and let the operator check it.

    One verdict per technique and no combined grade: PL carries a single spec
    line and no centre or ratio spec, so an overall pass/fail would assert a
    judgment the data cannot support.
    """
    dropped = state.get(dropped_key) or 0
    if not dropped:
        return
    fitted = state.get(fitted_key) or 0
    if state.get(figure_key):
        st.caption(
            f"**{technique}:** {dropped} of {fitted} spectra fitted but scored "
            f"R² ≤ {R_SQUARED_MIN} and were excluded from the {technique} "
            f"figure and statistics."
        )
    else:
        st.warning(
            f"**No {technique} stats figure:** all {fitted} spectra fitted, and "
            f"every one scored R² ≤ {R_SQUARED_MIN}, so none reached the figure. "
            f"The usual cause is the wrong material — check that "
            f"**{state.get('material')}** is what this sample is."
        )


_HAS_RESULTS = any(state.get(key) for _, _, key in _ARTIFACTS) or state.get("raman_dropped")

if _HAS_RESULTS:
    st.markdown("---")
    st.subheader("Results")

    # Inline: the overview and the two OM figures — the ones you look at every
    # run. The fitted spectra and the stats panels go behind expanders: they
    # are what you open when something looks wrong, and eight figures of this
    # size stacked would bury the summary under a page of scrolling. Tabs would
    # hide the OM grid well enough to miss a bad one by never clicking.
    if state.get("summary_png"):
        st.image(state["summary_png"], caption="1 — Summary", width="stretch")

    if state.get("om_png"):
        frames = state.get("frames") or []
        if frames:
            cols = st.columns(3)
            for col, label, (mean, std) in zip(cols, frames[0].labels, class_summary(frames)):
                col.metric(label, f"{mean:.1f} %", f"± {std:.1f} %", delta_color="off")
        st.image(state["om_png"], caption="2 — OM analysis", width="stretch")

    if state.get("om_diagnostic_png"):
        # Collapsed: this copy is for deciding whether to trust the one above,
        # which is a question you ask only when something looks off.
        with st.expander("3 — OM diagnostic (green-channel histograms)"):
            st.caption(
                "Each frame's histogram with its mode and both thresholds marked. "
                "Coverage percentages alone cannot show whether a frame segmented "
                "sensibly; a saturated frame reads as a clean wafer in the overlay "
                "and as a single spike here. Internal review only — the clean copy "
                "above is the one a report carries."
            )
            st.image(state["om_diagnostic_png"], width="stretch")

    if state.get("domain_grid_png"):
        with st.expander("4 — Domain size analysis"):
            stat = state.get("domain_stat")
            class_label = dict(zip(TARGETS, class_labels(
                state["reference_layer"], state["low_coverage"]
            )))[state["domain_target"]]
            if stat is not None:
                if stat.populations:
                    small, large = stat.populations
                    population_line = (
                        f"small: n={small.n}, {small.diameter_mean:.2f} ± "
                        f"{small.diameter_std:.2f} {stat.unit}  \n"
                        f"large: n={large.n}, {large.diameter_mean:.2f} ± "
                        f"{large.diameter_std:.2f} {stat.unit}"
                    )
                    if small.density_per_100um2 is not None:
                        population_line += (
                            f"  \nsmall: {small.density_per_100um2:.1f}/(100µm)² "
                            f"· large: {large.density_per_100um2:.1f}/(100µm)²"
                        )
                else:
                    population_line = (
                        f"median {stat.diameter_median:.2f} {stat.unit} "
                        f"· mean {stat.diameter_mean:.2f} ± {stat.diameter_std:.2f} {stat.unit}"
                    )
                    if stat.density_per_100um2 is not None:
                        population_line += f"  \n{stat.density_per_100um2:.1f}/(100µm)²"
                st.caption(
                    f"**{class_label}** — {stat.n_domains} domains"
                    + (f" (+ {stat.n_edge} at the frame edge, counted ½ in density)"
                       if stat.n_edge else "")
                    + f" across {stat.n_frames} frames "
                    f"· {population_line}"
                    + ("" if stat.calibrated else
                       " — no µm/px calibration set for this magnification; "
                       "sizes shown in pixels.")
                )
            else:
                st.caption(f"**{class_label}** — no domains found.")
            st.image(state["domain_grid_png"], width="stretch")

    for number, caption, key in (
        (5, "Raman — fitted spectra", "raman_grid_png"),
        (6, "Raman — quality panels", "raman_stats_png"),
        (7, "PL — fitted spectra", "pl_grid_png"),
        (8, "PL — quality panels", "pl_stats_png"),
    ):
        if state.get(key):
            with st.expander(f"{number} — {caption}"):
                st.image(state[key], width="stretch")

    _render_gate_verdict("Raman", "raman_dropped", "raman_fitted", "raman_stats_png")
    _render_gate_verdict("PL", "pl_dropped", "pl_fitted", "pl_stats_png")

    errors = (state.get("raman_errors") or []) + (state.get("pl_errors") or [])
    if errors:
        with st.expander(f"{len(errors)} point(s) failed to fit and were excluded"):
            for point, message in errors[:50]:
                st.error(f"Point {point}: {message}")

    # ------------------------------------------------------------------ Save
    st.subheader("Save Report")
    st.caption(
        "One dialog, one name, one file per artifact. The figures are numbered "
        "in reading order — alphabetical sort would put the summary last — and "
        "every table lands in one `.xlsx` beside them: `Summary`, `Raman`, "
        "`PL`, `OM_Stats`, `OM_Points`."
    )
    if st.button("💾 Save QC Report As...", width="stretch"):
        try:
            save_path = prompt_save_path(
                default_dir=state["folder"],
                default_filename=f"{scan.sample_name}_QC_Report.png",
                title="Save QC Report",
                filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
                default_extension=".png",
            )
            if save_path:
                # One dialog, many files: the chosen name supplies the stem and
                # each artifact appends its own number, suffix and extension, so
                # the set stays together and none can overwrite another. The
                # extension the dialog collected is dropped — it only ever named
                # the first image, and the workbook is not a PNG.
                base, _ext = os.path.splitext(save_path)
                saved_files = []
                for number, suffix, key in _ARTIFACTS:
                    payload = state.get(key)
                    if not payload:
                        continue
                    target = f"{base}_{number}_{suffix}.png"
                    with open(target, "wb") as f:
                        f.write(payload)
                    saved_files.append(target)

                if state.get("xlsx_bytes"):
                    target = f"{base}.xlsx"
                    with open(target, "wb") as f:
                        f.write(state["xlsx_bytes"])
                    saved_files.append(target)

                st.success("Saved:\n" + "\n".join(f"- {p}" for p in saved_files))
            # save_path is None => user cancelled => no-op
        except Exception as e:
            st.error(f"Failed to save: {e}")
