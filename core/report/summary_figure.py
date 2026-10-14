"""
The QC Report's composed pages, drawn with matplotlib.

Two builders, both returning PNG bytes on a 13.333 x 7.5 inch page:

- `build_summary_figure()` — the overview: title bar; the 3x3 grid of raw OM
  frames with the per-class segmentation table beneath it on the left half;
  the Raman fit-summary table (optionally with trailing intensity-ratio rows)
  stacked above the PL one on the right half.
- `build_fit_grid_figure()` — one technique's nine points as three stacked
  columns (1/4/7, 2/5/8, 3/6/9), each column one image whose three panels share
  an X-axis, under one legend and one pair of axis titles serving the whole
  grid.

These replace `core/report/pptx.py`, deleted at v5.0.0 along with the
PowerPoint COM slide renderer that used to rasterize it. The page geometry is
carried over unchanged — the same inch rects, the same derived table heights,
the same aspect-preserving contain-fit for every embedded image — because it
was tuned against real samples and the proportions are the part worth keeping.
What is deliberately *not* carried over is PowerPoint's table styling: the blue
banded look came from a theme default that lives nowhere in this repo, and
these pages now sit beside five matplotlib figures in the same report. Matching
their siblings beats matching a deck nobody generates any more.

Technique-agnostic, like the module it replaces: it takes image bytes,
`PeakStat`s and `OpticalClassStat`s and imports neither technique package.
"""

import io
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")  # no display; must precede the pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.textpath import TextPath  # noqa: E402
from PIL import Image  # noqa: E402

from .models import DomainSizeStat, RAW_STAT_LABEL, OpticalClassStat, PeakStat  # noqa: E402

PAGE_WIDTH_IN, PAGE_HEIGHT_IN = 13.333, 7.5

_TITLE_LEFT, _TITLE_TOP, _TITLE_W, _TITLE_H = 0.30, 0.15, 12.73, 0.65
_TITLE_INSET = 0.10  # the text box's internal left pad, kept so the title doesn't move
_RULE_TOP, _RULE_H = 0.83, 0.02
_CONTENT_LEFT, _CONTENT_TOP = 0.30, 0.95

# ---- Summary page: OM grid (left half) ----
_GRID_W, _GRID_H = 6.20, 4.55
_GRID_GUTTER = 0.10
_GRID_CAPTION_TOP, _GRID_CAPTION_H = 5.52, 0.28

# The OM table fills the 1.7 inches the slide left blank under the grid: three
# class rows and a header at the same 0.30 in/row the stats tables use.
_OM_TABLE_TOP = 5.85
_OM_TABLE_H = 1.05
# One line under the class table saying which threshold pair produced it. A
# preset can be edited between runs, so a saved page that does not name its
# pair cannot be reproduced from itself.
_OM_NOTE_TOP = 7.00
_OM_NOTE_LEADING = 0.17

# ---- Summary page: stats tables (right half, stacked) ----
_TABLE_LEFT = 6.70
_TABLE_W = 6.33
_TABLE_CAPTION_GAP = 0.27
_RAMAN_TABLE_TOP = 1.22
# Both tables are sized from their own row counts and the PL one placed below
# whatever the Raman one needs, rather than both being fixed: the Raman table
# carries a ratio row per configured ratio, and hardcoded heights would push it
# through the table underneath as soon as a second one was added.
_TABLE_GAP = 0.54  # Raman table's bottom to the PL caption, which is 0.27 of it
_TABLE_MIN_H = 0.90
# The Raman axis note's top, below the Raman table. A 9 pt line is ~0.13 in,
# so it ends by 0.19 and clears the PL caption, centred 0.40 below the table.
_AXIS_NOTE_DROP = 0.06

# ---- Fit grid page: 3x3 fitted-spectrum grid, with one shared legend ----
# The legend and axis titles live on the page rather than inside each cell
# image. Nine copies of the same key and the same two axis titles were most of
# what made the individual spectra small; drawn once, the cells keep that space.
_FIT_LEGEND_TOP, _FIT_LEGEND_H = 0.90, 0.24
_FIT_YLABEL_W = 0.26  # left gutter holding the rotated Y-axis title
_FIT_XLABEL_TOP, _FIT_XLABEL_H = 7.12, 0.28

_FIT_GRID_LEFT = _CONTENT_LEFT + _FIT_YLABEL_W
_FIT_GRID_TOP = 1.18
_FIT_GRID_W = PAGE_WIDTH_IN - _FIT_GRID_LEFT - _CONTENT_LEFT
_FIT_GRID_H = 5.92
# Columns sit close together too: the gutter only has to separate them, and
# every hundredth of an inch here goes to the spectra.
_FIT_GRID_GUTTER = 0.06

