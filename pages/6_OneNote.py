"""
OneNote: this wafer's results, published to the team notebook.

The team keeps one OneNote page per wafer and fills it by hand — saving
figures out of this app, then pasting them in. This page does that step
directly: pick what is ready, check it against the wafer's expected material,
and append it to the wafer's page.

**One artifact at a time, appended.** The QC Report, the runcard and the
datalog are produced on three different pages at three different times — a
runcard exists before the wafer is grown, the QC report a day or two after it
is measured. So an upload publishes whatever is ready now and appends a dated
section to the wafer's page, creating the page on the first upload. Waiting
for all three to be in one session would mean waiting for a sitting that does
not happen.

**Point it at the tool, then pick the wafer.** A wafer's three artifacts live
in three unrelated subfolders of its tool folder — `OPTICALS`,
`RUNCARD/lcy/NSHA1P01`, `DATALOG` — so the folder worth picking once is the
tool, and `modules/onenote/io/tool_layout.py` knows where each artifact sits
under it. The OneNote page is named for the wafer.

**Two of the three often cannot be found by wafer ID**, and this page says
*not linked* rather than *missing* for those: 58% of HA1P01's wafers have a
datalog whose filename never carried the wafer ID, and 19 have optical data
with no runcard. The file is usually there; it just cannot be addressed.

**The material check is a guardrail, not validation.** Five of the six tool
lines grow WSe2, so it agrees for almost every wafer. It is here for the QU
wafer left on the previous sample's WSe2 preset, and for the tool line nobody
has added to the table yet. See `modules/onenote/io/prefix_store.py` for why
the table is a convention rather than a record: no per-wafer material record
exists anywhere on the share.

**Uploading never blocks analysis.** A Graph outage must not stop someone
fitting spectra, so a failure is recorded and shown as a standing list rather
than a toast — `st.error` vanishes on the next rerun, and a rerun is one
widget click away.
"""

from pathlib import Path

import streamlit as st

from core.io.folder_picker import prompt_folder_path
from core.io.report_settings import save_default_material
from modules.datalog.ui.datalog_state import get_datalog_state
from modules.datalog.ui.datalog_state import reset_results as reset_datalog_results
from modules.onenote.io import prefix_store, tool_layout
from modules.onenote.processing import artifacts
from modules.onenote.ui.onenote_state import (
    failed_uploads,
    get_onenote_state,
    reset_results,
)
from modules.optical.ui.qc_report_state import get_qc_report_state
from modules.optical.ui.qc_report_state import reset_results as reset_qc_results
from modules.runcard.ui.runcard_state import get_runcard_state
from modules.runcard.ui.runcard_state import reset_results as reset_runcard_results
from modules.spectra.io.preset_store import load_presets
from modules.spectra.processing.sample_scanner import (
    default_magnification,
    scan_sample_folder,
)

#: The Wafer selectbox's widget key; see where it is seeded.
_WAFER_KEY = "onenote_wafer"

state = get_onenote_state()
qc_state = get_qc_report_state()
runcard_state = get_runcard_state()
datalog_state = get_datalog_state()

st.title("📓 OneNote")
st.markdown(
    "Publish this wafer's results to the team notebook — one page per wafer, "
    "appended to as each artifact becomes ready."
)

# --- 1. Tool folder and wafer -----------------------------------------------

st.subheader("1. Wafer")

# Pick the *tool* folder, not the wafer folder. A wafer's three artifacts live
# in three unrelated subfolders — OPTICALS, RUNCARD/lcy/NSHA1P01, DATALOG — so
# the thing the operator can point at once and reuse for every wafer is the
# tool. `tool_layout` declares where each one sits; see that module for why the
# layout is declared per tool rather than inferred.
col_pick, col_path = st.columns([1, 3])
with col_pick:
    pick_clicked = st.button("Select Tool Folder", width="stretch")
with col_path:
    st.caption(state.get("tool_folder") or "No tool folder selected yet.")

if pick_clicked:
    try:
        picked = prompt_folder_path(
            default_dir=state.get("tool_folder") or "",
            title="Select Tool Folder (e.g. HA1P01)",
        )
    except Exception as e:
        st.error(f"Failed to open folder browser: {e}")
        picked = None

    if picked:
        state["tool_folder"] = picked
        reset_results(state)
        st.rerun()

tool_folder = state.get("tool_folder")
layout = None

picked_wafer = None

if tool_folder:
    try:
        layout = tool_layout.resolve_tool(tool_folder)
    except tool_layout.UnknownToolError as e:
        # Named rather than silently empty: eight of the twelve tool folders
        # key their files on a run serial, so a wafer lookup in them finds
        # nothing at all. "No data" and "I don't know this tool" must differ.
        st.error(str(e))
    else:
        if Path(tool_folder).name != layout.name:
            # The pick was somewhere under the tool. Say which tool it
            # resolved to rather than silently retargeting — and if the pick
            # was itself a wafer folder, take that as the wafer.
            st.caption(f"Using tool folder `{layout.name}`.")
            picked_wafer = Path(tool_folder).name

wafer_id = ""

