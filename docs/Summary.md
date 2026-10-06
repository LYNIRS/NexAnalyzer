# NexAnalyzer v3.8.1 - Project Summary

## Overview

NexAnalyzer is Nexstrom's measurement data analyzer: a desktop web application that turns raw
measurement files into fitted results and shareable reports. Built with Streamlit and Python,
it is organized as a platform — a technique-agnostic `core/` plus one module per measurement
technique — with Raman and Photoluminescence (PL) spectra as the first module.

For the feature list, installation steps, and day-to-day usage, see [README.md](../README.md) and [USER_GUIDE.md](../USER_GUIDE.md). For version-by-version history, see [CHANGELOG.md](../CHANGELOG.md). This document covers architecture, data model, and the technical decisions behind them — the things a developer working on the codebase needs that a user-facing doc doesn't cover.

### The one rule

`modules/*` may import `core`; **`core` never imports `modules`**. `core` holds what has no
knowledge of peaks or spectra (report assembly, native dialogs, figure export, page-width
rendering); everything that knows what a peak is lives in `modules/spectra`. Within a tree
imports are relative, across trees absolute. Adding a technique means adding a package under
`modules/` and registering its pages in `app.py` — nothing in `core` changes.

`modules/` is one package per **subject**, which for the first two was one per measurement
technique: `spectra` (Raman & PL) and `optical` (OM). `datalog` and `runcard`, added at v5.1.0,
are not techniques — they read the deposition tool's own logs and recipes rather than anything
measured off a sample afterwards. They are separate packages rather than one `process` package
because they share nothing: different parsers, different data structures, different folders, not
one constant in common. A module here is a unit of independence, and those two are maximally
independent. One thing crosses, since v5.7.0, and in one direction: the Datalog page's runcard
detection reads recipes through `modules.runcard.io.parser` (in `datalog/io/runcard_index.py`), so
a recipe is never parsed two ways. Nothing in `runcard` imports `datalog`.

They are also the reason the navigation grew its "Process" section: that label names a *data
source*, which no future technique can falsify, where the old "Raman & PL" heading named a
technique and went stale the moment a page grew an OM image.

---

## Technical Architecture

### Frontend
- **Framework**: Streamlit (Python web framework for data apps), multi-page via `st.navigation()` (v2.11.0+)
- **Pages**: six, in three groups. Unlabelled — **Spectra** (`pages/1_Spectra.py`: sidebar + full-width plot, the entire spectrum workflow), **QC Report** (`pages/2_QC_Report.py`: seven figures and one workbook from a sample folder's 9-point OM + Raman + PL grid), **Material Presets** (`pages/3_Material_Presets.py`: create/edit/delete materials). Under **Process** — **Datalog** (`pages/4_Datalog.py`: a run folder's 1 Hz process logs as stacked PV/SV charts, with tolerance violations, summary stats, tagging, bulk rename and duplicate cleanup), **Runcard** (`pages/5_Runcard.py`: a recipe's reconstructed time profile, growth window and gas chemistry). Under **Publish** — **OneNote** (`pages/6_OneNote.py`: picks a wafer from a tool folder, checks its material against `data/material_prefixes.json`, and shows what the other pages have ready to publish to the wafer's OneNote page; sign-in and upload are not wired up yet). Sample Report and QC Panel were merged into QC Report at v5.0.0, and the Plot Explorer was dropped at the same time. The "Raman & PL" group was removed at v4.2.0 for naming a technique; "Process" names a data source instead, which is why it survives that argument.
- **State Management**: Streamlit session state with automatic persistence
- **Visualization**: Plotly (interactive multi-layer plots)

### Backend
- **Data Model**: `SpectrumFile` dataclass with `original_data`, `raw_data`, `processed_data` layers
- **Processing Pipeline**:
  1. X-range cropping (modifies `raw_data`)
  2. De-spiking (modifies `processed_data`)
  3. Baseline correction (modifies `processed_data`)
  4. Peak fitting (stores `FitResult`)
- **Algorithms**:
  - **De-spiking**: Modified Z-score spike detection
  - **Baseline**: Polynomial, ALS, Rolling Ball, Spline, airPLS
  - **Fitting**: Voigt profile (lmfit Levenberg-Marquardt)