_LEGEND_SWATCH_W, _LEGEND_SWATCH_H, _LEGEND_GAP = 0.20, 0.05, 0.06

#: Three columns across the grid's width, each running its full height: a
#: column is one image holding three stacked panels that share an X-axis.
FIT_GRID_COLUMNS = 3
_FIT_COLUMN_W = (_FIT_GRID_W - (FIT_GRID_COLUMNS - 1) * _FIT_GRID_GUTTER) / FIT_GRID_COLUMNS

#: Public: callers rendering a column figure to PNG (e.g. via
#: `export_figure_png`) should match this aspect ratio, otherwise the
#: aspect-preserving `_place_image` letterboxes it inside its column and leaves
#: part of the cell empty.
FIT_COLUMN_ASPECT_RATIO = _FIT_COLUMN_W / _FIT_GRID_H

# ---- Type ----
_FS_TITLE = 24
#: How far the title may shrink to fit the rule's width before it is allowed to
#: overflow. Below this the header stops reading as the page's title, and a
#: string that long is better shortened at the source than set in 10pt.
_FS_TITLE_MIN = 14
_FS_CAPTION = 12
_FS_GRID_CAPTION = 11
_FS_OM_NOTE = 9
_FS_LEGEND = 12
_FS_AXIS_LABEL = 12

# ---- Ink ----
_RULE_COLOR = "#404040"
_BAND_COLOR = "#f2f2f2"
_PLACEHOLDER_COLOR = "#000000"
_MUTED_COLOR = "#999999"
_MUTED_TEXT = "#555555"
_FLAG_COLOR = "#b00020"

_DASH = "—"  # an em dash, for a cell whose quantity does not exist


def _hex_to_rgb(value: str) -> Tuple[float, float, float]:
    """A peak template's "#RRGGBB" as an RGB triple, falling back to black for
    anything unparseable.

    `FittedPeak.color`, unlike `PeakDefinition.color`, is never validated, and
    this is the one consumer strict enough to notice: matplotlib would happily
    accept a CSS name here, so a legend swatch drawn from an unvalidated colour
    would silently disagree with the line it describes. That is why
    `modules.spectra.viz.palette` states its colours as hex and nothing else —
    this function is the reason that rule has teeth. It moved here from
    `core.report.pptx` at v5.0.0 when that module was deleted; the contract is
    unchanged, only its address.
    """
    try:
        raw = value.lstrip("#")
        return (
            int(raw[0:2], 16) / 255.0,
            int(raw[2:4], 16) / 255.0,
            int(raw[4:6], 16) / 255.0,
        )
    except (AttributeError, ValueError, IndexError):
        return (0.0, 0.0, 0.0)


# --------------------------------------------------------------- page helpers


def _new_page(dpi: int):
    fig = plt.figure(figsize=(PAGE_WIDTH_IN, PAGE_HEIGHT_IN), dpi=dpi)
    fig.patch.set_facecolor("white")
    return fig


def _rect(left: float, top: float, w: float, h: float) -> List[float]:
    """An inches rect measured from the page's top-left, as a figure rect.

    Every constant above is a PowerPoint-style top-left measurement, and
    matplotlib's origin is bottom-left. Converting in one place keeps the
    constants readable against the layout they came from.
    """
    return [
        left / PAGE_WIDTH_IN,
        1.0 - (top + h) / PAGE_HEIGHT_IN,
        w / PAGE_WIDTH_IN,
        h / PAGE_HEIGHT_IN,
    ]


def _blank_axes(fig, left: float, top: float, w: float, h: float):
    ax = fig.add_axes(_rect(left, top, w, h))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return ax


def _text(fig, left: float, top: float, text: str, *, size: float,
          bold: bool = False, italic: bool = False, va: str = "baseline",
          ha: str = "left", color: str = "black", rotation: float = 0.0) -> None:
    fig.text(
        left / PAGE_WIDTH_IN, 1.0 - top / PAGE_HEIGHT_IN, text,
        fontsize=size, fontweight="bold" if bold else "normal",
        fontstyle="italic" if italic else "normal",
        va=va, ha=ha, color=color, rotation=rotation,
    )