if layout is not None:
    wafers = tool_layout.list_wafers(layout)

    if not wafers:
        st.warning(f"No wafers found under `{layout.name}`.")
    else:
        # Newest first — the wafer someone wants to publish is nearly always
        # the one just measured.
        options = [""] + wafers

        # Keyed and seeded, never `index=`. Unkeyed, the selectbox's identity
        # includes its index, and the index came from the wafer chosen on the
        # *previous* run — so picking a second wafer straight after the first
        # changed the identity, Streamlit discarded the pick as belonging to a
        # widget that no longer existed, and the dropdown snapped back. The
        # seed fills only an absent or stale key: on the first visit, on
        # return from another page (which drops the key), or when a new tool
        # folder's list no longer holds the old wafer.
        if st.session_state.get(_WAFER_KEY) not in options:
            # A wafer folder picked by mistake is still a wafer the operator
            # named, so it seeds the dropdown rather than being discarded.
            previous = state.get("wafer_id") or picked_wafer
            st.session_state[_WAFER_KEY] = previous if previous in options else ""

        wafer_id = st.selectbox(
            "Wafer",
            options,
            key=_WAFER_KEY,
            format_func=lambda w: w or "Select a wafer…",
            help=f"{len(wafers)} wafers with optical data under {layout.name}.",
        )

if wafer_id != state.get("wafer_id"):
    state["wafer_id"] = wafer_id
    reset_results(state)

# --- What this wafer has on disk --------------------------------------------

wafer_paths = None

if layout is not None and wafer_id:
    wafer_paths = tool_layout.find_wafer(layout, wafer_id)

    st.markdown("**Found on disk**")
    for role, label in (
        (tool_layout.OPTICAL, "Raman / PL / OM"),
        (tool_layout.RUNCARD, "Runcard"),
        (tool_layout.DATALOG, "Datalog"),
    ):
        path = wafer_paths.path_for(role)
        # Read-only status, deliberately: whether a file is addressable is
        # something the app knows, not a decision the operator makes.
        #
        # Written to the key on every run rather than passed as `value=`. A
        # keyed checkbox is identified by its key alone, so `value=` is only
        # the first render's default: the first wafer picked set these ticks
        # for the whole session, and every later wafer showed its filenames
        # beside its predecessor's empty boxes.
        found_key = f"onenote_found_{role}"
        st.session_state[found_key] = path is not None
        st.checkbox(
            f"{label} — {path.name if path else 'not linked'}",
            disabled=True,
            key=found_key,
        )

    if not wafer_paths.linked(tool_layout.DATALOG):
        # "Not linked", never "missing". The log almost certainly exists —
        # 1186 of this tool's 1404 datalog CSVs simply never had the wafer ID
        # written into their name, so nothing can address them by wafer.
        # Calling it missing sends someone hunting for a file that is there.
        st.caption(
            "ℹ️ *Not linked* means the file can't be found **by wafer ID**, not "
            "that it doesn't exist. Most datalogs were written without the "
            "wafer ID in the filename, so they can't be matched automatically."
        )

# --- 2. Material check ------------------------------------------------------

st.subheader("2. Material check")

if not wafer_id:
    st.info("Name a wafer to check its material.")
else:
    # Defaulted from the prefix table rather than left blank. The tool line
    # determines the material, so asking the operator to pick what the app
    # already knows is a rote step — and a rote step chosen wrongly is exactly
    # the mismatch this check was built to catch. Pre-filling it means the
    # check guards a real disagreement instead of an empty box.
    presets = load_presets()
    materials = sorted(name for name, p in presets.items() if p.enabled)

    if not materials:
        st.warning(
            "No materials configured yet. Add one on the **Material Presets** page."
        )
        selected_material = None
    else:
        expected = prefix_store.resolve(wafer_id).material
        current = qc_state.get("material")

        if current in materials:
            default = current
        elif expected in materials:
            default = expected
        else:
            default = materials[0]

        selected_material = st.selectbox(
            "Material",
            options=materials,
            index=materials.index(default),
            help=(
                "Defaulted from the wafer's tool prefix. Shared with the QC "
                "Report page — changing it here changes it there."
            ),
        )

        # One value, not two: the QC Report builds its figures from this same
        # key, and a second copy would let the page name one material while
        # the figures on it were fitted with another.
        if selected_material != qc_state.get("material"):
            qc_state["material"] = selected_material
            save_default_material(selected_material)
            reset_qc_results(qc_state)

    check = prefix_store.check_material(wafer_id, selected_material)
    state["material_check"] = check

    if check.verdict == prefix_store.MATCH:
        st.success(f"✅ {check.summary()}")
    elif check.verdict == prefix_store.MISMATCH:
        # Warned here as well as written onto the page: the operator can fix a
        # mis-selection in seconds if told, and cannot if not.
        st.warning(f"⚠️ {check.summary()}")
    elif check.verdict == prefix_store.NO_SELECTION:
        st.info(f"{check.summary()} Pick one on the QC Report page.")
    else:
        # Unknown prefix — rendered differently from a mismatch on purpose.
        # "We did not check" and "we checked and it disagrees" must not look
        # alike, or a real mismatch gets read as the same routine noise.
        st.info(f"ℹ️ {check.summary()}")

        with st.expander(f"Add `{check.resolution.prefix}` to the table"):
            st.caption(
                "Saved to `data/material_prefixes.local.json`, which is not "
                "committed. Anything added here is marked *locally added, not "
                "yet shared* wherever it appears, until someone promotes it "
                "into `data/material_prefixes.json` so the team sees it too."
            )
            new_material = st.text_input(
                "Material for this prefix",
                key="onenote_new_prefix_material",
                placeholder="e.g. MoS2",
            )
            if st.button("Add locally", disabled=not new_material):
                prefix_store.add_local_prefix(check.resolution.prefix, new_material)
                reset_results(state)
                st.rerun()

