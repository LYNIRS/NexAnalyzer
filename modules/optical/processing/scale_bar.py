"""
Scale-bar reading: micron-per-pixel from an OM image that carries one.

The microscope software can burn a scale bar ("20 µm" over a solid bar, in a
filled box) into an exported image. This module finds the bar, measures its
length in pixels, and reads the label, so the Material Presets page can turn
one such image into a `core.io.om_calibration` entry without anyone counting
pixels by hand.

Three steps, kept separate so the page can fall back to the operator at each:

1. `detect_scale_bar()` -- the bar is a band of rows that all carry the same
   long saturated run, with contrasting pixels just past both ends. Film
   texture produces long bright runs too, but not identical ones stacked
   several rows deep with a clean edge on each side. The length is refined to
   sub-pixel from the end pixels' partial coverage, so an anti-aliased bar
   measures as precisely as a crisp one (and a crisp one exactly).
2. `read_scale_label()` -- template matching, not OCR. Each character of the
   label is cut out as a connected component and compared against digits and
   unit letters drawn from fonts every installation has (matplotlib's
   DejaVu Sans, plus Arial where Windows provides it). That reads the label
   the microscope software prints without a Tesseract install on every PC.
   Anything that doesn't parse as number + µm/um/nm/mm returns None rather
   than a guess -- the page then asks the operator to type the length.
3. `um_per_native_px()` -- the calibration store is per *native* camera pixel
   (2240 px wide on the VB1P01 camera; see `core.io.om_calibration`), while a
   scale-bar export is often smaller (1024 px). Measuring on the export and
   storing the result unscaled would be wrong by the export's shrink factor.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

#: Width of the full-size frame the VB1P01 camera saves, in pixels.
DEFAULT_NATIVE_WIDTH = 2240

#: A bar shorter than this fraction of the image width is not considered.
MIN_BAR_FRACTION = 0.05
#: Rows a bar must span. One- or two-row bright runs are texture or an edge.
MIN_BAR_THICKNESS = 3
#: Bar-to-flank contrast, as a fraction of the image's 0..1 grey range.
MIN_END_CONTRAST = 0.25

_UNIT_TO_UM = {"µm": 1.0, "um": 1.0, "nm": 1e-3, "mm": 1e3}
_LABEL_RE = re.compile(r"^(\d+(?:\.\d+)?)(µm|um|nm|mm)$")
_GLYPHS = "0123456789µumn"
_GLYPH_SIZE = 24


@dataclass(frozen=True)
class ScaleBar:
    """A detected bar. Coordinates are inclusive pixel indices."""

    x0: int
    x1: int
    y0: int
    y1: int
    #: Sub-pixel length along x. Equals x1 - x0 + 1 for a crisp bar.
    length_px: float
    #: True for a light bar on a dark box, False for dark on light.
    light: bool
    #: Grey level (0..1, in the bar's polarity) of the bar and its flanks.
    fg: float
    bg: float

    @property
    def thickness(self) -> int:
        return self.y1 - self.y0 + 1


@dataclass(frozen=True)
class ScaleLabel:
    """A label read off the image: what was seen and what it means."""

    text: str
    length_um: float


def to_gray(image) -> np.ndarray:
    """PIL image or array -> float grey in 0..1. Alpha is dropped."""
    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2) if arr.shape[2] >= 3 else arr[..., 0]
    if np.issubdtype(arr.dtype, np.integer):
        return arr.astype(float) / np.iinfo(arr.dtype).max
    arr = arr.astype(float)
    peak = arr.max() if arr.size else 1.0
    return arr / peak if peak > 1.0 else arr


def _runs(row: np.ndarray, min_len: int) -> List[Tuple[int, int]]:
    """(start, end) inclusive of every True run at least min_len long."""
    padded = np.concatenate(([False], row, [False])).astype(np.int8)
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s + 1 >= min_len]


def _bands(mask: np.ndarray, min_len: int) -> List[Tuple[int, int, int, int]]:
    """Stacks of near-identical runs: (x0, x1, y0, y1) for each."""
    open_bands: List[List[int]] = []  # [x0, x1, y0, y1]
    closed: List[Tuple[int, int, int, int]] = []
    for y in range(mask.shape[0]):
        still_open = []
        for s, e in _runs(mask[y], min_len):
            for band in open_bands:
                if band[3] == y - 1 and abs(band[0] - s) <= 1 and abs(band[1] - e) <= 1:
                    band[0], band[1], band[3] = min(band[0], s), max(band[1], e), y
                    still_open.append(band)
                    break
            else:
                still_open.append([s, e, y, y])
        for band in open_bands:
            if band not in still_open:
                closed.append(tuple(band))
        open_bands = still_open
    closed.extend(tuple(b) for b in open_bands)
    return closed


def _measure_band(v: np.ndarray, x0: int, x1: int, y0: int, y1: int):
    """(score, ScaleBar fields) for one candidate band, or None."""
    h, w = v.shape
    if x0 < 3 or x1 > w - 4:
        return None  # need flank pixels on both sides
    rows = slice(y0, y1 + 1)
    fg = float(np.median(v[rows, x0 + 1:x1]))
    flanks = np.concatenate([v[rows, x0 - 3:x0 - 1].ravel(), v[rows, x1 + 2:x1 + 4].ravel()])
    bg = float(np.median(flanks))
    contrast = fg - bg
    if contrast < MIN_END_CONTRAST:
        return None
    # Sub-pixel length: every pixel from one flank to the other contributes its
    # coverage, so a half-lit anti-aliased end counts as half a pixel.
    span = v[rows, x0 - 1:x1 + 2]
    coverage = np.clip((span - bg) / contrast, 0.0, 1.0)
    # Background noise and a bar's own shading are not partial coverage.
    coverage[coverage < 0.05] = 0.0
    coverage[coverage > 0.95] = 1.0
    length = float(np.median(coverage.sum(axis=1)))
    return length * contrast, dict(length_px=length, fg=fg, bg=bg)


def detect_scale_bar(image) -> Optional[ScaleBar]:
    """Find the scale bar, light-on-dark or dark-on-light; None if absent."""
    gray = to_gray(image)
    h, w = gray.shape
    min_len = max(10, int(round(MIN_BAR_FRACTION * w)))
    best = None
    for light in (True, False):
        v = gray if light else 1.0 - gray
        top = float(v.max())
        mask = v >= top - 0.15 * (top - float(v.min()))
        for x0, x1, y0, y1 in _bands(mask, min_len):
            thickness = y1 - y0 + 1
            if thickness < MIN_BAR_THICKNESS or thickness > max(MIN_BAR_THICKNESS, 0.1 * h):
                continue
            if thickness * 3 > x1 - x0 + 1:
                continue  # a block, not a bar
            measured = _measure_band(v, x0, x1, y0, y1)
            if measured is None:
                continue
            score, fields = measured
            if best is None or score > best[0]:
                best = (score, ScaleBar(x0=x0, x1=x1, y0=y0, y1=y1, light=light, **fields))
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Label reading
# ---------------------------------------------------------------------------

def _font_paths() -> List[str]:
    import matplotlib

    candidates = [
        os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf", "DejaVuSans.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    return [p for p in candidates if os.path.exists(p)]


def _normalise(mask: np.ndarray) -> Tuple[np.ndarray, float]:
    """Crop to ink, resample to a fixed square; plus the ink's aspect ratio."""
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ch, cw = crop.shape
    img = Image.fromarray((crop * 255).astype(np.uint8)).resize(
        (_GLYPH_SIZE, _GLYPH_SIZE), Image.BILINEAR
    )
    return np.asarray(img, dtype=float) / 255.0, cw / ch


