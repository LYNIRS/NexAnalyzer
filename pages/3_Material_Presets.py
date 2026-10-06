"""
Material Presets page: create, edit, and delete the material presets used
by the Spectra page's Run Auto-Workflow / Run All Files buttons, and by the
QC Report.

Presets are embedded in the app (data/materials.json) as of v2.11.0, replacing
the earlier Excel-file workflow (presets/material_presets.xlsx). This page is
the only way to manage them now.

As of v4.0.0 one entry is one **material**, not one material-and-technique
pair, and it holds up to three kinds of block:

  raman / pl   processing settings and peak templates, one per technique. A
               material may have either, both, or (for an optical-only
               material) neither.
  optical      layer-segmentation tuning, split by layer ("1L" / "2L"),
               because optical contrast against SiO2/Si genuinely differs
               between a monolayer and a bilayer while a Raman or PL spectrum
               does not.

Every widget key below is namespaced by material *and* block. Two
`st.data_editor`s in one expander sharing a key is not an error Streamlit
reports -- it silently serves one block's peak rows into the other.
"""

import pandas as pd
import streamlit as st

from core.io.om_calibration import load_om_calibration, save_om_calibration
from modules.optical.processing.contrast import layer_word
from modules.spectra.models.preset import (
    OPTICAL_LAYERS,
    MaterialPreset,
    OpticalParams,
    PeakTemplate,
    TechniquePreset,
)
from modules.spectra.models.spectrum import DESPIKE_THRESHOLD_MAX, DESPIKE_THRESHOLD_MIN
from modules.spectra.io.preset_store import load_store, save_presets

PEAK_COLUMNS = ["peak_label", "center", "center_tolerance", "width_fwhm", "shape", "color"]
BASELINE_ALGORITHMS = ["Polynomial", "ALS", "None (Skip)"]
MODES = ["Raman", "PL"]

#: Optical field -> (label, help). The help text is the algorithm doc's own
#: guidance, so the operator doesn't have to go and find it.
_OPTICAL_FIELDS = {
    "nsigma": (
        "nsigma",
        "Threshold in noise widths. 5 if the operator reports over-count, "
        "3 if faint domains are missed.",
    ),
    "minpx": (
        "minpx",
        "Smallest surviving blob, in pixels. Raise to suppress speckle.",
    ),
    "mask_margin": (
        "mask margin",
        "Fraction of the frame trimmed before analysis. Applies to both "
        "circular and rectangular frames — a preset cannot tell them apart.",
    ),
    "ff_divisor": (
        "flat-field divisor",
        "Blur sigma is max(H, W) / this, so LARGER means a smaller, more "
        "local background estimate. Raise to ~16 if vignetting is still "
        "visible.",
    ),
    "abs_threshold_below": (
        "absolute threshold, below (%)",
        "Fixed green contrast below the film mode, in percent. Set this and "
        "the 'above' box together to threshold on contrast instead of nsigma. "
        "Leave both empty to keep nsigma.",
    ),
    "abs_threshold_above": (
        "absolute threshold, above (%)",
        "Fixed green contrast above the film mode, in percent. Roughly half a "
        "layer step (~+4 %) puts the boundary midway between the film and the "
        "next layer up. Not a mirror of 'below': one layer up is a smaller "
        "step than the drop to substrate.",
    ),
    "marker_margin_sigma": (
        "domain marker sensitivity (σ)",
        "QC Report's domain-size measurement only, not the segmentation "
        "above. How many noise-widths past its class's own boundary a "
        "watershed marker must clear. Lower (e.g. 0.1-0.2) if real domains "
        "are going missing because they're too flat/faint; default 0.5.",
    ),
    "marker_min_distance": (
        "domain marker spacing (px)",
        "QC Report's domain-size measurement only. Minimum pixel separation "
        "between two accepted watershed markers. Raise it (e.g. 30-80) if "
        "one flake's own internal reflection highlights are being "
        "watershed-split into several domains instead of staying one; "
        "default 8.",
    ),
    "mask_closing_radius": (
        "domain edge closing radius (px)",
        "QC Report's domain-size measurement only. Bridges small gaps in "
        "the segmentation mask before domains are counted, absorbing "
        "fringe fragments a flake's noisy real edge leaves disconnected. "
        "Raise it a little (1-3) if small domains keep appearing right at "
        "or inside a large flake's footprint; too large starts merging "
        "genuinely separate close-together small flakes. Default 0 (off).",
    ),
}


def _peaks_to_df(peak_templates: list[PeakTemplate]) -> pd.DataFrame:
    if not peak_templates:
        return pd.DataFrame(columns=PEAK_COLUMNS)
    return pd.DataFrame([t.to_dict() for t in peak_templates], columns=PEAK_COLUMNS)


