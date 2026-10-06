"""
Parser for two-column .txt spectrum files.

This module provides functions to:
- Parse two-column .txt files (X, Y) with auto-delimiter detection
- Skip any header above the numbers and any footer below them
- Validate loaded data
- Convert to SpectrumData objects

Headers
-------
Instruments and export tools put different things above the data: a single
row of column names ("Raman shift (cm-1)<TAB>Intensity (a.u.)"), a block of
"#key=value" acquisition settings, "[Header]"/"[Data]" sections, or a
">>>>>Begin Spectral Data<<<<<" marker with a matching end marker below. Rather
than recognise each format, the parser looks for where the numbers start: the
first row of two or more numbers that the next few non-blank rows confirm.
Everything above it is header and everything after the last numeric row is
footer. A file with no header parses exactly as it did before.

A header line made only of numbers can't be told apart from data this way: a
lone "1024" is skipped (one number is not a row), but "532<TAB>600" directly
above the data would be read as its first point.
"""

import io
from itertools import islice

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Tuple, Optional, Literal, List, NamedTuple
from ..models.spectrum import SpectrumData


# Tried in this order on each candidate row; None means any run of whitespace.
# Tab first because it is the most common in instrument exports, and comma
# before whitespace so "1, 2" is read as two fields rather than "1," and "2".
_DELIMITERS: Tuple[Optional[str], ...] = ('\t', ',', ';', None)

# How many non-blank rows after a candidate first row must also be numeric
# before it is believed. Enough that a stray numeric line in a header doesn't
# start the data; few enough that a three-row test file still parses.
_CONFIRM_ROWS = 4


class _SpectrumText(NamedTuple):
    data: str           # the numeric rows only, newline-joined
    sep: Optional[str]  # delimiter of the numeric rows; None = whitespace


def _decode(raw: bytes) -> str:
    """Bytes to text: UTF-8 (with or without BOM), UTF-16 by its BOM, else cp1252.

    Instrument headers are where non-ASCII turns up ("µm", "°C", "cm⁻¹"), and
    exports from older Windows software are cp1252, not UTF-8. latin-1 is the
    last resort because it decodes any byte at all.
    """
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1')


def _is_numeric_row(line: str, sep: Optional[str]) -> bool:
    """Whether `line` is a row of numbers when split on `sep`.

    Trailing empty fields (a delimiter at the end of the line) are ignored. An
    empty field in the middle is a missing value, as pandas reads it, and
    doesn't disqualify the row -- but the first field (X) must be a number and
    at least two fields must be.
    """
    fields = [f.strip() for f in (line.split() if sep is None else line.split(sep))]
    while fields and not fields[-1]:
        fields.pop()
    numbers = 0
    for i, field in enumerate(fields):
        if not field and i > 0:
            continue
        try:
            float(field)
        except ValueError:
            return False
        numbers += 1
    return numbers >= 2


def _split_header(text: str) -> _SpectrumText:
    """Separate the numeric rows from any header above and footer below them."""
    lines = text.splitlines()

    for start, line in enumerate(lines):
        seps = [s for s in _DELIMITERS if _is_numeric_row(line, s)]
        if not seps:
            continue
        sep = seps[0]
        following = islice((ln for ln in islice(lines, start + 1, None) if ln.strip()),
                           _CONFIRM_ROWS)
        if all(_is_numeric_row(ln, sep) for ln in following):
            break
    else:
        raise ValueError(
            "No rows of numbers found. Expected X<delimiter>Y[<delimiter>Y...] "
            "rows, delimited by tab, comma, semicolon or whitespace."
        )

    end = len(lines) - 1
    while not _is_numeric_row(lines[end], sep):
        end -= 1

    return _SpectrumText(data="\n".join(lines[start:end + 1]), sep=sep)


