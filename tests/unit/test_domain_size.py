"""
Unit tests for modules.optical.processing.domain_size.

Uses the same synthetic-frame recipe as test_om_contrast.py (a vignetted,
noisy rectangular frame with planted domains), extended with distinctly
brighter, well-separated peaks per planted blob -- domain-size splitting
needs each flake to have its own local luminance maximum, which a flat
plateau (as test_om_contrast's single uniform patch is) does not provide.
"""

import numpy as np
import pytest
from PIL import Image

import modules.optical.processing.domain_size as domain_size_module
from modules.optical.processing.contrast import analyse_frame
from modules.optical.processing.domain_size import (
    TARGETS,
    DomainResult,
    domain_size_stat,
    measure_domains,
    pooled_diameters,
)


def _frame_with_blobs(tmp_path, blobs, name="50x-1.png", H=300, W=400, seed=1234):
    """A rectangular frame like test_om_contrast's, with `blobs` planted as
    (row_slice, col_slice) regions raised well above the noise floor and
    peaked at their own centre -- so each gets its own watershed marker."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(yy - H / 2, xx - W / 2) / np.hypot(H / 2, W / 2)
    green = 190.0 - 8.0 * r**2 + rng.normal(0, 1.2, size=(H, W))

    for row_slice, col_slice in blobs:
        by, bx = np.mgrid[row_slice, col_slice]
        cy = (row_slice.start + row_slice.stop) / 2
        cx = (col_slice.start + col_slice.stop) / 2
        radius = min(row_slice.stop - row_slice.start, col_slice.stop - col_slice.start) / 2
        dist = np.hypot(by - cy, bx - cx) / radius
        green[row_slice, col_slice] += 20.0 * np.clip(1.0 - dist, 0, 1)

    rgb = np.stack([green * 0.95, green, green * 1.02], axis=-1)
    path = tmp_path / name
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)
    return path


def _frame(tmp_path, blobs, **kwargs):
    path = _frame_with_blobs(tmp_path, blobs, **kwargs)
    return analyse_frame(path, point=1, ref_label="2L", name="50x-1")


def _signed_frame(tmp_path, blobs, sign, amp, name="50x-1.png", H=300, W=400,
                   seed=1234, noise_std=1.2):
    """Grayscale (R=G=B), unlike `_frame_with_blobs`, so `luminance()` and the
    green channel `classify()` thresholds on agree exactly -- needed for the
    "below"/"reference" cases below, where the margin between a planted
    blob's peak and its class boundary is deliberately tight rather than
    `_frame_with_blobs`'s go-to +20, and a ~1% RGB-vs-green scale mismatch is
    enough to move a peak across that boundary."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(yy - H / 2, xx - W / 2) / np.hypot(H / 2, W / 2)
    green = 190.0 - 8.0 * r**2 + rng.normal(0, noise_std, size=(H, W))

    for row_slice, col_slice in blobs:
        by, bx = np.mgrid[row_slice, col_slice]
        cy = (row_slice.start + row_slice.stop) / 2
        cx = (col_slice.start + col_slice.stop) / 2
        radius = min(row_slice.stop - row_slice.start, col_slice.stop - col_slice.start) / 2
        dist = np.hypot(by - cy, bx - cx) / radius
        green[row_slice, col_slice] += sign * amp * np.clip(1.0 - dist, 0, 1)

    rgb = np.stack([green, green, green], axis=-1)
    path = tmp_path / name
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)
    return analyse_frame(path, point=1, ref_label="2L", name="50x-1")