### Project Structure
```
nexanalyzer/
├── app.py                          # Composition root: page config, module state, routing
├── pages/
│   ├── 1_Spectra.py                # Spectra page: sidebar + full-width plot
│   ├── 2_QC_Report.py              # QC Report page: folder -> 7 figures + 1 workbook
│   ├── 3_Material_Presets.py       # Material Presets page: create/edit/delete materials
│   ├── 4_Datalog.py                # Datalog page: run browser, PV/SV charts, violations,
│   │                               # tagging and bulk rename
│   └── 5_Runcard.py                # Runcard page: one recipe's reconstructed profile
├── core/                           # Platform. Knows nothing about peaks or spectra.
│   ├── paths.py                    # PROJECT_ROOT / DATA_DIR, anchored on this file
│   ├── version.py                  # APP_NAME, __version__, REPO_URL (single source of truth)
│   ├── io/
│   │   ├── export.py               # Figure PNG/HTML rasterization, native Save-As dialog,
│   │   │                           # output filename construction
│   │   ├── folder_picker.py        # Native folder- and file-picker dialogs
│   │   │                           # (isolated subprocess)
│   │   └── report_settings.py      # Persisted default-material setting
│   ├── report/
│   │   ├── models.py               # SampleScan, PeakStat, OpticalClassStat — the row/scan
│   │   │                           # contracts a module fills in for the report renderer
│   │   ├── progress.py             # Weighted, staged progress for a long report build
│   │   └── summary_figure.py       # The composed report pages: the overview (OM grid +
│   │                               # class table + stats tables) and the per-technique
│   │                               # fitted-spectra grid, as matplotlib PNGs
│   └── viz/
│       └── render.py               # Page-width-aware st.plotly_chart wrapper
├── modules/                        # One package per subject (technique, or data source)
│   ├── datalog/                    # The deposition tool's 1 Hz process logs
│   │   ├── io/scanner.py           # Recursive run discovery, cheap metadata scan, cached
│   │   │                           # parse, PV/SV pair detection
│   │   ├── io/tag_store.py         # runcard_tags.json in the DATA folder — frozen format,
│   │   │                           # shared with datalog_monitor
│   │   ├── io/threshold_store.py   # thresholds.json, likewise frozen and shared
│   │   ├── io/renamer.py           # <timestamp>~tag.csv sweeps, with tag re-keying and
│   │   │                           # rollback (frozen filename format)
│   │   ├── io/duplicates.py        # Runs an outside copier stored twice: hidden from the
│   │   │                           # list, Recycle-Binned on demand
│   │   ├── io/config_store.py      # data/datalog.json — nexanalyzer's own remembered folder
│   │   ├── io/runcard_index.py     # The recipe folder and the runs, read for runcard matching
│   │   │                           # (the one import of modules.runcard)
│   │   ├── processing/analysis.py  # Summary stats, tolerance violations, plateau alignment
│   │   ├── processing/runcard_match.py  # Which runcard produced a run: replay, align, assign
│   │   │                           # (see docs/Runcard_Matching.md)
│   │   ├── ui/datalog_state.py     # Isolated session-state namespace for the Datalog page
│   │   └── viz/charts.py           # Stacked shared-x run figures, single and comparison
│   ├── runcard/                    # The deposition tool's recipes. Recipe semantics
│   │   │                           # are specified by docs/reference/render_runcard.py
│   │   ├── io/parser.py            # Runcard CSV -> RuncardCommand list, cached
│   │   ├── processing/growth_window.py  # Command list -> timeline, traces, growth window
│   │   ├── processing/stats.py     # The metrics a profile reports, and the gantt bar rows
│   │   ├── ui/runcard_state.py     # Isolated session-state namespace for the Runcard page
│   │   └── viz/profile.py          # The profile figure (Plotly; the ancestor drew SVG)
│   ├── onenote/                    # Publishing a wafer's results to its OneNote page
│   │   ├── io/prefix_store.py      # data/material_prefixes.json (+ gitignored .local overlay)
│   │   ├── io/tool_layout.py       # data/tool_layouts.json: where a tool folder keeps each artifact
│   │   ├── processing/artifacts.py # What each page has ready to publish, as bytes
│   │   └── ui/onenote_state.py     # Isolated session-state namespace for the OneNote page
│   ├── optical/                    # Optical microscopy: contrast-based layer classification
│   │   ├── processing/contrast.py  # The vendored segmentation (see OM_Contrast_Algo.md),
│   │   │                           # plus class_summary/contrast_summary across frames
│   │   ├── io/frame_tables.py      # The segmentation's numbers as rows: per-class stats and
│   │   │                           # the per-frame rows behind them
│   │   ├── ui/qc_report_state.py   # Isolated session-state namespace for the QC Report
│   │   └── viz/om_grid.py          # The OM grid figure, clean and histogram-diagnostic
│   └── spectra/                    # Raman & PL
│       ├── models/
│       │   ├── spectrum.py         # SpectrumFile, ProcessingSettings, SpectrumData
│       │   ├── peak.py             # PeakDefinition, FittedPeak, FitResult
│       │   └── preset.py           # MaterialPreset (+ TechniquePreset, OpticalParams),
│       │                           # PeakTemplate, parse_exclusion_ranges
│       ├── processing/
│       │   ├── parser.py           # Two-column .txt file parsing
│       │   ├── despiking.py        # Modified Z-score spike removal
│       │   ├── baseline.py         # 5 baseline algorithms + quality metrics
│       │   ├── fitting.py          # Voigt peak fitting with Levenberg-Marquardt
│       │   ├── auto_workflow.py    # One-click preset-driven pipeline execution
│       │   ├── sample_scanner.py   # Sample-folder discovery by filename pattern
│       │   ├── sample_batch.py     # Per-sample batch-fit orchestration
│       │   └── peak_metrics.py     # Peak intensity/stderr, raw-spectrum stats, mean/std
│       │                           # aggregation, intensity ratios (one rule, one place)
│       ├── io/
│       │   ├── preset_store.py     # JSON material-preset storage (data/materials.json),
│       │   │                       # schema v2 + the v1 migration
│       │   ├── results_csv.py      # Fit-results CSVs: per-file and master
│       │   └── results_excel.py    # The QC Report's one workbook: Summary, per-technique
│       │                           # sheets, and the two optical sheets
│       ├── ui/
│       │   ├── sidebar.py          # Material dropdown, the only processing entry point
│       │   │                       # (Run Auto-Workflow / Run All Files), Quick/Batch
│       │   │                       # Export, Reset to Raw, file list, View Options
│       │   └── session_state.py    # Session state management
│       ├── viz/
│       │   ├── live_plot.py        # Interactive multi-layer plot + file navigation
│       │   ├── fit_plot.py         # Static data+fit+components figures for export and the
│       │   │                       # QC Report's grids (`show_residuals=False`)
│       │   └── peak_quality.py     # The per-technique quality panels (Raman and PL),
│       │                           # driven by a QualityFigureSpec
│       └── utils/
│           ├── fit_staleness.py    # Preprocessing-hash fingerprinting (stale-fit detection)
│           └── preset_staleness.py # Per-block preset fingerprinting, so a Raman edit
│                                   # doesn't discard a 30-second OM figure
├── data/
│   ├── materials.json              # Shared material preset store (committed)
│   ├── material_prefixes.json      # Wafer-prefix -> material table (committed; .local overlay gitignored)
│   ├── tool_layouts.json           # Per-tool artifact layout for the OneNote page (committed)
│   ├── report_settings.json        # Per-installation preference (gitignored)
│   ├── datalog.json                # Datalog page's remembered folder (gitignored)
│   ├── runcard.json                # Runcard page's remembered folder (gitignored)
├── tests/
│   ├── unit/                       # pytest suite for core/ and modules/
│   └── integration/                # streamlit.testing.v1.AppTest-driven page tests
├── docs/                           # This file + algorithm notes
│   └── reference/                  # Vendored specifications: source kept byte-identical
│                                   # so it can be diffed against. Excluded from ruff.
├── pyproject.toml                  # pytest configuration
└── requirements.txt                # Python dependencies
```