@lru_cache(maxsize=1)
def _templates() -> Tuple[Tuple[str, np.ndarray, float, float], ...]:
    """(char, bitmap, aspect, height relative to a digit) per font and glyph."""
    out = []
    for path in _font_paths():
        font = ImageFont.truetype(path, 64)
        digit_height = None
        for char in _GLYPHS:
            canvas = Image.new("L", (120, 120), 0)
            ImageDraw.Draw(canvas).text((20, 10), char, font=font, fill=255)
            ink = np.asarray(canvas) > 127
            ys = np.nonzero(ink)[0]
            height = ys.max() - ys.min() + 1
            if digit_height is None:
                digit_height = height  # "0" comes first
            bitmap, aspect = _normalise(ink)
            out.append((char, bitmap, aspect, height / digit_height))
    return tuple(out)


def _classify(component: np.ndarray, rel_height: float) -> str:
    bitmap, aspect = _normalise(component)
    flat = bitmap.ravel() - bitmap.mean()
    best_char, best_score = "?", -np.inf
    for char, template, t_aspect, t_height in _templates():
        t_flat = template.ravel() - template.mean()
        denom = np.linalg.norm(flat) * np.linalg.norm(t_flat)
        corr = float(flat @ t_flat / denom) if denom else 0.0
        score = corr - 0.5 * abs(np.log(aspect / t_aspect)) - abs(rel_height - t_height)
        if score > best_score:
            best_char, best_score = char, score
    return best_char