class TestMeasureDomains:
    def test_no_bright_population_finds_nothing(self, tmp_path):
        frame = _frame(tmp_path, blobs=[])
        result = measure_domains(frame)

        assert result.n == 0
        assert len(result.diameters_px) == 0
        assert len(result.diameters_um) == 0
        assert not result.calibrated

    def test_one_blob_is_one_domain(self, tmp_path):
        frame = _frame(tmp_path, blobs=[(slice(100, 160), slice(150, 210))])
        result = measure_domains(frame)

        assert result.n == 1
        assert len(result.diameters_px) == 1
        assert result.diameters_px[0] > 0

    def test_two_separated_blobs_are_two_domains(self, tmp_path):
        frame = _frame(tmp_path, blobs=[
            (slice(60, 100), slice(60, 100)),
            (slice(200, 240), slice(280, 320)),
        ])
        result = measure_domains(frame)

        assert result.n == 2

    def test_two_touching_blobs_are_split(self, tmp_path):
        """Two same-size peaks sharing an edge: watershed should still find
        two markers and two domains, not one fused blob."""
        frame = _frame(tmp_path, blobs=[
            (slice(120, 170), slice(150, 200)),
            (slice(120, 170), slice(200, 250)),
        ])
        result = measure_domains(frame, marker_min_distance=8)

        assert result.n == 2

    def test_domain_touching_the_border_is_excluded(self, tmp_path):
        """A blob straddling the valid region's edge has an unknown true
        size and must not be counted -- same rule as a real frame's margin."""
        H = 300
        # RECT_MARGIN=0.05 excludes the outer 5%; a blob centred right on
        # that boundary straddles it.
        edge = int(H * 0.05)
        frame = _frame(tmp_path, blobs=[(slice(edge - 10, edge + 30), slice(150, 200))])
        result = measure_domains(frame)

        assert result.n == 0

    def test_calibration_converts_pixels_to_microns(self, tmp_path):
        frame = _frame(tmp_path, blobs=[(slice(100, 160), slice(150, 210))])

        uncalibrated = measure_domains(frame)
        calibrated = measure_domains(frame, um_per_px=0.1)

        assert not uncalibrated.calibrated
        assert calibrated.calibrated
        assert calibrated.diameters_um[0] == pytest.approx(
            uncalibrated.diameters_px[0] * 0.1
        )

    def test_min_domain_px_drops_small_fragments(self, tmp_path):
        frame = _frame(tmp_path, blobs=[(slice(100, 160), slice(150, 210))])
        result = measure_domains(frame, min_domain_px=10**6)

        assert result.n == 0

    def test_same_tuning_as_segmentation_finds_the_same_hi_population(self, tmp_path):
        """Passing the frame's own segmentation kwargs must not raise and
        must still find the planted domain -- this is the contract the QC
        Report page relies on (same `om_kwargs` for both calls)."""
        frame = _frame(tmp_path, blobs=[(slice(100, 160), slice(150, 210))])
        result = measure_domains(frame, nsigma=4.0, minpx=3, margin=None, ff_divisor=None)

        assert result.n == 1

    def test_an_invalid_target_raises(self, tmp_path):
        frame = _frame(tmp_path, blobs=[(slice(100, 160), slice(150, 210))])

        with pytest.raises(ValueError):
            measure_domains(frame, target="sideways")

    def test_above_is_the_default_and_matches_explicit_above(self, tmp_path):
        frame = _frame(tmp_path, blobs=[(slice(100, 160), slice(150, 210))])

        default = measure_domains(frame)
        explicit = measure_domains(frame, target="above")

        assert default.n == explicit.n == 1
        assert default.target == "above"

    def test_below_target_finds_a_planted_dark_domain(self, tmp_path):
        """A domain darker than its surroundings -- the mirror of the
        existing bright-blob case -- must still be found and split, via a
        local luminance *minimum* rather than maximum."""
        frame = _signed_frame(
            tmp_path, blobs=[(slice(100, 160), slice(150, 210))], sign=-1.0, amp=10.0,
        )
        result = measure_domains(frame, target="below")

        assert result.n == 1
        assert result.target == "below"
        assert result.diameters_px[0] > 0

    def test_reference_target_finds_a_planted_domain_within_the_reference_band(
        self, tmp_path
    ):
        """A domain whose brightness stays inside [th_lo, th_hi] -- neither
        below nor above the reference layer -- is only reachable through the
        "reference" target; the default "above" target would find nothing
        here since nothing crosses th_hi."""
        frame = _signed_frame(
            tmp_path, blobs=[(slice(130, 170), slice(330, 370))], sign=1.0, amp=1.3,
        )
        result = measure_domains(frame, target="reference")

        assert result.n == 1
        assert result.target == "reference"

    def test_targets_cover_every_declared_option(self):
        assert set(TARGETS) == {"below", "reference", "above"}


