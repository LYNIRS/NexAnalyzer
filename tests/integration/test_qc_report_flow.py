"""
Integration test for the QC Report page (pages/2_QC_Report.py).

Drives the real Streamlit page through streamlit.testing.v1.AppTest, which is
the only way to cover the batch workflow's st.session_state coupling and the
page's own button wiring. Merged at v5.0.0 from the Sample Report and QC Panel
flow tests, whose pages this one replaces.

Uses the repo's real Silicon (Raman-only) preset from data/materials.json
rather than external sample data, so the test is self-contained. The
WSe2-specific panel contents are covered by tests/unit/test_peak_quality.py and
the table contents by tests/unit/test_qc_report_tables.py; what this covers is
that a folder goes in and seven figures plus a workbook come out.

The Raman fixtures deliberately carry three spectra per file. That is the
multi-spectrum shape real sample folders use — TSM260803's files hold 25 each —
and until v3.10.0 the report path parsed only the first two columns and
silently excluded every such file, so the Raman half produced nothing at all.
"""

from io import BytesIO

import numpy as np
import pytest
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

import core.io.om_calibration as om_calibration_module
from core.paths import PROJECT_ROOT
from modules.optical.ui import qc_report_state as state_module
from modules.spectra.processing.sample_scanner import scan_sample_folder

PAGE = str(PROJECT_ROOT / "pages" / "2_QC_Report.py")
SUB_SPECTRA = 3
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _initial_state(**overrides):
    """The page's real initial dict, so a key added later cannot be missed here.

    Hand-writing the dict is what let the old flow tests drift from the state
    module: a new key would be absent from the fixture and the page would
    KeyError only in the test.
    """
    store: dict = {}
    real_st = state_module.st
    state_module.st = type("_St", (), {"session_state": store})()
    try:
        state_module.initialize_qc_report_state()
    finally:
        state_module.st = real_st
    fresh = store["qc_report"]
    # Domain-size measurement is opt-in and, when unset, resolves from this
    # machine's real report_settings.json -- defaulting it here keeps every
    # test below deterministic and exercising the feature as it did before it
    # became opt-in. A test of the opt-out path overrides it explicitly.
    fresh["measure_domain_size"] = True
    # Same reasoning: the size-population split also resolves from the real
    # report_settings.json when unset. False matches its own off-by-default,
    # and a test of the split path overrides it explicitly.
    fresh["split_size_populations"] = False
    fresh.update(overrides)
    return fresh


def _write_multi_spectrum_raman(path, seed):
    """One X column plus SUB_SPECTRA intensity columns, tab separated.

    A single peak near 520 cm-1, matching materials.json's Silicon (Raman)
    preset, over enough points that width_min's spectral-resolution term stays
    below the peak's FWHM.
    """
    x = np.linspace(0, 1000, 2000)
    sigma = 8.0 / 2.355
    rng = np.random.default_rng(seed)
    columns = [x]
    for offset in range(SUB_SPECTRA):
        peak = 1000.0 + 40.0 * offset
        columns.append(
            500.0
            + peak * np.exp(-0.5 * ((x - 520.0) / sigma) ** 2)
            + rng.normal(0, 5, size=x.size)
        )
    with open(path, "w") as f:
        for row in zip(*columns):
            f.write("\t".join(f"{v:.4f}" for v in row) + "\n")


def _write_om_frame(path, seed):
    """A frame with gentle vignetting, noise, and one brighter patch, so the
    segmentation has something to find rather than an empty class."""
    from PIL import Image

    H, W = 150, 200
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(yy - H / 2, xx - W / 2) / np.hypot(H / 2, W / 2)
    green = 190.0 - 8.0 * r**2 + rng.normal(0, 1.2, size=(H, W))
    green[40:60, 60:110] += 9.0
    green[100:112, 130:170] -= 9.0
    rgb = np.stack([green * 0.95, green, green * 1.02], axis=-1)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)


