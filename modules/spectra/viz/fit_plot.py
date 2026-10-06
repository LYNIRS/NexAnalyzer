"""
Static fit figures for export and reports.

Where live_plot.py renders the interactive, session-state-driven multi-layer
plot on the Spectra page, this module builds standalone figures with no
session-state dependencies: the data + fit + components view used for PNG/HTML
export, and the QC Report's stacked per-column grids.

Rendering these to the page goes through core.viz.render.render_plot().
"""

from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Data and total-fit colors, named because a legend drawn outside these figures
# (the QC Report draws one per grid page) has to match the lines it describes.
from .palette import (  # noqa: F401  (re-exported for callers)
    BACKGROUND_COLOR,
    COMPONENT_DASH,
    COMPONENT_OPACITY,
    FIT_TOTAL_COLOR,
    FIT_TOTAL_DASH,
    PROCESSED_COLOR,
    RAW_COLOR,
    RESIDUAL_COLOR,
)

# A compact figure is rendered small and then placed in a grid cell under 2
# inches high, so one figure pixel is roughly a third of a point on the slide.
# Plotly's default 12px text arrives there at about 3.5pt — unreadable — which
# is why these look far too big for a figure and are not.
COMPACT_FONT_PX = 26
COMPACT_TITLE_PX = 34
COMPACT_PANEL_LABEL_PX = 30

# Points per column in the QC Report's 3x3 grid: 1/4/7, 2/5/8, 3/6/9.
GRID_ROWS = 3


def axis_label(mode: str) -> str:
    """X-axis label for a spectroscopy mode."""
    return "Raman Shift (cm⁻¹)" if mode == "Raman" else "Wavelength (nm)"


def peak_normalization_scale(y_data: np.ndarray, fit_result=None) -> float:
    """
    The divisor that puts this spectrum's tallest peak at 1.0.

    Taken from the fitted curve rather than the raw maximum wherever there is
    one: a surviving cosmic ray or the Rayleigh edge routinely tops the raw
    data, and dividing by that would squash the actual peaks to a fraction of
    the frame. The fit does not chase single-sample spikes, so its maximum is
    the height of a real peak.

    Falls back to the data maximum with no fit, and to 1.0 when neither is
    positive — there is nothing meaningful to normalize against, and returning
    1.0 leaves the spectrum untouched rather than inverting or blanking it.
    """
    if fit_result is not None and getattr(fit_result, "total_fit_curve", None) is not None:
        curve = fit_result.total_fit_curve
        if len(curve):
            peak = float(np.max(curve))
            if peak > 0:
                return peak

    if y_data is not None and len(y_data):
        peak = float(np.max(y_data))
        if peak > 0:
            return peak

    return 1.0


