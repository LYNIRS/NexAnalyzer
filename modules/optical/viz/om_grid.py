"""
Image 1 of the QC Panel: the optical-microscopy analysis figure.

Layout follows the WSe2 analysis grid this replaces — grid positions as
Original / segmented-overlay pairs, three positions per row — with one addition:
a row of the green-channel histograms underneath, each marked with its mode
and both thresholds. A ten-point wafer instead mirrors its measurement map:
rows of 2-3-3-2, the short rows centred, so P1..P10 land where they sit on
the wafer.

That row is the point of the figure. Coverage percentages alone cannot show
whether a frame segmented sensibly; the predecessor algorithm's characteristic
failure was every position reading 99 %+ of one class, and in the overlay that
looks like a clean wafer rather than a broken measurement. The histogram shows
where the thresholds actually landed, so a saturated frame is visible as one.

matplotlib rather than Plotly: these are static report images, an eighteen-frame
raster montage is not something Plotly renders well, and matching the inherited
figures matters more here than matching the app's interactive charts.
"""

import io
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # no display; must precede the pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import gridspec  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from ..processing.contrast import GREEN, RED, FrameResult, class_summary  # noqa: E402

GRID_COLUMNS = 3
"""Grid positions per row, so nine positions fill three rows."""

#: Wafer-map row layouts keyed by position count. A ten-point wafer is
#: measured 2-3-3-2 -- two points on the top and bottom chords, three across
#: each middle row -- and the figure mirrors that so P1..P10 land where they
#: sit on the wafer. Any count without an entry fills GRID_COLUMNS-wide rows.
_ROW_LAYOUTS = {10: (2, 3, 3, 2)}


