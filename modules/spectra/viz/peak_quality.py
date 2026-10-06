"""
The QC Report's per-technique quality panels: Raman and PL.

Each figure is a grid whose columns are metric families (FWHM, peak centres,
diagnostic ratios) and whose rows are the peaks within each family. Every panel
is a strip plot across the sample's grid positions: the individual fits as a
jittered cloud, a mean-or-median +/- std marker per position, and the pooled
median as a dashed line.

One builder, two techniques, because the drawing never knew anything about
Raman: it reads `peak.label`, `peak.center`, `peak.width_fwhm` and
`peak_intensity(peak)`, all of which exist identically on a PL fit. Everything
technique-specific is a `QualityFigureSpec` value — columns, cleaning rule and
marker statistic travelling together so a caller cannot pair Raman's panels
with PL's cleaning. `results_excel.TechniqueResults` made the same choice for
the workbook; this is that pattern applied to the figure.

**The spec lines.** Grey horizontal references, drawn and never evaluated —
this figure reports, it does not judge. They are inherited literals, not preset
data, and there are only four in the whole lineage:

    Raman  E2g+A1g FWHM = 7      B2g centre = 308     LA/E2g+A1g = 0.13
    PL     Exciton & Trion FWHM = 35

There is deliberately **no PL centre spec and no PL ratio spec**. The ancestor
gates its only PL reference line on `if col == "FWHM"`, so the centre panels
never had one, and the 755/780 nm figures in the material preset are fit
*initialisation* guesses — promoting those to specs would invent a tolerance
nobody measured. The unity line the ancestor draws on the PL ratio is grey
dotted with no legend entry and is never called a spec, unlike every line it
does label "(Spec)"; it is left out for the same reason.

Anyone adding a pass/fail verdict needs to decide first what the rule is
(pooled median across the line? any position's mean? a fraction of individual
fits?), and that decision is not encoded here.

**Cleaning differs by technique, on purpose.** Raman takes the same 1.5x IQR
cut within each position that `peak_metrics.aggregate_fit_results` takes, so
the figure and the report's summary tables cannot show two different numbers
for one measurement. PL inherits a different rule — a bad-fit floor plus the
widest 5% of each peak's fits dropped across the sample — because that is what
the PL lineage does and its peaks fail differently. It also drops a peak fitted
to near zero (under 2% of its spectrum's strongest), whose centre and width are
then meaningless. Both run downstream of the same R-squared gate.

**The marker statistic differs too.** Raman's ancestor marks each position with
a mean, PL's with a median, and the PL summary schema stores medians only. The
statistic is named in the suptitle, because the difference is invisible in the
mark itself and silently moves every point on the figure.
"""

import io
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

import matplotlib

matplotlib.use("Agg")  # no display; must precede the pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import gridspec  # noqa: E402

from ..models.peak import FitResult  # noqa: E402
from ..processing.peak_metrics import (  # noqa: E402
    NEAR_ZERO_FRACTION,
    RAMAN_RATIO_PAIRS,
    iqr_mask,
    peak_intensity,
)

#: Plotly's qualitative sequence, carried over from the inherited scripts so a
#: position keeps the colour it has in the reports people already read. Cycled
#: by index, so more than eleven positions repeat rather than fail.
POSITION_COLORS = (
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
    "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52", "#1CBE4F",
)

_FS_SUPTITLE = 18
_FS_TITLE = 15
_FS_LABEL = 13
_FS_PANEL = 13
_FS_TICK = 12
_FS_REFTEXT = 11

_JITTER = 0.18
_MARKER_OFFSET = 0.28

#: Inches per column and per row. Chosen so a 3x2 grid is the 22x11 the Raman
#: figure has always been, and any other shape scales from it rather than
#: cramming more panels into a fixed canvas.
_COLUMN_W_IN = 22.0 / 3.0
_ROW_H_IN = 11.0 / 2.0


@dataclass(frozen=True)
class PeakPanel:
    """One panel plotting a fitted parameter of a single peak."""

    peak: str
    metric: str          # "center" | "fwhm" | "intensity"
    ylabel: str
    caption: str
    spec: Optional[float] = None