def _fit_title_size(text: str, available_in: float, base_size: float) -> float:
    """The largest size at or below `base_size` that keeps `text` inside
    `available_in` inches.

    The same problem `_fit_font_size` solves for a table cell, one line up: a
    figure title is not clipped to anything, so an over-wide one simply draws
    off the page. Every fit-grid page did, losing the tail of "— fitted
    spectra (9 points)", because the header carries a subtitle the summary
    page's does not and nothing measured the result.

    Width is linear in size, so one division is exact and no search is needed.
    """
    drawn_pt = _text_width_pt(text, base_size, bold=True)
    available_pt = available_in * 72.0
    if drawn_pt <= available_pt:
        return base_size
    return max(_FS_TITLE_MIN, base_size * available_pt / drawn_pt)


def _add_title_bar(fig, sample_name: str, material_name: str, report_date: str,
                   subtitle: Optional[str] = None) -> None:
    text = f"{sample_name}   |   Material: {material_name}   |   {report_date}"
    if subtitle:
        text += f"   |   {subtitle}"
    _text(
        fig, _TITLE_LEFT + _TITLE_INSET, _TITLE_TOP + _TITLE_H / 2, text,
        # Fitted to the rule beneath it, which is what the title reads as
        # sitting on, inset at the right by as much as at the left so a shrunk
        # title stops short of the rule's end rather than touching it. A short
        # title -- the summary page's -- measures under the limit and keeps the
        # full size, so that page is unchanged.
        size=_fit_title_size(text, _TITLE_W - 2 * _TITLE_INSET, _FS_TITLE),
        bold=True, va="center",
    )
    fig.add_artist(Rectangle(
        (_TITLE_LEFT / PAGE_WIDTH_IN, 1.0 - (_RULE_TOP + _RULE_H) / PAGE_HEIGHT_IN),
        _TITLE_W / PAGE_WIDTH_IN, _RULE_H / PAGE_HEIGHT_IN,
        transform=fig.transFigure, facecolor=_RULE_COLOR, edgecolor="none",
    ))


def _add_placeholder(fig, left: float, top: float, w: float, h: float) -> None:
    """An empty black-outlined, unfilled box marking missing content (no image,
    no fit, no stats) — deliberately blank rather than explaining why, so the
    report stays visually clean."""
    ax = _blank_axes(fig, left, top, w, h)
    ax.add_patch(Rectangle(
        (0, 0), 1, 1, transform=ax.transAxes,
        facecolor="none", edgecolor=_PLACEHOLDER_COLOR, linewidth=1.0,
    ))