def _fake_frame_result(shape=(60, 60)):
    """A FrameResult whose own content is never read: `analyse_array` is
    monkeypatched in the tests below, so only the object's existence and
    `.original.shape` (for the empty-mask early return, unused here) matter."""
    from modules.optical.processing.contrast import FrameResult

    h, w = shape
    rgb = np.zeros((h, w, 3))
    return FrameResult(
        name="fake", point=1, frame_type="rectangular", ref_label="2L",
        labels=("Below 2L", "Bilayer", "Above 2L"), percentages=(0.0, 100.0, 0.0),
        contrast_below=0.0, contrast_above=0.0, components_below=0, components_above=0,
        original=rgb, overlay=rgb.copy(), valid=np.ones((h, w), dtype=bool),
        hist_centers=np.zeros(1), hist_counts=np.zeros(1),
        mode=100.0, sigma_l=1.0, sigma_r=1.0, sigma_noise=1.0,
        threshold_low=98.0, threshold_high=102.0, shoulder=False,
    )


def _patch_analyse_array(monkeypatch, hi, lum, valid=None):
    """Stand in for `analyse_array()` with hand-specified masks and
    luminance, sidestepping flatfield/smoothing/quantization entirely -- the
    real pipeline's own self-referential flat-field normalisation makes it
    impractical to hand-derive a real image landing a peak within a fraction
    of a sigma of a threshold, which is exactly the regime these tests need
    to control precisely."""
    h, w = hi.shape
    if valid is None:
        valid = np.ones((h, w), dtype=bool)
    res = dict(
        a=np.zeros((h, w, 3)), s=np.stack([lum, lum, lum], axis=-1), valid=valid,
        lo=np.zeros((h, w), dtype=bool), ref=~hi & valid, hi=hi,
        st=dict(th_hi=102.0, th_lo=98.0, mode=100.0, sigma_noise=1.0),
    )
    monkeypatch.setattr(domain_size_module, "analyse_array", lambda *a, **k: res)


class TestNucleationDensity:
    """`DomainResult.area_um2` -- the frame's own analyzed (`valid`) region's
    real area, the denominator for nucleation density (count / area)."""

    def test_area_um2_is_valid_pixels_times_calibration_squared(self, monkeypatch):
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2
        valid = np.ones((60, 60), dtype=bool)
        valid[:5, :] = False  # a non-trivial analyzed area, not the full frame
        _patch_analyse_array(monkeypatch, hi, lum, valid=valid)

        result = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", um_per_px=0.5,
        )
        assert result.area_um2 == pytest.approx(float(valid.sum()) * 0.5 ** 2)

    def test_area_um2_is_none_when_uncalibrated(self, monkeypatch):
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2
        _patch_analyse_array(monkeypatch, hi, lum)

        result = domain_size_module.measure_domains(_fake_frame_result(), target="above")
        assert result.area_um2 is None

    def test_area_um2_is_set_even_when_no_domains_are_found(self, monkeypatch):
        """A frame with zero domains still has a real analyzed area -- 0
        domains over a real area is a legitimate (zero) density, not a
        missing one."""
        hi = np.zeros((60, 60), dtype=bool)  # no "above" pixels anywhere
        lum = np.full((60, 60), 100.0)
        valid = np.ones((60, 60), dtype=bool)
        _patch_analyse_array(monkeypatch, hi, lum, valid=valid)

        result = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", um_per_px=0.5,
        )
        assert result.n == 0
        assert result.area_um2 == pytest.approx(float(valid.sum()) * 0.5 ** 2)