`src/ui/control_panel/` (v2.7.1 layout) was a single ~2,700-line file through v2.7.1; it was
split into a package (one module per accordion section) in v2.8.0, with
`compute_preprocessing_hash()` / `mark_fit_stale_if_needed()` moved to
`modules/spectra/utils/fit_staleness.py` since they have no UI dependency (fixing a
layering violation where the processing-layer `auto_workflow.py` had to
import from the UI layer to reach them). In v2.9.0 the manual per-section
UI (`processing_range.py`, `despike.py`, `baseline.py`, `peak_fit.py`,
`shared.py`) was removed entirely, leaving only `export.py` in the package.
In v2.10.0 the package was retired altogether: its remaining pieces (Quick
Export, Batch Export, View Options, Reset to Raw) moved into `sidebar.py`,
the on-screen plot preview was dropped (the composite figure is still built
internally for PNG/HTML export, just not rendered), and Project save/load
was removed from the UI. In v3.0.0 the orphaned `project_io.py`/`project.py`
modules behind it were deleted too, along with `StylingPreferences` (written
to session state on every launch but never read). See
[CHANGELOG.md](../CHANGELOG.md) for the details.

**v2.11.0**: material presets moved from an Excel file
(`presets/material_presets.xlsx`, parsed by the now-deleted
`src/io/preset_parser.py` (deleted)) to an embedded JSON store
(`data/materials.json`, read/written by `modules/spectra/io/preset_store.py`)
edited through the new Material Presets page. `PresetLibrary` (a wrapper
class whose only job was decoding Excel's `"Material_Mode"` sheet-name
convention back into two fields) was removed in favor of a plain
`dict[(material_name, mode), MaterialPreset]`, since JSON presets already
store those as separate fields — no decode step needed. `PeakTemplate`'s
initial-guess field was also dropped: `modules/spectra/processing/fitting.py` auto-estimates intensity from
the actual data and never reads the preset's value (this was already
documented on `PeakDefinition.intensity`), so the field was pure schema
weight carried over from the Excel format. The app also became multi-page
(`app.py` is now just an `st.navigation()` entrypoint) so the new editor
page could exist alongside the original single-page workflow, and View
Options moved to the very bottom of the sidebar. See
[CHANGELOG.md](../CHANGELOG.md) for the full list.