def _seeded_app(tmp_path, points=range(1, 10), images=True):
    for point in points:
        _write_multi_spectrum_raman(tmp_path / f"RM_{point}.txt", seed=point)
        if images:
            _write_om_frame(tmp_path / f"50x_{point}.png", seed=100 + point)

    scan = scan_sample_folder(str(tmp_path))
    at = AppTest.from_file(PAGE, default_timeout=180)
    at.session_state["qc_report"] = _initial_state(
        folder=str(tmp_path),
        scan=scan,
        magnification="50x" if images else None,
        material="Silicon",
        reference_layer="2L",
    )
    return at, scan


def _generated(tmp_path, **kwargs):
    at, scan = _seeded_app(tmp_path, **kwargs)
    at.run()
    next(b for b in at.button if "Generate QC Report" in b.label).click().run()
    return at, scan


class TestPageFlow:
    def test_empty_state_renders_without_error(self):
        """No folder selected yet — most of the page is skipped, but the top
        section (title, folder-pick button) must still render cleanly."""
        at = AppTest.from_file(PAGE, default_timeout=60).run()

        assert not at.exception

    def test_one_run_produces_every_figure_it_can(self, tmp_path):
        at, _scan = _generated(tmp_path)

        assert not at.exception, [e.value for e in at.exception]
        state = at.session_state["qc_report"]
        for key in ("summary_png", "om_png", "om_diagnostic_png",
                    "raman_grid_png", "raman_stats_png"):
            assert state[key][:8] == _PNG_MAGIC, key
        # Silicon defines no PL block, so PL is skipped rather than drawn empty.
        assert state["pl_grid_png"] is None
        assert state["pl_stats_png"] is None

    def test_the_workbook_is_built(self, tmp_path):
        at, _scan = _generated(tmp_path)

        assert at.session_state["qc_report"]["xlsx_bytes"][:2] == b"PK"

    def test_every_sub_spectrum_is_fitted_not_just_the_first(self, tmp_path):
        """The defect this guards: parse_spectrum() read a multi-column file as
        two columns and the rest were dropped on the floor."""
        at, _scan = _generated(tmp_path)

        stats = at.session_state["qc_report"]["raman_stats"]

        assert stats, "no Raman peaks were aggregated"
        assert stats[0].n == 9 * SUB_SPECTRA

    def test_one_frame_per_grid_position_is_segmented(self, tmp_path):
        at, _scan = _generated(tmp_path)

        frames = at.session_state["qc_report"]["frames"]

        assert [f.point for f in frames] == list(range(1, 10))
        assert all(f.labels == ("Below 2L", "Bilayer", "Above 2L") for f in frames)

    def test_coverage_percentages_are_a_partition(self, tmp_path):
        at, _scan = _generated(tmp_path)

        for frame in at.session_state["qc_report"]["frames"]:
            assert sum(frame.percentages) == pytest.approx(100.0, abs=0.01)

    def test_domain_size_measurement_can_be_switched_off(self, tmp_path):
        """The opt-out path: everything else still renders, but no domain-size
        figure or table is produced and nothing measures the frames a second
        time."""
        at, _scan = _seeded_app(tmp_path)
        at.session_state["qc_report"]["measure_domain_size"] = False
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        state = at.session_state["qc_report"]
        assert state["om_png"][:8] == _PNG_MAGIC
        assert state["domain_grid_png"] is None
        assert state["domain_results"] is None
        assert state["domain_stat"] is None
        assert state["summary_png"][:8] == _PNG_MAGIC

    def test_domain_size_calibration_matches_regardless_of_magnification_case(
        self, tmp_path, monkeypatch
    ):
        """The defect this guards: a folder named "50X_1.png" (a common
        capitalisation) has magnification "50X", and a naive dict lookup
        against a calibration entry saved as "50x" silently missed, reporting
        pixels instead of microns with no error at all."""
        for point in range(1, 10):
            _write_multi_spectrum_raman(tmp_path / f"RM_{point}.txt", seed=point)
            _write_om_frame(tmp_path / f"50X_{point}.png", seed=100 + point)
        monkeypatch.setattr(
            om_calibration_module, "load_om_calibration", lambda path=None: {"50x": 0.1}
        )

        scan = scan_sample_folder(str(tmp_path))
        assert scan.magnifications() == ["50X"]

        at = AppTest.from_file(PAGE, default_timeout=180)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan, magnification="50X",
            material="Silicon", reference_layer="2L",
        )
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        stat = at.session_state["qc_report"]["domain_stat"]
        assert stat is not None
        assert stat.calibrated, "case mismatch left the sample reported in pixels"
        assert stat.unit == "µm"

    def test_domain_target_selects_which_class_is_measured(self, tmp_path):
        """Switching away from the "above" default must actually change what
        gets measured, not just get accepted and ignored."""
        at, _scan = _seeded_app(tmp_path)
        at.session_state["qc_report"]["domain_target"] = "below"
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        state = at.session_state["qc_report"]
        assert state["domain_results"] is not None
        assert all(r.target == "below" for r in state["domain_results"])

    def test_split_size_populations_defaults_off(self, tmp_path):
        """Off by default -- the split has fired on frames with no real
        second population, so an operator who hasn't asked for it should see
        the plain pooled measurement even on a sample whose domains would
        otherwise clear the ratio."""
        at, _scan = _generated(tmp_path)

        stat = at.session_state["qc_report"]["domain_stat"]
        assert stat is not None
        assert stat.populations is None
        assert all(r.population_is_large is None for r in at.session_state["qc_report"]["domain_results"])

    def test_split_size_populations_when_enabled_reaches_the_stat(self, tmp_path):
        """Ticking it must actually reach measure_domains()/domain_size_stat(),
        not just get accepted and ignored."""
        at, _scan = _seeded_app(tmp_path)
        at.session_state["qc_report"]["split_size_populations"] = True
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        stat = at.session_state["qc_report"]["domain_stat"]
        assert stat is not None
        assert stat.populations is not None

    def test_low_coverage_relabels_the_reference_class_as_substrate(self, tmp_path):
        """Ticking it must actually reach the segmentation's own labels, not
        just get accepted and ignored -- the whole point is that a
        sparse-nucleation sample's majority class should read "Substrate",
        not the named reference layer."""
        at, _scan = _seeded_app(tmp_path)
        at.session_state["qc_report"]["low_coverage"] = True
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        state = at.session_state["qc_report"]
        assert state["frames"], "no frames segmented"
        for frame in state["frames"]:
            assert frame.labels == ("Below Substrate", "Substrate", "Bilayer")

    def test_a_sample_with_no_optical_images_still_fits_its_spectra(self, tmp_path):
        at, _scan = _generated(tmp_path, images=False)

        assert not at.exception, [e.value for e in at.exception]
        state = at.session_state["qc_report"]
        assert state["om_png"] is None, "no images means no OM figure, not an empty one"
        assert state["raman_stats_png"] is not None
        # The summary page still renders — its image grid is placeholders.
        assert state["summary_png"][:8] == _PNG_MAGIC

    def test_a_folder_of_images_with_no_spectra_at_all_still_reports(self, tmp_path):
        """The one run still calls `run_sample_batch` with both presets None.
        Nothing else in the suite takes that path, and it is the shape an
        operator hits when the OM frames arrive before the spectrometer run."""
        for point in range(1, 10):
            _write_om_frame(tmp_path / f"50x_{point}.png", seed=100 + point)
        at = AppTest.from_file(PAGE, default_timeout=300)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan_sample_folder(str(tmp_path)),
            magnification="50x", material="Silicon", reference_layer="2L",
        )
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        state = at.session_state["qc_report"]
        assert state["summary_png"][:8] == _PNG_MAGIC
        assert state["om_png"][:8] == _PNG_MAGIC
        assert state["raman_grid_png"] is None
        assert state["raman_stats_png"] is None
        # The workbook still ships: the optical sheets are a complete record.
        assert state["xlsx_bytes"][:2] == b"PK"
        assert at.status[0].state == "complete"

    def test_one_unreadable_frame_does_not_discard_the_whole_report(self, tmp_path):
        """Before the merge a bad image only cost the QC Panel's two figures;
        the Sample Report produced the Raman half independently. With one run
        producing everything, an unguarded `analyse_frame` would unwind past
        every later stage and lose all seven figures and the workbook to one
        truncated file."""
        at, _scan = _seeded_app(tmp_path)
        (tmp_path / "50x_2.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really a png")
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        state = at.session_state["qc_report"]
        assert [f.point for f in state["frames"]] == [1, 3, 4, 5, 6, 7, 8, 9]
        assert state["om_png"][:8] == _PNG_MAGIC
        # The half that has nothing to do with the bad frame survives intact.
        assert state["raman_stats_png"][:8] == _PNG_MAGIC
        assert state["xlsx_bytes"][:2] == b"PK"
        assert any("point 2" in w.value for w in at.warning)

    def test_changing_the_magnification_drops_the_other_groups_results(self, tmp_path):
        """The summary page's grid caption names the magnification, so keeping
        50x images under a heading that says 100x is a report that lies about
        its own contents."""
        for point in range(1, 10):
            _write_multi_spectrum_raman(tmp_path / f"RM_{point}.txt", seed=point)
            _write_om_frame(tmp_path / f"50x_{point}.png", seed=100 + point)
            _write_om_frame(tmp_path / f"100x_{point}.png", seed=200 + point)
        at = AppTest.from_file(PAGE, default_timeout=300)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan_sample_folder(str(tmp_path)),
            magnification="100x", material="Silicon", reference_layer="2L",
        )
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()
        assert at.session_state["qc_report"]["om_png"] is not None

        next(s for s in at.selectbox if "Magnification" in s.label).select("50x").run()

        state = at.session_state["qc_report"]
        assert state["magnification"] == "50x"
        assert state["om_png"] is None
        assert state["frames"] is None
        assert state["summary_png"] is None
        assert state["xlsx_bytes"] is None

    def test_a_run_that_loses_everything_at_the_gate_still_stamps_its_fingerprint(
        self, tmp_path
    ):
        """That run is the one most likely to be followed by a preset fix, so
        it is the one that most needs the staleness check to fire afterwards.
        Keying the check on the stats figure meant it never did: the figure is
        absent precisely because the gate dropped everything."""
        rng = np.random.default_rng(0)
        for point in range(1, 10):
            x = np.linspace(0, 1000, 2000)
            y = 500.0 + rng.normal(0, 5, size=x.size)  # no Si peak anywhere
            (tmp_path / f"RM_{point}.txt").write_text(
                "\n".join(f"{a:.4f}\t{b:.4f}" for a, b in zip(x, y))
            )
        at = AppTest.from_file(PAGE, default_timeout=300)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan_sample_folder(str(tmp_path)),
            magnification=None, material="Silicon", reference_layer="2L",
        )
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        state = at.session_state["qc_report"]
        assert state["raman_fingerprint"] is not None, \
            "a preset edit after this run would never be detected as stale"

    def test_changing_the_reference_layer_drops_stale_optical_results(self, tmp_path):
        """A figure labelled 'Bilayer' must not survive a switch to 1L."""
        at, _scan = _generated(tmp_path)
        assert at.session_state["qc_report"]["om_png"] is not None

        next(s for s in at.selectbox if "film being measured" in s.label).select("1L").run()

        state = at.session_state["qc_report"]
        assert state["reference_layer"] == "1L"
        assert state["om_png"] is None

    def test_the_summary_page_goes_with_a_dropped_segmentation(self, tmp_path):
        """It prints the OM class table, so it cannot outlive the frames that
        table came from."""
        at, _scan = _generated(tmp_path)
        assert at.session_state["qc_report"]["summary_png"] is not None

        next(s for s in at.selectbox if "film being measured" in s.label).select("1L").run()

        assert at.session_state["qc_report"]["summary_png"] is None
        assert at.session_state["qc_report"]["xlsx_bytes"] is None