# --- 3. What's ready --------------------------------------------------------

st.subheader("3. What's ready")

if not wafer_id:
    st.stop()

qc_payload = artifacts.qc_report_payload(qc_state, wafer_id)
runcard_run_ids = sorted((runcard_state.get("figures") or {}).keys())

# Each column offers to carry this wafer to the page that builds its artifact,
# rather than telling the operator to go there and find it again. The wafer is
# already resolved here, so re-picking the same folder on the next page is a
# step that exists only because the pages don't talk to each other.
col_qc, col_rc, col_dl = st.columns(3)

with col_qc:
    st.markdown(f"**{artifacts.QC_REPORT}**")
    if qc_payload:
        st.caption(
            f"{len(qc_payload.images)} figures inline, "
            f"{len(qc_payload.attachments)} attached"
        )
    else:
        st.caption("Not built yet.")
        optical = wafer_paths.path_for(tool_layout.OPTICAL) if wafer_paths else None
        if optical is not None and optical.is_dir():
            if st.button(f"Build for {wafer_id} →", key="onenote_goto_qc"):
                # `scan_sample_folder` skips subdirectories by design, so it
                # gets the wafer leaf and never the tool root — pointed at
                # HA1P01 it returns a silently empty report, not an error.
                if qc_state.get("folder") != str(optical):
                    qc_state["folder"] = str(optical)
                    qc_state["scan"] = scan_sample_folder(str(optical))
                    qc_state["magnification"] = default_magnification(qc_state["scan"])
                    reset_qc_results(qc_state)
                st.switch_page("pages/2_QC_Report.py")
        elif optical is not None:
            # The pre-202609 months are flat `.wip` project files, so there is
            # no folder of spectra for the QC Report to read.
            st.caption(f"`{optical.name}` is an archived project file.")

with col_rc:
    st.markdown(f"**{artifacts.RUNCARD}**")
    if runcard_run_ids:
        st.caption(f"{len(runcard_run_ids)} chart(s) drawn")
    else:
        st.caption("No chart drawn yet.")
        if layout is not None:
            runcard_folder = layout.subfolder(tool_layout.RUNCARD)
            if runcard_folder is not None and runcard_folder.is_dir():
                if st.button("Open Runcard →", key="onenote_goto_runcard"):
                    # The declared subfolder, not the tool root: the loader
                    # already recurses, and a root of HA1P01 would pull in
                    # another tool's cards from RUNCARD/ymc/NSHA1N01.
                    if runcard_state.get("folder") != str(runcard_folder):
                        runcard_state["folder"] = str(runcard_folder)
                        reset_runcard_results(runcard_state)
                    st.switch_page("pages/5_Runcard.py")

with col_dl:
    st.markdown(f"**{artifacts.DATALOG}**")
    st.caption("Chart and violations, from the Datalog page.")
    if layout is not None:
        datalog_folder = layout.subfolder(tool_layout.DATALOG)
        if datalog_folder is not None and datalog_folder.is_dir():
            if st.button("Open Datalog →", key="onenote_goto_datalog"):
                # Deliberately the DATALOG subfolder and never the tool root:
                # the Datalog page's bulk rename walks from its root, and
                # `runcard_tags.json` is keyed by path relative to it, so a
                # wider root would both widen that rename and orphan 231 tags.
                if datalog_state.get("folder") != str(datalog_folder):
                    datalog_state["folder"] = str(datalog_folder)
                    reset_datalog_results(datalog_state)
                st.switch_page("pages/4_Datalog.py")

# --- 4. Publish -------------------------------------------------------------

st.subheader("4. Publish")

st.info(
    "Sign-in and upload are not wired up yet — this page currently shows what "
    "*would* be published. The material check above is live."
)

# --- Failed uploads ---------------------------------------------------------
# Kept out of `reset_results` deliberately: the log records what happened, and
# that stays true whatever the operator picks next. A failure that vanished on
# the next folder change is exactly the silent loss the non-blocking design was
# chosen to avoid.

failures = failed_uploads(state)
if failures:
    st.subheader("⚠️ Not uploaded")
    st.caption("These failed and are still not on the notebook. Retry when ready.")
    for entry in failures:
        st.markdown(
            f"- **{entry['artifact']}** for `{entry['wafer_id']}` — {entry['detail']}"
        )