**v5.0.0**: merged the **Sample Report** and **QC Panel** pages into one
**QC Report** page (`pages/2_QC_Report.py`), and deleted the `.pptx` output
with the PowerPoint COM renderer behind it. The two pages read the same folder,
ran the same scan and the same fit, and each produced half of what an operator
wanted, so running both meant picking the same folder twice and fitting the
same spectra twice. One run now produces seven numbered PNGs and one workbook.
`core/report/summary_figure.py` replaces `pptx.py`, drawing the overview page
and the fitted-spectra grids in matplotlib on the same 13.333 x 7.5 inch
geometry — the proportions were tuned against real samples and worth keeping;
PowerPoint's table styling was a theme default that lived nowhere in this repo
and was not. `raman_quality.py` became `peak_quality.py`, parameterized by a
`QualityFigureSpec` so PL gets the same panels with its own columns, cleaning
rule and marker statistic (PL's lineage uses a median where Raman's uses a
mean, and the figure says which in its suptitle). PL carries exactly one spec
line, the inherited 35 nm FWHM; there is deliberately no PL centre or ratio
spec, because none exists in the analysis this was ported from. The four CSVs
the two pages wrote between them are now sheets in the one workbook — two of
them were near-duplicates of sheets it already carried. `python-pptx` and
`pywin32` are gone from `requirements.txt`; nothing in the app needs Office
installed any more. The paragraph below describes the page this replaced.