class TestAdaptiveThreshold:
    """The page must actually derive the pair, not silently use the base.

    This is the regression the whole rebase existed to avoid: on the 202609
    batch the derivation moved the pair on 21 of 55 wafers, and on HADH57 the
    difference between the derived and the fixed pair was 3.6 points of bilayer
    coverage. A merged page that skipped `derive_pair` would have put every
    wafer back on fixed contrast with nothing on screen to say so.

    Silicon carries no abs pair, so these use WSe2 — the preset that has one.
    """

    def _wse2_app(self, tmp_path):
        for point in range(1, 10):
            _write_om_frame(tmp_path / f"50x_{point}.png", seed=100 + point)
        at = AppTest.from_file(PAGE, default_timeout=300)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan_sample_folder(str(tmp_path)),
            magnification="50x", material="WSe2", reference_layer="2L",
        )
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()
        return at

    def test_the_run_derives_a_pair_and_keeps_it(self, tmp_path):
        at = self._wse2_app(tmp_path)

        assert not at.exception, [e.value for e in at.exception]
        derived = at.session_state["qc_report"]["optical_threshold"]
        assert derived is not None, "the page did not derive a pair"
        assert derived.base == (6.0, 4.25), "the preset pair should be the base"

    def test_the_derived_pair_is_what_actually_segmented(self, tmp_path):
        """Not just computed and dropped: the frames must carry cuts placed by
        the derived pair, not by the base."""
        at = self._wse2_app(tmp_path)
        state = at.session_state["qc_report"]
        derived = state["optical_threshold"]
        frame = state["frames"][0]

        expected_low = frame.mode * (1.0 - derived.pair[0] / 100.0)
        expected_high = frame.mode * (1.0 + derived.pair[1] / 100.0)
        assert frame.threshold_low == pytest.approx(expected_low, rel=1e-6)
        assert frame.threshold_high == pytest.approx(expected_high, rel=1e-6)

    def test_the_pair_reaches_the_workbook(self, tmp_path):
        at = self._wse2_app(tmp_path)
        state = at.session_state["qc_report"]

        wb = load_workbook(BytesIO(state["xlsx_bytes"]))
        rows = list(wb["OM_Stats"].iter_rows(min_row=1, values_only=True))
        header, first = rows[0], rows[1]

        assert first[header.index("Threshold_Below_pct")] == pytest.approx(
            state["optical_threshold"].pair[0])
        assert first[header.index("Threshold_Base_Below_pct")] == pytest.approx(6.0)

    def test_the_derivation_is_announced_on_screen(self, tmp_path):
        at = self._wse2_app(tmp_path)

        assert any("adaptive pair" in m.value for m in at.markdown),             "the run did not say which pair it derived"

    def test_a_preset_without_a_pair_derives_nothing(self, tmp_path):
        """Silicon is nsigma-only. The derivation needs a base pair to move, so
        asking for it would be meaningless rather than merely slow."""
        for point in range(1, 10):
            _write_om_frame(tmp_path / f"50x_{point}.png", seed=100 + point)
        at = AppTest.from_file(PAGE, default_timeout=300)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan_sample_folder(str(tmp_path)),
            magnification="50x", material="Silicon", reference_layer="2L",
        )
        at.run()
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()

        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["qc_report"]["optical_threshold"] is None
        assert at.session_state["qc_report"]["om_png"] is not None