def _df_to_peaks(df: pd.DataFrame) -> list[PeakTemplate]:
    peaks = []
    for _, row in df.iterrows():
        if pd.isna(row.get("peak_label")) or str(row.get("peak_label")).strip() == "":
            continue
        peaks.append(PeakTemplate(
            peak_label=str(row["peak_label"]).strip(),
            center=float(row["center"]),
            center_tolerance=float(row["center_tolerance"]),
            width_fwhm=float(row["width_fwhm"]),
            shape=float(row["shape"]),
            color=str(row["color"]).strip(),
        ))
    return peaks


def _render_technique_block(key_prefix: str, mode: str,
                            block: TechniquePreset = None) -> TechniquePreset:
    """Editable fields for one technique block. Returns what the widgets hold.

    `key_prefix` already carries the material and the block, so the caller
    cannot accidentally give two blocks the same widget identity.
    """
    d = block  # shorthand

    col3, col4 = st.columns(2)
    with col3:
        despike_threshold = st.number_input(
            "Despike threshold",
            min_value=DESPIKE_THRESHOLD_MIN, max_value=DESPIKE_THRESHOLD_MAX,
            value=d.despike_threshold if d else 6.0, step=0.5,
            key=f"{key_prefix}_despike"
        )
    with col4:
        baseline_algorithm = st.selectbox(
            "Baseline algorithm", options=BASELINE_ALGORITHMS,
            index=BASELINE_ALGORITHMS.index(d.baseline_algorithm) if d else 0,
            key=f"{key_prefix}_baseline_algo"
        )

    baseline_degree = None
    baseline_lambda = None
    baseline_p = None
    if baseline_algorithm == "Polynomial":
        baseline_degree = st.number_input(
            "Baseline degree", min_value=1, max_value=10,
            value=d.baseline_degree if (d and d.baseline_degree) else 5, step=1,
            key=f"{key_prefix}_degree"
        )
    elif baseline_algorithm == "ALS":
        col5, col6 = st.columns(2)
        with col5:
            baseline_lambda = st.number_input(
                "ALS lambda", min_value=1000.0, max_value=1000000.0,
                value=d.baseline_lambda if (d and d.baseline_lambda) else 10000.0, step=1000.0,
                key=f"{key_prefix}_lambda"
            )
        with col6:
            baseline_p = st.number_input(
                "ALS p", min_value=0.001, max_value=0.1,
                value=d.baseline_p if (d and d.baseline_p) else 0.001, step=0.001,
                format="%.3f", key=f"{key_prefix}_p"
            )

    x_range_enabled = st.checkbox(
        "Limit X-range", value=d.x_range_enabled if d else False,
        key=f"{key_prefix}_xrange_enabled"
    )
    x_min = x_max = None
    if x_range_enabled:
        col7, col8 = st.columns(2)
        with col7:
            x_min = st.number_input(
                "X min", value=d.x_min if (d and d.x_min is not None) else 0.0,
                key=f"{key_prefix}_xmin"
            )
        with col8:
            x_max = st.number_input(
                "X max", value=d.x_max if (d and d.x_max is not None) else 1000.0,
                key=f"{key_prefix}_xmax"
            )

    max_iterations = st.number_input(
        "Max fit iterations", min_value=500, max_value=20000,
        value=d.max_iterations if d else 2000, step=500,
        help="Iteration budget for the Voigt fit. Higher = more attempts to "
             "converge on a difficult spectrum, at the cost of time per fit.",
        key=f"{key_prefix}_max_iterations"
    )

    exclusion_ranges = st.text_input(
        "Exclusion ranges (optional)",
        value=d.exclusion_ranges if (d and d.exclusion_ranges) else "",
        help="Semicolon-separated ranges to exclude from baseline fitting, e.g. '1200-1400; 2600-2800'",
        key=f"{key_prefix}_exclusion"
    )

    st.caption("Peak templates")
    st.caption(
        "**± Tolerance** is the half-width of the window the fitted centre may "
        "move in, and it is honoured exactly — since v4.0.0 the fitter no "
        "longer overwrites it with the mode default."
    )
    peaks_df = st.data_editor(
        _peaks_to_df(d.peak_templates) if d else pd.DataFrame(columns=PEAK_COLUMNS),
        num_rows="dynamic",
        width="stretch",
        column_config={
            "peak_label": st.column_config.TextColumn("Label", required=True),
            "center": st.column_config.NumberColumn("Center", required=True),
            "center_tolerance": st.column_config.NumberColumn("± Tolerance", required=True),
            "width_fwhm": st.column_config.NumberColumn("FWHM", required=True),
            "shape": st.column_config.NumberColumn("Shape (0=Gauss, 1=Lorentz)", min_value=0.0, max_value=1.0, required=True),
            "color": st.column_config.TextColumn("Color (#RRGGBB)", required=True),
        },
        key=f"{key_prefix}_peaks"
    )

    return TechniquePreset(
        x_range_enabled=x_range_enabled,
        x_min=x_min,
        x_max=x_max,
        despike_threshold=float(despike_threshold),
        baseline_algorithm=baseline_algorithm,
        baseline_degree=int(baseline_degree) if baseline_degree is not None else None,
        baseline_lambda=float(baseline_lambda) if baseline_lambda is not None else None,
        baseline_p=float(baseline_p) if baseline_p is not None else None,
        exclusion_ranges=exclusion_ranges.strip() or None,
        max_iterations=int(max_iterations),
        peak_templates=_df_to_peaks(peaks_df),
    )


