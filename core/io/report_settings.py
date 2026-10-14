"""
Persisted per-installation settings for the QC Report page: the default
material, whether to run domain-size measurement, whether that
measurement also tests for a small/large size-population split, and whether
Raman axes are corrected to the laser line (shared with the Spectra page).

A small JSON sidecar (separate from data/materials.json, whose schema/tests
this deliberately doesn't touch) so the page can remember an operator's last
choices across app restarts. Per-installation, so it's gitignored.

Read-modify-write, not overwrite: a save call loads whatever is already on
disk, updates only its own key, and writes the merged document back. Two
independent settings sharing one file means a naive `json.dump({"my_key": ...})`
would silently erase the other one on the very next save.
"""

import json
from pathlib import Path
from typing import Optional, Union

from ..paths import DATA_DIR


def get_report_settings_path() -> Path:
    """Absolute path to this installation's report_settings.json."""
    return DATA_DIR / "report_settings.json"


def _load_document(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_document(path: Path, updates: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = _load_document(path)
    document.update(updates)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
        f.write("\n")


def load_default_material(path: Optional[Union[str, Path]] = None) -> Optional[str]:
    """
    Load the persisted default material name.

    Returns None if the file doesn't exist, is malformed, or has no such key
    (never raises).
    """
    path = Path(path) if path is not None else get_report_settings_path()
    material = _load_document(path).get("default_material")
    return material if isinstance(material, str) and material else None


def save_default_material(material_name: str, path: Optional[Union[str, Path]] = None) -> None:
    """Persist `material_name` as the default material."""
    path = Path(path) if path is not None else get_report_settings_path()
    _save_document(path, {"default_material": material_name})


def load_measure_domain_size(path: Optional[Union[str, Path]] = None) -> bool:
    """
    Whether the QC Report should measure flake domain size by default.

    False if the file doesn't exist, is malformed, or has no such key --
    domain-size analysis roughly doubles the optical-analysis time (see
    core.report.progress), so it is opt-in rather than assumed.
    """
    path = Path(path) if path is not None else get_report_settings_path()
    value = _load_document(path).get("measure_domain_size")
    return value if isinstance(value, bool) else False


def save_measure_domain_size(value: bool, path: Optional[Union[str, Path]] = None) -> None:
    """Persist `value` as the default for the domain-size measurement checkbox."""
    path = Path(path) if path is not None else get_report_settings_path()
    _save_document(path, {"measure_domain_size": value})


def load_split_size_populations(path: Optional[Union[str, Path]] = None) -> bool:
    """
    Whether the domain-size measurement should also test for a small/large
    size-population split by default.

    False if the file doesn't exist, is malformed, or has no such key -- the
    split (domain_size.split_size_populations) has fired on frames with no
    real second population at all (pure background texture), so it defaults
    off rather than assumed, unlike domain-size measurement itself which is
    merely expensive rather than sometimes wrong.
    """
    path = Path(path) if path is not None else get_report_settings_path()
    value = _load_document(path).get("split_size_populations")
    return value if isinstance(value, bool) else False


def save_split_size_populations(value: bool, path: Optional[Union[str, Path]] = None) -> None:
    """Persist `value` as the default for the size-population split checkbox."""
    path = Path(path) if path is not None else get_report_settings_path()
    _save_document(path, {"split_size_populations": value})


def load_correct_raman_axis(path: Optional[Union[str, Path]] = None) -> bool:
    """
    Whether Raman maps are shifted so their laser line sits at 0 cm⁻¹ when
    loaded (see modules.spectra.processing.axis_calibration).

    True if the file doesn't exist, is malformed, or has no such key -- an
    uncalibrated session moves every Raman centre by several cm⁻¹, while a
    calibrated one falls inside the calibration's deadband and is left as
    read, so on is the setting that cannot make a good session worse. One
    setting for both the Spectra page and the QC Report, so the two cannot
    load the same file onto different axes.
    """
    path = Path(path) if path is not None else get_report_settings_path()
    value = _load_document(path).get("correct_raman_axis")
    return value if isinstance(value, bool) else True


def save_correct_raman_axis(value: bool, path: Optional[Union[str, Path]] = None) -> None:
    """Persist `value` as the Raman laser-line axis correction setting."""
    path = Path(path) if path is not None else get_report_settings_path()
    _save_document(path, {"correct_raman_axis": value})
