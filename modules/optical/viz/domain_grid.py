"""
Figure 4 of the QC Report: the domain-size analysis grid.

Same layout convention as `om_grid.py`'s Image 1 -- Original/analyzed pairs,
three positions per row, the same tight intra-pair gap and looser
inter-position gap -- reusing that module's layout constants and helpers
directly rather than re-deriving them, so the two figures read as one
family. What differs is the analyzed panel's content: a red fill over each
measured domain instead of a three-class layer overlay, and a caption
reporting domain count and size instead of class percentages.

Built as its own module rather than a new mode of `build_om_grid_figure`
because the two feed on different data shapes: that function takes
`FrameResult`s alone (a fixed three-class overlay already baked in by
`contrast._overlay`), while this one also needs each frame's
`domain_size.DomainResult` to know which pixels to fill.
"""

import io
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # no display; must precede the pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import gridspec  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from ..processing.contrast import FrameResult  # noqa: E402
from ..processing.domain_size import (  # noqa: E402
    DENSITY_AREA_UM2,
    DomainResult,
    pooled_diameters,
    split_size_populations,
)
from .om_grid import (  # noqa: E402
    GRID_COLUMNS,
    _blank,
    _crop_to_valid,
    _dim,
    _grid_units,
    _row_layout,
    _FS_COVERAGE,
    _FS_PANEL,
    _FS_SUPTITLE,
    _PAIR_WSPACE,
    _POSITION_WSPACE,
)

#: Domain fill tint. One class only -- "a measured domain" -- unlike the
#: three-class layer overlay, so one colour is enough. Used whenever a
#: frame's domains didn't split into two size populations.
RED_FILL = np.array([220, 30, 30])
#: Split-population fill tints, used instead of `RED_FILL` for a frame whose
#: `DomainResult.population_is_large` is set (see `domain_size.split_size_populations`).
SMALL_FILL = np.array([235, 200, 30])
LARGE_FILL = np.array([30, 120, 235])
_FILL_ALPHA = 0.65


def _domain_overlay(original: np.ndarray, label_mask: np.ndarray) -> np.ndarray:
    """`original` with every labelled domain filled red, alpha-blended so the
    flake is still visible under the fill -- matching the reports built by
    hand before this was a QC Report figure."""
    over = original.copy()
    mask = label_mask > 0
    over[mask] = RED_FILL * _FILL_ALPHA + over[mask] * (1 - _FILL_ALPHA)
    return over


def _split_domain_overlay(
    original: np.ndarray, label_mask: np.ndarray, population_is_large: np.ndarray
) -> np.ndarray:
    """`original` with the small population filled `SMALL_FILL` and the large
    population filled `LARGE_FILL`, one overlay carrying both colours rather
    than two separate images.

    `population_is_large` is aligned to `np.unique(label_mask)`'s positive
    ids in the same order `measure_domains()` built `diameters_px` in --
    `DomainResult` doesn't carry the id array itself, but `label_mask`'s own
    sorted unique positive values reproduce it exactly.
    """
    ids = np.unique(label_mask)
    ids = ids[ids > 0]
    small_ids, large_ids = ids[~population_is_large], ids[population_is_large]

    over = original.copy()
    small_mask = np.isin(label_mask, small_ids)
    large_mask = np.isin(label_mask, large_ids)
    over[small_mask] = SMALL_FILL * _FILL_ALPHA + over[small_mask] * (1 - _FILL_ALPHA)
    over[large_mask] = LARGE_FILL * _FILL_ALPHA + over[large_mask] * (1 - _FILL_ALPHA)
    return over