def _read_spectrum_dataframe(filepath: str, nrows: Optional[int] = None) -> pd.DataFrame:
    """
    Read a spectrum .txt file into a DataFrame, skipping any header and footer.

    The delimiter is the first of tab, comma, semicolon and whitespace that
    makes the first data row numeric. Columns that are empty in every row --
    left by a delimiter at the end of each line, or a doubled one -- are
    dropped. Returns a DataFrame of at least 2 columns.

    `nrows` limits how much is read, for callers that only need the shape.
    """
    # Checked explicitly so a missing file surfaces as FileNotFoundError
    # rather than as a parse failure.
    if not Path(filepath).exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    raw = Path(filepath).read_bytes()
    if not raw.strip():
        raise pd.errors.EmptyDataError("No data in file")

    text = _split_header(_decode(raw))
    df = pd.read_csv(io.StringIO(text.data),
                     sep=r'\s+' if text.sep is None else text.sep,
                     header=None, engine='python', nrows=nrows)
    df = df.dropna(axis=1, how='all')

    if df.shape[1] < 2:
        raise ValueError(
            "File must have at least 2 columns (X plus 1+ Y columns). "
            "Expected delimiter: tab, comma, semicolon or whitespace."
        )

    return df


def count_spectra(filepath: str) -> int:
    """
    How many spectra a file holds, without reading its values.

    Reads a single row to learn the column count, so callers that need to size
    the work ahead of time — the report's progress weighting, chiefly — do not
    have to parse a 2.6 MB file twice. Uses the same delimiter sniffing as the
    real parse, so its answer cannot disagree with `parse_spectrum_multi`.

    Returns 0 for a file that cannot be read at all, since a caller sizing work
    should not have to handle an exception for a file the fit loop will report
    as an error a moment later anyway.
    """
    try:
        return max(0, _read_spectrum_dataframe(filepath, nrows=1).shape[1] - 1)
    except Exception:
        return 0


def parse_spectrum_multi(filepath: str) -> List[SpectrumData]:
    """
    Parse a .txt spectrum file with one X column and 1+ Y columns.

    Returns one SpectrumData per Y column (all sharing the same X).
    Two-column files yield a list of length 1.

    Raises
    ------
    ValueError
        If the file cannot be parsed or every Y column fails validation.
    FileNotFoundError
        If the file does not exist.
    """
    try:
        df = _read_spectrum_dataframe(filepath)
    except FileNotFoundError:
        raise FileNotFoundError(f"Spectrum file not found: {filepath}")
    except pd.errors.EmptyDataError:
        raise ValueError(f"File is empty: {filepath}")
    except Exception as e:
        raise ValueError(
            f"Failed to parse spectrum file '{filepath}': {e}"
        )

    try:
        X = df.iloc[:, 0].values.astype(np.float64)
    except ValueError as e:
        raise ValueError(
            f"Failed to convert X column to numeric values: {e}. "
            f"A non-numeric line inside the data block is not skipped as a header."
        )

    spectra: List[SpectrumData] = []
    errors: List[str] = []
    for col_idx in range(1, df.shape[1]):
        try:
            Y = df.iloc[:, col_idx].values.astype(np.float64)
            spectra.append(SpectrumData(X=X, Y=Y))
        except Exception as e:
            errors.append(f"column {col_idx + 1}: {e}")

    if not spectra:
        raise ValueError(
            f"No usable Y columns in '{filepath}'. Errors: {'; '.join(errors)}"
        )

    return spectra


def parse_spectrum(filepath: str) -> SpectrumData:
    """
    Parse two-column .txt spectrum file.

    Parameters
    ----------
    filepath : str
        Path to .txt file (two columns: X, Y).

    Returns
    -------
    SpectrumData
        Parsed and validated spectrum data.

    Raises
    ------
    ValueError
        If file cannot be parsed or validation fails.
    FileNotFoundError
        If file does not exist.

    Notes
    -----
    File format requirements (FR-001 to FR-004):
    - Two columns: X (wavenumber or wavelength), Y (intensity)
    - Delimiter: tab, comma, semicolon or whitespace (auto-detected)
    - Any header above the data and footer below it is skipped (see module docstring)
    - Numeric values only
    - Y values must be non-negative

    Examples
    --------
    >>> data = parse_spectrum("sample_raman.txt")
    >>> print(data.X.shape, data.Y.shape)
    (1000,) (1000,)
    """
    spectra = parse_spectrum_multi(filepath)
    if len(spectra) > 1:
        raise ValueError(
            f"File has {len(spectra) + 1} columns (1 X + {len(spectra)} Y). "
            f"Use parse_spectrum_multi() for multi-Y files."
        )
    return spectra[0]


