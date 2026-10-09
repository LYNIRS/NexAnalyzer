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
#: Edge domains (touching the analysed border: counted one half toward
#: density, never sized) reuse `RED_FILL` at this much lower alpha, so they
#: read as the same flakes, drawn fainter -- present, but not measured.
_EDGE_ALPHA = 0.25


def _edge_overlay(over: np.ndarray, edge_mask: Optional[np.ndarray]) -> np.ndarray:
    """`over` with `edge_mask`'s pixels tinted `RED_FILL` at `_EDGE_ALPHA`."""
    if edge_mask is None or not edge_mask.any():
        return over
    over = over.copy()
    over[edge_mask] = RED_FILL * _EDGE_ALPHA + over[edge_mask] * (1 - _EDGE_ALPHA)
    return over


def _panel_caption(domain: DomainResult) -> str:
    """The lines above `domain`'s panel, in a fixed order: flake count, then
    nucleation density, then domain size. A line with nothing to show (no
    calibration, so no density; no sized domain, so no size) is left out
    rather than drawn empty.

    A split frame gives density and size one line per population -- a
    per-panel column is narrow enough that "small: X/(100µm)²  ·  large:
    Y/(100µm)²" centred as one line bled into the next panel's own title.
    """
    diam = domain.diameters_um if domain.calibrated else domain.diameters_px
    unit = "µm" if domain.calibrated else "px"
    edge = _edge_suffix(domain.n_edge)

    def _size(values: np.ndarray) -> str:
        std = values.std(ddof=1) if values.size > 1 else 0.0
        return f"{values.mean():.2f}±{std:.2f}{unit}"

    if domain.population_is_large is not None:
        small_d = diam[~domain.population_is_large]
        large_d = diam[domain.population_is_large]
        lines = [f"n={small_d.size} small · {large_d.size} large{edge}"]
        if domain.area_um2:
            # Each population's own sized domains only: an edge domain has no
            # size, so it belongs to neither (see domain_size._pooled_population_stat).
            lines += [
                f"small: {small_d.size / domain.area_um2 * DENSITY_AREA_UM2:.1f}/(100µm)²",
                f"large: {large_d.size / domain.area_um2 * DENSITY_AREA_UM2:.1f}/(100µm)²",
            ]
        lines += [f"small: {_size(small_d)}", f"large: {_size(large_d)}"]
        return "\n".join(lines)

    lines = [f"n={domain.n}{edge}"]
    if domain.area_um2:
        lines.append(f"{domain.n_counted / domain.area_um2 * DENSITY_AREA_UM2:.1f}/(100µm)²")
    if domain.n:
        lines.append(_size(diam))
    return "\n".join(lines)