**v2.12.0** (superseded by v5.0.0): added the **Sample Report** page — the "future page" the
v2.11.0 nav-section grouping was explicitly left room for. Given a sample
folder with a 9-point OM + Raman + PL grid, it fits every Raman/PL file
against one material's presets (`modules/spectra/processing/sample_batch.py`, a thin
per-file loop over the existing `execute_auto_workflow()` — no new fitting
logic) and assembles a three-slide `.pptx` (`core/report/pptx.py`): an
overview slide (OM grid + fit-summary tables, `modules/spectra/processing/peak_metrics.py`),
then a 3x3 grid of each point's fitted spectrum for Raman and for PL, reusing
`plotter.plot_composite()` (now with a `show_residuals` flag, off for the
small grid cells) rather than building a second plotting code path. The
generated `.pptx` is rendered back to PNG for the on-screen preview and for
saved page images (`core/report/slides.py`) via PowerPoint COM automation —
run in an isolated subprocess rather than in-process, since Streamlit executes
page scripts on a worker thread and COM apartments are thread-local (calling
`win32com` directly from the page crashed with an unrecoverable
`RPC_E_DISCONNECTED`, invisible to ordinary `try/except`). See
Saving writes the numbers next to the deck: `modules/spectra/io/results_excel.py`
builds an `.xlsx` from the same `PeakStat`s the slide tables are built from —
a per-point sheet per technique plus the summary — so the workbook cannot
disagree with the report it ships beside. See
[CHANGELOG.md](../CHANGELOG.md) for the full list, including the still-open gap
around sample folders whose point files are themselves multi-spectrum
hyperspectral files.

---

## Data Model

### SpectrumFile
```python
@dataclass
class SpectrumFile:
    filename: str                            # Original filename
    mode: Literal["Raman", "PL"]             # Spectroscopy mode
    original_data: SpectrumData              # True original (never modified)
    raw_data: SpectrumData                   # After X-range cropping
    processed_data: SpectrumData             # After despike + baseline
    source_dir: Optional[str] = None         # Folder the file was loaded from (v2.7+)
    processing_settings: ProcessingSettings  # Parameters for all algorithms
    peak_table: list = ...                   # User-defined PeakDefinition list
    fit_result: Optional[FitResult] = None
    auto_detected: bool = False              # Mode auto-detected from filename?
    x_range_enabled: bool = False
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    # Workflow status flags
    despike_done: bool = False
    baseline_done: bool = False
    fit_done: bool = False
    fit_stale: bool = False                  # True if preprocessing changed since last fit
    last_preprocessing_hash: Optional[str] = None
```
See `src/models/spectrum.py` for the authoritative field list and validation rules.

### ProcessingSettings
```python
@dataclass
class ProcessingSettings:
    despike_threshold: float = 30.0
    despike_applied: bool = False
    baseline_algorithm: Literal["Polynomial", "ALS"] = "ALS"
    baseline_degree: int = 3
    baseline_lambda: float = 10000.0
    baseline_p: float = 0.001
    baseline_applied: bool = False
    y_shift: float = 0.0                     # Auto Y-shift applied for baseline stability
```

### FitResult
```python
@dataclass
class FitResult:
    success: bool
    fitted_peaks: list[FittedPeak]
    total_fit_curve: np.ndarray
    residuals: np.ndarray
    chi_squared: float
    r_squared: float
    convergence_time: float
    error_message: str = ""
```

---

### MaterialPreset (v4.0.0)

`data/materials.json`, schema v2 — an object with `schema_version` and
`materials`, not the v1 array. One entry is one **material**:

```
MaterialPreset
├── material_name, enabled, description
├── raman   : TechniquePreset | None   # x-range, despike, baseline, peak templates
├── pl      : TechniquePreset | None
└── optical : dict[str, OpticalParams] # keyed by layer: "1L", "2L"
```

Technique is no longer part of a preset's identity; it comes from each file's
filename via `parser.detect_mode_from_filename()`, and callers resolve a block
with `preset.block_for(mode)`. Only `optical` splits by layer, because optical
contrast against SiO₂/Si genuinely differs between a monolayer and a bilayer
while a Raman or PL spectrum does not — the signal says which peaks it has.

Layers are stored as `"1L"`/`"2L"` and rendered through `contrast.layer_word()`.
`OpticalParams` fields default to `None`, meaning "use `contrast.py`'s default",
so the numbers live in one place and a material with no optical block runs the
vendored algorithm untouched.