@dataclass(frozen=True)
class RatioPanel:
    """One panel plotting a per-spectrum intensity ratio of two peaks."""

    numerator: str
    denominator: str
    ylabel: str
    caption: str
    spec: Optional[float] = None


Panel = Union[PeakPanel, RatioPanel]
PanelColumns = Tuple[Tuple[str, Tuple[Panel, ...]], ...]


@dataclass(frozen=True)
class CleaningRule:
    """Which fitted peaks reach the figure.

    Applied to individual peak records, not whole fits: a spectrum whose Trion
    fit ran wide still contributes its Exciton, exactly as the inherited
    scripts filter rows of a fit table rather than whole spectra.

    ``per_position_iqr`` is the 1.5x Tukey cut taken *within* each grid
    position, matching `peak_metrics.aggregate_fit_results`. It is the whole of
    Raman's rule and none of PL's.

    ``relative_intensity_floor`` drops a peak whose intensity is below that
    fraction of the strongest peak *in the same spectrum*. A component the fit
    drove to zero still reports a centre and a width, but they describe nothing
    -- the parameters are free once the peak has no height -- and plotting them
    puts noise in the same cloud as real fits.
    """

    fwhm_floor: Optional[float] = None
    center_floor: Optional[float] = None
    fwhm_top_fraction: Optional[float] = None
    per_position_iqr: bool = True
    relative_intensity_floor: Optional[float] = None


#: Raman: the same per-position IQR cut the summary tables take, and nothing
#: else. A difference here would put two numbers for one measurement in front
#: of the same reader.
RAMAN_CLEANING = CleaningRule(per_position_iqr=True)

#: PL: `FWHM > 5 nm` rejects fits that collapsed onto noise, a peak under 2% of
#: its own spectrum's strongest is dropped as fitted-to-zero, then the widest 5%
#: of each peak's fits are dropped across the whole sample. No per-position IQR
#: cut: this already removed the tail that cut exists to remove, and taking both
#: would cut real position-to-position spread.
#:
#: The inherited `Center > 700 nm` floor is gone (v5.10.6). It was WSe2's
#: emission band written as a constant, so it discarded every MoS2 peak
#: (620-680 nm) and left MoS2's figure empty. It never removed a WSe2 fit: the
#: fitter's own centre bounds, which carry the preset's tolerances, keep every
#: WSe2 PL peak above 740 nm.
PL_CLEANING = CleaningRule(
    fwhm_floor=5.0,
    fwhm_top_fraction=0.05,
    per_position_iqr=False,
    relative_intensity_floor=NEAR_ZERO_FRACTION,
)

#: How each known Raman ratio is *labelled*: (ylabel, caption, spec). Which
#: ratios exist is not decided here — `peak_metrics.RAMAN_RATIO_PAIRS` decides
#: that, and this module reads it. Splitting it that way is the point: the
#: summary table and this figure must draw the same set of ratios, and an entry
#: added to the constant that nobody labels still gets a panel (below) rather
#: than silently going missing from the figure while appearing in the table.
_RAMAN_RATIO_PRESENTATION = {
    ("LA", "E2g+A1g"): ("I(LA) / I(E₂g+A₁g)", "Defect ratio", 0.13),
    ("C", "LB"): ("I(C) / I(LB)", "Stacking ratio", None),
}


def _raman_ratio_panels(
    pairs: Sequence[Tuple[str, str]] = RAMAN_RATIO_PAIRS,
) -> Tuple[RatioPanel, ...]:
    """A `RatioPanel` per entry of `RAMAN_RATIO_PAIRS`, labelled where known."""
    panels = []
    for numerator, denominator in pairs:
        ylabel, caption, spec = _RAMAN_RATIO_PRESENTATION.get(
            (numerator, denominator),
            (f"I({numerator}) / I({denominator})", f"{numerator} / {denominator}", None),
        )
        panels.append(RatioPanel(numerator, denominator, ylabel, caption, spec=spec))
    return tuple(panels)


