"""
Flake domain-size measurement, on top of `contrast.py`'s segmentation.

Splits one of `classify()`'s three classes -- below reference, reference, or
above reference, the same three the OM class table already names via
`contrast.class_labels()` -- into individual flakes and measures each one.
`target` picks which: for a sparse-growth sample where "above reference" is
the flake population, that population is exactly what the OM diagnostic's
green overlay already shows. This module's own job starts where segmentation
leaves off: splitting touching/chained flakes into individual domains within
the chosen class and measuring each one.

Method, the "above" direction validated against real VBBF46 frames before
this module existed; "below" and "reference" are a documented, symmetric
extension of the same method, not independently validated against real
images yet:

1. Re-run `contrast.analyse_array()` on the frame's own `original`/`valid`
   (already loaded and resized -- no repeat disk I/O) to get the target mask
   (`lo`, `ref`, or `hi`) and the smoothed luminance it was thresholded from.
2. Optionally close small gaps in that mask (`mask_closing_radius`, module
   default off) before anything downstream sees it. `classify()`'s per-pixel
   threshold reproduces a flake's real (noisy, few-pixel-wide) edge as a
   ragged boundary rather than a clean line; wherever that raggedness opens
   even a 1px gap, step 3 below treats the far side as its own connected
   component and counts it as a separate domain sitting right at or inside
   the real flake's footprint. Closing bridges those gaps first.
3. Find one marker per flake: each flake has a distinct optical extremum at
   its own centre (a real reflection highlight). For "above" and "reference"
   that is a local luminance *maximum*; for "below" a local luminance
   *minimum*, since a below-reference domain is darker than its surroundings
   rather than brighter. The marker's own floor sits `marker_margin_sigma *
   sigma_noise` (module default `MARKER_MARGIN_SIGMA`, overridable per
   material/layer via `OpticalParams.marker_margin_sigma`) past the class's
   own boundary -- `th_hi` for "above", `th_lo` for "below" -- not a fixed
   pixel value, so a marker can't sit at the mask's own faint edge rather
   than a flake's true centre. An early version hardcoded one and it did not
   generalise across frames with different overall exposure. A raw connected
   component with no point clearing that floor at all -- flat, low-contrast,
   no distinct reflection -- gets exactly one fallback marker anyway, at its
   own brightest (or, for "below", darkest) point regardless of the floor:
   without this a component that never produces a qualifying peak is not
   measured small, it is dropped entirely, which is a far bigger effect than
   the floor is meant to have and undercounts badly against the OM class
   table's own (floor-free) component count.
4. Watershed-grow each marker through the target mask, splitting touching
   flakes at the ridge between two markers.
5. Drop domains smaller than `MIN_DOMAIN_PX` (oversplit noise, not real
   flakes), then set aside domains touching the valid region's border: their
   true size is cut off, so they are not sized, but they are real flakes and
   count `EDGE_WEIGHT` (one half) each toward nucleation density. A flake
   straddling the border is shared with the neighbouring field, so counting
   it whole would overcount and dropping it undercounts; one half is the
   unbiased weight in expectation. Applied to both frame types alike.
6. Measure equivalent circular diameter = 2*sqrt(area/pi), in pixels always,
   and in micrometres when a calibration is supplied. Pixels here are
   `contrast.load()`'s working pixels; the calibration is per native camera
   pixel and is rescaled by `FrameResult.scale` first.
7. Optionally split those diameters into a small and a large size population
   -- `split_size_populations()`, a KDE valley on `log10(diameter)` gated on
   a minimum ratio (`MIN_POPULATION_RATIO`), so it only fires on a genuine
   gap and reports one population, unchanged, otherwise. Independent of steps
   1-6: it runs on whatever diameters they produced, per frame in
   `measure_domains()` and again on the whole sample's pooled diameters in
   `domain_size_stat()`.

What this deliberately does not do: decide whether a fused, unsplit cluster
is one large flake or several unresolved ones. That judgement call was
discussed and set aside when this module was built -- the split is
best-effort, not outlier/solidity-screened, matching the reports built by
hand before this became a QC Report feature.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from scipy import ndimage as ndi
from scipy.signal import argrelextrema
from scipy.stats import gaussian_kde
from skimage.feature import peak_local_max
from skimage.segmentation import watershed

from core.report.models import DomainSizeStat, SizePopulationStat

from .contrast import DEFAULT_MINPX, DEFAULT_NSIGMA, FrameResult, analyse_array, luminance

#: The three classes `classify()` produces, in `contrast.class_labels()`'s
#: own (below, reference, above) order. What the QC Report page offers.
TARGETS = ("below", "reference", "above")

#: Marker floor is this many sigma_noise past the target class's own
#: boundary. A marker only slightly past that boundary sits at the mask's own
#: faint edge, not a flake's true centre.
MARKER_MARGIN_SIGMA = 0.5

#: Minimum pixel separation between two accepted markers. Lower splits
#: touching flakes more aggressively (risking oversplitting one flake into
#: several); raise it if flakes are being oversplit.
DEFAULT_MARKER_MIN_DISTANCE = 8

#: Domains smaller than this after splitting are dropped as likely oversplit
#: fragments rather than real flakes.
DEFAULT_MIN_DOMAIN_PX = 40

#: Morphological-closing radius (pixels) applied to the target mask before
#: connected-component/marker/watershed. 0 is off: `classify()`'s per-pixel
#: threshold mask is used exactly as-is, matching every material that hasn't
#: tuned this. Raise it (see `OpticalParams.mask_closing_radius`) to bridge
#: the 1-2px gaps a flake's noisy real edge leaves in the mask -- those gaps
#: are what turns a ragged edge into extra, separately-counted fringe
#: domains sitting right at or inside a real flake's footprint. Too large a
#: radius starts merging genuinely separate close-together small flakes.
DEFAULT_MASK_CLOSING_RADIUS = 0

#: A set of domains only splits into small/large populations when the two
#: groups' mean diameters differ by at least this ratio -- a difference this
#: sample's frames actually showed (large flakes vs. an excess of sub-micron
#: ones), not an arbitrary bimodality test. Below this, `split_size_populations`
#: reports one population rather than manufacture a split from ordinary spread.
MIN_POPULATION_RATIO = 2.0

#: `gaussian_kde`'s bandwidth for the log-diameter density `split_size_populations`
#: looks for a valley in. Fixed rather than exposed anywhere: it is a curve-
#: smoothing choice, not a per-material physical setting the way
#: `marker_min_distance`/`marker_margin_sigma` are.
POPULATION_KDE_BANDWIDTH = 0.25

#: Below this many domains, a KDE valley is noise, not evidence of two
#: populations -- `split_size_populations` returns "no split" rather than
#: fit a density to a handful of points.
MIN_DOMAINS_FOR_SPLIT = 20

#: What a domain touching the valid region's border counts for in nucleation
#: density -- see step 5 of the module docstring. Edge domains are never
#: sized, so this weight never reaches a diameter statistic.
EDGE_WEIGHT = 0.5

#: Nucleation density is reported per this many um2 -- (100 um)^2 -- rather
#: than per um2 directly: a flake count over one um2 is a fraction far
#: smaller than one for any real sample, which reads worse than the same
#: rate scaled up to a block size an operator can picture.
DENSITY_AREA_UM2 = 100.0 ** 2


def split_size_populations(diameters: np.ndarray) -> Optional[np.ndarray]:
    """Split `diameters` into a small and a large population, or report there
    isn't one.

    Method: `gaussian_kde` on `log10(diameters)`, valleys via
    `scipy.signal.argrelextrema`, walked from the smallest diameter upward --
    the first valley whose two resulting group means clear
    `MIN_POPULATION_RATIO` is the split. This deliberately is not "the single
    biggest gap between adjacent sorted diameters": that check looks at the
    two most-separated *neighbouring* points, which on real (noisy) data is
    dominated by the sparse tail's random spacing rather than the actual
    density, and is blind to a genuine valley that still has a few points
    scattered through it. A density valley is what an operator visually
    reading a bimodal histogram is actually responding to.

    Returns `None` -- "one population" -- when there are fewer than
    `MIN_DOMAINS_FOR_SPLIT` diameters, the density has no valley, or every
    valley's group-mean ratio falls short of `MIN_POPULATION_RATIO`. Never
    forces a split from an ordinarily-spread, unimodal set.

    Otherwise returns a boolean array the same length and order as
    `diameters`: True marks a domain as the large population, False the
    small one. Only ever two groups -- this does not recurse into further
    valleys, matching a "small row, large row" report rather than open-ended
    multi-population clustering.
    """
    if diameters.size < MIN_DOMAINS_FOR_SPLIT:
        return None

    logd = np.log10(diameters)
    if np.std(logd) == 0.0:
        # gaussian_kde raises LinAlgError on a zero-variance dataset (a
        # singular covariance matrix) -- every domain being exactly the same
        # size is a real input (a synthetic fixture, or a genuinely uniform
        # frame), not one only a malformed caller could produce, and it is
        # trivially "no split": there is no spread to find a gap in.
        return None
    kde = gaussian_kde(logd, bw_method=POPULATION_KDE_BANDWIDTH)
    xs = np.linspace(logd.min(), logd.max(), 400)
    ys = kde(xs)
    valleys = argrelextrema(ys, np.less)[0]
    if not valleys.size:
        return None

    order = np.argsort(diameters)
    d_sorted = diameters[order]
    for valley in valleys:
        split_value = 10.0 ** xs[valley]
        is_large_sorted = d_sorted >= split_value
        small = d_sorted[~is_large_sorted]
        large = d_sorted[is_large_sorted]
        if small.size == 0 or large.size == 0:
            continue
        if large.mean() / small.mean() >= MIN_POPULATION_RATIO:
            is_large = np.empty(diameters.shape, dtype=bool)
            is_large[order] = is_large_sorted
            return is_large

    return None


@dataclass(frozen=True)
class DomainResult:
    """One frame's detected domains, in pixels and (if calibrated) microns.

    `label_mask` is 0 (no domain) or a positive domain id, the same (H, W)
    shape as the `FrameResult.original` it was measured from -- not cropped
    to `valid`, matching `FrameResult` itself, so a renderer crops both the
    same way `om_grid._crop_to_valid` already does.

    `diameters_um` is an empty array, not a placeholder value, when
    `um_per_px` was not supplied -- there is no calibration to invent, and an
    empty array is what "not measured in physical units" looks like to code
    that sums or averages it, rather than a 0.0 that would read as a real
    zero-size domain.

    `target` is the class this result's domains were split from -- carried
    here so a renderer can label what it's showing without the caller
    threading the argument through separately.

    `population_is_large` is `None` for the ordinary case -- this frame's own
    domains didn't clear `MIN_POPULATION_RATIO`, or there were too few to
    test. When set, it is a boolean array the same length and order as
    `diameters_px`/`diameters_um` (see `split_size_populations`), letting a
    renderer colour and caption the two populations separately without
    re-deriving the split itself.

    `n_edge` counts domains touching the valid region's border (above
    `MIN_DOMAIN_PX`), and `edge_mask` marks their pixels. They are left out
    of `n`, `diameters_*` and `label_mask` -- their size is cut off -- and
    count `EDGE_WEIGHT` each toward density; see `n_counted`.

    `area_um2` is the frame's own analyzed (`valid`) region's real area --
    `valid.sum() * um_per_px**2` -- for nucleation density (count / area).
    `None`, not a pixel-count figure, when `um_per_px` was not supplied: a
    density in 1/px² is not comparable across magnifications and would be
    worse than reporting nothing, the same reasoning `diameters_um` already
    follows.
    """

    point: int
    n: int
    diameters_px: np.ndarray
    diameters_um: np.ndarray
    calibrated: bool
    label_mask: np.ndarray
    target: str = "above"
    population_is_large: Optional[np.ndarray] = None
    area_um2: Optional[float] = None
    n_edge: int = 0
    edge_mask: Optional[np.ndarray] = None

    @property
    def n_counted(self) -> float:
        """Flakes counted toward nucleation density: every sized domain,
        plus `EDGE_WEIGHT` per edge domain."""
        return self.n + EDGE_WEIGHT * self.n_edge


def measure_domains(
    frame: FrameResult,
    *,
    nsigma: float = DEFAULT_NSIGMA,
    minpx: int = DEFAULT_MINPX,
    margin: Optional[float] = None,
    ff_divisor: Optional[float] = None,
    abs_threshold=None,
    target: str = "above",
    marker_margin_sigma: Optional[float] = None,
    marker_min_distance: Optional[int] = None,
    min_domain_px: int = DEFAULT_MIN_DOMAIN_PX,
    mask_closing_radius: Optional[int] = None,
    um_per_px: Optional[float] = None,
    detect_size_populations: bool = True,
) -> DomainResult:
    """Measure flake domains in one already-segmented frame.

    The tuning arguments (`nsigma`, `minpx`, `margin`, `ff_divisor`,
    `abs_threshold`) should be the same ones the frame's own OM segmentation
    used -- the mask this re-derives is the same population the OM figure's
    overlay already shows, and measuring a different population would make
    the domain-size figure disagree with the OM one drawn from the same
    frame.

    `target` selects which of `classify()`'s three classes to split into
    domains -- `"below"`, `"reference"`, or `"above"` (`TARGETS`), matching
    `contrast.class_labels()`'s own order and wording. `"above"` is the
    default and reproduces this module's original, VBBF46-validated
    behaviour exactly.

    `marker_margin_sigma` overrides `MARKER_MARGIN_SIGMA`; None keeps the
    module default. Lower it when real domains are going missing because
    they're too flat to clear the default floor -- see `OpticalParams
    .marker_margin_sigma` for the per-material/layer preset field. It still
    only helps a component that has *some* peak near the floor; a genuinely
    flat component relies on the fallback marker below regardless of this
    setting.

    `marker_min_distance` overrides `DEFAULT_MARKER_MIN_DISTANCE`; None keeps
    the module default (8), the value this module's "above" path was
    validated against for VBBF46 -- see `OpticalParams.marker_min_distance`
    for the per-material/layer preset field. Raise it when one flake's own
    internal reflection highlights are being watershed-split into several
    markers (and so several domains) instead of staying one; the module
    default stays put for anyone who hasn't tuned it.

    `um_per_px` is the calibration for the frame's magnification, in microns
    per *native camera* pixel -- what `data/om_calibration.json` stores; None
    means uncalibrated, and `DomainResult.calibrated` is False. Measurement
    runs on `frame.original`, which `contrast.load()` has already shrunk to
    `MAXDIM`, so it is converted here by `frame.scale` before any pixel count
    becomes a length or an area. Applying it to working pixels unconverted
    under-reported every diameter by 1/scale (1.6x on a 2240-px frame) and
    over-reported density by its square.

    `mask_closing_radius` overrides `DEFAULT_MASK_CLOSING_RADIUS`; None or 0
    keeps the mask exactly as `classify()` produced it. Raise it when a
    flake's own ragged, noisy real edge is leaving small fringe fragments
    disconnected from the flake's main body -- each one becomes its own
    (mis-)counted domain otherwise, sitting right at or inside the real
    flake's footprint. See `OpticalParams.mask_closing_radius` for the
    per-material/layer preset field. Too large a radius starts merging
    genuinely separate close-together small flakes into one.

    `detect_size_populations` gates the `split_size_populations()` call
    below; False forces `DomainResult.population_is_large` to `None`
    regardless of what the diameters would otherwise show. The QC Report
    page exposes this as its own checkbox, off by default -- the split has
    fired on frames with no real second population at all (pure background
    texture producing enough size spread on its own to clear
    `MIN_POPULATION_RATIO`), so an operator who hasn't asked for it should
    see the plain pooled measurement, not a split that may not be real.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS!r}, got {target!r}")
    mmd = DEFAULT_MARKER_MIN_DISTANCE if marker_min_distance is None else marker_min_distance
    closing_radius = (
        DEFAULT_MASK_CLOSING_RADIUS if mask_closing_radius is None else mask_closing_radius
    )

    res = analyse_array(
        frame.original, frame.ref_label, nsigma=nsigma, minpx=minpx,
        margin=margin, ff_divisor=ff_divisor, abs_threshold=abs_threshold,
    )
    valid, st = res["valid"], res["st"]
    mask = {"below": res["lo"], "reference": res["ref"], "above": res["hi"]}[target]
    if closing_radius:
        # Bridges the 1-2px gaps a flake's noisy real edge leaves in the raw
        # threshold mask, reconnecting a fringe fragment into its parent
        # flake's own connected component instead of letting it become its
        # own (mis-)counted domain. Re-intersected with `valid`: closing can
        # dilate a pixel or two past the aperture's inner edge before eroding
        # back, and `mask` must stay confined to `valid` the same way
        # `classify()` already guarantees for the unclosed mask -- the
        # border-touching-domain exclusion below depends on it.
        mask = ndi.binary_closing(mask, iterations=closing_radius) & valid

    # Microns per pixel of `frame.original`, not per camera pixel -- see above.
    if um_per_px is not None:
        um_per_px = um_per_px / frame.scale

    if not mask.any():
        empty = np.zeros(0, dtype=float)
        area_um2 = float(valid.sum()) * um_per_px**2 if um_per_px is not None else None
        return DomainResult(
            point=frame.point, n=0, diameters_px=empty, diameters_um=empty,
            calibrated=um_per_px is not None,
            label_mask=np.zeros(frame.original.shape[:2], dtype=int),
            target=target, area_um2=area_um2,
        )

    margin_sigma = MARKER_MARGIN_SIGMA if marker_margin_sigma is None else marker_margin_sigma
    lum = luminance(res["s"])
    # "above"/"reference" look for a flake's bright reflection highlight, a
    # local luminance maximum; "below" is darker than its surroundings, so
    # its own extremum is a local *minimum* instead. Negating luminance turns
    # that minimum into a maximum so the rest of the pipeline -- peak-finding,
    # then watershed on the negated landscape -- stays one code path. The
    # floor sits `margin_sigma * sigma_noise` short of (for "above" and
    # "below", past) the class's own boundary: `th_hi` for "above", `th_lo`
    # for "below". "reference" anchors to `th_hi` too rather than to its own
    # `mode`: unlike above/below, which already sit `nsigma` away from the
    # mode by construction, the reference class surrounds the mode itself, so
    # a mode-anchored floor sits only a fraction of a sigma above ordinary
    # background texture and finds a spurious "marker" in nearly every
    # smoothed noise ripple across what is often most of the frame. Anchoring
    # near the class's own upper edge instead asks for a peak nearly as
    # extreme as "above"'s own threshold, which is what actually filters
    # background texture out in practice.
    if target == "below":
        signed_lum = -lum
        marker_floor = margin_sigma * st["sigma_noise"] - st["th_lo"]
    elif target == "reference":
        signed_lum = lum
        marker_floor = st["th_hi"] - margin_sigma * st["sigma_noise"]
    else:
        signed_lum = lum
        marker_floor = st["th_hi"] + margin_sigma * st["sigma_noise"]

    masked_lum = np.where(mask, signed_lum, 0.0)
    coords = peak_local_max(
        masked_lum, min_distance=mmd, labels=mask,
        threshold_abs=marker_floor,
    )

    markers = np.zeros(mask.shape, dtype=int)
    for marker_id, (y, x) in enumerate(coords, start=1):
        markers[y, x] = marker_id

    # A raw component that never produces a point clearing `marker_floor` --
    # flat, low-contrast, no distinct reflection -- gets no marker above and
    # would vanish from `labelled` entirely rather than surviving as one
    # (unsplit) domain. That is a much bigger effect than the floor is meant
    # to have: it drops the whole component, not just a size estimate, and
    # undercounts badly against the OM class table's own floor-free component
    # count. One fallback marker per un-marked component, at its own
    # extremum regardless of the floor, fixes that without touching
    # components that already got a real marker above.
    raw_components, n_raw = ndi.label(mask)
    marked = set(raw_components[coords[:, 0], coords[:, 1]].tolist()) if len(coords) else set()
    next_id = len(coords) + 1
    for component_id in range(1, n_raw + 1):
        if component_id in marked:
            continue
        component = raw_components == component_id
        y, x = np.unravel_index(
            np.argmax(np.where(component, signed_lum, -np.inf)), signed_lum.shape
        )
        markers[y, x] = next_id
        next_id += 1

    labelled = watershed(-masked_lum, markers, mask=mask)

    # A domain touching the excluded margin has an unknown true size, same as
    # one cut off by the sensor edge, and must not be counted. `clear_border`
    # cannot do this: `mask` (and so `labelled`) is already confined to
    # `valid` (classify() computes each class as `... & valid`), so a
    # domain's labelled pixels are never actually *outside* valid for
    # `clear_border` to notice -- it would only ever see background there and
    # clear nothing. What identifies a truncated domain instead is touching
    # the *inner edge* of `valid`: the one-pixel ring just inside the
    # excluded margin, where a domain that continues past the frame's
    # analysed area meets it.
    # Such a domain is still a real flake, though: it is kept aside as an
    # edge domain, unsized but counted `EDGE_WEIGHT` toward density. The
    # `min_domain_px` noise floor runs first, so an edge fragment too small
    # to be a flake anywhere else is not counted at the edge either.
    ids = np.unique(labelled)
    ids = ids[ids > 0]
    if ids.size:
        sizes = ndi.sum(np.ones_like(labelled), labelled, index=ids)
        keep_ids = ids[sizes >= min_domain_px]
    else:
        keep_ids = ids
    border_ring = valid & ~ndi.binary_erosion(valid)
    touching_ids = np.unique(labelled[border_ring])
    touching_ids = touching_ids[np.isin(touching_ids, keep_ids)]
    edge_mask = np.isin(labelled, touching_ids)
    final = np.where(np.isin(labelled, keep_ids) & ~edge_mask, labelled, 0)

    kept = np.unique(final)
    kept = kept[kept > 0]
    sizes_px = (
        ndi.sum(np.ones_like(final), final, index=kept) if kept.size
        else np.zeros(0, dtype=float)
    )
    diameters_px = 2.0 * np.sqrt(sizes_px / np.pi)
    diameters_um = diameters_px * um_per_px if um_per_px is not None else np.zeros(0, dtype=float)
    population_is_large = (
        split_size_populations(diameters_px) if detect_size_populations else None
    )
    area_um2 = float(valid.sum()) * um_per_px**2 if um_per_px is not None else None

    return DomainResult(
        point=frame.point, n=int(kept.size), diameters_px=diameters_px,
        diameters_um=diameters_um, calibrated=um_per_px is not None,
        label_mask=final, target=target, population_is_large=population_is_large,
        area_um2=area_um2, n_edge=int(touching_ids.size), edge_mask=edge_mask,
    )


def pooled_diameters(results, calibrated: bool) -> np.ndarray:
    """Every domain's diameter across `results`, in the unit `calibrated`
    selects. Empty if no result has any domains, or none is calibrated when
    `calibrated=True` was asked for."""
    attr = "diameters_um" if calibrated else "diameters_px"
    arrays = [getattr(r, attr) for r in results if len(getattr(r, attr))]
    return np.concatenate(arrays) if arrays else np.zeros(0, dtype=float)


def domain_size_stat(
    results, detect_size_populations: bool = True
) -> Optional[DomainSizeStat]:
    """Pooled domain-size statistics across `results`, for the summary page's
    domain-size table. None when no frame produced any domain -- the
    renderer's placeholder-box convention, matching `frame_class_stats`'
    empty-list one.

    Reports in microns if any frame was calibrated, else pixels -- a sample
    is calibrated or it is not, by magnification, so a mixed set should not
    occur in practice; if it did, this prefers the calibrated unit rather
    than silently discarding those frames' measurements.

    `detect_size_populations` mirrors `measure_domains()`'s own argument of
    the same name; False forces `DomainSizeStat.populations` to `None`
    regardless of what the pooled diameters would otherwise show.
    """
    n_total = sum(r.n for r in results)
    if n_total == 0:
        return None
    n_edge = sum(r.n_edge for r in results)

    calibrated = any(r.calibrated and r.n for r in results)
    pooled = pooled_diameters(results, calibrated=calibrated)
    if pooled.size == 0:
        return None

    area_um2 = (
        sum(r.area_um2 for r in results if r.area_um2 is not None) if calibrated else None
    )
    n_counted = n_total + EDGE_WEIGHT * n_edge
    density_per_100um2 = n_counted / area_um2 * DENSITY_AREA_UM2 if area_um2 else None

    return DomainSizeStat(
        n_domains=n_total,
        n_frames=sum(1 for r in results if r.n),
        diameter_median=float(np.median(pooled)),
        diameter_mean=float(pooled.mean()),
        diameter_std=float(pooled.std(ddof=1)) if pooled.size > 1 else 0.0,
        unit="µm" if calibrated else "px",
        calibrated=calibrated,
        populations=(
            _pooled_population_stat(pooled, area_um2) if detect_size_populations else None
        ),
        area_um2=area_um2,
        density_per_100um2=density_per_100um2,
        n_edge=n_edge,
    )


def _pooled_population_stat(
    pooled: np.ndarray, area_um2: Optional[float],
) -> Optional[Tuple[SizePopulationStat, SizePopulationStat]]:
    """The sample-level (small, large) split for `domain_size_stat`'s table.

    Re-run on the pooled diameters rather than unioning each frame's own
    `DomainResult.population_is_large` -- pooling first gives the KDE more
    points to work with, and a sample-level table wants one consistent split,
    not nine frames' independent (and possibly differently-placed) valleys.

    `area_um2` is the same pooled analyzed area `domain_size_stat` computed --
    shared by both populations, since it describes the sample, not either
    population's own footprint. Each population's density counts its own
    sized domains only: an edge domain has no size, so it belongs to neither
    population, and the two densities sum to less than the pooled one by
    exactly the edge domains' `EDGE_WEIGHT` share.
    """
    is_large = split_size_populations(pooled)
    if is_large is None:
        return None

    def _stat(label: str, values: np.ndarray) -> SizePopulationStat:
        return SizePopulationStat(
            label=label, n=int(values.size),
            diameter_median=float(np.median(values)),
            diameter_mean=float(values.mean()),
            diameter_std=float(values.std(ddof=1)) if values.size > 1 else 0.0,
            density_per_100um2=(
                values.size / area_um2 * DENSITY_AREA_UM2 if area_um2 else None
            ),
        )

    return (_stat("small", pooled[~is_large]), _stat("large", pooled[is_large]))