def validate_spectrum_file(filepath: str) -> Tuple[bool, str]:
    """
    Validate .txt spectrum file without loading it.

    Parameters
    ----------
    filepath : str
        Path to .txt file.

    Returns
    -------
    is_valid : bool
        True if file is valid.
    message : str
        Empty if valid, else error message.

    Examples
    --------
    >>> is_valid, msg = validate_spectrum_file("sample.txt")
    >>> if not is_valid:
    ...     print(f"Validation failed: {msg}")
    """
    try:
        parse_spectrum(filepath)
        return True, ""
    except Exception as e:
        return False, str(e)
def estimate_spectral_resolution(X: np.ndarray) -> float:
    """
    Estimate spectral resolution (median step size).

    Used for auto-calculating width_min in peak fitting bounds.

    Parameters
    ----------
    X : np.ndarray
        X array (wavenumber or wavelength).

    Returns
    -------
    resolution : float
        Median step size in X units.

    Examples
    --------
    >>> X = np.linspace(100, 1000, 1000)
    >>> resolution = estimate_spectral_resolution(X)
    >>> print(f"{resolution:.3f} cm⁻¹")
    0.901 cm⁻¹
    """
    if len(X) < 2:
        return 1.0  # Fallback for single-point spectra (should not happen)

    # Calculate step sizes
    steps = np.diff(X)

    # Use median to be robust to irregular spacing
    resolution = np.median(np.abs(steps))

    return resolution


# The one place the app decides what a filename says about its technique.
# sample_scanner matches these as whole prefixes ("rm-1.txt"), while
# detect_mode_from_filename matches them as a leading substring
# ("RM_carbon_sample.txt") -- two questions, but they must not disagree about
# the vocabulary. They did until v4.0.0: the scanner read VABD38's "Raman_1.txt"
# as Raman while the detector, which knew only "RM", returned None for it.
RAMAN_PREFIXES = frozenset({"RAMAN", "RM"})
PL_PREFIXES = frozenset({"PL"})


def detect_mode_from_filename(filename: str) -> Optional[Literal["Raman", "PL"]]:
    """
    Auto-detect spectroscopy mode from filename prefix.

    v2.1+ (FR-12): Automatic mode detection based on naming conventions.

    Parameters
    ----------
    filename : str
        File name (with or without path).

    Returns
    -------
    mode : Optional[Literal["Raman", "PL"]]
        "Raman" if the filename starts with any of `RAMAN_PREFIXES`
        "PL" if it starts with any of `PL_PREFIXES` (both case-insensitive)
        None if no match (manual mode selection required)

    Notes
    -----
    Detection Rules (FR-12):
    - RM*, RAMAN* → Raman mode (e.g., RM_sample.txt, Raman_1.txt)
    - PL* → PL mode (e.g., PL_emission.txt, pl_test.txt)
    - Other patterns → None (no auto-detection)

    Case-insensitive matching ensures compatibility with various naming conventions.

    Examples
    --------
    >>> detect_mode_from_filename("RM_carbon_sample.txt")
    'Raman'
    >>> detect_mode_from_filename("Raman_1.txt")
    'Raman'
    >>> detect_mode_from_filename("pl_emission_test.txt")
    'PL'
    >>> detect_mode_from_filename("sample_001.txt")
    None
    >>> detect_mode_from_filename("/path/to/RM_data.txt")
    'Raman'
    """
    # Extract basename (remove path if present)
    import os
    basename = os.path.basename(filename)

    # Convert to uppercase for case-insensitive matching
    basename_upper = basename.upper()

    if basename_upper.startswith(tuple(RAMAN_PREFIXES)):
        return "Raman"

    if basename_upper.startswith(tuple(PL_PREFIXES)):
        return "PL"

    # No match
    return None