class TestInventory:
    """Said before the run, because a naming typo and a genuine absence look
    identical once the report is built."""

    def test_the_counts_are_shown_before_anything_runs(self, tmp_path):
        at, _scan = _seeded_app(tmp_path)
        at.run()

        found = [m.value for m in at.markdown if "**Found:**" in m.value]

        assert found, "the inventory line is missing"
        assert "9 Raman" in found[0]
        assert "0 PL" in found[0]
        assert "9 OM" in found[0]

    def test_a_material_without_a_pl_block_says_so_rather_than_blaming_the_folder(
        self, tmp_path
    ):
        """Silicon defines no PL peaks. With PL files present, the cause is the
        preset, not the file naming — and they are fixed in different places."""
        for point in range(1, 10):
            _write_multi_spectrum_raman(tmp_path / f"RM_{point}.txt", seed=point)
            _write_multi_spectrum_raman(tmp_path / f"PL_{point}.txt", seed=point)
        at = AppTest.from_file(PAGE, default_timeout=180)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan_sample_folder(str(tmp_path)),
            magnification=None, material="Silicon", reference_layer="2L",
        )
        at.run()

        captions = [c.value for c in at.caption]

        assert any("defines no PL peaks" in c for c in captions)
        assert not any("No PL files in this folder" in c for c in captions)

    def test_an_empty_folder_says_there_is_nothing_to_do(self, tmp_path):
        at = AppTest.from_file(PAGE, default_timeout=60)
        at.session_state["qc_report"] = _initial_state(
            folder=str(tmp_path), scan=scan_sample_folder(str(tmp_path)),
            magnification=None, material="Silicon", reference_layer="2L",
        )
        at.run()

        assert any("Nothing to analyse" in w.value for w in at.warning)