#: Raman's panels. Column order and titles match the inherited script so the
#: output is recognisable to people already reading these reports.
PANEL_COLUMNS: PanelColumns = (
    ("FWHM", (
        PeakPanel("E2g+A1g", "fwhm", "FWHM (cm⁻¹)", "E₂g + A₁g", spec=7.0),
        PeakPanel("2LA", "fwhm", "FWHM (cm⁻¹)", "2LA(M)"),
    )),
    ("Peak Centers", (
        PeakPanel("E2g+A1g", "center", "Raman shift (cm⁻¹)", "E₂g + A₁g center"),
        PeakPanel("B2g", "center", "Raman shift (cm⁻¹)", "B₂g center", spec=308.0),
    )),
    ("Diagnostic Ratios", _raman_ratio_panels()),
)

#: PL's panels. Two peaks, so the ratio column holds one panel where the others
#: hold two and the bottom-right cell stays empty. That gap is honest: the
#: lineage defines exactly one PL ratio, and filling the slot would mean either
#: inventing a second or moving an amplitude panel into a column titled
#: "Diagnostic Ratios". Amplitude is uncalibrated peak height, sensitive to
#: laser power and focus, and carries no reference line — the least diagnostic
#: of the three families the ancestor plots.
#:
#: The column titles are Raman's, not the ancestor's: the two figures are read
#: side by side in one report, and matching headers are worth more than
#: fidelity to strings the PL script never needed to write down.
PL_PANEL_COLUMNS: PanelColumns = (
    ("FWHM", (
        PeakPanel("Exciton", "fwhm", "FWHM (nm)", "Exciton", spec=35.0),
        PeakPanel("Trion", "fwhm", "FWHM (nm)", "Trion", spec=35.0),
    )),
    ("Peak Centers", (
        PeakPanel("Exciton", "center", "Wavelength (nm)", "Exciton center"),
        PeakPanel("Trion", "center", "Wavelength (nm)", "Trion center"),
    )),
    ("Diagnostic Ratios", (
        RatioPanel("Exciton", "Trion", "I(Exciton) / I(Trion)", "Exciton / Trion"),
    )),
)


#: Axis labels per technique, for panels built from a material's own peaks
#: rather than from the inherited columns above.
_AXIS_LABELS = {
    "Raman": ("FWHM (cm⁻¹)", "Raman shift (cm⁻¹)"),
    "PL": ("FWHM (nm)", "Wavelength (nm)"),
}


def panels_for_labels(labels: Sequence[str], technique: str) -> PanelColumns:
    """FWHM and centre columns for `labels`, in the order given.

    The inherited `PANEL_COLUMNS` name WSe₂'s peaks (`E2g+A1g`, `2LA`, `B2g`)
    and PL's name the WSe₂ lineage's two. A material whose peaks are called
    something else — MoS₂ fits `E2g` and `A1g` as separate peaks — matches none
    of them, and every panel rendered "no E2g+A1g fits" however good the fits
    were. This builds the columns from the peaks actually present instead.

    **No spec lines, deliberately.** The four in this lineage are inherited
    literals tied to WSe₂'s peaks (see the module docstring); there is no
    measured tolerance for an arbitrary material's peaks, and a grey line the
    reader takes for a spec is worse than no line. Panels built here carry
    `spec=None`.

    **No ratio column either.** `RAMAN_RATIO_PAIRS` is the lineage's two
    diagnostic ratios; which ratios mean something for another material is a
    materials question, not a plotting one.
    """
    fwhm_label, center_label = _AXIS_LABELS.get(technique, _AXIS_LABELS["Raman"])
    return (
        ("FWHM", tuple(
            PeakPanel(label, "fwhm", fwhm_label, label) for label in labels
        )),
        ("Peak Centers", tuple(
            PeakPanel(label, "center", center_label, f"{label} center")
            for label in labels
        )),
    )


