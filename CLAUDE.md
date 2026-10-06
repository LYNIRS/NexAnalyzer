# CLAUDE.md

NexAnalyzer — Nexstrom's measurement data analyzer. Streamlit app: raw spectra
and microscope frames in, fitted results and QC report figures out.

Architecture, data model and the reasoning behind them live in
[docs/Summary.md](docs/Summary.md); version history in
[CHANGELOG.md](CHANGELOG.md). This file holds only what's easy to get wrong.

## Commands

```bash
pytest                  # 577 tests; pythonpath and testpaths come from pyproject.toml
python -m ruff check .   # F + E9 only — deliberately narrow, so a hit is real breakage
streamlit run app.py     # or start.bat, which also creates venv and pulls updates
```

## Two quantities, two names — never "amplitude"

A peak has a maximum and it has an area. These are different numbers, they differ
by a factor of ~FWHM × 1.064, and confusing them has caused real reporting bugs
here (v3.3.0 shipped a report that disagreed with its own CSV).

| Quantity | Our name | lmfit's name |
| --- | --- | --- |
| The fitted curve's maximum | **intensity** | — |
| The area under the peak | **area** | `amplitude` |

- `peak_metrics.peak_intensity()` / `peak_intensity_and_stderr()` is the only way
  to get an intensity. `PeakDefinition.intensity`, `PeakStat.intensity_*`,
  `intensity_max`, and the `Intensity` column all mean the maximum.
- `FittedPeak.area` / `area_stderr` is the integral. It is named `area` precisely
  *because* lmfit calls it `amplitude`.
- **Every reporting surface shows intensity** — the on-screen Fit Results table,
  both CSVs, the QC Report's summary tables and quality panels, and the
  LA/E2g+A1g and C/LB ratios. They agree, and must keep agreeing.
- The word "amplitude" appears only where lmfit's own parameter is addressed by
  name (`params.add(f"{prefix}amplitude", ...)`). Don't reintroduce it anywhere
  else. Guard tests in
  [tests/unit/test_peak_metrics.py](tests/unit/test_peak_metrics.py) assert that
  `amplitude*` and `height*` stay gone from the models.

The full reasoning is in the
[peak_metrics.py](modules/spectra/processing/peak_metrics.py) module docstring.

## Other invariants

- **`modules/*` may import `core`; `core` never imports `modules`.** See "The one
  rule" in docs/Summary.md.
- **The diagnostic ratios live in one constant.** `peak_metrics.RAMAN_RATIO_PAIRS`
  is `LA/E2g+A1g` and `C/LB`. The quality figure and the QC Report's summary
  table both read it; don't write the pair out again.
- **Spec lines are drawn and never evaluated, and there are only four.**
  `peak_quality` carries Raman's `E2g+A1g` FWHM = 7, `B2g` centre = 308,
  `LA/E2g+A1g` = 0.13, and PL's Exciton/Trion FWHM = 35. They are inherited
  literals from the wafer-comparison scripts, not preset data. **PL has no
  centre spec and no ratio spec** — the ancestor gates its only PL reference
  line on the FWHM panel, and the preset's 755/780 nm are *fit-initialisation*
  guesses. Promoting those to `spec=` would invent a tolerance nobody measured.
- **FWHM means the Voigt FWHM.** Use `fitting.voigt_fwhm(sigma, gamma)`. Reporting
  `2.355 * sigma` — the Gaussian half, ignoring the Lorentzian — was a real bug
  fixed in v3.4.0. It under-reports every width.
- **The preset owns position and width; the data owns intensity.** `fit_voigt_peaks()`
  auto-estimates the initial intensity from the spectrum at fit time, because
  intensity depends on measurement conditions while center/FWHM are material
  properties. `PeakDefinition.intensity` is a required-but-unused placeholder.
  The *tolerance* is part of "owns position": carry it on
  `PeakDefinition.center_tolerance`, never by writing `center_min`/`center_max`
  from a caller. `calculate_auto_bounds()` runs on every fit and is the only
  thing that writes those two, so bounds set anywhere else are overwritten with
  the mode default — which is what cost WSe₂ five of its seven tolerances before
  v4.0.0.
- **A preset is keyed by material alone; technique comes from the filename.**
  `MaterialPreset` holds `raman` / `pl` `TechniquePreset` blocks and an
  `optical` dict of `OpticalParams` keyed by layer. Resolve with
  `preset.block_for(spectrum.mode)`; a block may legitimately be absent.
  `data/materials.json` is v2 (an object with `schema_version`); the v1 array is
  migrated on load by a shim in `preset_store`, and
  [tests/unit/test_preset_migration.py](tests/unit/test_preset_migration.py)
  asserts the committed file is already v2 so that shim stays deletable.
- **Optical layers are stored as `"1L"`/`"2L"`, never as the word.**
  `contrast.layer_word()` is one-way presentation and passes unknown values
  through, so keying persisted JSON by its output would orphan every stored
  block the day the wording changed. Render the word, store the code.