class TestMarkerFallbackAndSensitivity:
    """The defect this guards: a raw connected component that never produces
    a point clearing `marker_floor` -- flat, low-contrast, no distinct
    reflection -- used to get zero markers and vanish from the domain count
    entirely, rather than surviving as one (unsplit) domain the way the OM
    class table's own (floor-free) component count would. Uses a
    monkeypatched `analyse_array` so the marker floor's margin against a
    hand-specified luminance is exact, not subject to the real pipeline's
    flat-field normalisation.
    """

    def test_a_flat_component_below_the_floor_still_becomes_one_domain(
        self, monkeypatch
    ):
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2  # above th_hi=102.0, below the default floor 102.5
        _patch_analyse_array(monkeypatch, hi, lum)

        result = domain_size_module.measure_domains(_fake_frame_result(), target="above")

        assert result.n == 1

    def test_a_component_with_a_real_marker_gets_no_fallback(self, monkeypatch):
        """A component that already clears the floor somewhere must not also
        receive a fallback marker -- that would risk an extra, spurious
        split rather than leaving an already-working case alone."""
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2
        lum[20:25, 20:25] = 110.0  # clears the floor comfortably
        _patch_analyse_array(monkeypatch, hi, lum)

        result = domain_size_module.measure_domains(_fake_frame_result(), target="above")

        assert result.n == 1

    def test_lowering_marker_margin_sigma_resolves_a_second_real_peak(self, monkeypatch):
        """A weak secondary peak that clears th_hi but not the default
        0.5-sigma floor is invisible to real marker-finding (the fallback
        only adds one marker for the whole un-marked component, so a
        component that already has a strong marker stays one domain).
        Lowering the margin lets the weak peak register as its own marker,
        splitting the two into separate domains -- the tunable's whole
        purpose."""
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2
        lum[20:25, 20:25] = 110.0    # strong peak
        lum[30:35, 35:40] = 102.4    # weak peak: > th_hi + 0.1*sigma, < default floor
        _patch_analyse_array(monkeypatch, hi, lum)

        default_margin = domain_size_module.measure_domains(
            _fake_frame_result(), target="above"
        )
        # Between the baseline's own excess over th_hi (0.2 sigma) and the
        # weak peak's (0.4 sigma): low enough to admit the weak peak as a
        # real marker, still high enough that the flat baseline itself does
        # not also clear it (which would trigger widespread tie-breaking
        # across the whole component instead of a clean two-way split).
        lowered_margin = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", marker_margin_sigma=0.3
        )

        assert default_margin.n == 1
        assert lowered_margin.n == 2


class TestMarkerMinDistance:
    """`marker_min_distance` controls whether two close, independently-strong
    peaks in the same connected component stay two markers (two domains) or
    get merged into one -- the tunable that lets a material whose flakes show
    internal reflection highlights (like VBBF's triangular flakes) avoid
    having each flake watershed-split into fragments."""

    def _two_peaks(self, monkeypatch, gap):
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2  # below the default floor -- no stray markers
        lum[20:25, 20:25] = 110.0
        lum[20:25, 20 + gap:25 + gap] = 111.0
        _patch_analyse_array(monkeypatch, hi, lum)

    def test_default_keeps_two_peaks_ten_apart_separate(self, monkeypatch):
        self._two_peaks(monkeypatch, gap=10)
        result = domain_size_module.measure_domains(_fake_frame_result(), target="above")
        assert result.n == 2

    def test_raising_it_merges_the_same_two_peaks(self, monkeypatch):
        self._two_peaks(monkeypatch, gap=10)
        result = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", marker_min_distance=15
        )
        assert result.n == 1

    def test_none_matches_the_module_default(self, monkeypatch):
        self._two_peaks(monkeypatch, gap=10)
        explicit_default = domain_size_module.measure_domains(
            _fake_frame_result(), target="above",
            marker_min_distance=domain_size_module.DEFAULT_MARKER_MIN_DISTANCE,
        )
        omitted = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", marker_min_distance=None,
        )
        assert explicit_default.n == omitted.n == 2