def _render_optical_block(key_prefix: str, layer: str,
                          params: OpticalParams = None) -> OpticalParams:
    """Editable fields for one layer's optical tuning.

    An empty box means "use the algorithm's default", which is why every field
    is a text input rather than a number input: a number input has no empty
    state, so it would silently turn every default into a stored value the
    moment anyone opened the expander.
    """
    values = {}
    columns = st.columns(len(_OPTICAL_FIELDS))
    for column, (name, (label, help_text)) in zip(columns, _OPTICAL_FIELDS.items()):
        current = getattr(params, name) if params else None
        with column:
            raw = st.text_input(
                label,
                value="" if current is None else str(current),
                placeholder="default",
                help=help_text,
                key=f"{key_prefix}_{layer}_{name}",
            )
        raw = raw.strip()
        if not raw:
            values[name] = None
            continue
        try:
            is_int_field = name in ("minpx", "marker_min_distance", "mask_closing_radius")
            values[name] = int(raw) if is_int_field else float(raw)
        except ValueError:
            st.error(f"{layer} {label}: '{raw}' is not a number.")
            values[name] = None

    return OpticalParams(**values)


def _render_preset_form(presets: dict, key_prefix: str,
                        existing: MaterialPreset = None, original_key=None):
    """
    Render the editable fields for one material (existing or new) and handle
    its Save/Delete buttons. Mutates `presets` and persists via
    save_presets() when the user saves or deletes.
    """
    d = existing  # shorthand

    col1, col2 = st.columns([3, 1])
    with col1:
        material_name = st.text_input(
            "Material name", value=d.material_name if d else "", key=f"{key_prefix}_name"
        )
    with col2:
        # A real control at last. Until v4.0.0 this was hardcoded True on save,
        # so nothing could write False and editing a hand-disabled preset
        # silently re-enabled it.
        enabled = st.checkbox(
            "Enabled", value=d.enabled if d else True,
            help="Unchecked materials stay in the file but are hidden from "
                 "every material dropdown.",
            key=f"{key_prefix}_enabled"
        )

    description = st.text_input(
        "Notes (optional)", value=d.description if d else "", key=f"{key_prefix}_description"
    )

    # ------------------------------------------------------------- Technique
    blocks = {}
    for mode in MODES:
        current = d.block_for(mode) if d else None
        has_block = st.checkbox(
            f"{mode} settings",
            value=current is not None,
            key=f"{key_prefix}_has_{mode.lower()}",
            help=f"Unchecking removes this material's {mode} block entirely.",
        )
        if has_block:
            with st.container(border=True):
                blocks[mode] = _render_technique_block(
                    f"{key_prefix}_{mode.lower()}", mode, current
                )
        else:
            blocks[mode] = None

    # --------------------------------------------------------------- Optical
    st.markdown("**Optical (layer segmentation)**")
    st.caption(
        "Blank means the algorithm's own default. A layer with every field "
        "blank stores no block at all, and the QC Report says it is running "
        "defaults."
    )
    optical = {}
    for index, layer in enumerate(OPTICAL_LAYERS):
        # One full-width row per layer, not side-by-side columns: each row
        # already splits into st.columns(len(_OPTICAL_FIELDS)) (9, once the
        # domain-size-only fields are counted), and nesting that inside a
        # half-width column left every field box uselessly narrow.
        if index > 0:
            st.divider()
        st.caption(f"**{layer_word(layer)}** ({layer})")
        candidate = _render_optical_block(
            f"{key_prefix}_optical", layer,
            d.optical.get(layer) if d else None,
        )
        # Only store a block that says something. An all-blank layer is
        # "untuned", which is the absence of a block, not an empty one.
        if candidate.to_dict():
            optical[layer] = candidate

    # Unknown layers round-trip untouched rather than being dropped on edit.
    if d:
        for layer, params in d.optical.items():
            if layer not in OPTICAL_LAYERS:
                optical[layer] = params
                st.warning(
                    f"Keeping unknown optical layer '{layer}', which this page "
                    f"cannot edit. Nothing reads it."
                )

    # ---------------------------------------------------------------- Buttons
    button_col1, button_col2 = st.columns([1, 1])
    with button_col1:
        save_clicked = st.button(
            "💾 Save" if d else "➕ Create Material",
            key=f"{key_prefix}_save", type="primary", width="stretch"
        )
    with button_col2:
        delete_clicked = False
        if d:
            delete_clicked = st.button(
                "🗑️ Delete", key=f"{key_prefix}_delete", width="stretch"
            )

    if delete_clicked:
        del presets[original_key]
        save_presets(presets)
        st.success(f"Deleted {original_key}")
        st.rerun()

    if save_clicked:
        new_key = material_name.strip()

        if not new_key:
            st.error("Material name is required.")
            return

        if new_key != original_key and new_key in presets:
            st.error(f"A material named {new_key} already exists.")
            return

        candidate = MaterialPreset(
            material_name=new_key,
            enabled=enabled,
            description=description.strip(),
            raman=blocks["Raman"],
            pl=blocks["PL"],
            optical=optical,
        )

        errors = candidate.validate()
        if errors:
            for err in errors:
                st.error(err)
            return

        if original_key is not None and original_key != new_key:
            del presets[original_key]
        presets[new_key] = candidate
        save_presets(presets)
        st.success(f"Saved {new_key}")
        st.rerun()