`preset_store.migrate_legacy()` is a pure function (dicts in, dicts out) that
raises rather than guessing on a half-migrated file. The committed store is
already v2; the loader shim exists only for a local uncommitted v1 edit.

## Key Technical Decisions

### 1. Three-Layer Data Model
- **original_data**: Preserves true original before any processing (Issue #5 fix)
- **raw_data**: After X-range cropping (reset point for despike/baseline)
- **processed_data**: After all processing (used for peak fitting)

**Rationale:** Allows "Reset to Raw" to restore the full original dataset, not just the cropped version.

### 2. Mode-Aware Parameter Bounds — as the *fallback*
- Raman: center tolerance ≥5 cm⁻¹ (or 5% of FWHM, whichever is larger)
- PL: center tolerance ≥30 nm (or 10% of FWHM, whichever is larger)
- Adaptive FWHM bounds: 0.5× to 3× initial guess

**Rationale:** Prevents parameter runaway, improves convergence rate — see [Fitting_Algo.md](Fitting_Algo.md) §8 for measured before/after numbers.

**Since v4.0.0 these apply only to a peak with no opinion of its own.** A peak
carrying `center_tolerance` — every peak built from a preset does — keeps that
tolerance through every recalculation. Before v4.0.0 the mode default
overwrote it unconditionally, so five of WSe₂'s seven peaks were fitted against
±5 cm⁻¹ regardless of what the preset said, and LA railed against that wall.

A tolerance window that clamps to a spectrum narrower than the peak's position
would invert (`center_min > center_max`); lmfit accepts that silently and
**swaps** the bounds, so such a peak is instead pinned to the nearest data edge
and passed as a fixed parameter.

### 3. Selectbox On-Change Callback for Navigation
- Previous approach: button updates state → `st.rerun()` → race condition
- Current approach: button updates state → selectbox callback handles rerun
- No manual `st.rerun()` calls in button handlers

**Rationale:** Fixes a race condition between button handlers and selectbox widget evaluation.

### 4. "None (Skip)" Baseline Option
- Physically accurate for PL emission spectra (the peak IS the signal)
- Avoids fighting baseline algorithms when no real background exists
- Still marks the baseline stage as "done" so the workflow can advance

**Rationale:** When a peak covers most of the spectrum, it violates baseline algorithms' core assumption (peaks = narrow features against a background).

### 5. File-Upload Handlers Must Guard Against Re-Processing
- `st.file_uploader`'s return value stays truthy across reruns until the user removes the file or picks a different one
- A handler that acts on it unconditionally re-runs on every subsequent rerun — at best silently redoing the same work, at worst (if it also calls `st.rerun()`) an infinite rerun loop that prevents the rest of the app from ever rendering
- Fixed (v2.8.0) in the "Load Project" handler by tracking the uploaded file's stable `.file_id` and only processing when it changes

**Rationale:** This is a general Streamlit `file_uploader` gotcha, not specific to project loading — worth knowing before adding another upload-driven feature.

---

## Known Issues & Limitations

1. **No recursive folder scan**: users pick individual files (or multi-select within one dialog session); subtree walking is not supported.
2. **Cloud deployment constraints**: the native tkinter file picker (and the QC Report page's folder picker) only work on local Windows Streamlit installations (not Streamlit Cloud / headless servers / macOS or Linux). The PowerPoint COM dependency is gone as of v5.0.0.
3. **No multi-stage peak fitting**: single-stage Levenberg-Marquardt optimization is still prone to local minima with many closely-spaced peaks — see [Fitting_Algo.md](Fitting_Algo.md) §6.5.
4. ~~**Sample Report doesn't yet support multi-spectrum-per-point sample folders**~~ — **fixed in v3.10.0.** A sample folder's `Raman_N.txt`/`PL_N.txt` is often a map: one X column plus 25 or 100 intensity columns. These used to fail the two-column `parse_spectrum()` and be excluded gracefully rather than fit, so such a sample's report came out empty for that technique. `sample_batch` now uses `parse_spectrum_multi()` and fits every column, grouping them under their grid point. Outlier removal happens *within* a point (see `peak_metrics.aggregate_fit_results`), so within-point scatter is cleaned while position-to-position variation — the thing the report exists to show — is preserved.
5. **WSe₂'s LA peak is cornered against its own preset bounds.** After v4.0.0
   restored the preset's centre tolerance, 130 of TSM260803's 225 fits still sit
   at LA's ±10 lower wall (125.0), and its Voigt FWHM is railed at 36.83 against
   a ceiling of 36.844 — `width_max = 3 × width_fwhm` in both σ and γ. Raising
   `center_tolerance` and `width_fwhm` in the preset raises both ceilings; that
   is a judgement about the material, not about the code. The defect ratio rests
   on this peak.
6. **WSe₂'s "center" template sits outside its own x-range crop** (0 cm⁻¹ against
   `x_min = 6`), so it is pinned to the data's lower edge and fitted with its
   position fixed. Before v4.0.0 this produced an inverted bound that lmfit
   silently swapped, and a reported position belonging to neither the preset nor
   the data. Pinning is honest but it is still a preset that asks for a peak the
   spectrum does not contain — either move the peak or widen the crop.

For resolved issues, see [CHANGELOG.md](../CHANGELOG.md).

---

## Testing

A pytest suite lives under `tests/unit/`, covering the pure processing/IO/model
logic: despiking, all 5 baseline algorithms, Voigt fitting, spectrum parsing,
JSON preset storage, CSV/project export and import, and stale-fit detection.
Run with `pytest` from the repo root (configured via
`pyproject.toml`).

`tests/integration/test_qc_report_flow.py` (v5.0.0, merged from the Sample
Report and QC Panel flow tests) drives the actual QC Report page via
`streamlit.testing.v1.AppTest` — folder scan through Generate through seven
figures and a workbook on disk — closing part of the UI-workflow integration
gap below for that one page. It builds its state dict from the real
`initialize_qc_report_state()` rather than hand-writing one, so a key added to
the page cannot be missed by the fixture.

**Not yet covered**:
- End-to-end UI-workflow integration tests for the Analysis page (X-range → Despike → Baseline → Fit → Export)
- A benchmark suite against real Raman/PL spectra with known ground truth
- The native tkinter folder and Save-As dialogs — they drive a real desktop app, and are stubbed at the seam in the flow test instead

---

## Quick Reference

### File Navigation
- **Dropdown**: Select any file from the loaded list
- **◀ (Previous)**: Navigate to previous file (wraps around)
- **▶ (Next)**: Navigate to next file (wraps around)
- **Counter**: Shows "File X of Y"

### Plot Layer Toggles
- **Raw**: Original data (before any processing)
- **De-spiked**: After spike removal
- **Baseline-Corrected**: After baseline subtraction
- **Fit Total**: Sum of all fitted peaks
- **Peak Components**: Individual peak curves
- **Residuals**: Baseline-corrected data minus Fit Total, in a panel below the spectrum (shared X axis)
- **Peaks on Background**: Adds the subtracted baseline (gray) back under the data, fit and components; residuals stay about zero. Needs the baseline stored by the auto-workflow (v5.13.0+); Quick Export follows it

### Baseline Algorithms
1. **Polynomial**: Fast, good for smooth backgrounds (Raman)
2. **ALS** (Asymmetric Least Squares): Adaptive asymmetry (Raman/PL)
3. **Rolling Ball**: Local baseline, robust to outliers
4. **Spline**: Smooth interpolation with local control
5. **airPLS**: Adaptive iterative reweighted penalized least squares (best for wide peaks)
6. **None (Skip)**: Skip baseline correction (for ultra-wide PL peaks)

### Peak Shape Parameter
- **0.0**: Pure Gaussian (narrow, symmetric)
- **0.5**: Mixed Voigt (most common for Raman)
- **1.0**: Pure Lorentzian (wide, long tails for PL)

---

## Acknowledgments

- **Streamlit**: Web framework for data apps
- **Plotly**: Interactive visualization library
- **lmfit**: Levenberg-Marquardt optimization
- **NumPy/SciPy**: Scientific computing libraries

---

**Last Updated:** 2026-08-21
**Project Version:** v2.12.0