@dataclass(frozen=True)
class QualityFigureSpec:
    """Everything that makes this figure one technique's rather than another's.

    Bundled rather than passed as four arguments so a call site cannot pair
    Raman's columns with PL's cleaning — a mismatch that would render without
    error and be wrong only in its numbers.
    """

    technique: str
    columns: PanelColumns
    cleaning: CleaningRule
    statistic: str  # "mean" | "median" — the per-position marker

    def columns_for(self, labels: Sequence[str]) -> PanelColumns:
        """`columns`, or peak-derived ones when they name none of `labels`.

        The test is "does any panel address a peak this sample actually
        fitted", not "is this WSe₂": a material that happens to share the
        inherited peak names keeps the inherited columns, spec lines and all.
        Only a sample that would otherwise render entirely empty falls back.
        """
        # dict, not set: peak order is the order the fitter reports, which is
        # the preset's own — E2g before A1g, ascending in wavenumber. Sorting
        # would put A1g first and read as wrong to anyone who knows the
        # spectrum.
        present = dict.fromkeys(labels)
        for _, panels in self.columns:
            for panel in panels:
                named = (
                    (panel.numerator, panel.denominator)
                    if isinstance(panel, RatioPanel)
                    else (panel.peak,)
                )
                if any(name in present for name in named):
                    return self.columns
        if not present:
            return self.columns
        return panels_for_labels(tuple(present), self.technique)


RAMAN_QUALITY = QualityFigureSpec("Raman", PANEL_COLUMNS, RAMAN_CLEANING, "mean")
PL_QUALITY = QualityFigureSpec("PL", PL_PANEL_COLUMNS, PL_CLEANING, "median")


class _PeakRecord(NamedTuple):
    """One fitted peak at one grid position.

    ``spectrum`` identifies the fit it came from, which is what lets a ratio be
    formed within a single spectrum after cleaning has dropped peaks from
    others.
    """

    point: int
    spectrum: int
    label: str
    center: float
    fwhm: float
    intensity: float


def _records(fits_by_point: Sequence[Tuple[int, FitResult]]) -> List[_PeakRecord]:
    """Flatten `(position, FitResult)` pairs to one record per fitted peak."""
    out: List[_PeakRecord] = []
    for index, (point, fit) in enumerate(fits_by_point):
        for peak in fit.fitted_peaks:
            out.append(_PeakRecord(
                point=point,
                spectrum=index,
                label=peak.label,
                center=peak.center,
                fwhm=peak.width_fwhm,
                intensity=peak_intensity(peak),
            ))
    return out


def _clean(records: Sequence[_PeakRecord], rule: CleaningRule) -> List[_PeakRecord]:
    """Apply a rule's record-level filters, in the inherited order.

    Floors first, then the top-fraction cut — which matters, because the cut's
    quantile is taken over what the floors left, not over the raw set.
    """
    kept = list(records)

    if rule.fwhm_floor is not None:
        kept = [r for r in kept if r.fwhm is not None and r.fwhm > rule.fwhm_floor]
    if rule.center_floor is not None:
        kept = [r for r in kept if r.center > rule.center_floor]
    if rule.relative_intensity_floor is not None:
        # Measured against the full record set, so a strongest peak that
        # another floor dropped still sets its spectrum's reference.
        strongest: Dict[int, float] = {}
        for record in records:
            strongest[record.spectrum] = max(
                strongest.get(record.spectrum, record.intensity), record.intensity
            )
        kept = [
            r for r in kept
            if r.intensity >= rule.relative_intensity_floor * strongest[r.spectrum]
        ]

    if rule.fwhm_top_fraction:
        # Per label, not pooled: peaks of different widths would otherwise have
        # the broad one's whole distribution judged against the narrow one's.
        by_label: Dict[str, List[float]] = {}
        for record in kept:
            by_label.setdefault(record.label, []).append(record.fwhm)
        cutoffs = {
            label: float(np.quantile(widths, 1.0 - rule.fwhm_top_fraction))
            for label, widths in by_label.items() if widths
        }
        kept = [r for r in kept if r.fwhm <= cutoffs.get(r.label, np.inf)]

    return kept