st.title("🧪 Material Presets")
st.markdown(
    "Create and edit the material presets used by **Run Auto-Workflow** / "
    "**Run All Files** on the Spectra page, and by the QC Report."
)

store = load_store()
presets = store.presets
if store.migrated:
    st.warning(
        "This store is still in the pre-v4.0.0 format and was migrated in "
        "memory to display it. Save any material below to write the new format "
        "to disk."
    )
for warning in store.warnings:
    st.info(warning)

st.caption(f"{len(presets)} material(s) configured")

for key in sorted(presets.keys()):
    preset = presets[key]
    techniques = [m for m in MODES if preset.block_for(m)]
    layers = sorted(preset.optical)
    summary = " · ".join(filter(None, [
        ", ".join(techniques) or "no spectra",
        f"optical {', '.join(layers)}" if layers else "",
        "" if preset.enabled else "disabled",
    ]))
    with st.expander(f"{preset.material_name} — {summary}"):
        _render_preset_form(
            presets, key_prefix=f"edit_{key}", existing=preset, original_key=key
        )

st.markdown("---")
st.subheader("Add New Material")
with st.expander("➕ New material", expanded=len(presets) == 0):
    _render_preset_form(presets, key_prefix="new")

st.markdown("---")
st.subheader("Objective / Magnification Calibration")
st.caption(
    "Micron-per-pixel, by magnification group — not by material: a 50X "
    "objective has the same field of view regardless of what film is under "
    "it. Used by the QC Report's domain-size analysis to convert measured "
    "pixel sizes into real units. A magnification with no row here is "
    "reported in pixels, honestly, rather than guessed."
)

calibration = load_om_calibration()
calibration_df = pd.DataFrame(
    [{"Magnification": mag, "um_per_px": value} for mag, value in sorted(calibration.items())],
    columns=["Magnification", "um_per_px"],
)
edited_calibration_df = st.data_editor(
    calibration_df,
    num_rows="dynamic",
    width="stretch",
    column_config={
        "Magnification": st.column_config.TextColumn(
            "Magnification", required=True, help='e.g. "50x", matching the OM folder naming.'
        ),
        "um_per_px": st.column_config.NumberColumn(
            "µm/px", required=True, min_value=0.0, format="%.5f"
        ),
    },
    key="om_calibration_editor",
)

if st.button("💾 Save Calibration"):
    rows = edited_calibration_df.dropna(subset=["Magnification", "um_per_px"]).to_dict("records")
    magnifications = [str(r["Magnification"]).strip() for r in rows]
    duplicates = {m for m in magnifications if magnifications.count(m) > 1}
    if duplicates:
        st.error(f"Duplicate magnification(s): {', '.join(sorted(duplicates))}")
    elif any(not m for m in magnifications):
        st.error("Magnification cannot be blank.")
    elif any(float(r["um_per_px"]) <= 0 for r in rows):
        st.error("µm/px must be > 0.")
    else:
        save_om_calibration({
            str(r["Magnification"]).strip(): float(r["um_per_px"]) for r in rows
        })
        st.success("Calibration saved.")
        st.rerun()
