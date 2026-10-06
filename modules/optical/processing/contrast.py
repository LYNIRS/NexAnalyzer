"""
Optical-contrast layer classification for TMD films on SiO2/Si.

Vendored from `tmd_contrast.py`, whose reasoning is documented in
docs/OM_Contrast_Algo.md. The analysis functions below are kept close to
verbatim so they stay checkable against that document line by line; read it
before changing any of them.

The physics, in one line: on this substrate thicker is brighter, so a film's
green-channel histogram is a noise peak at the reference layer's brightness
with a shoulder wherever a thicker or thinner domain exists.

What the algorithm does **not** do is decide which layer the reference is. It
finds a population darker than the film and one brighter than it; the operator
states whether the film is 1L or 2L. Class names are therefore ordinal —
"Below 2L" / "Bilayer" / "Above 2L" — because darker-than-bilayer could be
monolayer or bare substrate and one frame cannot tell them apart. The upstream
script called that class "substrate", which asserts more than the measurement
supports.

Two deliberate deviations from the vendored source, both behaviour-preserving:

- `skimage.filters.gaussian` is replaced by `scipy.ndimage.gaussian_filter`,
  which avoids adding scikit-image for one call. This is only equivalent with
  `mode="nearest"` passed explicitly: skimage defaults to that, scipy defaults
  to `"reflect"`. It is not a detail. The flat-field blur runs at sigma =
  max(H,W)/8, about 175 px on a 1400 px frame, so the edge policy sets the
  background estimate over a wide border — swapping it moved sigma_L by up to
  0.5, which moved `sigma_noise`, every threshold derived from it, and coverage
  by more than two points of a percent. Both calls below therefore pin the mode,
  and tests/unit/test_om_contrast.py locks the numbers against the upstream
  script's own output.
- `Image.LANCZOS` is spelled `Image.Resampling.LANCZOS`, its non-deprecated
  name.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

# ---------------------------------------------------------------- constants
MAXDIM = 1400           # working resolution
RECT_MARGIN = 0.05
CIRC_MARGIN = 0.10
FF_SIGMA_RECT = 8       # flat-field sigma = max(H,W) / this
FF_SIGMA_CIRC = 12
SMOOTH_SIGMA = 1.0
HIST_BINS = 300
HIST_SMOOTH = 3
HWHM_TO_SIGMA = 1.177   # HWHM = 1.177 sigma for a Gaussian
SHOULDER_RATIO = 1.3
DEFAULT_NSIGMA = 4.0
DEFAULT_MINPX = 3

#: Overlay tints. Red marks the darker class, green the brighter one.
RED = np.array([220, 50, 50])
GREEN = np.array([40, 210, 100])

#: Layer-count label to the word a report should print for it.
_LAYER_WORDS = {"1L": "Monolayer", "2L": "Bilayer", "3L": "Trilayer"}

REFERENCE_CHOICES = ("1L", "2L")
"""Reference layers the operator may declare. The algorithm cannot infer this."""


def layer_word(ref_label: str) -> str:
    """Turn a layer count into the word a report prints: 2L to Bilayer.

    Unknown labels are returned unchanged.
    """
    return _LAYER_WORDS.get(ref_label, ref_label)


def class_labels(ref_label: str, low_coverage: bool = False) -> Tuple[str, str, str]:
    """
    The (darker, reference, brighter) class names for a reference layer.

    Ordinal by design: the algorithm separates pixels darker and brighter than
    the reference film, and cannot say what those populations are made of.
    "Below 2L" is honest where "Substrate" or "Monolayer" would be a guess.

    `classify()` always treats the frame's most common pixel value as "the
    reference" -- `ref_label` never reaches the threshold math, it only names
    the result. For a sparse-nucleation sample that majority *is* bare
    substrate, not the named film, and `low_coverage` says so: the reference
    class is relabelled "Substrate" and the brighter class becomes the named
    film itself (what's actually above bare substrate), rather than
    "Above {ref_label}" over what would really be substrate. Still just two
    populations underneath -- this doesn't separate a thicker layer from the
    named one if both are present above substrate in the same frame.
    """
    if low_coverage:
        return ("Below Substrate", "Substrate", layer_word(ref_label))
    return (f"Below {ref_label}", layer_word(ref_label), f"Above {ref_label}")


# ---------------------------------------------------------------- steps
def load(path) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    w, h = im.size
    sc = min(1.0, MAXDIM / max(w, h))
    im = im.resize((round(w * sc), round(h * sc)), Image.Resampling.LANCZOS)
    return np.asarray(im).astype(float)


def luminance(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def is_circular(a: np.ndarray) -> bool:
    """Dark aperture corners?  Small (3 %) extreme-corner boxes — larger boxes
    straddle the aperture edge and lose discrimination."""
    H, W = a.shape[:2]
    lum = luminance(a)
    c = max(2, int(min(H, W) * 0.03))
    corners = np.concatenate([lum[:c, :c].ravel(), lum[:c, -c:].ravel(),
                              lum[-c:, :c].ravel(), lum[-c:, -c:].ravel()])
    centre = np.median(lum[H // 3: 2 * H // 3, W // 3: 2 * W // 3])
    return bool(np.median(corners) / centre < 0.5)


def make_mask(a: np.ndarray, margin: Optional[float] = None):
    """Return (aperture, valid, frame_type)."""
    H, W = a.shape[:2]
    if is_circular(a):
        lum = luminance(a)
        centre = np.median(lum[H // 3: 2 * H // 3, W // 3: 2 * W // 3])
        ap = lum > centre * 0.5
        ap = ndi.binary_fill_holes(ap)
        ap = ndi.binary_opening(ap, iterations=3)
        m = CIRC_MARGIN if margin is None else margin
        valid = ndi.binary_erosion(ap, iterations=int(min(H, W) * m))
        return ap, valid, "circular"
    ap = np.ones((H, W), bool)
    m = RECT_MARGIN if margin is None else margin
    valid = np.zeros((H, W), bool)
    valid[int(H * m): int(H * (1 - m)), int(W * m): int(W * (1 - m))] = True
    return ap, valid, "rectangular"


def flatfield(a: np.ndarray, aperture: np.ndarray, frame_type: str,
              ff_divisor: Optional[float] = None) -> np.ndarray:
    """Divide out the illumination gradient.

    `ff_divisor` is a **divisor**, not a sigma: the blur runs at
    max(H, W) / ff_divisor, so a larger number means a *smaller* sigma and a
    more local background estimate (docs/OM_Contrast_Algo.md: "/16 if
    vignetting still visible"). None keeps the per-frame-type defaults.

    An override applies to both frame types. It cannot do otherwise: whether a
    frame is circular is decided per image by `is_circular`, so a preset -- the
    only thing that sets this -- has no way to say "circular only". Same
    reasoning as `make_mask`'s margin.
    """
    H, W = a.shape[:2]
    if ff_divisor is None:
        ff_divisor = FF_SIGMA_CIRC if frame_type == "circular" else FF_SIGMA_RECT
    s = max(H, W) / ff_divisor
    out = a.copy()
    for ch in range(3):
        plane = a[..., ch].copy()
        if frame_type == "circular":
            plane[~aperture] = np.median(plane[aperture])   # fill black BEFORE blur
        bg = ndi.gaussian_filter(plane, sigma=s, mode="nearest")
        out[..., ch] = a[..., ch] / np.maximum(bg, 1) * bg[aperture].mean()
    return out


def histogram_stats(gv: np.ndarray) -> dict:
    edges = np.linspace(gv.min(), gv.max(), HIST_BINS)
    hist, _ = np.histogram(gv, bins=edges)
    ctr = (edges[:-1] + edges[1:]) / 2
    hs = ndi.gaussian_filter1d(hist.astype(float), HIST_SMOOTH)
    i = int(np.argmax(hs))
    mode = ctr[i]
    hm = hs[i] / 2
    right = hs[i:] < hm
    left = hs[: i + 1][::-1] < hm
    hw_r = (ctr[i + int(np.argmax(right))] - mode) if right.any() else 5.0
    hw_l = (mode - ctr[i - int(np.argmax(left))]) if left.any() else 5.0
    return dict(ctr=ctr, hs=hs, mode=mode,
                sigma_l=hw_l / HWHM_TO_SIGMA, sigma_r=hw_r / HWHM_TO_SIGMA)


def despeckle(m: np.ndarray, minpx: int) -> np.ndarray:
    cc, n = ndi.label(m)
    if n == 0:
        return m
    sz = ndi.sum(m, cc, index=np.arange(1, n + 1))
    return np.isin(cc, np.where(sz >= minpx)[0] + 1)


def _threshold_pair(abs_threshold) -> Tuple[float, float]:
    """`abs_threshold` as an explicit (below, above) percent pair.

    A scalar means the same cut on both sides. A 2-tuple states them
    separately, which the HA bilayer films need: 2L->3L is one layer step up,
    but 2L->substrate can be two or more steps down, so the natural boundaries
    sit at roughly +4 % and -6 % and are not mirror images. Thresholding
    symmetrically puts the low cut inside the film's own noise and inflates the
    "Below" class -- which is what over-reported substrate on the 202609 set.
    """
    if isinstance(abs_threshold, (tuple, list)):
        below, above = abs_threshold
        return float(below), float(above)
    return float(abs_threshold), float(abs_threshold)


def classify(g: np.ndarray, valid: np.ndarray, nsigma: float, minpx: int,
             abs_threshold=None):
    """
    Split `valid` pixels into darker / reference / brighter by the green
    channel's own noise width.

    `sigma_noise = min(sigma_l, sigma_r)` is the step that makes this work.
    Whichever side of the histogram carries a domain population grows a
    shoulder that inflates its own half-width, so using each side's own sigma
    pushes that threshold away from the very domains it should be catching.
    The narrower side is the uncontaminated noise width.

    `abs_threshold` overrides that rule with a fixed green contrast, in percent
    of the film mode, and exists because `min(sigma_l, sigma_r)` protects only
    against *one* contaminated side. A film whose domains are small and
    pervasive rather than few and large widens **both** halves equally, the
    minimum is then no longer a noise width, and the threshold it sets rises
    above a full layer step -- so the population hides itself. HADH51 is the
    worked example: nine frames whose sigma_l and sigma_r agree to twelve
    decimal places, 4 sigma landing at +7.1 % against a ~5 % layer step, and
    0.14 % of a visibly trilayer-rich film reported as "Above 2L".

    Stated as a contrast the threshold cannot be moved by the very thing it
    measures. Half a layer step (~2.5 %) is the midpoint between the reference
    film at 0 % and the next layer at ~+5 %, which is where a decision boundary
    belongs. The sigma statistics are still computed and returned, because the
    diagnostic histograms draw them and `shoulder` still reads as intended.
    """
    st = histogram_stats(g[valid])
    st["sigma_noise"] = min(st["sigma_l"], st["sigma_r"])          # key step
    st["shoulder"] = bool(
        max(st["sigma_l"], st["sigma_r"]) / max(st["sigma_noise"], 1e-6) > SHOULDER_RATIO
    )
    if abs_threshold is None:
        st["th_hi"] = st["mode"] + nsigma * st["sigma_noise"]
        st["th_lo"] = st["mode"] - nsigma * st["sigma_noise"]
    else:
        below, above = _threshold_pair(abs_threshold)
        st["th_hi"] = st["mode"] * (1.0 + above / 100.0)
        st["th_lo"] = st["mode"] * (1.0 - below / 100.0)
    hi = despeckle((g > st["th_hi"]) & valid, minpx)
    lo = despeckle((g < st["th_lo"]) & valid, minpx)
    ref = valid & ~hi & ~lo
    return lo, ref, hi, st


def interior_mean(s: np.ndarray, m: np.ndarray):
    """Mean RGB of a class, eroded 1 px to exclude boundary-mixed pixels."""
    mi = ndi.binary_erosion(m, iterations=1)
    if mi.sum() < 30:
        mi = m
    return s[mi].reshape(-1, 3).mean(0) if mi.any() else None


def measure(s: np.ndarray, lo: np.ndarray, ref: np.ndarray,
            hi: np.ndarray, valid: np.ndarray) -> dict:
    """Area fraction, component count, green contrast and mean RGB per class."""
    c_ref = interior_mean(s, ref)
    out: Dict[str, dict] = {}
    for name, m in (("below", lo), ("above", hi)):
        c = interior_mean(s, m)
        pct = m.sum() / valid.sum() * 100
        ncomp = ndi.label(m)[1]
        if c is None or c_ref is None:
            out[name] = dict(pct=pct, n=ncomp, cg=float("nan"), rgb=None)
        else:
            out[name] = dict(pct=pct, n=ncomp,
                             cg=(c[1] - c_ref[1]) / c_ref[1] * 100, rgb=c)
    out["ref"] = dict(pct=ref.sum() / valid.sum() * 100, rgb=c_ref)
    return out


def analyse_array(a: np.ndarray, ref_label: str, nsigma: float = DEFAULT_NSIGMA,
                  minpx: int = DEFAULT_MINPX, margin: Optional[float] = None,
                  ff_divisor: Optional[float] = None,
                  abs_threshold=None) -> dict:
    """Segment an already-loaded frame array. Returns the raw working dict;
    see `analyse_frame`.

    Split out of `analyse()` so a caller with an array already in memory --
    domain-size analysis, reusing a `FrameResult.original` -- does not repeat
    the disk read and LANCZOS resize `load()` does. Those are most of a
    frame's cost; flat-field, smoothing and thresholding on an array already
    in memory are cheap by comparison.
    """
    aperture, valid, ftype = make_mask(a, margin)
    flat = flatfield(a, aperture, ftype, ff_divisor)
    # sigma 0 on the channel axis blurs each channel independently, which is
    # what skimage's channel_axis=2 does in the vendored source; mode="nearest"
    # matches its default edge policy.
    s = ndi.gaussian_filter(
        flat, sigma=(SMOOTH_SIGMA, SMOOTH_SIGMA, 0), mode="nearest"
    )
    lo, ref, hi, st = classify(s[..., 1], valid, nsigma, minpx, abs_threshold)
    meas = measure(s, lo, ref, hi, valid)
    return dict(a=a, s=s, valid=valid, lo=lo, ref=ref, hi=hi, st=st,
                meas=meas, ftype=ftype, ref_label=ref_label)


def analyse(path, ref_label: str, nsigma: float = DEFAULT_NSIGMA,
            minpx: int = DEFAULT_MINPX, margin: Optional[float] = None,
            ff_divisor: Optional[float] = None,
            abs_threshold=None) -> dict:
    """Segment one frame from disk. Returns the raw working dict; see
    `analyse_frame`. `load()` then `analyse_array()` -- see that function for
    why a caller with an in-memory array should call it directly instead."""
    a = load(path)
    return analyse_array(a, ref_label, nsigma=nsigma, minpx=minpx, margin=margin,
                         ff_divisor=ff_divisor, abs_threshold=abs_threshold)


# ---------------------------------------------------------------- app surface
@dataclass(frozen=True)
class FrameResult:
    """One segmented frame, in the shape the figures and exports want."""

    name: str
    point: int
    frame_type: str
    ref_label: str
    labels: Tuple[str, str, str]        # (below, reference, above)
    percentages: Tuple[float, float, float]
    contrast_below: float               # green contrast %, relative to reference
    contrast_above: float
    components_below: int
    components_above: int
    original: np.ndarray                # (H,W,3) float, display copy
    overlay: np.ndarray                 # (H,W,3) float, class-tinted
    valid: np.ndarray                   # (H,W) bool
    hist_centers: np.ndarray
    hist_counts: np.ndarray
    mode: float
    sigma_l: float
    sigma_r: float
    sigma_noise: float
    threshold_low: float
    threshold_high: float
    shoulder: bool

    @property
    def reference_pct(self) -> float:
        return self.percentages[1]


def _overlay(res: dict) -> np.ndarray:
    over = res["a"].copy()
    lo, hi = res["lo"], res["hi"]
    over[lo] = RED * 0.55 + over[lo] * 0.45
    over[hi] = GREEN * 0.55 + over[hi] * 0.45
    return over


def analyse_frame(path, point: int, ref_label: str, name: Optional[str] = None,
                  nsigma: float = DEFAULT_NSIGMA, minpx: int = DEFAULT_MINPX,
                  margin: Optional[float] = None,
                  ff_divisor: Optional[float] = None,
                  abs_threshold=None, low_coverage: bool = False) -> FrameResult:
    """Segment one frame and return it as a `FrameResult`.

    Every tuning argument defaults to the module constant it overrides, so
    `analyse_frame(path, point, ref)` with no tuning is the vendored algorithm
    exactly -- which is what keeps tests/unit/test_om_contrast.py's locked
    numbers meaningful. `abs_threshold` defaults to None for the same reason:
    unset, `classify` thresholds on nsigma exactly as it always has.

    `low_coverage` only changes `labels` (see `class_labels`) -- it never
    reaches `analyse`/`classify`, so the segmentation itself is identical
    either way.
    """
    res = analyse(path, ref_label, nsigma=nsigma, minpx=minpx, margin=margin,
                  ff_divisor=ff_divisor, abs_threshold=abs_threshold)
    st, meas = res["st"], res["meas"]
    return FrameResult(
        name=name or str(point),
        point=point,
        frame_type=res["ftype"],
        ref_label=ref_label,
        labels=class_labels(ref_label, low_coverage),
        percentages=(meas["below"]["pct"], meas["ref"]["pct"], meas["above"]["pct"]),
        contrast_below=meas["below"]["cg"],
        contrast_above=meas["above"]["cg"],
        components_below=meas["below"]["n"],
        components_above=meas["above"]["n"],
        original=res["a"],
        overlay=_overlay(res),
        valid=res["valid"],
        hist_centers=st["ctr"],
        hist_counts=st["hs"],
        mode=st["mode"],
        sigma_l=st["sigma_l"],
        sigma_r=st["sigma_r"],
        sigma_noise=st["sigma_noise"],
        threshold_low=st["th_lo"],
        threshold_high=st["th_hi"],
        shoulder=st["shoulder"],
    )


def _across_frames(values: Sequence[float]) -> Tuple[float, float]:
    """(mean, std) of one per-frame quantity.

    Standard deviation uses ddof=1 when more than one frame contributed, so a
    single-frame sample reports 0.0 rather than nan. The one place that rule
    lives, so the coverage summary and the contrast summary cannot drift apart.
    """
    if not len(values):
        return (0.0, 0.0)
    arr = np.asarray(values, dtype=float)
    ddof = 1 if arr.size > 1 else 0
    return (float(arr.mean()), float(arr.std(ddof=ddof)))


def class_summary(frames: Sequence[FrameResult]) -> Tuple[Tuple[float, float], ...]:
    """(mean, std) coverage per class across frames, in label order."""
    if not frames:
        return ((0.0, 0.0),) * 3
    pcts = np.array([f.percentages for f in frames], dtype=float)
    return tuple(_across_frames(pcts[:, i]) for i in range(3))


def contrast_summary(
    frames: Sequence[FrameResult],
) -> Tuple[Optional[Tuple[float, float]], ...]:
    """(mean, std) green contrast for the below and above classes, in that order.

    Two entries, not three: contrast is measured *relative to* the reference
    film, so the reference class has none of its own. Aggregated the same way
    as `class_summary`, and returned beside it by the CSV export.

    Only frames where the class exists contribute. A frame with no pixels in a
    class records its contrast as nan, which used to make the whole wafer's
    figure nan -- SU261001 printed "nan ± nan" for Above 1L because a few of
    its nine frames had no above-class pixels. An entry is None when no frame
    has the class at all, which the summary table and workbook render as empty.
    """
    if not frames:
        return ((0.0, 0.0),) * 2

    def _present(values):
        finite = [v for v in values if np.isfinite(v)]
        return _across_frames(finite) if finite else None

    return (
        _present([f.contrast_below for f in frames]),
        _present([f.contrast_above for f in frames]),
    )