class TestMaskClosingRadius:
    """`mask_closing_radius` bridges small gaps in the target mask before
    connected-component/marker/watershed -- the tunable that lets a
    material whose flakes' ragged real edges leave small fringe fragments
    disconnected from the flake's main body avoid counting each fragment as
    its own domain."""

    def _blob_and_gapped_fragment(self, monkeypatch, gap):
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:40, 10:40] = True                  # main blob, 30x30
        hi[10:18, 40 + gap:48 + gap] = True       # fringe fragment, 8x8
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2  # flat -- both components rely on the fallback marker
        _patch_analyse_array(monkeypatch, hi, lum)

    def test_default_keeps_a_two_pixel_gap_as_two_domains(self, monkeypatch):
        self._blob_and_gapped_fragment(monkeypatch, gap=2)
        result = domain_size_module.measure_domains(_fake_frame_result(), target="above")
        assert result.n == 2

    def test_closing_bridges_the_same_gap_into_one_domain(self, monkeypatch):
        self._blob_and_gapped_fragment(monkeypatch, gap=2)
        result = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", mask_closing_radius=3,
        )
        assert result.n == 1

    def test_zero_and_none_are_both_the_off_default(self, monkeypatch):
        self._blob_and_gapped_fragment(monkeypatch, gap=2)
        zero = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", mask_closing_radius=0,
        )
        omitted = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", mask_closing_radius=None,
        )
        assert zero.n == omitted.n == 2

    def test_omitting_it_entirely_reproduces_the_pre_existing_domain_count(
        self, monkeypatch
    ):
        """The guarantee the whole opt-in design rests on: a caller that
        never passes this argument at all sees exactly what it saw before
        this feature existed."""
        self._blob_and_gapped_fragment(monkeypatch, gap=2)
        result = domain_size_module.measure_domains(_fake_frame_result(), target="above")
        assert result.n == 2


class TestDetectSizePopulationsGate:
    """`detect_size_populations` -- the QC Report page's own checkbox,
    off by default -- must actually short-circuit the split call, not just
    discard its result: a caller that turns the checkbox off should not pay
    for (or be exposed to a bug in) logic they explicitly declined."""

    def test_false_prevents_split_size_populations_from_running_at_all(self, monkeypatch):
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2
        _patch_analyse_array(monkeypatch, hi, lum)

        def _boom(diameters):
            raise AssertionError("split_size_populations must not be called")

        monkeypatch.setattr(domain_size_module, "split_size_populations", _boom)

        result = domain_size_module.measure_domains(
            _fake_frame_result(), target="above", detect_size_populations=False,
        )
        assert result.population_is_large is None

    def test_true_is_the_default_and_runs_the_split(self, monkeypatch):
        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2
        _patch_analyse_array(monkeypatch, hi, lum)

        calls = []
        real_split = domain_size_module.split_size_populations

        def _spy(diameters):
            calls.append(diameters)
            return real_split(diameters)

        monkeypatch.setattr(domain_size_module, "split_size_populations", _spy)

        domain_size_module.measure_domains(_fake_frame_result(), target="above")
        assert len(calls) == 1

    def test_domain_size_stat_false_prevents_the_pooled_split_too(self, monkeypatch):
        def _boom(pooled):
            raise AssertionError("_pooled_population_stat must not be called")

        monkeypatch.setattr(domain_size_module, "_pooled_population_stat", _boom)

        diam = np.full(30, 3.0, dtype=float)
        result = DomainResult(
            point=1, n=30, diameters_px=diam, diameters_um=np.zeros(0, dtype=float),
            calibrated=False, label_mask=np.zeros((10, 10), dtype=int),
        )
        stat = domain_size_module.domain_size_stat([result], detect_size_populations=False)

        assert stat is not None
        assert stat.populations is None