def _peak_values(records: Sequence[_PeakRecord], panel: PeakPanel) -> Dict[int, np.ndarray]:
    out: Dict[int, list] = {}
    for record in records:
        if record.label != panel.peak:
            continue
        if panel.metric == "center":
            value = record.center
        elif panel.metric == "fwhm":
            value = record.fwhm
        else:
            value = record.intensity
        out.setdefault(record.point, []).append(value)
    return {p: np.asarray(v, float) for p, v in out.items()}


def _ratio_values(records: Sequence[_PeakRecord], panel: RatioPanel) -> Dict[int, np.ndarray]:
    """Ratios formed within one spectrum, then collected by position.

    Per spectrum first, never mean(numerator)/mean(denominator): only the
    per-spectrum form has a spread that means anything.
    """
    by_spectrum: Dict[int, Tuple[int, Dict[str, float]]] = {}
    for record in records:
        point, intensities = by_spectrum.setdefault(record.spectrum, (record.point, {}))
        intensities[record.label] = record.intensity

    out: Dict[int, list] = {}
    for point, intensities in by_spectrum.values():
        numerator = intensities.get(panel.numerator)
        denominator = intensities.get(panel.denominator)
        if numerator is not None and denominator:
            out.setdefault(point, []).append(numerator / denominator)
    return {p: np.asarray(v, float) for p, v in out.items()}


def _draw_panel(ax, by_point: Dict[int, np.ndarray], positions: Sequence[int],
                panel: Panel, is_bottom: bool, rng: np.random.Generator,
                statistic: str, per_position_iqr: bool) -> None:
    pooled = []
    for index, position in enumerate(positions):
        values = by_point.get(position)
        if values is None or values.size == 0:
            continue
        clean = values[iqr_mask(values)] if per_position_iqr else values
        if clean.size == 0:
            continue
        colour = POSITION_COLORS[index % len(POSITION_COLORS)]
        jitter = position + rng.uniform(-_JITTER, _JITTER, size=clean.size)
        ax.scatter(jitter, clean, color=colour, s=18, alpha=0.5,
                   edgecolor="none", zorder=3)
        centre = float(np.median(clean)) if statistic == "median" else float(clean.mean())
        std = clean.std(ddof=1) if clean.size > 1 else 0.0
        ax.errorbar(position + _MARKER_OFFSET, centre, yerr=std,
                    fmt="s", color=colour, ecolor=colour, elinewidth=2,
                    capsize=4, capthick=2, markersize=8, markerfacecolor=colour,
                    markeredgecolor=colour, markeredgewidth=1.2, zorder=5)
        pooled.extend(clean.tolist())

    if pooled:
        median = float(np.median(pooled))
        ax.axhline(median, color="black", linestyle="--", linewidth=1.3,
                   alpha=0.8, zorder=2, label=f"Median = {median:.2f}")
        ax.legend(loc="upper right", fontsize=_FS_REFTEXT, framealpha=0.85)
    else:
        ax.text(0.5, 0.5, f"no {getattr(panel, 'peak', 'data')} fits",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=_FS_LABEL, color="#999999")

    if panel.spec is not None:
        ax.axhline(panel.spec, color="grey", linewidth=1.2, alpha=0.6, zorder=1)
        # Value at the right edge in axes-fraction x, data y, so it tracks the
        # line without a second legend entry competing with the median's.
        ax.text(1.005, panel.spec, f"{panel.spec:g}", transform=ax.get_yaxis_transform(),
                va="center", ha="left", fontsize=_FS_REFTEXT, color="grey")

    ax.text(0.02, 0.95, panel.caption, transform=ax.transAxes, va="top", ha="left",
            fontsize=_FS_PANEL, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85, lw=0))

    ax.set_ylabel(panel.ylabel, fontsize=_FS_LABEL)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.set_xticks(list(positions))
    ax.tick_params(axis="both", labelsize=_FS_TICK)
    ax.set_xlim(min(positions) - 0.7, max(positions) + 0.8)
    if is_bottom:
        ax.set_xlabel("Position", fontsize=_FS_LABEL)
    else:
        ax.tick_params(labelbottom=False)