def _add_spectrum_traces(
    fig,
    row: int,
    x: np.ndarray,
    y_data: np.ndarray,
    fit_result,
    x_label: str,
    show_components: bool,
    showlegend: bool,
    marker_px: float,
    fit_line_px: float,
    component_line_px: float,
    scale: float = 1.0,
    background: Optional[np.ndarray] = None,
) -> None:
    """
    Add one spectrum's data, total fit and peak components to `row` of `fig`.

    `scale` divides every Y series, so data and fit stay superimposed however
    the spectrum is normalized (see `peak_normalization_scale`).

    `background`, when given, is the baseline that was subtracted before the
    fit. It is drawn as its own line and added back under the data, the total
    fit and every component, so the peaks sit on the background they were
    separated from rather than on zero. The data then is the de-spiked
    spectrum, before baseline removal.
    """
    offset = np.asarray(background) if background is not None else 0.0

    # Baseline-corrected data — the series that was actually fitted, and the
    # same one the Spectra page draws as its "Baseline-corrected" layer, so it
    # takes that layer's color rather than the raw file's.
    fig.add_trace(go.Scatter(
        x=x,
        y=(np.asarray(y_data) + offset) / scale,
        mode='markers',
        name='Data',
        marker=dict(size=marker_px, color=PROCESSED_COLOR),
        showlegend=showlegend,
        hovertemplate=f'{x_label}: %{{x:.2f}}<br>Intensity: %{{y:.3g}}<extra></extra>'
    ), row=row, col=1)

    if not (fit_result and getattr(fit_result, "total_fit_curve", None) is not None):
        _add_background_trace(fig, row, x, background, x_label, showlegend, fit_line_px, scale)
        return

    # Total fit: the sum of all Voigt components, compared against the data by
    # eye to judge R².
    fig.add_trace(go.Scatter(
        x=x,
        y=(np.asarray(fit_result.total_fit_curve) + offset) / scale,
        mode='lines',
        name='Total Fit',
        line=dict(width=fit_line_px, color=FIT_TOTAL_COLOR, dash=FIT_TOTAL_DASH),
        showlegend=showlegend,
        hovertemplate=f'{x_label}: %{{x:.2f}}<br>Fit: %{{y:.3g}}<extra></extra>'
    ), row=row, col=1)

    # Individual peaks, so the contribution of each is visible (e.g. D-band
    # against G-band).
    if show_components and fit_result.fitted_peaks:
        for i, peak in enumerate(fit_result.fitted_peaks):
            if peak.component_curve is None:
                continue
            fig.add_trace(go.Scatter(
                x=x,
                y=(np.asarray(peak.component_curve) + offset) / scale,
                mode='lines',
                name=peak.label or f"Peak {i+1}",
                # The peak's own color from its Material Preset, rather than
                # Plotly's automatic cycling: the same peak then draws the same
                # color in every point's plot, which is what lets one legend
                # describe all nine of them.
                line=dict(width=component_line_px, dash=COMPONENT_DASH, color=peak.color or None),
                opacity=COMPONENT_OPACITY,
                showlegend=showlegend,
                hovertemplate=f'{x_label}: %{{x:.2f}}<br>Component: %{{y:.3g}}<extra></extra>'
            ), row=row, col=1)

    _add_background_trace(fig, row, x, background, x_label, showlegend, fit_line_px, scale)


def _add_background_trace(fig, row, x, background, x_label, showlegend, line_px, scale) -> None:
    """The subtracted baseline as a gray line, or nothing without one.

    Drawn after the components but ranked first in the legend: a peak the fit
    resolved to nothing (WSe2's C and center, often) is a flat line lying
    exactly on the background, and drawn first the background vanished under it.
    """
    if background is None:
        return
    fig.add_trace(go.Scatter(
        x=x,
        y=np.asarray(background) / scale,
        mode='lines',
        name='Background',
        line=dict(width=line_px, color=BACKGROUND_COLOR),
        legendrank=999,  # ahead of the default 1000, so it leads the legend
        showlegend=showlegend,
        hovertemplate=f'{x_label}: %{{x:.2f}}<br>Background: %{{y:.3g}}<extra></extra>'
    ), row=row, col=1)


def _apply_compact_style(fig, title: Optional[str], margin: dict) -> None:
    """Font and margin sizing for figures rendered into a small grid cell."""
    fig.update_layout(
        margin=margin,
        font=dict(size=COMPACT_FONT_PX),
    )
    if title is not None:
        fig.update_layout(title=dict(text=title, x=0.5, xanchor="center", font=dict(size=COMPACT_TITLE_PX)))
    # Fewer ticks than Plotly chooses for a figure this size, so the enlarged
    # labels have room instead of colliding.
    fig.update_xaxes(nticks=6)
    fig.update_yaxes(nticks=4)
    for annotation in fig.layout.annotations:  # make_subplots renders subplot titles as annotations
        annotation.font.size = COMPACT_TITLE_PX