def build_domain_grid_figure(
    frames: Sequence[FrameResult],
    domain_results: Sequence[DomainResult],
    sample_name: str,
    magnification: Optional[str] = None,
    points: Optional[Sequence[int]] = None,
    class_label: Optional[str] = None,
    dpi: int = 160,
) -> bytes:
    """
    Render the domain-size analysis figure and return it as PNG bytes.

    `domain_results` must cover the same points as `frames` (one
    `domain_size.measure_domains()` call per frame) -- a point present in one
    but not the other is treated as an empty cell, the same way
    `build_om_grid_figure` treats a missing `FrameResult`.

    `points` follows `build_om_grid_figure`'s own convention: defaults to
    1..max(point), so a wafer missing a position still draws it as an empty
    cell in the right place rather than shifting every later point over.

    `class_label` names the OM class the domains were split from (e.g.
    "Bilayer", "Below 2L") -- the same wording `contrast.class_labels()`
    gives the OM class table, so a saved figure says what it measured without
    the reader cross-referencing the reference-layer selector. None omits it.
    """
    if not frames:
        raise ValueError("build_domain_grid_figure needs at least one frame")

    by_point = {f.point: f for f in frames}
    by_domain = {d.point: d for d in domain_results}
    if points is None:
        points = list(range(min(min(by_point), 1), max(by_point) + 1))
    points = list(points)

    layout = _row_layout(len(points))
    rows = len(layout)
    units = _grid_units(len(points))

    # Computed before the figure exists, not after, because the suptitle's
    # own line count -- 2 ordinarily, +2 when the pooled split fires, +1 more
    # when density is shown -- decides how much header space `figsize` must
    # reserve. `fig.suptitle` doesn't grow the figure to fit itself; a fixed
    # allowance sized for the 2-line case overflowed into the grid's own
    # first row the moment a 4-5 line title became possible.
    calibrated = any(d.calibrated for d in domain_results if d.n)
    pooled = pooled_diameters(domain_results, calibrated=calibrated)
    n_total = sum(d.n for d in domain_results)
    unit = "µm" if calibrated else "px"
    area_um2_total = (
        sum(d.area_um2 for d in domain_results if d.area_um2 is not None)
        if calibrated else None
    )
    pooled_is_large = split_size_populations(pooled) if pooled.size else None
    header_lines = 4 if pooled_is_large is not None else 2
    if area_um2_total:
        header_lines += 1
    # 0.55 in/line reproduces the figure's own original fixed "+1.1" budget
    # exactly at the 2-line case, scaled for however many lines there are.
    header_in = 0.55 * header_lines
    content_in = 3.15 * rows
    total_in = content_in + header_in

    fig = plt.figure(figsize=(21, total_in), dpi=dpi)
    try:
        # `top=` is set explicitly rather than left to matplotlib's default
        # subplot margin: that default is its own fixed *fraction* of figure
        # height, not of the suptitle's actual line count, so growing
        # `header_in` above grew the title's headroom only partially --
        # enough to stop clobbering the side panels' labels, not the centre
        # column's, which sits closest to a centred multi-line title.
        grid = gridspec.GridSpec(
            rows, units, figure=fig, height_ratios=[1.0] * rows,
            hspace=0.20, wspace=_POSITION_WSPACE, top=content_in / total_in,
        )

        pair_span = units // GRID_COLUMNS
        centred = False  # domain grids follow plain chunked layouts only so far
        cells = [
            (row, offset + col * pair_span)
            for row, count in enumerate(layout)
            for offset in [(units - count * pair_span) // 2 if centred else 0]
            for col in range(count)
        ]

        any_split = False
        for (row, left), point in zip(cells, points):
            frame = by_point.get(point)
            domain = by_domain.get(point)

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
            ax_raw.imshow(_dim(original, valid))
            ax_raw.axis("off")
            ax_raw.set_title(f"P{point}  (raw)", fontsize=_FS_PANEL, fontweight="bold", loc="left")

            if domain is None:
                _blank(ax_seg)
                ax_seg.set_title("no domain data", fontsize=_FS_COVERAGE, fontweight="bold")
                continue

            diam = domain.diameters_um if domain.calibrated else domain.diameters_px
            panel_unit = "µm" if domain.calibrated else "px"
            n = domain.n
            if domain.population_is_large is not None:
                any_split = True
                overlay_full = _split_domain_overlay(
                    frame.original, domain.label_mask, domain.population_is_large
                )
                small_d = diam[~domain.population_is_large]
                large_d = diam[domain.population_is_large]
                small_std = small_d.std(ddof=1) if small_d.size > 1 else 0.0
                large_std = large_d.std(ddof=1) if large_d.size > 1 else 0.0
                caption = (
                    f"small: n={small_d.size}, {small_d.mean():.2f}±{small_std:.2f}{panel_unit}\n"
                    f"large: n={large_d.size}, {large_d.mean():.2f}±{large_std:.2f}{panel_unit}"
                )
                if domain.area_um2:
                    # Each population's density gets its own new line, not
                    # combined onto one -- a per-panel column is narrow
                    # enough that "small: X/(100µm)²  ·  large: Y/(100µm)²"
                    # centred as one line was wide enough to bleed into the
                    # next panel's own title.
                    small_density = small_d.size / domain.area_um2 * DENSITY_AREA_UM2
                    large_density = large_d.size / domain.area_um2 * DENSITY_AREA_UM2
                    caption += (
                        f"\nsmall: {small_density:.1f}/(100µm)²"
                        f"\nlarge: {large_density:.1f}/(100µm)²"
                    )
            else:
                overlay_full = _domain_overlay(frame.original, domain.label_mask)
                if n:
                    mean_v = float(diam.mean())
                    std_v = float(diam.std(ddof=1)) if n > 1 else 0.0
                    caption = f"n={n}, {mean_v:.2f}±{std_v:.2f}{panel_unit}"
                else:
                    caption = "n=0"
                if domain.area_um2:
                    density = n / domain.area_um2 * DENSITY_AREA_UM2
                    caption += f"\n{density:.1f}/(100µm)²"

            overlay, _ = _crop_to_valid(overlay_full, frame.valid)
            ax_seg.imshow(_dim(overlay, valid))
            ax_seg.axis("off")
            ax_seg.set_title(caption, fontsize=_FS_COVERAGE, fontweight="bold")

        if any_split:
            legend_handles = [
                Patch(facecolor=SMALL_FILL / 255.0, label="Small population"),
                Patch(facecolor=LARGE_FILL / 255.0, label="Large population"),
            ]
        else:
            legend_handles = [Patch(facecolor=RED_FILL / 255.0, label="Measured domain")]
        fig.legend(
            handles=legend_handles,
            loc="lower center", ncol=len(legend_handles), frameon=False, fontsize=_FS_PANEL + 1,
            bbox_to_anchor=(0.5, 0.01),
        )

        magnification_label = f" {magnification}" if magnification else ""
        class_suffix = f" — {class_label}" if class_label else ""
        if pooled_is_large is not None:
            small_p, large_p = pooled[~pooled_is_large], pooled[pooled_is_large]
            small_std = small_p.std(ddof=1) if small_p.size > 1 else 0.0
            large_std = large_p.std(ddof=1) if large_p.size > 1 else 0.0
            summary_line = (
                f"N = {n_total} domains total\n"
                f"small: n={small_p.size}, {small_p.mean():.2f} ± {small_std:.2f} {unit}\n"
                f"large: n={large_p.size}, {large_p.mean():.2f} ± {large_std:.2f} {unit}"
            )
            if area_um2_total:
                small_density = small_p.size / area_um2_total * DENSITY_AREA_UM2
                large_density = large_p.size / area_um2_total * DENSITY_AREA_UM2
                summary_line += (
                    f"\nsmall: {small_density:.1f}/(100µm)²  ·  "
                    f"large: {large_density:.1f}/(100µm)²"
                )
            if not calibrated:
                summary_line += "  ·  no µm/px calibration set — pixel units shown"
        elif pooled.size:
            median_v, mean_v, std_v = (
                float(np.median(pooled)), float(pooled.mean()),
                float(pooled.std(ddof=1)) if pooled.size > 1 else 0.0,
            )
            summary_line = (
                f"N = {n_total} domains total  ·  median {median_v:.2f} {unit}  "
                f"·  mean {mean_v:.2f} ± {std_v:.2f} {unit}"
            )
            if area_um2_total:
                density_total = n_total / area_um2_total * DENSITY_AREA_UM2
                summary_line += f"\n{density_total:.1f}/(100µm)²"
            if not calibrated:
                summary_line += "  ·  no µm/px calibration set — pixel units shown"
        else:
            summary_line = f"N = {n_total} domains total"
        fig.suptitle(
            f"{sample_name} — OM{magnification_label} domain size{class_suffix} "
            f"({len(points)} positions, watershed-split)\n{summary_line}",
            fontsize=_FS_SUPTITLE, fontweight="bold",
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)