def build_peak_quality_figure(
    fits_by_point: Sequence[Tuple[int, FitResult]],
    sample_name: str,
    material_name: str = "WSe₂",
    spec: QualityFigureSpec = RAMAN_QUALITY,
    dpi: int = 160,
) -> bytes:
    """
    Render one technique's quality figure and return it as PNG bytes.

    `fits_by_point` is `(position, FitResult)` pairs, already passed through
    `peak_metrics.filter_fits_by_quality`; `spec.cleaning` is applied on top of
    that gate, and differs by technique — see the module docstring.

    `spec` carries the columns, the cleaning rule and the per-position marker
    statistic as one value: pass `RAMAN_QUALITY` or `PL_QUALITY`.

    The grid is computed from `spec.columns_for(...)` — the spec's own columns,
    or ones built from this sample's peak labels when the spec's name none of
    them — so a column with fewer panels than its neighbours simply leaves its
    bottom cell empty rather than having its last panel silently dropped.

    Returns bytes rather than a Figure so a caller cannot leak the canvas.
    """
    if not fits_by_point:
        raise ValueError("build_peak_quality_figure needs at least one fit")
    if not spec.columns:
        raise ValueError("build_peak_quality_figure needs at least one panel column")

    records = _clean(_records(fits_by_point), spec.cleaning)

    # Resolved from the fits, not from `spec`: a material whose peaks the
    # inherited columns never name gets columns built from its own. Taken
    # before cleaning, so the panel set is a property of the material rather
    # than of how well this particular sample fitted — a sample cleaned down to
    # nothing keeps its panels and shows them empty, which is the honest
    # result.
    columns = spec.columns_for(
        [peak.label for _, fit in fits_by_point for peak in fit.fitted_peaks]
    )

    # Positions come from the fits, not from the surviving records: a position
    # whose every fit was cleaned away keeps its tick and shows as a gap, which
    # is the thing worth seeing. Dropping it would close the gap and make nine
    # positions look like eight.
    positions = sorted({point for point, _ in fits_by_point})
    spectra = len(fits_by_point)

    n_cols = len(columns)
    n_rows = max(len(panels) for _, panels in columns)

    fig = plt.figure(figsize=(_COLUMN_W_IN * n_cols, _ROW_H_IN * n_rows), dpi=dpi)
    try:
        grid = gridspec.GridSpec(n_rows, n_cols, figure=fig, hspace=0.0, wspace=0.30)

        for column, (title, panels) in enumerate(columns):
            top = None
            for row, panel in enumerate(panels):
                ax = fig.add_subplot(grid[row, column], sharex=top)
                if top is None:
                    top = ax
                    ax.set_title(title, fontsize=_FS_TITLE, fontweight="bold", pad=10)
                values = (
                    _ratio_values(records, panel)
                    if isinstance(panel, RatioPanel)
                    else _peak_values(records, panel)
                )
                # Seeded per panel rather than once for the figure: the
                # inherited script draws jitter from one module-level generator
                # consumed in panel order, so inserting or reordering a panel
                # silently moves every later panel's cloud. The stride is the
                # row count, so seeds stay distinct at any grid shape.
                _draw_panel(
                    ax, values, positions, panel,
                    is_bottom=(row == len(panels) - 1),
                    rng=np.random.default_rng(42 + column * n_rows + row),
                    statistic=spec.statistic,
                    per_position_iqr=spec.cleaning.per_position_iqr,
                )

        per_position = spectra / len(positions) if positions else 0
        marker = "median ± std" if spec.statistic == "median" else "mean ± std"
        fig.suptitle(
            f"{sample_name}  {material_name} — {spec.technique} quality analysis "
            f"({len(positions)} positions × {per_position:.0f} spectra, "
            f"{marker} per position)",
            fontsize=_FS_SUPTITLE, fontweight="bold", y=1.0,
        )

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)