def plot_composite(
    x: np.ndarray,
    y_data: np.ndarray,
    fit_result,  # FitResult object
    mode: str = "Raman",
    title: str = "Peak Fit Results",
    show_components: bool = True,
    show_residuals: bool = True,
    show_legend: bool = True,
    x_range: Optional[Tuple[float, float]] = None,
    y_range: Optional[Tuple[float, float]] = None,
    compact: bool = False,
    normalize: bool = False,
    background: Optional[np.ndarray] = None,
) -> go.Figure:
    """
    Create composite plot with data, fit, components, and (optionally) residuals.

    Layout: 3/4 main plot (data + fit + components) + 1/4 residuals subplot,
    or a single full-height main plot when `show_residuals` is False.

    Parameters
    ----------
    x : np.ndarray
        Wavenumber (cm⁻¹) or wavelength (nm).
    y_data : np.ndarray
        Processed intensity data.
    fit_result : FitResult
        Fitting results with total_fit_curve, fitted_peaks, residuals.
    mode : str, default="Raman"
        "Raman" or "PL" (affects axis labels).
    title : str, default="Peak Fit Results"
        Plot title.
    show_components : bool, default=True
        Whether to show individual peak components.
    show_residuals : bool, default=True
        Whether to include the residuals subplot. False gives a single
        full-height data+fit panel — used where the plot is rendered small,
        where a quarter-height residual strip is unreadable anyway.
    show_legend : bool, default=True
        Whether this figure draws its own legend. False where a set of these
        figures is shown together under one shared legend.
    x_range, y_range : Optional[Tuple[float, float]], default=None
        Explicit axis ranges; None autoscales this figure independently.
        See `shared_axis_ranges`.
    compact : bool, default=False
        Strip the axis titles and size fonts and margins for a figure rendered
        small in a grid, where the slide labels the axes once.
    normalize : bool, default=False
        Divide the spectrum by its own tallest peak, putting that peak at 1.0.
        See `peak_normalization_scale`.
    background : Optional[np.ndarray], default=None
        The subtracted baseline, on `x`. Given, the data, fit and components
        are drawn on top of it (see `_add_spectrum_traces`); the residuals stay
        about zero. Ignored unless it matches `x` in length, so a stale one
        falls back to the baseline-corrected view rather than raising.

    Returns
    -------
    fig : plotly.graph_objects.Figure
        Interactive Plotly figure with subplots.
    """
    # Shrunk into a grid cell, default-weight lines and markers thin out to
    # near-invisible; these are the same shapes drawn heavier to survive it.
    marker_px = 5 if compact else 4
    fit_line_px = 3.0 if compact else 2
    component_line_px = 2.4 if compact else 1.5

    if show_residuals:
        # Row 1 (75%) data and fit; row 2 (25%) residuals, which should be
        # random noise about zero for a good fit — a pattern there means a
        # systematic error, such as a missing peak.
        fig = make_subplots(
            rows=2, cols=1,
            row_heights=[0.75, 0.25],
            vertical_spacing=0.05,
            subplot_titles=("Data & Fit", "")
        )
    else:
        fig = make_subplots(rows=1, cols=1)

    x_label = axis_label(mode)
    scale = peak_normalization_scale(y_data, fit_result) if normalize else 1.0
    if background is not None and len(background) != len(x):
        background = None

    _add_spectrum_traces(
        fig, 1, x, y_data, fit_result, x_label, show_components, show_legend,
        marker_px, fit_line_px, component_line_px, scale, background,
    )

    if show_residuals and fit_result and fit_result.total_fit_curve is not None:
        fig.add_trace(go.Scatter(
            x=x,
            y=np.asarray(fit_result.residuals) / scale,
            mode='markers',
            name='Residuals',
            marker=dict(size=3, color=RESIDUAL_COLOR),
            showlegend=False,
            hovertemplate=f'{x_label}: %{{x:.2f}}<br>Residual: %{{y:.3g}}<extra></extra>'
        ), row=2, col=1)

        # Zero line, to see whether the residuals are centered.
        fig.add_trace(go.Scatter(
            x=[x.min(), x.max()],
            y=[0, 0],
            mode='lines',
            line=dict(width=1, color='gray', dash='dash'),
            showlegend=False
        ), row=2, col=1)

    # `compact` drops the axis titles entirely: the QC Report labels the
    # axes once for a whole grid, so repeating them per cell only shrinks the
    # spectra.
    if not compact:
        fig.update_xaxes(title_text=x_label, row=2 if show_residuals else 1, col=1)
        fig.update_yaxes(title_text=_y_axis_title(normalize), row=1, col=1)
        if show_residuals:
            fig.update_yaxes(title_text="Residual", row=2, col=1)

    # Explicit ranges put a whole set of figures on one scale; None leaves each
    # to autoscale, which is right for a figure viewed on its own.
    if x_range is not None:
        fig.update_xaxes(range=list(x_range))
    if y_range is not None:
        fig.update_yaxes(range=list(y_range), row=1, col=1)

    fig.update_layout(
        title=title,
        hovermode='closest',
        template='plotly_white',
        height=600,
        showlegend=show_legend,
        legend=dict(x=1.02, y=1, bgcolor='rgba(255,255,255,0.8)')
    )

    if compact:
        # Hand the spectrum the pixels the legend, axis titles and default
        # margins were using. The left margin still has to fit the Y tick
        # labels, and the bottom the X ones.
        _apply_compact_style(fig, title, dict(l=92, r=22, t=56, b=70))

    return fig