def _read_region(v: np.ndarray, threshold: float) -> Optional[str]:
    """Characters of the one text line in `v` (ink is high), left to right."""
    labels, n = ndimage.label(v >= threshold)
    if n == 0:
        return None
    rh, rw = v.shape
    comps = []
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        ys, xs = sl
        # Anything cut by the region's edge is film or box border, not text.
        if ys.start == 0 or xs.start == 0 or ys.stop == rh or xs.stop == rw:
            continue
        comps.append((xs.start, ys.start, ys.stop, labels[sl] == i))
    if not comps:
        return None
    line_height = max(c[2] - c[1] for c in comps)
    if line_height < 6:
        return None
    # The baseline is where the tallest glyphs (digits) end.
    baseline = int(np.median([c[2] for c in comps if c[2] - c[1] >= 0.8 * line_height]))
    chars = []
    for x, top, bottom, mask in sorted(comps, key=lambda c: c[0]):
        height = bottom - top
        if mask.sum() < 3:
            continue
        if height < 0.3 * line_height:
            # A decimal point sits on the baseline; anything else this small is noise.
            if abs(bottom - baseline) <= max(2, 0.15 * line_height):
                chars.append(".")
            continue
        chars.append(_classify(mask, height / line_height))
    return "".join(chars) or None


def _text_regions(v: np.ndarray, bar: ScaleBar):
    """Candidate label boxes: inside the bar's box if it has one, else beside it."""
    h, w = v.shape
    span = bar.length_px
    pad = int(round(0.3 * span))
    xa, xb = max(0, bar.x0 - pad), min(w, bar.x1 + pad + 1)
    reach = int(round(max(4 * bar.thickness, 0.35 * span)))

    # The box: background-coloured pixels connected to the bar's flank.
    near_bg = v <= bar.bg + 0.25 * (bar.fg - bar.bg)
    labels, _ = ndimage.label(near_bg)
    box_id = labels[(bar.y0 + bar.y1) // 2, bar.x0 - 2]
    box = None
    if box_id:
        ys, xs = np.nonzero(labels == box_id)
        if ys.size < 0.25 * h * w:
            box = (ys.min(), ys.max() + 1, xs.min(), xs.max() + 1)

    if box is not None:
        top, bottom, left, right = box
        yield top, bar.y0, left, right
        yield bar.y1 + 1, bottom, left, right
    yield bar.y1 + 1, min(h, bar.y1 + 1 + reach), xa, xb
    yield max(0, bar.y0 - reach), bar.y0, xa, xb


def read_scale_label(image, bar: ScaleBar) -> Optional[ScaleLabel]:
    """The bar's printed length, read from beside it; None if not readable."""
    gray = to_gray(image)
    v = gray if bar.light else 1.0 - gray
    threshold = (bar.fg + bar.bg) / 2
    for top, bottom, left, right in _text_regions(v, bar):
        if bottom - top < 6 or right - left < 6:
            continue
        text = _read_region(v[top:bottom, left:right], threshold)
        if not text:
            continue
        match = _LABEL_RE.match(text)
        if match:
            value = float(match.group(1))
            if value > 0:
                return ScaleLabel(text=f"{match.group(1)} {match.group(2)}",
                                  length_um=value * _UNIT_TO_UM[match.group(2)])
    return None


def um_per_native_px(
    length_um: float, length_px: float, image_width: int, native_width: int = DEFAULT_NATIVE_WIDTH
) -> float:
    """µm per native camera pixel, from a bar measured on a (possibly resized) image."""
    if length_um <= 0 or length_px <= 0 or image_width <= 0 or native_width <= 0:
        raise ValueError("lengths and widths must be positive")
    return (length_um / length_px) * (image_width / native_width)