class TestSplitSizePopulations:
    """`split_size_populations` on synthetic diameter arrays directly --
    no image/segmentation machinery needed, matching what the function
    itself operates on."""

    def test_a_clean_two_fold_gap_splits(self):
        rng = np.random.default_rng(0)
        small = rng.normal(10, 1, size=100)
        large = rng.normal(80, 5, size=10)
        diameters = np.concatenate([small, large])

        is_large = domain_size_module.split_size_populations(diameters)

        assert is_large is not None
        assert is_large.sum() == 10
        assert (~is_large).sum() == 100
        # Aligned to `diameters`' own order, not sorted.
        assert not is_large[:100].any()
        assert is_large[100:].all()

    def test_a_continuous_unimodal_spread_does_not_split(self):
        rng = np.random.default_rng(1)
        diameters = rng.uniform(10, 20, size=200)
        assert domain_size_module.split_size_populations(diameters) is None

    def test_too_few_domains_does_not_split_even_with_a_real_gap(self):
        diameters = np.concatenate([np.full(5, 10.0), np.full(5, 80.0)])
        assert diameters.size < domain_size_module.MIN_DOMAINS_FOR_SPLIT
        assert domain_size_module.split_size_populations(diameters) is None

    def test_a_gap_short_of_the_ratio_does_not_split(self):
        rng = np.random.default_rng(2)
        # ~1.5x apart -- clearly two clusters, but short of MIN_POPULATION_RATIO.
        small = rng.normal(10, 0.5, size=100)
        large = rng.normal(15, 0.5, size=100)
        diameters = np.concatenate([small, large])
        assert domain_size_module.split_size_populations(diameters) is None