def plot_fit_column(
    points: Sequence[Tuple[int, np.ndarray, np.ndarray, object]],
    mode: str = "Raman",
    show_components: bool = True,
    x_range: Optional[Tuple[float, float]] = None,
    y_range: Optional[Tuple[float, float]] = None,
    normalize: bool = True,
    rows: int = GRID_ROWS,
) -> go.Figure:
    """
    One column of the QC Report grid: its points stacked on a shared X-axis.

    The three points of a column are one vertical line across the wafer, and
    drawing them against a single X-axis is what makes them read that way —
    peaks line up down the column instead of each panel restating the same
    axis. Only the bottom panel carries tick labels, and the two rows of labels
    that saves is most of the extra height the spectra get.

    Parameters
    ----------
    points : sequence of (point_index, x, y_data, fit_result)
        In top-to-bottom order. Fewer than `rows` leaves the lower panels
        empty rather than restacking, so columns stay aligned across the grid.
    normalize : bool, default=True
        Scale each point by its own tallest peak, putting every panel's peak
        at 1.0. Comparable peak shape and position, at the cost of intensity:
        a weak point and a strong one reach the same height by construction.
    x_range, y_range : Optional[Tuple[float, float]]
        Applied to every panel. Pass the same values to all three columns to
        keep the whole grid on one scale.

    Returns
    -------
    fig : plotly.graph_objects.Figure
        A `rows`-row figure, sized and styled for a report grid cell.
    """
    fig = make_subplots(
        rows=rows, cols=1,
        shared_xaxes=True,  # only the bottom panel keeps tick labels
        # Truly flush. The gap only ever existed to keep the Y tick labels of
        # touching panels apart, and there are no Y ticks any more.
        vertical_spacing=0.0,
    )

    x_label = axis_label(mode)
    for row, (_point, x, y_data, fit_result) in enumerate(points[:rows], start=1):
        scale = peak_normalization_scale(y_data, fit_result) if normalize else 1.0
        _add_spectrum_traces(
            fig, row, x, y_data, fit_result, x_label, show_components,
            showlegend=False,  # the slide draws one legend for the whole grid
            # The components are the thickest line here, as they are on the
            # Spectra page (2.5 against the total fit's 1.5). They carry the
            # preset colors, which are the reason to look at this slide; drawn
            # thinner than the black total fit and under a dense marker cloud,
            # they were the least visible thing on it.
            marker_px=4, fit_line_px=2.6, component_line_px=3.2, scale=scale,
        )

    if x_range is not None:
        fig.update_xaxes(range=list(x_range))
    if y_range is not None:
        fig.update_yaxes(range=list(y_range))

    fig.update_layout(
        hovermode='closest',
        template='plotly_white',
        showlegend=False,
    )
    # No figure title, and barely any margin: the slide's title bar names the
    # sample and technique, each panel labels itself from the inside, and with
    # no Y ticks the left edge needs nothing but a hairline.
    _apply_compact_style(fig, None, dict(l=14, r=18, t=10, b=74))

    # Every panel is normalized to its own peak at 1.0, so a Y scale would
    # repeat the same three numbers nine times. Dropping the labels, the tick
    # marks and the horizontal grid gives the width and the height back to the
    # spectra, and lets the panels sit flush.
    fig.update_yaxes(showticklabels=False, ticks="", showgrid=False, zeroline=False)

    # Point numbers go inside their panel rather than above it. A title band
    # above each panel costs height three times over; a corner label costs
    # none, and sits in space the spectrum's tail isn't using.
    for row, (point, *_rest) in enumerate(points[:rows], start=1):
        fig.add_annotation(
            text=f"Point {point}",
            row=row, col=1,
            xref="x domain", yref="y domain",
            x=0.015, y=0.98, xanchor="left", yanchor="top",
            showarrow=False,
            font=dict(size=COMPACT_PANEL_LABEL_PX),
        )

    return fig