class TestProgress:
    def test_the_status_resolves_to_complete(self, tmp_path):
        """The user's question during a minute-long build is "is this alive?".
        A status that ends 'complete' answers it; one stuck on 'running' does
        not."""
        at, _scan = _generated(tmp_path)

        assert not at.exception
        assert len(at.status) == 1
        assert at.status[0].state == "complete"

    def test_the_final_label_names_the_sample(self, tmp_path):
        at, scan = _generated(tmp_path)

        assert scan.sample_name in at.status[0].label

    def test_a_failure_mid_build_marks_the_status_errored_not_running(
        self, tmp_path, monkeypatch
    ):
        """Without the `with` form, an exception leaves the status spinning
        forever — indistinguishable from the hang it exists to rule out."""
        import core.report.summary_figure as summary_module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("composition failed")

        monkeypatch.setattr(summary_module, "build_summary_figure", _boom)

        at, _scan = _generated(tmp_path)

        assert len(at.status) == 1
        assert at.status[0].state == "error", \
            "a failed build must not look like it is still working"


class TestWorkbook:
    def test_it_holds_every_point_the_summary_averaged(self, tmp_path):
        """The summary page reports one mean over nine points; the workbook has
        to be able to say which point was which, traced back to its file."""
        at, _scan = _generated(tmp_path)

        wb = load_workbook(BytesIO(at.session_state["qc_report"]["xlsx_bytes"]))

        assert "Summary" in wb.sheetnames
        assert "Raman" in wb.sheetnames
        assert "PL" not in wb.sheetnames, "PL wasn't measured; it gets no sheet"

        ws = wb["Raman"]
        headers = [cell.value for cell in ws[1]]
        rows = list(ws.iter_rows(min_row=2, values_only=True))

        # Silicon's preset is a single peak, and each file holds SUB_SPECTRA.
        assert len(rows) == 9 * SUB_SPECTRA
        assert all(row[headers.index("Peak_Label")] == "Si" for row in rows)
        # Near 520 cm-1 for every point, which is what the fixture generates.
        assert all(515 < row[headers.index("Center")] < 525 for row in rows)

    def test_the_optical_sheets_travel_in_the_same_workbook(self, tmp_path):
        """One file for every table. Until v5.0.0 these were four loose CSVs
        written beside the images."""
        at, _scan = _generated(tmp_path)

        wb = load_workbook(BytesIO(at.session_state["qc_report"]["xlsx_bytes"]))

        assert "OM_Stats" in wb.sheetnames
        assert "OM_Points" in wb.sheetnames
        assert [row[0] for row in wb["OM_Points"].iter_rows(min_row=2, values_only=True)] \
            == list(range(1, 10))

    def test_the_domain_size_sheet_travels_too(self, tmp_path):
        """The sheet exists and its shape agrees with what the run actually
        found -- a "Population" column and two data rows when the pooled
        domains split, one row without it otherwise. Whether this particular
        fixture's noise floor produces enough stray domains to clear
        MIN_DOMAINS_FOR_SPLIT is incidental; either shape is a pass."""
        at, _scan = _generated(tmp_path)

        stat = at.session_state["qc_report"]["domain_stat"]
        assert stat is not None
        wb = load_workbook(BytesIO(at.session_state["qc_report"]["xlsx_bytes"]))

        assert "Domain_Size" in wb.sheetnames
        ws = wb["Domain_Size"]
        rows = [[cell.value for cell in row] for row in ws.iter_rows()]
        header_row = next(row for row in rows if "Domains_N" in row)
        data_rows = rows[rows.index(header_row) + 1:]

        if stat.populations:
            assert "Population" in header_row
            assert len(data_rows) == 2
        else:
            assert "Population" not in header_row
            assert len(data_rows) == 1