def _place_image(fig, image_bytes: bytes, left: float, top: float,
                 max_w: float, max_h: float) -> None:
    """Draw `image_bytes` aspect-preserving-scaled to fit within (max_w, max_h)
    inches, centered in that box.

    The axes is sized to the *fitted* rect rather than the full cell, so the
    letterbox is page background rather than a stretched image — the same
    contain-fit the deck used, without needing to know the image's pixels
    beforehand.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            array = np.asarray(im.convert("RGB"))
    except Exception:
        _add_placeholder(fig, left, top, max_w, max_h)
        return

    native_h, native_w = array.shape[0], array.shape[1]
    if not native_w or not native_h:
        _add_placeholder(fig, left, top, max_w, max_h)
        return

    native_ratio = native_w / native_h
    if native_ratio > max_w / max_h:
        width, height = max_w, max_w / native_ratio
    else:
        width, height = max_h * native_ratio, max_h

    ax = fig.add_axes(_rect(
        left + (max_w - width) / 2, top + (max_h - height) / 2, width, height
    ))
    ax.imshow(array, aspect="auto", interpolation="antialiased")
    ax.axis("off")


# ---------------------------------------------------------------------- tables


#: Fraction of a column a cell's text may occupy before the table's font is
#: shrunk to fit. Below 1.0 so neighbouring columns keep visible air between
#: them rather than merely not overlapping.
_CELL_FILL = 0.92

#: Never shrink past this, however wide the number. A table nobody can read is
#: no better than one that overlaps; at this point the value itself is the
#: problem and should be caught in review.
_MIN_TABLE_FONT = 7.0


def _text_width_pt(text: str, size: float, bold: bool) -> float:
    """Rendered width of `text` in points, without needing a live renderer."""
    if not text:
        return 0.0
    prop = FontProperties(size=size, weight="bold" if bold else "normal")
    return TextPath((0, 0), text, size=size, prop=prop).get_extents().width


def _fit_font_size(headers: Sequence[str], rows: Sequence[Sequence[str]],
                   col_fracs: Sequence[float], table_w_in: float,
                   base_size: float) -> float:
    """The largest size at or below `base_size` that keeps every cell inside
    its own column.

    Column fractions are fixed, but the values are not: a PL intensity runs to
    "50122.8 ± 4110.2" where a Raman FWHM is "6.9 ± 0.3". Centred text in a
    matplotlib axes is not clipped to anything, so an over-wide cell simply
    draws across its neighbour — which is how the PL table's Intensity and FWHM
    columns came to overlap and print an unreadable "3980.31.4 ± 2.0". The
    PowerPoint table this replaced auto-shrank its own cell text; nothing here
    does that unless it is asked to.
    """
    widths_pt = [frac * table_w_in * 72.0 for frac in col_fracs]
    worst = 1.0
    for row, bold in ((headers, True), *((r, False) for r in rows)):
        for value, column_pt in zip(row, widths_pt):
            drawn = _text_width_pt(str(value), base_size, bold)
            if drawn <= 0:
                continue
            worst = min(worst, (column_pt * _CELL_FILL) / drawn)
    return max(_MIN_TABLE_FONT, min(base_size, base_size * worst))


def _draw_table(fig, left: float, top: float, w: float, h: float,
                headers: Sequence[str], rows: Sequence[Sequence[str]],
                col_fracs: Sequence[float], font_size: float,
                bold_from: Optional[int] = None) -> None:
    """A plain banded table: bold header over a rule, alternating row shading.

    Styled to sit beside `om_grid` and `peak_quality` rather than to imitate
    the PowerPoint table this replaces — see the module docstring.

    `bold_from` bolds every row from that index onward, which is how the
    intensity-ratio rows are set apart from the peak rows above them.
    """
    ax = _blank_axes(fig, left, top, w, h)
    ax.set_ylim(1, 0)  # y increases downward, so row 0 is the header

    font_size = _fit_font_size(headers, rows, col_fracs, w, font_size)

    n_rows = len(rows) + 1
    row_h = 1.0 / n_rows

    edges = [0.0]
    for frac in col_fracs:
        edges.append(edges[-1] + frac)

    def _cell(row_index: int, col_index: int, value: str, bold: bool) -> None:
        y = (row_index + 0.5) * row_h
        if col_index == 0:
            x, ha = edges[0] + 0.008, "left"
        else:
            x, ha = (edges[col_index] + edges[col_index + 1]) / 2, "center"
        ax.text(x, y, value, ha=ha, va="center", fontsize=font_size,
                fontweight="bold" if bold else "normal")

    for band in range(1, n_rows):
        if band % 2 == 0:
            ax.add_patch(Rectangle((0, band * row_h), 1, row_h,
                                   facecolor=_BAND_COLOR, edgecolor="none"))

    for col_index, header in enumerate(headers):
        _cell(0, col_index, header, bold=True)
    ax.add_line(Line2D([0, 1], [row_h, row_h], color=_RULE_COLOR, linewidth=1.0))

    for row_offset, row in enumerate(rows, start=1):
        bold = bold_from is not None and (row_offset - 1) >= bold_from
        for col_index, value in enumerate(row):
            _cell(row_offset, col_index, value, bold=bold)


def _table_font(n_data_rows: int) -> int:
    """Font that keeps `n_data_rows` legible without overflowing the table."""
    if n_data_rows <= 6:
        return 13
    return 11 if n_data_rows <= 10 else 9


def _table_height(n_data_rows: int) -> float:
    """Inches a table of `n_data_rows` plus its header needs.

    Derived rather than fixed so the caller can stack tables without knowing
    how many ratio rows the one above it ended up with.
    """
    per_row = {13: 0.30, 11: 0.26}.get(_table_font(n_data_rows), 0.22)
    return max(_TABLE_MIN_H, (n_data_rows + 1) * per_row)


def _stats_caption(technique_label: str, stats: Optional[Sequence[PeakStat]],
                   has_ratios: bool) -> str:
    """"fit summary" would be wrong for a table whose first row is measured off
    the spectrum rather than fitted, so the heading follows what's actually in
    it. Kept no longer than the ratios variant, which is known to fit on one
    line — a wrapped caption overlaps the table below it."""
    spread = "peaks mean ± std, ratios median ± MAD" if has_ratios else "mean ± std"
    has_empirical = any(stat.label == RAW_STAT_LABEL for stat in (stats or []))
    return (
        f"{technique_label} summary (empirical + fitted, {spread})"
        if has_empirical else f"{technique_label} fit summary ({spread})"
    )


def stats_table_content(
    stats: Optional[Sequence[PeakStat]],
    ratios: Optional[Sequence[Tuple[str, Tuple[float, float, int]]]] = None,
    show_fwhm_v1: bool = False,
) -> Tuple[List[str], List[List[str]], List[float], int]:
    """One technique's table as (headers, rows, column fractions, bold_from).

    Separate from the drawing so the rules it encodes — what a ratio row looks
    like, where it sits, how an unmeasurable width is printed — can be checked
    without rendering a canvas and reading pixels back. Each of those rules
    came from a real reporting bug; see `tests/unit/test_summary_figure.py`.

    Each entry of `ratios` becomes a row below the peaks: its label in the Peak
    column and `median ± MAD` in the Intensity column (they are ratios of
    intensities), with center/FWHM dashed out since they don't apply. Labels
    are expected to mark themselves as medians, since the peak rows above are
    means. `bold_from` is where those rows begin, which is how the renderer
    sets them apart.

    `show_fwhm_v1` inserts a "FWHM (v1)" column carrying the pre-v3.4.0
    Gaussian-only width, for a caller who wants the deprecated value in the
    report for comparison. Off by default, since every other reporting surface
    shows only the correct (post-v3.4.0) width.
    """
    stats = list(stats or [])
    ratios = list(ratios or [])

    headers = ["Peak", "Center", "Intensity", "FWHM", "n"]
    col_fracs = [0.30, 0.23, 0.23, 0.16, 0.08]
    if show_fwhm_v1:
        # FWHM (v1) inserted right after FWHM; the rest of the row shrinks to
        # make room rather than growing the table past its allotted width.
        headers = ["Peak", "Center", "Intensity", "FWHM", "FWHM (v1)", "n"]
        col_fracs = [0.24, 0.19, 0.19, 0.14, 0.14, 0.10]

    rows: List[List[str]] = []
    for stat in stats:
        row = [
            stat.label,
            f"{stat.center_mean:.1f} ± {stat.center_std:.1f}",
            f"{stat.intensity_mean:.1f} ± {stat.intensity_std:.1f}",
            f"{stat.fwhm_mean:.1f} ± {stat.fwhm_std:.1f}" if stat.fwhm_mean is not None else _DASH,
        ]
        if show_fwhm_v1:
            row.append(
                f"{stat.fwhm_v1_mean:.1f} ± {stat.fwhm_v1_std:.1f}"
                if stat.fwhm_v1_mean is not None else _DASH
            )
        row.append(str(stat.n))
        rows.append(row)

    for ratio_label, (median, mad, n) in ratios:
        # Three decimals: these ratios run around 0.1, where two would round
        # away most of the point-to-point variation the rows exist to show.
        row = [ratio_label, _DASH, f"{median:.3f} ± {mad:.3f}", _DASH]
        if show_fwhm_v1:
            row.append(_DASH)
        row.append(str(n))
        rows.append(row)

    return headers, rows, col_fracs, len(stats)


def _add_stats_table(fig, technique_label: str, stats: Optional[Sequence[PeakStat]],
                     left: float, top: float, w: float, h: float,
                     ratios: Optional[Sequence[Tuple[str, Tuple[float, float, int]]]] = None,
                     show_fwhm_v1: bool = False) -> None:
    """One technique's fit-summary table, with its caption above it.

    A leading `RAW_STAT_LABEL` row is the empirical measurement rather than a
    fitted peak, and the caption says so instead of calling the table a "fit
    summary". A technique with no stats gets a placeholder box rather than a
    missing table, so the page's shape does not change with its contents.
    """
    _text(fig, left, top - _TABLE_CAPTION_GAP / 2,
          _stats_caption(technique_label, stats, bool(ratios)),
          size=_FS_CAPTION, bold=True, va="center")

    if not stats:
        _add_placeholder(fig, left, top, w, h)
        return

    headers, rows, col_fracs, bold_from = stats_table_content(
        stats, ratios, show_fwhm_v1
    )
    _draw_table(fig, left, top, w, h, headers, rows, col_fracs,
                _table_font(len(rows)), bold_from=bold_from)


def _add_om_note(fig, note: Optional[str]) -> None:
    """The threshold pair that produced the table above.

    A plain string rather than a threshold object: the segmentation lives in
    `modules.optical`, and `core` cannot import it. The page formats, this
    draws.
    """
    if note:
        _text(fig, _CONTENT_LEFT, _OM_NOTE_TOP, note,
              size=_FS_OM_NOTE, color=_MUTED_TEXT)


def _add_om_table(fig, classes: Optional[Sequence[OpticalClassStat]]) -> None:
    """The segmentation's per-class coverage and contrast, under the OM grid.

    Three rows, matching the QC figures' own class tiles rather than the
    nine-row per-frame detail — that belongs in the workbook, and nine rows
    would not fit here beside two more tables.
    """
    if not classes:
        return

    headers = ["Class", "Coverage %", "Contrast %", "N"]
    col_fracs = [0.40, 0.24, 0.24, 0.12]
    rows = [
        [
            entry.label,
            f"{entry.coverage_mean:.1f} ± {entry.coverage_std:.1f}",
            (
                f"{entry.contrast_mean:.1f} ± {entry.contrast_std:.1f}"
                if entry.contrast_mean is not None else _DASH
            ),
            str(entry.n_frames),
        ]
        for entry in classes
    ]
    _draw_table(fig, _CONTENT_LEFT, _OM_TABLE_TOP, _GRID_W, _OM_TABLE_H,
                headers, rows, col_fracs, _table_font(len(rows)))


def _add_domain_table(fig, stat: Optional[DomainSizeStat],
                      left: float, top: float, w: float, h: float,
                      class_label: Optional[str] = None) -> None:
    """Pooled domain-size statistics, one row, stacked below the PL table.

    A placeholder box when domain-size analysis did not run at all (no OM
    frames, or no domain found to split) -- the same convention `_add_stats_table`
    uses for a technique with no stats, so a missing block always reads the
    same way regardless of which one it is.

    Reports pixel units, honestly labelled, when the sample's magnification
    has no micron-per-pixel calibration set -- see `DomainSizeStat`'s own
    docstring. This never invents a calibration to fill the gap.

    `class_label` names the OM class the domains were split from (e.g.
    "Bilayer", "Below 2L"), the same wording the OM class table uses. None
    omits it -- the caption reads just "Domain size" as before.
    """
    caption = "Domain size (equivalent circular diameter)"
    if class_label:
        caption += f" — {class_label}"
    _text(fig, left, top - _TABLE_CAPTION_GAP / 2, caption,
          size=_FS_CAPTION, bold=True, va="center")

    if stat is None or stat.n_domains == 0:
        _add_placeholder(fig, left, top, w, h)
        return

    if stat.populations:
        # Two rows (small, large) instead of the pooled one -- a ≥2x size
        # gap was found (domain_size.split_size_populations), so the pooled
        # mean alone would blend two real, differently-sized flake
        # populations into a number that describes neither. A leading
        # "Population" column is what tells the two rows apart; the unsplit
        # table below has never needed one. `Frames` repeats the
        # sample-level count on both rows: populations aren't counted
        # per-frame, only domains are.
        headers = ["Size", "Domains (n)", "Frames",
                   f"Median ({stat.unit})", f"Mean ± Std ({stat.unit})",
                   "Density (/(100µm)²)"]
        col_fracs = [0.09, 0.14, 0.11, 0.19, 0.22, 0.25]
        rows = [
            [pop.label.capitalize(), str(pop.n), str(stat.n_frames),
             f"{pop.diameter_median:.2f}", f"{pop.diameter_mean:.2f} ± {pop.diameter_std:.2f}",
             f"{pop.density_per_100um2:.1f}" if pop.density_per_100um2 is not None else _DASH]
            for pop in stat.populations
        ]
    else:
        # "Edge (×½)": domains touching a frame's border -- not sized, so in
        # no diameter column, but counted one half each in Density.
        headers = ["Domains (n)", "Edge (×½)", "Frames", f"Median ({stat.unit})",
                   f"Mean ± Std ({stat.unit})", "Density (/(100µm)²)"]
        col_fracs = [0.13, 0.11, 0.10, 0.18, 0.23, 0.25]
        rows = [[
            str(stat.n_domains), str(stat.n_edge), str(stat.n_frames),
            f"{stat.diameter_median:.2f}", f"{stat.diameter_mean:.2f} ± {stat.diameter_std:.2f}",
            f"{stat.density_per_100um2:.1f}" if stat.density_per_100um2 is not None else _DASH,
        ]]
    _draw_table(fig, left, top, w, h, headers, rows, col_fracs, _table_font(len(rows)))

    if not stat.calibrated:
        _text(fig, left, top + h + 0.03,
             "No µm/px calibration set for this magnification — sizes shown in pixels.",
             size=_FS_OM_NOTE, color=_MUTED_TEXT)


# ------------------------------------------------------------------- OM grid


def _add_om_grid(fig, om_image_bytes: Dict[int, bytes],
                 magnification_label: Optional[str]) -> None:
    """The nine raw microscope frames, row-major, point 1 top-left.

    Raw frames, not the segmented overlays: this page says "here is the
    sample", and the OM analysis figure saved beside it says "here is what
    segmentation did to it". Drawing the overlay twice would spend the
    overview's whole left half restating the next image.
    """
    cell_w = (_GRID_W - 2 * _GRID_GUTTER) / 3
    cell_h = (_GRID_H - 2 * _GRID_GUTTER) / 3

    for point in range(1, 10):
        row, col = divmod(point - 1, 3)
        cell_left = _CONTENT_LEFT + col * (cell_w + _GRID_GUTTER)
        cell_top = _CONTENT_TOP + row * (cell_h + _GRID_GUTTER)

        image_bytes = om_image_bytes.get(point)
        if image_bytes is not None:
            _place_image(fig, image_bytes, cell_left, cell_top, cell_w, cell_h)
        else:
            _add_placeholder(fig, cell_left, cell_top, cell_w, cell_h)

    caption = "OM"
    if magnification_label:
        caption += f" ({magnification_label})"
    _text(fig, _CONTENT_LEFT, _GRID_CAPTION_TOP + _GRID_CAPTION_H / 2, caption,
          size=_FS_GRID_CAPTION, italic=True, va="center")


# ------------------------------------------------------------------- builders


def build_summary_figure(
    *,
    sample_name: str,
    material_name: str,
    report_date: str,
    magnification_label: Optional[str] = None,
    om_image_bytes: Optional[Dict[int, bytes]] = None,
    om_classes: Optional[Sequence[OpticalClassStat]] = None,
    om_threshold_note: Optional[str] = None,
    raman_stats: Optional[Sequence[PeakStat]] = None,
    pl_stats: Optional[Sequence[PeakStat]] = None,
    raman_ratios: Optional[Sequence[Tuple[str, Tuple[float, float, int]]]] = None,
    show_fwhm_v1: bool = False,
    domain_stat: Optional[DomainSizeStat] = None,
    domain_class_label: Optional[str] = None,
    raman_axis_note: Optional[str] = None,
    dpi: int = 144,
) -> bytes:
    """Render the overview page and return it as PNG bytes.

    `raman_axis_note` is one line under the Raman table saying whether its
    centres are on a laser-corrected axis — a plain string for the same reason
    as `om_threshold_note`: the calibration lives in `modules`. None draws
    nothing.

    `raman_ratios` is a sequence of (label, (median, MAD, n)) — per-point
    peak-intensity ratios (see `peak_metrics.compute_peak_intensity_ratio`)
    appended as bolded rows below the Raman fit summary, in the order given.
    Empty or None adds no rows.

    `om_threshold_note` is one line naming the threshold pair that produced
    `om_classes` — a preset can be edited between runs, so a page that does
    not say which pair ran cannot be reproduced from itself.

    `om_classes` is the segmentation summary drawn under the image grid;
    None omits the table, which is what an unanalysed or images-only folder
    gets. A technique with no stats gets a placeholder box rather than a
    missing table, so the page's shape does not change with its contents.

    `domain_stat` is the pooled flake domain-size summary, drawn as a third
    table stacked below the PL one on the right side -- the left side has no
    room left, the OM table already fills what was free there. None (no
    domain-size analysis ran, e.g. no OM frames) gets a placeholder box like
    the other two tables. `domain_class_label` names the OM class it was
    measured from (e.g. "Bilayer"); None omits it from the caption.

    At the default 144 dpi the page is 1920x1080.
    """
    fig = _new_page(dpi)
    try:
        _add_title_bar(fig, sample_name, material_name, report_date)
        _add_om_grid(fig, om_image_bytes or {}, magnification_label)
        _add_om_table(fig, om_classes)
        _add_om_note(fig, om_threshold_note)

        raman_height = _table_height(len(raman_stats or []) + len(raman_ratios or []))
        _add_stats_table(
            fig, "Raman", raman_stats, _TABLE_LEFT, _RAMAN_TABLE_TOP, _TABLE_W,
            raman_height, ratios=raman_ratios, show_fwhm_v1=show_fwhm_v1,
        )
        if raman_axis_note and raman_stats:
            # In the gap above the PL caption, which sits _TABLE_GAP / 2 lower.
            _text(fig, _TABLE_LEFT, _RAMAN_TABLE_TOP + raman_height + _AXIS_NOTE_DROP,
                  raman_axis_note, size=_FS_OM_NOTE, color=_MUTED_TEXT, va="top")
        pl_top = _RAMAN_TABLE_TOP + raman_height + _TABLE_GAP
        pl_height = _table_height(len(pl_stats or []))
        _add_stats_table(
            fig, "PL", pl_stats, _TABLE_LEFT, pl_top, _TABLE_W,
            pl_height, show_fwhm_v1=show_fwhm_v1,
        )
        domain_rows = 2 if domain_stat and domain_stat.populations else 1
        _add_domain_table(
            fig, domain_stat, _TABLE_LEFT, pl_top + pl_height + _TABLE_GAP,
            _TABLE_W, _table_height(domain_rows), class_label=domain_class_label,
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)


def _add_fit_legend(fig, entries: Optional[Sequence[Tuple[str, str]]]) -> None:
    """One legend for all nine cells: a coloured swatch and label per trace,
    spread evenly across the grid's width above it.

    Drawn on the page rather than into the cell images so it appears once
    instead of nine times.
    """
    if not entries:
        return

    slot_w = _FIT_GRID_W / len(entries)
    swatch_top = _FIT_LEGEND_TOP + (_FIT_LEGEND_H - _LEGEND_SWATCH_H) / 2

    for i, (label, color) in enumerate(entries):
        slot_left = _FIT_GRID_LEFT + i * slot_w
        fig.add_artist(Rectangle(
            (slot_left / PAGE_WIDTH_IN,
             1.0 - (swatch_top + _LEGEND_SWATCH_H) / PAGE_HEIGHT_IN),
            _LEGEND_SWATCH_W / PAGE_WIDTH_IN, _LEGEND_SWATCH_H / PAGE_HEIGHT_IN,
            transform=fig.transFigure, facecolor=_hex_to_rgb(color), edgecolor="none",
        ))
        _text(
            fig, slot_left + _LEGEND_SWATCH_W + _LEGEND_GAP,
            _FIT_LEGEND_TOP + _FIT_LEGEND_H / 2, label,
            size=_FS_LEGEND, va="center",
        )


def build_fit_grid_figure(
    *,
    sample_name: str,
    material_name: str,
    report_date: str,
    technique: str,
    column_images: Dict[int, bytes],
    legend: Optional[Sequence[Tuple[str, str]]] = None,
    x_label: str = "",
    y_label: str = "Normalized intensity",
    dpi: int = 144,
) -> bytes:
    """Render one technique's nine fitted spectra as a page, and return PNG bytes.

    `column_images` maps column index (0, 1, 2) to one PNG holding that
    column's three stacked points — 0 is points 1/4/7, 1 is 2/5/8, 2 is 3/6/9 —
    rendered at `FIT_COLUMN_ASPECT_RATIO`. A missing column becomes a
    placeholder, keeping the others in their usual positions.

    `legend` is (label, "#RRGGBB") pairs describing the traces, drawn once
    above the grid. The column images are expected to carry no legend of their
    own; passing None just omits it.
    """
    fig = _new_page(dpi)
    try:
        _add_title_bar(
            fig, sample_name, material_name, report_date,
            subtitle=f"{technique} — fitted spectra (9 points)",
        )
        _add_fit_legend(fig, legend)

        # Rotated in the left gutter, centred on the grid's height.
        _text(fig, _CONTENT_LEFT + _FIT_YLABEL_W / 2, _FIT_GRID_TOP + _FIT_GRID_H / 2,
              y_label, size=_FS_AXIS_LABEL, va="center", ha="center", rotation=90)
        _text(fig, _FIT_GRID_LEFT + _FIT_GRID_W / 2, _FIT_XLABEL_TOP + _FIT_XLABEL_H / 2,
              x_label, size=_FS_AXIS_LABEL, va="center", ha="center")

        for col in range(FIT_GRID_COLUMNS):
            col_left = _FIT_GRID_LEFT + col * (_FIT_COLUMN_W + _FIT_GRID_GUTTER)
            image_bytes = column_images.get(col)
            if image_bytes is not None:
                _place_image(fig, image_bytes, col_left, _FIT_GRID_TOP,
                             _FIT_COLUMN_W, _FIT_GRID_H)
            else:
                _add_placeholder(fig, col_left, _FIT_GRID_TOP,
                                 _FIT_COLUMN_W, _FIT_GRID_H)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)