def _edge_suffix(n_edge: int) -> str:
    return f" + {n_edge} edge" if n_edge else ""


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
    detect_size_populations: bool = True,
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

    `detect_size_populations` mirrors `measure_domains()`/`domain_size_stat()`'s
    own argument and must be given the same value: it gates the pooled split
    in the figure's title, which is computed here rather than read from a
    `DomainResult`. False keeps the title to the single pooled population even
    when the pooled diameters would clear `MIN_POPULATION_RATIO` -- before it
    existed, an unticked "Split into small/large size populations" still got
    a small/large title above per-frame panels that showed one population.
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
    # own line count -- the title, then count, density (when calibrated) and
    # size (when anything was sized) -- decides how much header space `figsize` must
    # reserve. `fig.suptitle` doesn't grow the figure to fit itself; a fixed
    # allowance sized for the 2-line case overflowed into the grid's own
    # first row the moment a 4-5 line title became possible.
    calibrated = any(d.calibrated for d in domain_results if d.n)
    pooled = pooled_diameters(domain_results, calibrated=calibrated)
    n_total = sum(d.n for d in domain_results)
    n_edge_total = sum(d.n_edge for d in domain_results)
    n_counted_total = sum(d.n_counted for d in domain_results)
    unit = "µm" if calibrated else "px"
    area_um2_total = (
        sum(d.area_um2 for d in domain_results if d.area_um2 is not None)
        if calibrated else None
    )
    pooled_is_large = (
        split_size_populations(pooled)
        if detect_size_populations and pooled.size else None
    )
    # The title's summary, one line each in the same order as every panel
    # caption: flake count, then nucleation density, then domain size.
    edge = _edge_suffix(n_edge_total)
    no_calibration = "  ·  no µm/px calibration set — pixel units shown"
    if pooled_is_large is not None:
        small_p, large_p = pooled[~pooled_is_large], pooled[pooled_is_large]
        small_std = small_p.std(ddof=1) if small_p.size > 1 else 0.0
        large_std = large_p.std(ddof=1) if large_p.size > 1 else 0.0
        summary_lines = [
            f"N = {n_total} domains total (small {small_p.size}  ·  large {large_p.size}){edge}"
        ]
        if area_um2_total:
            summary_lines.append(
                f"small: {small_p.size / area_um2_total * DENSITY_AREA_UM2:.1f}/(100µm)²  ·  "
                f"large: {large_p.size / area_um2_total * DENSITY_AREA_UM2:.1f}/(100µm)²"
            )
        summary_lines.append(
            f"small: {small_p.mean():.2f} ± {small_std:.2f} {unit}  ·  "
            f"large: {large_p.mean():.2f} ± {large_std:.2f} {unit}"
            + ("" if calibrated else no_calibration)
        )
    elif pooled.size:
        std_v = float(pooled.std(ddof=1)) if pooled.size > 1 else 0.0
        summary_lines = [f"N = {n_total} domains total{edge}"]
        if area_um2_total:
            summary_lines.append(
                f"{n_counted_total / area_um2_total * DENSITY_AREA_UM2:.1f}/(100µm)²"
            )
        summary_lines.append(
            f"median {float(np.median(pooled)):.2f} {unit}  "
            f"·  mean {float(pooled.mean()):.2f} ± {std_v:.2f} {unit}"
            + ("" if calibrated else no_calibration)
        )
    else:
        summary_lines = [f"N = {n_total} domains total{edge}"]

    # 0.55 in/line reproduces the figure's own original fixed "+1.1" budget
    # exactly at the 2-line case (title + one summary line), scaled for
    # however many lines there are.
    header_in = 0.55 * (1 + len(summary_lines))
    # The first row's panel captions grow upward into that header: a split
    # frame's runs to five lines and reached the title's last line.
    # `content_in` already fits two.
    first_row = [by_domain.get(p) for p in points[:layout[0]]]
    caption_lines = max(
        (_panel_caption(d).count("\n") + 1 for d in first_row if d), default=2
    )
    header_in += 0.2 * max(0, caption_lines - 2)
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
        any_edge = False
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

            if domain.population_is_large is not None:
                any_split = True
                overlay_full = _split_domain_overlay(
                    frame.original, domain.label_mask, domain.population_is_large
                )
            else:
                overlay_full = _domain_overlay(frame.original, domain.label_mask)
            caption = _panel_caption(domain)

            any_edge = any_edge or bool(domain.n_edge)
            overlay_full = _edge_overlay(overlay_full, domain.edge_mask)
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
        if any_edge:
            # The fill as it would sit on white, so the swatch is as faint as
            # the flakes it names.
            edge_rgb = (RED_FILL * _EDGE_ALPHA + 255.0 * (1 - _EDGE_ALPHA)) / 255.0
            legend_handles.append(
                Patch(facecolor=edge_rgb, label="Edge domain (not sized, counted ½ in density)")
            )
        fig.legend(
            handles=legend_handles,
            loc="lower center", ncol=len(legend_handles), frameon=False, fontsize=_FS_PANEL + 1,
            bbox_to_anchor=(0.5, 0.01),
        )

        magnification_label = f" {magnification}" if magnification else ""
        class_suffix = f" — {class_label}" if class_label else ""
        fig.suptitle(
            f"{sample_name} — OM{magnification_label} domain size{class_suffix} "
            f"({len(points)} positions, watershed-split)\n" + "\n".join(summary_lines),
            fontsize=_FS_SUPTITLE, fontweight="bold",
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)