class TestSave:
    """The save path, with the native dialog stubbed out.

    Worth testing despite the dialog itself being untestable: the bug this
    guards is a wrong call into `prompt_save_path` (its first argument is
    `default_dir`, and the filename keyword is `default_filename`), which the
    page catches and reports as a toast. Nothing else in the suite touches this
    code, so it shipped broken once already.

    One dialog now writes the figures plus the workbook off one chosen stem.
    Their contents are covered by the unit tests; what is covered here is that
    the run produced them and the save loop wrote them — the seam a state key
    missing from an artifact tuple would break.
    """

    def _run_and_save(self, tmp_path, monkeypatch, target, **kwargs):
        import core.io.export as export_module

        calls = {}

        def _fake_dialog(default_dir, default_filename, **extra):
            calls["default_dir"] = default_dir
            calls["default_filename"] = default_filename
            calls.update(extra)
            return target

        monkeypatch.setattr(export_module, "prompt_save_path", _fake_dialog)

        at, _scan = _generated(tmp_path, **kwargs)
        next(b for b in at.button if "Save QC Report" in b.label).click().run()
        return at, calls

    def test_the_figures_are_numbered_in_reading_order(self, tmp_path, monkeypatch):
        """Alphabetical sort puts "Summary" last and interleaves the two
        techniques, so a colleague handed the folder meets them in an order
        nobody chose."""
        out = tmp_path / "out"
        out.mkdir()
        at, _calls = self._run_and_save(
            tmp_path, monkeypatch, str(out / "TSM_QC_Report.png")
        )

        assert not at.exception, [e.value for e in at.exception]
        written = sorted(p.name for p in out.glob("*.png"))
        assert written == [
            "TSM_QC_Report_1_Summary.png",
            "TSM_QC_Report_2_OM.png",
            "TSM_QC_Report_3_OM_diagnostic.png",
            "TSM_QC_Report_4_Domain_size.png",
            "TSM_QC_Report_5_Raman.png",
            "TSM_QC_Report_6_Raman_stats.png",
        ]
        for path in out.glob("*.png"):
            assert path.read_bytes()[:8] == _PNG_MAGIC

    def test_the_workbook_lands_beside_them_under_the_same_stem(
        self, tmp_path, monkeypatch
    ):
        out = tmp_path / "out"
        out.mkdir()
        self._run_and_save(tmp_path, monkeypatch, str(out / "TSM_QC_Report.png"))

        assert [p.name for p in out.glob("*.xlsx")] == ["TSM_QC_Report.xlsx"]

    def test_a_technique_with_no_data_writes_no_file_for_it(self, tmp_path, monkeypatch):
        """Silicon has no PL block, so nothing numbered 7 or 8 appears rather
        than an empty figure claiming a measurement."""
        out = tmp_path / "out"
        out.mkdir()
        self._run_and_save(tmp_path, monkeypatch, str(out / "TSM_QC_Report.png"))

        assert not list(out.glob("*_7_*"))
        assert not list(out.glob("*_8_*"))

    def test_the_dialog_opens_in_the_sample_folder(self, tmp_path, monkeypatch):
        out = tmp_path / "out"
        out.mkdir()
        _at, calls = self._run_and_save(tmp_path, monkeypatch, str(out / "x.png"))

        assert calls["default_dir"] == str(tmp_path)
        assert calls["default_filename"].endswith("_QC_Report.png")
        assert calls["default_extension"] == ".png"

    def test_cancelling_the_dialog_writes_nothing(self, tmp_path, monkeypatch):
        out = tmp_path / "out"
        out.mkdir()
        at, _calls = self._run_and_save(tmp_path, monkeypatch, None)

        assert not at.exception
        assert list(out.iterdir()) == []

    def test_the_clean_copy_and_the_diagnostic_copy_are_different_images(
        self, tmp_path, monkeypatch
    ):
        """If `show_histograms` stopped being honoured, both files would still
        be written and both would still be valid PNGs, so only comparing them
        catches it."""
        out = tmp_path / "out"
        out.mkdir()
        self._run_and_save(tmp_path, monkeypatch, str(out / "TSM_QC_Report.png"))

        clean = (out / "TSM_QC_Report_2_OM.png").read_bytes()
        diagnostic = (out / "TSM_QC_Report_3_OM_diagnostic.png").read_bytes()

        assert clean != diagnostic
        # The diagnostics row is extra content, so it is the larger figure.
        assert len(diagnostic) > len(clean)