class TestPooledDiametersAndStat:
    def _result(self, n, calibrated, unit_value=1.0, area_um2=None):
        diam = np.full(n, unit_value, dtype=float)
        empty = np.zeros(0, dtype=float)
        return DomainResult(
            point=1, n=n,
            diameters_px=diam if not calibrated else empty,
            diameters_um=diam if calibrated else empty,
            calibrated=calibrated,
            label_mask=np.zeros((10, 10), dtype=int),
            area_um2=area_um2,
        )

    def test_pooled_diameters_concatenates_across_frames(self):
        results = [self._result(2, calibrated=True), self._result(3, calibrated=True)]
        pooled = pooled_diameters(results, calibrated=True)
        assert len(pooled) == 5

    def test_pooled_diameters_empty_when_nothing_measured(self):
        assert len(pooled_diameters([], calibrated=True)) == 0

    def test_domain_size_stat_none_when_nothing_found(self):
        results = [self._result(0, calibrated=False)]
        assert domain_size_stat(results) is None

    def test_domain_size_stat_reports_calibrated_unit(self):
        results = [self._result(2, calibrated=True, unit_value=2.0)]
        stat = domain_size_stat(results)

        assert stat is not None
        assert stat.calibrated
        assert stat.unit == "µm"
        assert stat.n_domains == 2
        assert stat.diameter_mean == pytest.approx(2.0)

    def test_domain_size_stat_falls_back_to_pixels_when_uncalibrated(self):
        results = [self._result(2, calibrated=False, unit_value=5.0)]
        stat = domain_size_stat(results)

        assert stat is not None
        assert not stat.calibrated
        assert stat.unit == "px"

    def test_domain_size_stat_populations_none_for_an_ordinary_sample(self):
        """`_result`'s constant-diameter frames have no spread at all, let
        alone a ≥2x gap -- the ordinary case stays a single pooled row."""
        results = [self._result(30, calibrated=False)]
        stat = domain_size_stat(results)
        assert stat is not None
        assert stat.populations is None

    def test_domain_size_stat_pools_a_real_split_across_frames(self):
        """A ≥2x gap split across two frames' pooled diameters, not visible
        from either frame alone with too few domains of its own -- pooling
        first (rather than unioning each frame's own split) is what gives
        `split_size_populations` enough points to find it."""
        rng = np.random.default_rng(3)
        small = rng.normal(10, 1, size=60)
        large = rng.normal(80, 5, size=6)
        empty = np.zeros(0, dtype=float)

        def _frame_result(point, diam):
            return DomainResult(
                point=point, n=len(diam), diameters_px=diam, diameters_um=empty,
                calibrated=False, label_mask=np.zeros((10, 10), dtype=int),
            )

        results = [
            _frame_result(1, np.concatenate([small[:30], large[:3]])),
            _frame_result(2, np.concatenate([small[30:], large[3:]])),
        ]
        stat = domain_size_stat(results)

        assert stat is not None
        assert stat.populations is not None
        small_pop, large_pop = stat.populations
        assert small_pop.label == "small"
        assert large_pop.label == "large"
        assert small_pop.n == 60
        assert large_pop.n == 6

    def test_domain_size_stat_pools_area_and_computes_density(self):
        results = [
            self._result(3, calibrated=True, area_um2=100.0),
            self._result(5, calibrated=True, area_um2=50.0),
        ]
        stat = domain_size_stat(results)

        assert stat is not None
        assert stat.area_um2 == pytest.approx(150.0)
        assert stat.density_per_100um2 == pytest.approx(
            8 / 150.0 * domain_size_module.DENSITY_AREA_UM2
        )

    def test_domain_size_stat_density_is_none_when_uncalibrated(self):
        results = [self._result(3, calibrated=False, area_um2=None)]
        stat = domain_size_stat(results)

        assert stat is not None
        assert stat.area_um2 is None
        assert stat.density_per_100um2 is None

    def test_split_populations_share_the_pooled_area_with_their_own_counts(self):
        """Both populations divide by the *same* area -- only their counts
        differ -- so a population with 10x the count of the other reports
        10x the density, not some area-weighted variant."""
        rng = np.random.default_rng(4)
        small = rng.normal(10, 1, size=100)
        large = rng.normal(80, 5, size=10)
        empty = np.zeros(0, dtype=float)

        def _frame_result(point, diam, area_um2):
            return DomainResult(
                point=point, n=len(diam), diameters_px=empty, diameters_um=diam,
                calibrated=True, label_mask=np.zeros((10, 10), dtype=int),
                area_um2=area_um2,
            )

        results = [_frame_result(1, np.concatenate([small, large]), area_um2=110.0)]
        stat = domain_size_stat(results)

        assert stat is not None
        assert stat.area_um2 == pytest.approx(110.0)
        small_pop, large_pop = stat.populations
        scale = domain_size_module.DENSITY_AREA_UM2
        assert small_pop.density_per_100um2 == pytest.approx(100 / 110.0 * scale)
        assert large_pop.density_per_100um2 == pytest.approx(10 / 110.0 * scale)