- **Thresholding has two modes, and `nsigma` is still the default.**
  `classify()` cuts at `mode ± nsigma · min(sigma_l, sigma_r)` unless
  `abs_threshold` is set, which replaces it with a fixed green contrast in
  percent — a scalar, or a `(below, above)` pair, since 2L→3L is one layer step
  up while 2L→substrate can be several down. `min(sigma_l, sigma_r)` protects
  against *one* contaminated side; a film with small pervasive domains widens
  both halves equally and then raises its own bar until the domains vanish
  (HADH51: sigmas equal to twelve decimals, 4σ landing at +7.1 % against a ~5 %
  layer step). A fixed contrast is only meaningful when
  `abs_threshold / (sigma_noise / mode)` clears ~2; below that the cut is inside
  the reference distribution and the percentages are noise. See the v4.4.0
  section in [docs/OM_Contrast_Algo.md](docs/OM_Contrast_Algo.md).
  **The pair is the preset's, for every wafer** (v5.6.0). The per-wafer
  *adaptive* derivation that stood here from v4.5.0 to v5.5.0 is gone: it
  placed 28 of its 42 cuts on the 2σ noise floor, which turns a non-detection
  into a number, and its stored results could not be reproduced from the
  frames that produced them. Coverage is a comparison — wafer against spec,
  wafer against wafer — and that needs one ruler.
- **`OpticalParams` fields default to `None` meaning "contrast.py's default".**
  Keep the numbers in `contrast.py` alone, so a material with no optical block
  runs `analyse_frame(**{})` — which is what keeps
  [tests/unit/test_om_contrast.py](tests/unit/test_om_contrast.py)'s locked
  numbers meaningful. `mask_margin` and `ff_divisor` apply to **both** frame
  types: `is_circular()` decides that per image at runtime, so a preset cannot
  address one of them. `ff_divisor` is a divisor, not a sigma — larger means a
  smaller blur.
- **`core/version.py` is the single source of truth for the version.** Bump it and
  add a CHANGELOG entry in the same change as any user-visible behaviour change;
  note explicitly when numbers don't move (renames) versus when they do.

- **The runcard recipe semantics have a vendored spec, and it wins.**
  [docs/reference/render_runcard.py](docs/reference/render_runcard.py) is the
  standalone CLI `modules/runcard/` is derived from — read it before changing
  the clock, the growth window, or how a channel's setpoints become segments,
  and run it beside the app to compare. It is excluded from ruff so it can stay
  byte-identical; do not tidy it. **The growth window is exactly the stretch at
  the peak**, ended by whichever comes first of heater-off and a commanded
  ramp-down. It is *not* a "within 5 °C" band — that is a defect the reference
  records as previously fixed, and which this app reintroduced at v5.1.0 by
  porting an older ancestor. See [docs/reference/README.md](docs/reference/README.md).

- **Runcard matching counts only `Wait` time, and on HA1P01 the filename carries the tag.**
  `processing/runcard_match.py` aligns a card's steps with a log's on a *wait
  clock*; Pumping and Check Status last as long as the chamber takes, so they are
  off the clock on both sides. Do not swap in `growth_window.build_timeline`,
  whose Check Status costs 10 s — right for drawing a recipe, wrong for matching.
  HA1P01's DATALOG is filled by a one-way robocopy (SulfurSync) that also copies
  the tool PC's `runcard_tags.json` over the OneDrive one, so a tag saved only
  in OneDrive is temporary; renames survive because the HA-DataRecord job runs
  with `keepRenamed`. See [docs/Runcard_Matching.md](docs/Runcard_Matching.md).

## Doc trust levels

- [docs/Summary.md](docs/Summary.md) — current.
- [docs/Runcard_Matching.md](docs/Runcard_Matching.md) — current as of v5.7.0; its
  validation numbers were measured on HA1P01 on 2026-09-28.
- [docs/Fitting_Algo.md](docs/Fitting_Algo.md) — §1–§4 verified against the code at
  v3.6.0. §5–§8 are history; their listings quote code as it stood at the time.
- [docs/Baseline_Algo.md](docs/Baseline_Algo.md) — paths repointed at v3.6.0, but the
  algorithm content has not been re-verified since v2.9.0. Check against the code
  before relying on it.
- [docs/OM_Contrast_Algo.md](docs/OM_Contrast_Algo.md) — the spec for
  `modules/optical/processing/contrast.py`, vendored verbatim from the research
  script at v3.10.0. The code was checked against it and against that script's
  own output; keep them in step, and read it before touching the segmentation.
  Its tuning figures are still the defaults, but since v4.0.0 they are
  overridable per material and layer from the preset, and the doc's
  "circular"-labelled rows (σ_ff, margin) are applied to both frame types.
  Its "Second divergence" section is **history**: the adaptive per-wafer pair
  it specifies was retired in v5.5.0 and the section is marked as such.
  Its final section documents v4.4.0's `abs_threshold`, which is a deliberate
  divergence from the vendored algorithm rather than a restatement of it.
