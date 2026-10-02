"""
Persisted micron-per-pixel calibration, by magnification.

Shared and committed (data/om_calibration.json), unlike report_settings.json's
per-installation UI preference: this describes the microscope's own
camera+objective pairing, a fact about the shared instrument rather than a
personal setting, so everyone reading a domain-size report needs the same
number that produced it.

Keyed by magnification string ("50x", "100x", ...) alone, never by material:
a 50x objective has the same field of view regardless of what film is under
it. This is a genuinely new concept as of the domain-size feature -- no
pixel-to-physical-distance calibration existed anywhere in the app before it,
and nothing here should invent one. A magnification with no entry means
"not calibrated", and callers must show that honestly (pixel units, not a
guessed micron figure) rather than filling the gap themselves.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Union

from ..paths import DATA_DIR

SCHEMA_VERSION = 1


def get_om_calibration_path() -> Path:
    """Absolute path to the shared om_calibration.json store."""
    return DATA_DIR / "om_calibration.json"


def load_om_calibration(path: Optional[Union[str, Path]] = None) -> Dict[str, float]:
    """
    Magnification -> micron-per-pixel, or an empty dict if the file is
    missing or malformed (never raises -- a broken calibration file should
    degrade to "nothing is calibrated", not take down the QC Report).
    """
    path = Path(path) if path is not None else get_om_calibration_path()
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}
    raw = data.get("calibration")
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, float] = {}
    for magnification, value in raw.items():
        try:
            out[str(magnification)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def save_om_calibration(
    calibration: Dict[str, float], path: Optional[Union[str, Path]] = None
) -> None:
    """Persist the full magnification -> micron-per-pixel mapping."""
    path = Path(path) if path is not None else get_om_calibration_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    document = {
        "schema_version": SCHEMA_VERSION,
        "calibration": {
            magnification: calibration[magnification]
            for magnification in sorted(calibration)
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
        f.write("\n")


def get_um_per_px(
    calibration: Dict[str, float], magnification: Optional[str]
) -> Optional[float]:
    """
    Case-insensitive calibration lookup.

    A magnification string is a raw filename/folder prefix
    (`sample_scanner`'s "any other prefix"); nothing normalises its case
    before it reaches here, so a folder's "50X" and a calibration entry typed
    as "50x" must resolve to the same number rather than silently falling
    back to uncalibrated pixels over a capitalisation mismatch --
    `default_magnification` already treats "100X"/"100x" as the same
    magnification for this exact reason.
    """
    if not magnification:
        return None
    target = magnification.casefold()
    for key, value in calibration.items():
        if key.casefold() == target:
            return value
    return None