class TestNativePixelCalibration:
    """The calibration is microns per *native camera* pixel, but measurement
    runs on `contrast.load()`'s copy shrunk to `MAXDIM`. The defect this
    guards: applying it to working pixels unconverted under-reported every
    diameter by 1/scale -- 1.6x on the VB1P01 camera's 2240-px frames."""

    def test_analyse_frame_records_the_resize_factor(self, tmp_path):
        small = _frame(tmp_path, [], name="small.png")
        assert small.scale == 1.0

        path = tmp_path / "native.png"
        Image.new("RGB", (2240, 1680), (180, 190, 195)).save(path)
        native = analyse_frame(path, point=1, ref_label="2L")
        assert native.scale == pytest.approx(1400 / 2240)  # 0.625
        assert native.original.shape[:2] == (1050, 1400)

    def test_calibration_is_divided_by_the_frame_scale(self, monkeypatch):
        from dataclasses import replace

        hi = np.zeros((60, 60), dtype=bool)
        hi[10:50, 10:50] = True
        lum = np.full((60, 60), 100.0)
        lum[hi] = 102.2
        _patch_analyse_array(monkeypatch, hi, lum)
        frame = replace(_fake_frame_result(), scale=0.5)

        result = domain_size_module.measure_domains(frame, target="above", um_per_px=0.1)

        # One working pixel spans two camera pixels: 0.2 um, not 0.1.
        assert result.diameters_um == pytest.approx(result.diameters_px * 0.2)
        assert result.area_um2 == pytest.approx(60 * 60 * 0.2 ** 2)

    def test_same_scene_at_two_camera_resolutions_measures_the_same(self, tmp_path):
        """A 1400-px frame (not resized) and the same field shot at 2800 px
        (resized by half) must report the same physical diameter when each
        carries its own native calibration."""
        blob = (slice(400, 560), slice(600, 760))
        low = _frame_with_blobs(tmp_path, [blob], name="low.png", H=1050, W=1400)
        high = tmp_path / "high.png"
        Image.open(low).resize((2800, 2100), Image.Resampling.LANCZOS).save(high)

        um_low = 0.1
        r_low = measure_domains(
            analyse_frame(low, point=1, ref_label="2L"), um_per_px=um_low)
        r_high = measure_domains(
            analyse_frame(high, point=1, ref_label="2L"), um_per_px=um_low / 2)

        assert r_low.n == r_high.n == 1
        assert r_high.diameters_um[0] == pytest.approx(r_low.diameters_um[0], rel=0.03)
        assert r_high.area_um2 == pytest.approx(r_low.area_um2, rel=0.01)


class TestEdgeDomains:
    """Domains touching the analysed border are not sized (their size is cut
    off) but are real flakes, counted `EDGE_WEIGHT` (one half) each toward
    nucleation density -- on rectangular and circular frames alike."""

    def test_an_edge_blob_is_counted_half_but_not_sized(self, tmp_path):
        edge = int(300 * 0.05)  # RECT_MARGIN boundary, as in the border test above
        frame = _frame(tmp_path, blobs=[
            (slice(edge - 10, edge + 30), slice(150, 200)),  # straddles the edge
            (slice(120, 170), slice(180, 230)),              # interior
        ])
        result = measure_domains(frame, um_per_px=0.1)

        assert result.n == 1 and result.diameters_um.size == 1
        assert result.n_edge == 1
        assert result.n_counted == pytest.approx(1.5)
        assert result.edge_mask.any()
        assert not (result.edge_mask & (result.label_mask > 0)).any()

    def test_an_edge_fragment_below_min_domain_px_is_not_counted(self, tmp_path):
        edge = int(300 * 0.05)
        frame = _frame(tmp_path, blobs=[(slice(edge - 10, edge + 30), slice(150, 200))])
        result = measure_domains(frame, min_domain_px=100_000)

        assert result.n_edge == 0
        assert not result.edge_mask.any()

    def test_density_counts_edge_domains_at_half_weight(self):
        diam = np.full(4, 2.0)
        results = [DomainResult(
            point=1, n=4, diameters_px=np.zeros(0), diameters_um=diam,
            calibrated=True, label_mask=np.zeros((10, 10), dtype=int),
            area_um2=200.0, n_edge=2,
        )]
        stat = domain_size_stat(results)

        assert stat.n_domains == 4 and stat.n_edge == 2
        assert stat.density_per_100um2 == pytest.approx(
            (4 + 0.5 * 2) / 200.0 * domain_size_module.DENSITY_AREA_UM2
        )
        assert stat.diameter_mean == pytest.approx(2.0)  # edge domains never sized
