# NexAnalyzer User Guide

A step-by-step guide for installing and running NexAnalyzer on **Windows** and **macOS**.

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Easiest Setup: One-Click Installer (Windows)](#easiest-setup-one-click-installer-windows)
3. [Step 1: Install Python](#step-1-install-python)
4. [Step 2: Install Git](#step-2-install-git)
5. [Step 3: Download NexAnalyzer](#step-3-download-nexanalyzer)
6. [Step 4: Run NexAnalyzer](#step-4-run-nexanalyzer)
7. [Step 5: Create a Desktop Shortcut (Optional)](#step-5-create-a-desktop-shortcut-optional)
8. [How Updates Work](#how-updates-work)
9. [Quick Start: Auto-Workflow with Material Presets](#quick-start-auto-workflow-with-material-presets)
10. [QC Report: Seven Figures and a Workbook from a Sample Folder](#qc-report-seven-figures-and-a-workbook-from-a-sample-folder)
11. [File Format](#file-format)
12. [Troubleshooting](#troubleshooting)

---

## System Requirements

- **OS**: Windows 10/11 or macOS 12+
- **Python**: 3.10 or higher
- **Git**: required for auto-updates — the app pulls the latest version on every launch
- **GitHub account**: not needed — the repository is public, so anyone can clone it
- **Disk space**: ~500 MB (including Python and dependencies)
- **Browser**: Any modern browser (Chrome, Edge, Firefox, Safari)

---

## Easiest Setup: One-Click Installer (Windows)

**On Windows, this replaces Steps 1 to 3 entirely.**

1. Download **[Install-NexAnalyzer.bat](https://raw.githubusercontent.com/angyulu/NexAnalyzer/main/Install-NexAnalyzer.bat)** — right-click that link and choose
   **Save link as...**
2. Your browser will probably warn about keeping a `.bat` file. Choose **Keep**.
3. **Double-click the file you just downloaded.**

That is the whole setup. It installs Python and Git if they are missing, downloads NexAnalyzer
to `C:\Users\YourName\nexanalyzer`, puts a **NexAnalyzer** shortcut on your Desktop, and
starts the app. The first run takes a few minutes while it downloads the Python packages the
app needs — leave the window alone until your browser opens by itself.

**After that, just double-click NexAnalyzer on your Desktop.** It updates itself every launch.

> - If Windows asks permission while Git or Python installs, allow it.
> - If the window says it cannot see the newly installed program yet, close it and
>   double-click the file again — it carries on from where it stopped.
> - If it refuses because the folder is inside OneDrive or too deep in the filesystem, that is
>   deliberate: both break the app. Let it use the default location.

Steps 1 to 5 below are the manual route — use them on macOS, or on Windows if the installer
cannot run on your machine.

---

## Step 1: Install Python

### Windows

1. Go to **https://www.python.org/downloads/**
2. Click **"Download Python 3.x.x"** (the latest version)
3. Run the installer
4. **IMPORTANT**: Check the box **"Add Python to PATH"** at the bottom of the installer
5. Click **"Install Now"**
6. Verify: Open **Command Prompt** (search "cmd" in Start menu) and type:
   ```
   python --version
   ```
   You should see something like `Python 3.12.x`.

### macOS

**Option A — Official installer:**
1. Go to **https://www.python.org/downloads/**
2. Download the macOS installer
3. Run the `.pkg` file and follow the prompts

**Option B — Homebrew (if you have Homebrew installed):**
```bash
brew install python3
```

**Verify**: Open **Terminal** and type:
```bash
python3 --version
```
You should see something like `Python 3.12.x`.

---

## Step 2: Install Git

NexAnalyzer keeps itself up to date by pulling the latest version from GitHub every time you
launch it, and that needs Git. Without Git the app still runs — it just never updates.

### Windows

1. Go to **https://git-scm.com/downloads** and download **Git for Windows**
2. Run the installer and accept all the defaults — none of them need changing
3. Verify: open a **new** Command Prompt (an already-open one won't see the change) and type:
   ```
   git --version
   ```
   You should see something like `git version 2.47.0`.

### macOS

Git ships with the Xcode command line tools. Open **Terminal** and type:
```bash
git --version
```
If it's missing, macOS will offer to install it — accept. Or with Homebrew: `brew install git`.

---

## Step 3: Download NexAnalyzer

> **Clone it with Git — don't download the ZIP.** A ZIP copy has no Git history, so it can
> never auto-update: you would be frozen on whatever version you downloaded.

You don't need a GitHub account, and there is nothing to sign up for — the repository is
public, so the command below just works.

### Windows

Open **Command Prompt** and run:

```
cd %USERPROFILE%
git clone https://github.com/angyulu/NexAnalyzer.git nexanalyzer
```

> **Don't clone into OneDrive, Dropbox, or any synced folder.** The app builds a `venv` folder
> holding thousands of files; sync clients fight with it, corrupt it, and slow every launch.
> `C:\Users\YourName\nexanalyzer` (as above) is a good spot.

The download starts immediately and takes a few seconds — there is no login step.

You will end up with `C:\Users\YourName\nexanalyzer` containing:

```
nexanalyzer/
├── app.py
├── start.bat          ← Windows launcher
├── start.sh           ← Mac/Linux launcher
├── requirements.txt
├── core/              ← platform code
├── modules/           ← analysis modules (spectra)
├── data/              ← material presets
└── ...
```

### macOS

```bash
cd ~
git clone https://github.com/angyulu/NexAnalyzer.git nexanalyzer
```

That's it — no login step.

---

## Step 4: Run NexAnalyzer

### Windows

1. Open the `nexanalyzer` folder in File Explorer
2. **Double-click `start.bat`**
3. A terminal window will appear. On **every** run it checks GitHub for a newer version
   (see [How Updates Work](#how-updates-work)). On the **first** run it also:
   - Creates a virtual environment (`venv` folder)
   - Installs all required packages
   - This may take 1-2 minutes
4. Your browser will automatically open to **http://localhost:8501**
5. To stop the app, press `Ctrl+C` in the terminal window, or simply close it

### macOS

1. Open **Terminal**
2. Navigate to the nexanalyzer folder:
   ```bash
   cd ~/nexanalyzer
   ```
3. Make the script executable (first time only):
   ```bash
   chmod +x start.sh
   ```
4. Run:
   ```bash
   ./start.sh
   ```
5. Every run checks GitHub for a newer version (see [How Updates Work](#how-updates-work));
   on the first run it also creates a virtual environment and installs packages
6. Your browser will open to **http://localhost:8501**
7. To stop the app, press `Ctrl+C` in the terminal

---

## Step 5: Create a Desktop Shortcut (Optional)

### Windows

1. Right-click `start.bat` → **Create shortcut**
2. Drag the shortcut to your Desktop
3. (Optional) Right-click the shortcut → **Properties** → **Change Icon** to customize

### macOS

1. Open **Automator** (search in Spotlight)
2. Choose **Application**
3. Add a **Run Shell Script** action with:
   ```bash
   cd ~/nexanalyzer && ./start.sh
   ```
4. Save as `NexAnalyzer` to your Desktop or Applications folder

**Alternatively**, create a simple alias in Terminal:
```bash
echo 'alias nexanalyzer="cd ~/nexanalyzer && ./start.sh"' >> ~/.zshrc
source ~/.zshrc
```
Then just type `nexanalyzer` in any terminal to launch.

---

## How Updates Work

**You don't need to do anything to stay current.** Every time you launch NexAnalyzer, the
launcher runs three steps before the app starts:

1. **Pull the latest version** from GitHub
2. **Install any new dependencies** listed in `requirements.txt`
3. **Start the app**

So the entire update procedure is: **close the app, launch it again.** No re-downloading, no
copying folders, no reinstalling.

Worth knowing:

- **Updating never blocks you.** If you are offline, GitHub is unreachable, or Git isn't
  installed, the launcher prints a warning and starts the version you already have.
- **Watch the terminal for one line.** `Up to date with the latest version.` means the update
  succeeded. Anything beginning with `[WARN]` or `[SKIP]` means you are running an **older**
  version — read the message; Troubleshooting below covers each one.
- **Your work is never overwritten.** The pull is fast-forward-only, so the launcher will never
  create a merge commit or discard local changes. If it cannot update cleanly, it stops.
- **Editing presets can block updates.** Changing material presets in the app rewrites
  `data/materials.json`, which is a shared, tracked file. Git then refuses to overwrite your
  edit, and updates stay blocked until you resolve it — see
  [Updates are blocked by local changes](#updates-are-blocked-by-local-changes).
- **Check which version you are on** at the bottom of the app's sidebar
  (e.g. `NexAnalyzer v3.0.0`).

---

## Quick Start: Auto-Workflow with Material Presets

NexAnalyzer processes spectra using the **auto-workflow** with a material preset — this is the only way to process a spectrum (as of v2.9.0 there's no manual step-by-step UI). It runs the entire pipeline (X-range crop, de-spiking, baseline correction, peak fitting) in a single click.

### 1. Configure a Material Preset

Material presets are created and edited directly in the app (as of v2.11.0 — no Excel file needed).

1. Open the **Material Presets** page (top of the sidebar navigation)
2. Expand **"➕ New material"** and fill in:
   - **Material name** and **Mode** (Raman or PL)
   - **Despike threshold**, **Baseline algorithm** (Polynomial / ALS / None (Skip)) and its parameters
   - Optionally: **X-range** limits, **exclusion ranges** (e.g. `240-270; 400-420`), and **notes**
   - **Peak templates**: add one row per peak (label, center, ± tolerance, FWHM, shape, color) — use the table's `+` button to add more rows
3. Click **"➕ Create Material"**

To change a material later, expand it in the list on the same page, edit its fields, and click **"💾 Save"** (or **"🗑️ Delete"** to remove it).

### 2. Load Your Spectrum Files

1. In the sidebar under **Load Spectra**, click **"Browse Spectrum Files"**
2. A native file dialog opens, filtered to `.txt` files (use the dropdown to switch to "All files" if needed)
3. Pick one file, or Ctrl-click (Cmd-click on macOS) to pick multiple files at once
4. Each file is parsed and added to the loaded-files list; files with multiple Y columns appear as separate entries named `file__1.txt`, `file__2.txt`, etc.
5. The mode (Raman/PL) is auto-detected from filename patterns (e.g., `RM*` → Raman, `PL*` → PL)
6. Re-picking a file that's already loaded is skipped silently (with a count); the dialog opens at the last-picked directory on subsequent uses

### 3. Select a Material

1. Back on the **Spectra** page, in the sidebar under **Material Presets**, use the **material dropdown** to select the target material (all configured materials are listed, both Raman and PL)
2. A summary of the preset settings (baseline algorithm, peak count, notes) appears below

### 4. Run the Workflow

**Single file:**
- Click **"Run Auto-Workflow"** to process the currently selected spectrum
- The pipeline executes: X-range crop → De-spike → Baseline correction → Peak fitting
- Results appear in the plot and fit results table below it

**All files (batch):**
- If multiple files are loaded, a **"Run All Files"** button appears
- Click it to process every loaded file using the selected preset
- A progress bar shows the batch status
- After completion, a summary shows how many files succeeded

### 5. Review and Export

- Use the **file navigation arrows** (above the plot) to browse through processed files
- Check the **fit results table** below each plot for peak parameters and R² values
- In the sidebar, use **Quick Export** to download PNG/HTML/CSV, or **Batch Export** to download a master CSV across all fitted files

---

## QC Report: Seven Figures and a Workbook from a Sample Folder

The **QC Report** page (second in the sidebar navigation) turns a sample folder's 9-point
OM + Raman + PL measurement grid into seven figures and one spreadsheet, with no manual
plotting.

> **Changed in v5.0.0.** This page replaces the old **Sample Report** and **QC Panel** pages,
> which read the same folder and each did half the job. There is no `.pptx` any more, and
> nothing on this page needs Microsoft Office installed.

### 1. Name the folder after the wafer

**The folder's own name becomes the wafer ID on the report.** It is used verbatim in the header
of the figures, and as the default filename (`<foldername>_QC_Report.png`). There is no field in
the app to type or correct it, and nothing validates it — so a folder called `New folder (2)`
produces a report titled `New folder (2)`.

Name the folder after the wafer, for example `VABA52`. To fix a wrong title, rename the folder
and generate the report again.

### 2. Put the measurement files in it

Your sample folder should contain, for each of the 9 grid points:

- A Raman spectrum named like `RM_1.txt` … `RM_9.txt` (also accepts `Raman_`, `rm_`, `raman_`,
  and either `-` or `_` before the number, e.g. `RM-8.txt`)
- A PL spectrum named like `PL_1.txt` … `PL_9.txt` (or `pl_`)
- An OM image named like `100x_1.bmp` … `100x_9.bmp` (any magnification prefix works, e.g.
  `10x_`; `.bmp`/`.png`/`.jpg`/`.tif` all accepted)

A file may hold one spectrum or many — a map file with one X column and 25 or 100 intensity
columns is fitted column by column, and all of them are grouped under that grid point.

Any other file in the folder (old exports, project files, etc.) is simply listed as "ignored" —
it doesn't need to be removed.

### 3. Set up the run

1. Click **"Select Sample Folder"** and pick the folder.
2. If OM images exist at more than one magnification, pick which one to use in the **3x3 grid**.
3. Pick the **Layer** being measured — `1L` or `2L`. The algorithm cannot infer this, and a
   wrong choice mislabels every class by one layer *with no visible symptom in the figure*, as
   well as selecting a different tuning block.
4. Pick the **Material** — one dropdown selects the Raman preset, the PL preset and the optical
   tuning for that material. Your last choice is remembered next time.

### 4. Check the inventory, then generate

Above the Run button the page says what it found, for example:

```
Found: 9 OM (100x) · 9 Raman · 0 PL
  ↳ No PL files in this folder — the PL figures and sheet will be skipped.
    Check the file naming if you expected some.
```

**Read this line before clicking Run.** A folder whose PL files are named `PL1.txt` instead of
`PL_1.txt` scans as a sample with no PL, and once the report is built that looks exactly like a
sample that genuinely has none. The page distinguishes the two causes, because they are fixed in
different places:

| What it says | What to do |
|---|---|
| *No PL files in this folder* | Check the file naming in the folder |
| *`<Material>` defines no PL peaks* | Add a PL block on the **Material Presets** page |

Then click **"🚀 Generate QC Report"**. Segmenting nine frames is the slow part — around half a
minute — and the progress bar is weighted so it reflects that rather than racing through and
stopping.

### 5. What you get

| | Figure | Shows |
|---|---|---|
| 1 | **Summary** | The overview page: the 3x3 grid of raw OM frames with the per-class segmentation table beneath it, and the Raman and PL fit-summary tables beside it |
| 2 | **OM** | Layer segmentation across the nine positions — original and overlay per frame |
| 3 | **OM diagnostic** | The same, plus each frame's green-channel histogram with its mode and both thresholds marked |
| 4 | **Raman** | The nine fitted Raman spectra, three per column, under one shared legend |
| 5 | **Raman stats** | Fitted FWHM, peak centres and the diagnostic ratios across the positions, with the inherited spec lines drawn |
| 6 | **PL** | The nine fitted PL spectra |
| 7 | **PL stats** | The same panels for PL |

On screen, the summary page and both OM figures are shown inline; the fitted spectra and the
stats panels sit behind expanders — they are what you open when something looks wrong.

> **The grey spec lines are drawn, never judged.** The report reports; it does not pass or fail
> a wafer. Raman carries three (E₂g+A₁g FWHM = 7, B₂g centre = 308, LA/E₂g+A₁g = 0.13) and PL
> carries one (Exciton and Trion FWHM = 35 nm). PL has no centre or ratio spec — those numbers
> do not exist in the analysis this was ported from.

### 6. Save

Click **"💾 Save QC Report As..."** and choose a location. One dialog, one name, and everything
lands beside it sharing that stem:

- the seven figures as `_1_Summary.png` through `_7_PL_stats.png` — **numbered**, because
  alphabetical sort would put the summary last and interleave the two techniques
- one `.xlsx` holding every number behind them:

| Sheet | Holds |
|---|---|
| `Summary` | Each technique's mean ± std table, the intensity ratios, and any point that failed to fit |
| `Raman` / `PL` | One row per fitted peak per point — centre, intensity, FWHM and their stderrs, R², χ², and the file each row came from |
| `OM_Stats` | Per-class coverage and contrast, with how many frames were noise-limited |
| `OM_Points` | One row per frame: its coverages, contrasts, and the histogram landmarks the thresholds were placed from |

Anything that had no data writes no file — a Raman-only sample produces five figures, not seven
with two blank ones.

---

## File Format

NexAnalyzer accepts **two-column .txt files**:

```
120.5	1523.2
121.0	1540.8
121.5	1535.1
...
```

- **Column 1**: Wavenumber (cm⁻¹) or Wavelength (nm)
- **Column 2**: Intensity
- **Delimiter**: Tab, comma, semicolon or spaces
- **Header**: optional. Lines above the numbers (column names, acquisition
  settings, section markers) and below them (end markers) are skipped:

```
Raman shift (cm-1)	Intensity (a.u.)
120.5	1523.2
121.0	1540.8
...
```

A header line that is itself two numbers (for example `532	600`) can't be told
apart from data, and will be read as the first point.

---

## Troubleshooting

### "Python is not installed or not in PATH"
- **Windows**: Reinstall Python and make sure to check **"Add Python to PATH"**. Then restart your terminal.
- **Mac**: Make sure `python3 --version` works in Terminal. If not, reinstall from python.org.

### "pip install fails" or network errors
- Check your internet connection — packages are downloaded from the internet on first run
- If behind a corporate proxy, ask your IT team for proxy settings
- Try running manually:
  ```
  pip install -r requirements.txt
  ```

### The browser doesn't open automatically
- Manually open your browser and go to **http://localhost:8501**

### "Address already in use" error
- Another instance of NexAnalyzer (or another Streamlit app) is already running
- Close the other terminal window, or run on a different port:
  ```
  streamlit run app.py --server.port 8502
  ```

### App is slow or unresponsive
- Large files or many loaded spectra can use significant memory
- Try loading fewer files at once
- Close other browser tabs to free memory

### Updates are blocked by local changes

The launcher prints `[WARN] Update BLOCKED by your local changes` and lists the files. This
almost always means you edited material presets in the app, which rewrites the shared
`data/materials.json`. **You will keep launching an old version until this is resolved.**

Set your changes aside, then update:
```
git stash
```
Relaunch the app. To bring your changes back afterwards:
```
git stash pop
```

If you don't need your local edits and just want the official version:
```
git checkout -- data/materials.json
```

> If your preset changes *should* be shared with the team, send them to Angyu rather than
> keeping them local — presets are committed to the repo so everyone gets them on update.

### "[SKIP] Git not found - cannot auto-update"

Git isn't installed, or isn't on your PATH. Redo [Step 2](#step-2-install-git), then open a
**new** terminal — an already-open one won't pick up the change.

### "[SKIP] Not a git checkout - cannot auto-update"

You downloaded a ZIP instead of cloning, so there is no Git history to update from. Copy out any
files you care about, delete the folder, and redo [Step 3](#step-3-download-nexanalyzer) using
`git clone`.

### "Repository not found", or GitHub asks you to sign in

The repository is public, so you should never be asked to log in. A sign-in prompt or a
"not found" error almost always means the URL is wrong — GitHub reports a mistyped URL as
"not found" rather than admitting the repo doesn't exist. Check it character for character:

```
https://github.com/angyulu/NexAnalyzer.git
```

If the URL is right and it still fails, the repository may have been switched to private —
ask Angyu for access.

### "[WARN] Could not update - offline, or no access to the repo"

Harmless if you are off the network — the app starts on your current version. If you *are*
online, check that https://github.com/angyulu/NexAnalyzer opens in your browser. A corporate
proxy or firewall blocking GitHub is the other usual cause; ask IT for the proxy settings.

### Forcing an update by hand

Normally unnecessary, since relaunching the app updates it. But from inside the `nexanalyzer`
folder you can always run:
```
git pull
```
