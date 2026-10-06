"""Page-level tests for pages/6_OneNote.py, for the failures that only exist in the wiring.

Both are about Streamlit widget identity across reruns, which
test_tool_layout.py cannot see: the lookup there was always right, and the
page drew the right filenames beside the wrong ticks.

`AppTest` runs the script in this process, so patching the *source* module
(`core.io.folder_picker`) is enough — the page's `from x import y` lines
re-execute on every run and pick the patch up.
"""

import pytest
from streamlit.testing.v1 import AppTest

import core.io.folder_picker as folder_picker
from core.paths import PROJECT_ROOT

_PAGE = str(PROJECT_ROOT / "pages" / "6_OneNote.py")


@pytest.fixture
def tool_root(tmp_path):
    """A miniature HA1P01: one wafer with all three artifacts, one with optical only."""
    root = tmp_path / "HA1P01"

    for wafer in ("HADH78", "HADI04"):
        (root / "OPTICALS" / "202609" / wafer).mkdir(parents=True)

    runcards = root / "RUNCARD" / "lcy" / "NSHA1P01" / "202609"
    runcards.mkdir(parents=True)
    (runcards / "HADH78.csv").write_text("x", encoding="utf-8")

    datalogs = root / "DATALOG" / "2026_09"
    datalogs.mkdir(parents=True)
    (datalogs / "2026-09-21_121151~HADH78.csv").write_text("x", encoding="utf-8")

    return root


def _pick_tool(monkeypatch, folder):
    """Open the page and pick `folder` through Select Tool Folder."""
    monkeypatch.setattr(folder_picker, "prompt_folder_path", lambda **kwargs: str(folder))
    at = AppTest.from_file(_PAGE, default_timeout=60).run()
    for button in at.button:
        if button.label == "Select Tool Folder":
            return button.click().run()
    raise AssertionError("the page drew no Select Tool Folder button")


def _select_wafer(at, wafer):
    for box in at.selectbox:
        if box.label == "Wafer":
            return box.select(wafer).run()
    raise AssertionError("the page drew no Wafer selectbox")


def _found(at):
    """The Found-on-disk ticks, by the label's artifact name."""
    return {box.label.split(" — ")[0]: box.value for box in at.checkbox}


class TestFoundOnDiskFollowsTheWafer:
    def test_ticks_follow_a_wafer_picked_after_a_bare_one(self, tool_root, monkeypatch):
        # The reported case: a wafer with no runcard or datalog first, then
        # HADH78, which has both. The labels named HADH78's files while the
        # boxes kept HADI04's empty ticks.
        at = _pick_tool(monkeypatch, tool_root)
        at = _select_wafer(at, "HADI04")
        assert _found(at) == {"Raman / PL / OM": True, "Runcard": False, "Datalog": False}

        at = _select_wafer(at, "HADH78")

        assert not at.exception, [e.value for e in at.exception]
        assert _found(at) == {"Raman / PL / OM": True, "Runcard": True, "Datalog": True}

    def test_ticks_clear_going_the_other_way(self, tool_root, monkeypatch):
        at = _pick_tool(monkeypatch, tool_root)
        at = _select_wafer(at, "HADH78")
        at = _select_wafer(at, "HADI04")

        assert _found(at) == {"Raman / PL / OM": True, "Runcard": False, "Datalog": False}


class TestTheWaferPickSticks:
    def test_a_second_pick_straight_after_the_first_is_kept(self, tool_root, monkeypatch):
        # Unkeyed, the selectbox's identity included an index taken from the
        # previous run's wafer, so this second pick landed on a widget that no
        # longer existed and the dropdown snapped back to the first.
        at = _pick_tool(monkeypatch, tool_root)
        at = _select_wafer(at, "HADI04")
        at = _select_wafer(at, "HADH78")

        assert at.session_state["onenote"]["wafer_id"] == "HADH78"

    def test_a_picked_wafer_folder_seeds_the_dropdown(self, tool_root, monkeypatch):
        at = _pick_tool(monkeypatch, tool_root / "OPTICALS" / "202609" / "HADH78")

        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["onenote"]["wafer_id"] == "HADH78"
        assert _found(at)["Runcard"] is True
