"""Integration test for the Spectra sidebar's "Save PNGs to folder" batch
image export button.

Runs through AppTest rather than calling the export helpers directly: the
button only appears once files are fitted, and `prompt_save_folder` has to be
monkeypatched to avoid popping a real OS dialog during the test -- both are
Streamlit-page-lifecycle concerns, not pure-function ones.
"""

import os

import numpy as np
from streamlit.testing.v1 import AppTest

import modules.spectra.ui.sidebar as sidebar_module
from core.paths import PROJECT_ROOT


def _write_silicon_raman_file(path, seed, peak_center=520.0):
    """A single peak matching data/materials.json's Silicon (Raman) preset."""
    x = np.linspace(0, 1000, 2000)
    sigma = 8.0 / 2.355
    rng = np.random.default_rng(seed)
    y = 500.0 + 1000.0 * np.exp(-0.5 * ((x - peak_center) / sigma) ** 2) + rng.normal(0, 5, size=x.size)
    with open(path, "w") as f:
        for xi, yi in zip(x, y):
            f.write(f"{xi:.4f}\t{yi:.4f}\n")


def _app_with_fitted_files(tmp_path, count=3):
    """The Spectra page with `count` Silicon files loaded and fitted."""
    paths = []
    for i in range(1, count + 1):
        path = tmp_path / f"RM_sample{i}.txt"
        _write_silicon_raman_file(path, seed=i)
        paths.append(str(path))

    at = AppTest.from_file(str(PROJECT_ROOT / "pages/1_Spectra.py"), default_timeout=180)
    at.session_state["pending_files_to_load"] = paths
    at.run()

    material = next(s for s in at.sidebar.selectbox if "aterial" in (s.label or ""))
    material.set_value("Silicon").run()

    next(b for b in at.sidebar.button if "Run All Files" in b.label).click().run()
    assert all(f.fit_done for f in at.session_state["files"].values())
    return at


class TestBatchImageExportButton:
    def test_saves_one_png_per_fitted_file(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        at = _app_with_fitted_files(tmp_path, count=3)

        monkeypatch.setattr(sidebar_module, "prompt_save_folder", lambda **kwargs: str(out_dir))
        next(b for b in at.sidebar.button if "Save PNGs to folder" in b.label).click().run()

        assert not at.exception
        saved = os.listdir(out_dir)
        assert len(saved) == 3
        assert all(name.endswith("_fit.png") for name in saved)

    def test_a_cancelled_dialog_saves_nothing(self, tmp_path, monkeypatch):
        """prompt_save_folder returns None on cancel -- the button must be a
        no-op, not an error."""
        at = _app_with_fitted_files(tmp_path, count=3)

        monkeypatch.setattr(sidebar_module, "prompt_save_folder", lambda **kwargs: None)
        next(b for b in at.sidebar.button if "Save PNGs to folder" in b.label).click().run()

        assert not at.exception
        assert not any("Saved" in m.value for m in at.sidebar.success)

    def test_the_success_message_survives_the_run(self, tmp_path, monkeypatch):
        """Matches the master-CSV button's own convention: the summary must
        still be on screen after the run that produced it, not require a
        second rerun to appear."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        at = _app_with_fitted_files(tmp_path, count=3)

        monkeypatch.setattr(sidebar_module, "prompt_save_folder", lambda **kwargs: str(out_dir))
        next(b for b in at.sidebar.button if "Save PNGs to folder" in b.label).click().run()

        messages = [m.value for m in at.sidebar.success]
        assert any("3" in m and str(out_dir) in m for m in messages), messages