def _y_axis_title(normalized: bool) -> str:
    """Y-axis title, which stops being arbitrary units once normalized."""
    return "Normalized intensity" if normalized else "Intensity (a.u.)"


def y_axis_title(normalized: bool) -> str:
    """Public form of `_y_axis_title`, for a caller labelling the axis itself
    (the QC Report writes it on the grid page, not into the figures)."""
    return _y_axis_title(normalized)


def shared_axis_ranges(
    series: Iterable[Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]],
    pad: float = 0.05,
) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
    """
    One (x_range, y_range) pair covering every spectrum in `series`.

    Independently autoscaled plots can't be compared by eye — a weak point and
    a strong one both fill their frame. Plotting them all on these ranges makes
    the differences the thing you actually see.

    Pass already-normalized Y values if the figures will be normalized, so the
    range describes what is actually drawn.

    Parameters
    ----------
    series : iterable of (x, y_data, total_fit_curve)
        The fit curve may be None (an unfitted point still sets the range).
    pad : float, default=0.05
        Fraction of the Y span added at each end, so peaks don't touch the
        frame. X is left unpadded — its limits are the preset's crop range and
        are meaningful as they stand.

    Returns
    -------
    (x_range, y_range), or (None, None) if `series` yields nothing usable.
    """
    x_lo = y_lo = float("inf")
    x_hi = y_hi = float("-inf")

    for x, y_data, fit_curve in series:
        if x is None or len(x) == 0:
            continue
        x_lo, x_hi = min(x_lo, float(np.min(x))), max(x_hi, float(np.max(x)))
        for y in (y_data, fit_curve):
            if y is not None and len(y):
                y_lo, y_hi = min(y_lo, float(np.min(y))), max(y_hi, float(np.max(y)))

    if x_lo > x_hi or y_lo > y_hi:
        return None, None

    # A perfectly flat spectrum would give a zero-height range that Plotly
    # renders as a single line across the middle; give it something to show.
    span = y_hi - y_lo
    margin = span * pad if span > 0 else (abs(y_hi) * pad or 1.0)
    return (x_lo, x_hi), (y_lo - margin, y_hi + margin)


def fit_legend_entries(fit_results: Sequence) -> List[Tuple[str, str]]:
    """
    (label, hex color) for every trace this module draws, so one legend can be
    rendered outside the figures.

    Data and Total Fit first, then each distinct fitted peak in the order it is
    first seen across `fit_results` — taking the union rather than reading one
    fit, so a point that failed to resolve a peak doesn't drop it from the key.
    """
    entries = [("Data", PROCESSED_COLOR), ("Total Fit", FIT_TOTAL_COLOR)]
    seen = set()

    for result in fit_results:
        if result is None or not getattr(result, "fitted_peaks", None):
            continue
        for i, peak in enumerate(result.fitted_peaks):
            label = peak.label or f"Peak {i + 1}"
            if label in seen:
                continue
            seen.add(label)
            entries.append((label, peak.color or PROCESSED_COLOR))

    return entries
