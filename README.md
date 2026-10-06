# NexAnalyzer v4.5.0

Nexstrom's measurement data analyzer. A desktop web app that turns raw measurement files into
fitted results and shareable reports, driven by per-material presets rather than manual
parameter tuning.

**Modules available today:** Raman & photoluminescence spectra (peak fitting + sample reports),
and optical-microscopy layer QC (contrast segmentation with `nsigma` or a fixed
contrast pair — see `docs/OM_Contrast_Algo.md`). The platform is built so
further techniques plug in alongside it — see [Architecture](#architecture).

---

## Quick start

### Windows: one click

Download **[Install-NexAnalyzer.bat](https://raw.githubusercontent.com/angyulu/NexAnalyzer/main/Install-NexAnalyzer.bat)** (right-click the link, *Save link as...*) and
double-click it. It installs Python and Git if they are missing, downloads the app, puts a
shortcut on your Desktop, and launches it — nothing else to do. Your browser may warn about
keeping a `.bat` file; choose **Keep**.

### Any platform: clone it yourself

**Prerequisites:** Python 3.10+ (tick "Add Python to PATH" on Windows) and
[Git](https://git-scm.com/downloads).

```bash
git clone https://github.com/angyulu/NexAnalyzer.git nexanalyzer
cd nexanalyzer
```

- **Windows**: double-click `start.bat`
- **macOS / Linux**: `./start.sh`

The launcher pulls the latest version, sets up a virtual environment, installs dependencies,
and opens the app at `http://localhost:8501`. It auto-updates on every run, and never blocks:
if you're offline or Git is missing, it launches the version you already have.

> Downloading a ZIP works too, but ZIP copies have no Git history, so they **won't
> auto-update** — clone with Git.

<details>
<summary>Manual setup</summary>

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
streamlit run app.py
```
</details>

---

## Features

### Raman & PL spectra

- **Material presets** drive the whole pipeline in one click: X-range crop → cosmic-ray spike
  removal (modified Z-score) → baseline correction (Polynomial, ALS, Rolling Ball, Spline,
  airPLS, or None) → multi-peak Voigt fitting. Peak positions and processing parameters are
  defined per material and edited in-app on the **Material Presets** page.
- **Batch processing**: run the same preset across every loaded file ("Run All Files").
- **Interactive plots** with auto-managed layers — after a run the plot shows the fit result,
  with raw and intermediate layers available under View Options.
- **Export**: PNG/HTML figures, per-file fit parameters, and a master CSV across all fitted files.

### QC reports

- **One run, seven figures and a workbook** from a sample folder's 9-point OM + Raman + PL
  measurement grid: pick the folder, pick a material, click Generate. One folder scan, one
  fitting pass, every artifact derived from it — so no two figures can describe different fits
  of the same sample.

  | | Figure |
  |---|---|
  | 1 | **Summary** — OM grid, its per-class segmentation table, and the Raman and PL fit-summary tables |
  | 2 | **OM** — layer segmentation across the grid positions |
  | 3 | **OM diagnostic** — the same, plus the green-channel histograms showing where each threshold landed |
  | 4 | **Raman** — the nine fitted Raman spectra |
  | 5 | **Raman stats** — fitted FWHM, peak centres and diagnostic ratios, with the inherited spec lines drawn |
  | 6 | **PL** — the nine fitted PL spectra |
  | 7 | **PL stats** — the same panels for PL |

- **One workbook** carries every number behind them: `Summary`, `Raman`, `PL`, `OM_Stats`,
  `OM_Points`.
- **Auto-detect**: anything without data is skipped rather than drawn empty, and an inventory
  above the Run button says what was found before anything runs.

> **Changed in v5.0.0.** The Sample Report and QC Panel pages are merged into one **QC Report**
> page, and the `.pptx` output is gone along with the PowerPoint COM rendering behind it. No
> part of the app needs Office installed any more. The Plot Explorer was dropped at the same
> time.

---

## Architecture

```
app.py                 Composition root: page config, module session state, routing
pages/                 One file per screen (Spectra, QC Report, Material Presets)
core/                  Platform — knows nothing about peaks or spectra
  io/                  Native dialogs, figure rasterization, output filenames
  report/              Composed report pages, staged progress, report row contracts
  viz/                 Page-width-aware figure rendering
  paths.py             Where app data lives
  version.py           Single source of truth for name + version
modules/               One package per measurement technique
  spectra/             Raman & PL: models, processing, UI, figures
data/                  materials.json (shared, committed) + local preferences
tests/                 unit/ + integration/
docs/                  Architecture and algorithm notes
```

**The dependency rule:** `modules/*` may import `core`; `core` never imports `modules`.
Within a tree, imports are relative; across trees, absolute. Adding a technique means adding a
package under `modules/` and registering its pages in `app.py` — nothing in `core` changes.

---

## Usage

1. **Load data** — **Browse Spectrum Files** in the sidebar, pick one or more `.txt` files.
2. **Configure a material** (first time only) — on **Material Presets**, add a material with its
   processing settings and peak templates.
3. **Select the material** on the **Spectra** page sidebar dropdown.
4. **Run** — **🚀 Run Auto-Workflow** (one file) or **🚀 Run All Files** (batch).
5. **Export** — Quick Export (PNG/HTML/CSV) or Batch Export (master CSV).

### Input file format

Two-column (or multi-Y) `.txt` files:

- Column 1: Wavenumber (cm⁻¹) or wavelength (nm)
- Column 2+: Intensity — one column for standard files, several for multi-Y acquisitions
- Delimiter: tab, comma, semicolon, or whitespace (auto-detected)
- Header: optional — column names, `#key=value` settings, `[Header]` sections or
  begin/end markers above or below the numbers are skipped. The data starts at
  the first row of two or more numbers.
- Encoding: UTF-8, UTF-16 or Windows-1252

---

## Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)** — step-by-step usage
- **[CHANGELOG.md](CHANGELOG.md)** — version history
- **[docs/Summary.md](docs/Summary.md)** — architecture, data model, design decisions
- **[docs/Baseline_Algo.md](docs/Baseline_Algo.md)** — baseline correction theory and analysis
- **[docs/Fitting_Algo.md](docs/Fitting_Algo.md)** — peak fitting theory and implementation history

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # 136 tests
python -m ruff check .
```

## License

Proprietary — © Nexstrom. Internal use only; see [LICENSE](LICENSE).

## Contact

angyu.lu@nexstrom.com