def _row_layout(n_points: int) -> tuple:
    """Row lengths for `n_points` grid positions."""
    layout = _ROW_LAYOUTS.get(n_points)
    if layout is None:
        layout = (GRID_COLUMNS,) * (n_points // GRID_COLUMNS)
        if n_points % GRID_COLUMNS:
            layout += (n_points % GRID_COLUMNS,)
    return layout


def _grid_units(n_points: int) -> int:
    """Width of the layout grid, in cells.

    lcm(6, n): each Original/overlay pair spans units/3 cells (so three pairs
    fill a row), a two-pair row centres on a units/6 offset, and the n
    histograms below span units/n each -- all of which must come out whole.
    Nine positions give the 18 this module always used; ten give 30.
    """
    return int(np.lcm(6, max(n_points, 1)))

#: Gap between one position's raw and analyzed panels, on their own nested
#: GridSpec so it can be tighter than `_POSITION_WSPACE` -- that value is
#: shared by every column boundary, so narrowing it there would also close the
#: gap between one position and the next. `wspace` is a fraction of the
#: column it divides, not an absolute size, so both this and
#: `_POSITION_WSPACE` are back-solved for a specific pixel target rather than
#: chosen by eye: at this module's fixed `figsize` width and `dpi=160`, with
#: panels near the ~4:3 aspect a camera frame crops to, 0.0241 measures out to
#: a 10 px gap and 0.3797 to 40 px. A frame whose aspect strays far from that
#: -- letterboxed inside its panel -- widens the visible gap beyond these
#: figures; the fraction still targets the panel box, not the pixels drawn
#: inside it.
_PAIR_WSPACE = 0.0241
_POSITION_WSPACE = 0.3797

_FS_SUPTITLE = 17
_FS_PANEL = 10
_FS_COVERAGE = 10
_FS_HIST = 7

_DIM_SCALE, _DIM_OFFSET = 0.30, 26
"""For a circular frame, the excluded margin is an eroded disc, so its own
bounding box still has invalid corners -- those are dimmed rather than
cropped a second time, and dimmed rather than blacked out so nine such
corners don't dominate the page."""


def _crop_to_valid(img: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trim the excluded margin off `img`, so a rectangular frame's border is
    gone entirely rather than shown dimmed. A circular frame's `valid` is an
    eroded disc, so its bounding box still has invalid corners -- `valid` is
    cropped alongside `img` so `_dim()` can still mark those."""
    rows = np.flatnonzero(valid.any(axis=1))
    cols = np.flatnonzero(valid.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return img, valid
    r0, r1 = rows[0], rows[-1] + 1
    c0, c1 = cols[0], cols[-1] + 1
    return img[r0:r1, c0:c1], valid[r0:r1, c0:c1]


def _dim(img: np.ndarray, valid: np.ndarray) -> np.ndarray:
    return np.where(valid[..., None], img, img * _DIM_SCALE + _DIM_OFFSET).astype(np.uint8)


def _blank(ax) -> None:
    """An empty cell for a grid position that has no frame."""
    ax.set_facecolor("#f2f2f2")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#cccccc")


def _histogram(ax, frame: FrameResult) -> None:
    ctr, hs = frame.hist_centers, frame.hist_counts
    ax.fill_between(ctr, hs, color="#bbbbbb", alpha=0.4)
    ax.plot(ctr, hs, color="#444444", lw=0.7)
    ax.fill_between(ctr, hs, where=ctr < frame.threshold_low, color="#c03030", alpha=0.35)
    ax.fill_between(ctr, hs, where=ctr > frame.threshold_high, color="#2080b0", alpha=0.35)
    for x, colour, style in (
        (frame.mode, "black", "-"),
        (frame.threshold_low, "#c03030", "--"),
        (frame.threshold_high, "#2080b0", "--"),
    ):
        ax.axvline(x, color=colour, ls=style, lw=0.9, alpha=0.75)
    ax.set_yscale("log")
    ax.set_ylim(0.8, max(hs.max() * 2, 2.0))
    ax.set_title(f"P{frame.point}", fontsize=_FS_HIST + 1, fontweight="bold", pad=2)
    # Inside the axes, not in the title: at nine panels across, a title long
    # enough to carry these numbers overruns its neighbours and the row becomes
    # an unreadable smear of overlapping text.
    #
    # sigma_L and sigma_R are both shown because their disagreement is the
    # signal that one side carries a domain population rather than noise; the
    # narrower of the two is what sets the thresholds.
    flag = "\nshoulder" if frame.shoulder else ""
    ax.text(
        0.03, 0.96,
        f"mode {frame.mode:.1f}\n"
        f"σL {frame.sigma_l:.2f} / σR {frame.sigma_r:.2f}\n"
        f"σ {frame.sigma_noise:.2f}{flag}",
        transform=ax.transAxes, fontsize=_FS_HIST - 1, va="top", ha="left",
        linespacing=1.35,
        bbox=dict(boxstyle="round,pad=0.22", facecolor="white", alpha=0.78, lw=0),
    )
    ax.tick_params(labelsize=_FS_HIST - 1)


def build_om_grid_figure(
    frames: Sequence[FrameResult],
    sample_name: str,
    magnification: Optional[str] = None,
    points: Optional[Sequence[int]] = None,
    dpi: int = 160,
    show_histograms: bool = True,
) -> bytes:
    """
    Render the OM analysis figure and return it as PNG bytes.

    `points` names the grid positions to lay out, so a sample missing positions
    still puts the ones it has in the right cells rather than closing the gaps —
    several wafers in the archive carry only six frames, and one only positions
    4 to 6. Defaults to 1..max(point): the wafer map is detected from the point
    *numbers*, not the frame count, so a ten-point wafer missing P7 still draws
    as 2-3-3-2 with P7 an empty cell instead of collapsing into a nine-point
    grid that shifts every later point one cell over.

    `show_histograms` draws the diagnostics row. It exists because this figure
    serves two audiences: the histograms are what make a saturated frame legible
    *as* one, which is exactly what an internal reviewer needs and exactly what
    a customer reading a report does not. Call it twice -- the segmentation is
    already done by then, so the second call is the ~5 s render, not the ~30 s
    analysis.

    Returns PNG bytes rather than a Figure so callers cannot leak one: an
    un-closed matplotlib figure holds its canvas for the life of the process,
    and this draws nineteen axes of raster per sample.
    """
    if not frames:
        raise ValueError("build_om_grid_figure needs at least one frame")

    by_point = {f.point: f for f in frames}
    if points is None:
        # 1..max, not the sorted presence list: a ten-point wafer missing P7
        # is still a ten-point wafer, and its remaining points must keep
        # their wafer cells. min() guards the (unseen) zero-based wafer.
        points = list(range(min(min(by_point), 1), max(by_point) + 1))
    points = list(points)

    # Until v4.6.0 this truncated to nine positions while the title and the
    # coverage summary still counted every frame -- a ten-point wafer's P10
    # was segmented, averaged, and silently missing from the panels.
    layout = _row_layout(len(points))
    rows = len(layout)
    units = _grid_units(len(points))
    labels = frames[0].labels
    summary = class_summary(frames)

    # The diagnostics row costs its height plus a little of the padding the
    # legend needs underneath it; without it the panels keep their aspect.
    hist_rows = 1 if show_histograms else 0
    fig = plt.figure(figsize=(21, 3.15 * rows + (2.3 if show_histograms else 1.1)),
                     dpi=dpi)
    try:
        grid = gridspec.GridSpec(
            rows + hist_rows, units, figure=fig,
            height_ratios=[1.0] * rows + [0.60] * hist_rows,
            hspace=0.20, wspace=_POSITION_WSPACE,
        )

        pair_span = units // GRID_COLUMNS
        # Wafer-map layouts centre their short rows, the way the points sit on
        # the wafer; plain chunked layouts keep the left alignment they had.
        centred = len(points) in _ROW_LAYOUTS
        cells = [
            (row, offset + col * pair_span)
            for row, count in enumerate(layout)
            for offset in [(units - count * pair_span) // 2 if centred else 0]
            for col in range(count)
        ]

        for (row, left), point in zip(cells, points):
            frame = by_point.get(point)

            pair = grid[row, left:left + pair_span].subgridspec(1, 2, wspace=_PAIR_WSPACE)
            ax_raw = fig.add_subplot(pair[0, 0])
            ax_seg = fig.add_subplot(pair[0, 1])
            for ax in (ax_raw, ax_seg):
                ax.set_xticks([])
                ax.set_yticks([])

            if frame is None:
                _blank(ax_raw)
                _blank(ax_seg)
                ax_raw.set_title(f"P{point}  (no frame)", fontsize=_FS_PANEL, loc="left")
                continue

            original, valid = _crop_to_valid(frame.original, frame.valid)
            overlay, _ = _crop_to_valid(frame.overlay, frame.valid)

            ax_raw.imshow(_dim(original, valid))
            ax_raw.axis("off")
            ax_raw.set_title(f"P{point}  (raw)",
                             fontsize=_FS_PANEL, fontweight="bold", loc="left")

            ax_seg.imshow(_dim(overlay, valid))
            ax_seg.axis("off")
            below, reference, above = frame.percentages
            ax_seg.set_title(
                f"Analyzed: {below:.1f} % / {reference:.1f} % / {above:.1f} %",
                fontsize=_FS_COVERAGE, fontweight="bold",
            )

        if show_histograms:
            for index, point in enumerate(points):
                frame = by_point.get(point)
                span = units // len(points) if len(points) else units
                left = index * span
                ax = fig.add_subplot(grid[rows, left:left + span])
                if frame is None:
                    _blank(ax)
                    continue
                _histogram(ax, frame)

        fig.legend(
            handles=[
                Patch(facecolor=RED / 255.0, label=labels[0]),
                Patch(facecolor="#c8c8c8", label=labels[1]),
                Patch(facecolor=GREEN / 255.0, label=labels[2]),
            ],
            loc="lower center", ncol=3, frameon=False, fontsize=_FS_PANEL + 1,
            bbox_to_anchor=(0.5, 0.005),
        )

        magnification_label = f" {magnification}" if magnification else ""
        coverage = "   ·   ".join(
            f"{label} {mean:.1f} ± {std:.1f} %"
            for label, (mean, std) in zip(labels, summary)
        )
        fig.suptitle(
            f"{sample_name} — OM{magnification_label} layer segmentation "
            f"({len(frames)} positions, {frames[0].labels[1]} reference)\n{coverage}",
            fontsize=_FS_SUPTITLE, fontweight="bold",
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)
