"""
Unit tests for modules.optical.processing.contrast.

The segmentation numbers below are golden values, and they exist for one
specific reason. This module is vendored from `tmd_contrast.py` with
scikit-image's `gaussian` swapped for scipy's `gaussian_filter`. Those two
agree only when the boundary mode is pinned: skimage defaults to "nearest",
scipy to "reflect". The flat-field blur runs at sigma = max(H,W)/8, so that
policy governs a wide border of the background estimate, and dropping the
explicit mode shifted real coverage figures by more than two points of a
percent on real 50x frames.

That failure is silent — the overlay still looks plausible — so it needs a
numeric guard rather than a comment. Against the upstream script's own output
on nine TSM260803 frames the pinned version reproduces every field (coverage,
contrast, both sigmas, component counts) to within the reference CSV's
two-decimal rounding.
"""

import numpy as np
import pytest

from modules.optical.processing.contrast import (
    FrameResult,
    analyse_frame,
    class_labels,
    class_summary,
    despeckle,
    is_circular,
    layer_word,
)


def _synthetic_frame(tmp_path, name="50x-1.png"):
    """A rectangular frame with vignetting, noise, and planted domains.

    Deterministic: the gradient is what makes the flat-field boundary mode
    matter, and the planted patches are what the classifier should find.
    """
    from PIL import Image

    H, W = 300, 400
    rng = np.random.default_rng(1234)
    yy, xx = np.mgrid[0:H, 0:W]
    # Radial vignette, brightest at centre — the illumination profile the
    # flat-field step exists to remove.
    # Falloff kept gentle on purpose: a steep vignette is not fully removed by
    # a sigma = max(H,W)/8 background estimate, and the residual darkening at
    # the edges then classifies as a dark domain and swamps the planted one.
    # Real 50x frames fall off far more gradually than that.
    r = np.hypot(yy - H / 2, xx - W / 2) / np.hypot(H / 2, W / 2)
    base = 190.0 - 8.0 * r**2
    green = base + rng.normal(0, 1.2, size=(H, W))

    # Planted domains, well inside the 5 % valid margin.
    green[60:90, 80:140] += 9.0     # brighter: an "Above 2L" domain
    green[180:200, 250:300] -= 9.0  # darker: a "Below 2L" domain

    rgb = np.stack([green * 0.95, green, green * 1.02], axis=-1)
    path = tmp_path / name
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(path)
    return path


#: Captured from this module against the synthetic frame above, with the blur
#: mode pinned to "nearest", after the vendored port was confirmed to reproduce
#: tmd_contrast.py's own output on nine real TSM260803 frames to within the
#: reference CSV's two-decimal rounding.
GOLDEN = {
    "mode": 186.98909812620286,
    "sigma_noise": 0.5726692650216417,
    "below": 1.0401234567901234,
    "reference": 97.07098765432099,
    "above": 1.8888888888888888,
}


class TestClassLabels:
    def test_bilayer_reference_labels_are_ordinal(self):
        """Darker-than-bilayer could be monolayer or bare substrate and one
        frame cannot tell them apart, so the label must not claim either."""
        assert class_labels("2L") == ("Below 2L", "Bilayer", "Above 2L")

    def test_monolayer_reference_relabels_all_three(self):
        assert class_labels("1L") == ("Below 1L", "Monolayer", "Above 1L")

    def test_unknown_layer_word_passes_through(self):
        assert layer_word("7L") == "7L"

    def test_low_coverage_relabels_the_reference_as_substrate(self):
        """A sparse-nucleation sample's majority pixel population is bare
        substrate, not the named film -- classify() always calls the
        majority "the reference" regardless of what ref_label says, so
        low_coverage tells the labels to say substrate instead."""
        assert class_labels("1L", low_coverage=True) == (
            "Below Substrate", "Substrate", "Monolayer"
        )
        assert class_labels("2L", low_coverage=True) == (
            "Below Substrate", "Substrate", "Bilayer"
        )

    def test_low_coverage_defaults_off(self):
        assert class_labels("2L") == class_labels("2L", low_coverage=False)


class TestFrameGeometry:
    def test_a_full_frame_is_not_mistaken_for_a_circular_aperture(self, tmp_path):
        from PIL import Image
        arr = np.full((200, 260, 3), 180, dtype=np.uint8)
        path = tmp_path / "rect.png"
        Image.fromarray(arr).save(path)

        assert is_circular(np.asarray(Image.open(path)).astype(float)) is False

    def test_dark_corners_are_read_as_a_circular_aperture(self, tmp_path):
        from PIL import Image
        H, W = 200, 200
        yy, xx = np.mgrid[0:H, 0:W]
        disc = np.hypot(yy - H / 2, xx - W / 2) < H / 2 * 0.9
        arr = np.where(disc[..., None], 200, 5).astype(np.uint8)
        arr = np.repeat(arr, 3, axis=2) if arr.shape[-1] == 1 else arr
        path = tmp_path / "circ.png"
        Image.fromarray(np.broadcast_to(arr[..., :1], (H, W, 3)).copy()).save(path)

        assert is_circular(np.asarray(Image.open(path)).astype(float)) is True


class TestDespeckle:
    def test_components_below_the_pixel_floor_are_dropped(self):
        m = np.zeros((20, 20), bool)
        m[2, 2] = True              # 1 px, noise
        m[10:14, 10:14] = True      # 16 px, a real domain

        cleaned = despeckle(m, minpx=3)

        assert not cleaned[2, 2]
        assert cleaned[10:14, 10:14].all()

    def test_an_empty_mask_survives(self):
        m = np.zeros((10, 10), bool)
        assert not despeckle(m, minpx=3).any()


class TestAnalyseFrame:
    def test_planted_domains_are_found_on_both_sides(self, tmp_path):
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L")

        below, reference, above = frame.percentages
        assert above > 0.5, "the planted bright domain was not detected"
        assert below > 0.2, "the planted dark domain was not detected"
        assert reference > 90.0, "most of the frame is the reference film"
        assert below + reference + above == pytest.approx(100.0, abs=0.01)

    def test_contrast_signs_follow_the_substrate_physics(self, tmp_path):
        """Thicker is brighter on SiO2/Si: the brighter class must report a
        positive green contrast against the reference and the darker one a
        negative contrast. A sign flip here means the classes are swapped."""
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L")

        assert frame.contrast_above > 0
        assert frame.contrast_below < 0

    def test_thresholds_straddle_the_mode_by_nsigma(self, tmp_path):
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L",
                              nsigma=4.0)

        assert frame.threshold_high == pytest.approx(frame.mode + 4.0 * frame.sigma_noise)
        assert frame.threshold_low == pytest.approx(frame.mode - 4.0 * frame.sigma_noise)

    def test_noise_sigma_is_the_narrower_side(self, tmp_path):
        """The side carrying a domain population grows a shoulder that inflates
        its own half-width; the narrower side is the uncontaminated noise."""
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L")

        assert frame.sigma_noise == min(frame.sigma_l, frame.sigma_r)

    def test_raising_nsigma_shrinks_the_detected_domains(self, tmp_path):
        path = _synthetic_frame(tmp_path)

        loose = analyse_frame(path, point=1, ref_label="2L", nsigma=3.0)
        strict = analyse_frame(path, point=1, ref_label="2L", nsigma=6.0)

        assert strict.percentages[2] < loose.percentages[2]
        assert strict.reference_pct > loose.reference_pct

    def test_result_carries_what_the_figure_needs(self, tmp_path):
        frame = analyse_frame(_synthetic_frame(tmp_path), point=3, ref_label="2L",
                              name="50x-3")

        assert isinstance(frame, FrameResult)
        assert frame.point == 3
        assert frame.name == "50x-3"
        assert frame.frame_type == "rectangular"
        assert frame.labels == ("Below 2L", "Bilayer", "Above 2L")
        assert frame.original.shape == frame.overlay.shape
        assert frame.valid.shape == frame.original.shape[:2]
        assert frame.hist_centers.shape == frame.hist_counts.shape

    def test_low_coverage_relabels_the_frame_without_changing_the_segmentation(
        self, tmp_path
    ):
        """Only `labels` differs -- low_coverage never reaches `analyse`, so
        the actual pixel classification (and everything derived from it)
        must be identical either way."""
        path = _synthetic_frame(tmp_path)
        plain = analyse_frame(path, point=3, ref_label="1L", name="50x-3")
        low_cov = analyse_frame(
            path, point=3, ref_label="1L", name="50x-3", low_coverage=True
        )

        assert low_cov.labels == ("Below Substrate", "Substrate", "Monolayer")
        assert low_cov.percentages == plain.percentages
        assert low_cov.contrast_below == plain.contrast_below
        assert low_cov.contrast_above == plain.contrast_above
        assert np.array_equal(low_cov.overlay, plain.overlay)

    def test_the_overlay_tints_only_classified_pixels(self, tmp_path):
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L")

        untouched = np.isclose(frame.overlay, frame.original).all(axis=-1)
        assert untouched.mean() > 0.9, "the overlay recoloured the reference film"
        assert not untouched.all(), "the overlay marked nothing at all"


class TestGaussianBoundaryModeRegression:
    """Guards the one substitution made against the vendored source."""

    def test_flatfield_uses_the_nearest_edge_policy(self, tmp_path):
        """scipy's default "reflect" is not equivalent to skimage's "nearest".
        At sigma = max(H,W)/8 the difference reaches deep into the frame, so
        this asserts the two policies genuinely disagree here — if they ever
        stop disagreeing, this guard has quietly stopped guarding anything."""
        from scipy import ndimage as ndi

        from modules.optical.processing.contrast import FF_SIGMA_RECT, load

        a = load(_synthetic_frame(tmp_path))
        sigma = max(a.shape[:2]) / FF_SIGMA_RECT
        plane = a[..., 1]

        nearest = ndi.gaussian_filter(plane, sigma=sigma, mode="nearest")
        reflect = ndi.gaussian_filter(plane, sigma=sigma, mode="reflect")

        assert not np.allclose(nearest, reflect, atol=0.05)

    def test_segmentation_is_stable_for_a_fixed_input(self, tmp_path):
        """Golden values. Any change to the blur, the histogram or the
        thresholds moves these; that is the point."""
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L")

        assert frame.mode == pytest.approx(GOLDEN["mode"], abs=1e-006)
        assert frame.sigma_noise == pytest.approx(GOLDEN["sigma_noise"], abs=1e-006)
        assert frame.percentages[0] == pytest.approx(GOLDEN["below"], abs=1e-006)
        assert frame.percentages[1] == pytest.approx(GOLDEN["reference"], abs=1e-006)
        assert frame.percentages[2] == pytest.approx(GOLDEN["above"], abs=1e-006)



class TestClassSummary:
    def _frame(self, pcts):
        return FrameResult(
            name="f", point=1, frame_type="rectangular", ref_label="2L",
            labels=class_labels("2L"), percentages=pcts,
            contrast_below=-4.0, contrast_above=5.0,
            components_below=1, components_above=1,
            original=np.zeros((2, 2, 3)), overlay=np.zeros((2, 2, 3)),
            valid=np.ones((2, 2), bool),
            hist_centers=np.zeros(3), hist_counts=np.zeros(3),
            mode=190.0, sigma_l=1.0, sigma_r=1.0, sigma_noise=1.0,
            threshold_low=186.0, threshold_high=194.0, shoulder=False,
        )

    def test_a_single_frame_reports_zero_spread_not_nan(self):
        summary = class_summary([self._frame((1.0, 97.0, 2.0))])

        assert summary[1] == (97.0, 0.0)

    def test_mean_and_std_across_frames(self):
        frames = [self._frame((1.0, 97.0, 2.0)), self._frame((3.0, 93.0, 4.0))]

        below, reference, above = class_summary(frames)

        assert below[0] == pytest.approx(2.0)
        assert reference[0] == pytest.approx(95.0)
        assert above[0] == pytest.approx(3.0)
        assert reference[1] == pytest.approx(np.std([97.0, 93.0], ddof=1))

    def test_no_frames_is_not_an_error(self):
        assert class_summary([]) == ((0.0, 0.0),) * 3


class TestTuningOverridesAreOptional:
    """v4.0.0 made nsigma/minpx/margin/ff_divisor settable from a preset. The
    contract that keeps the golden numbers above meaningful is that *not*
    passing them is byte-identical to the vendored algorithm."""

    def test_passing_nothing_matches_passing_the_module_defaults(self, tmp_path):
        from modules.optical.processing.contrast import (
            DEFAULT_MINPX,
            DEFAULT_NSIGMA,
            FF_SIGMA_RECT,
        )

        path = _synthetic_frame(tmp_path)
        bare = analyse_frame(path, point=1, ref_label="2L")
        spelled_out = analyse_frame(
            path, point=1, ref_label="2L",
            nsigma=DEFAULT_NSIGMA, minpx=DEFAULT_MINPX,
            margin=None, ff_divisor=FF_SIGMA_RECT,
        )

        assert bare.percentages == spelled_out.percentages
        assert bare.mode == spelled_out.mode
        assert bare.sigma_noise == spelled_out.sigma_noise

    def test_an_empty_kwargs_dict_is_the_untouched_algorithm(self, tmp_path):
        """What a material with no optical block produces."""
        from modules.spectra.models.preset import OpticalParams

        path = _synthetic_frame(tmp_path)
        bare = analyse_frame(path, point=1, ref_label="2L")
        via_preset = analyse_frame(path, point=1, ref_label="2L",
                                   **OpticalParams().as_kwargs())

        assert bare.percentages == via_preset.percentages

    def test_ff_divisor_is_a_divisor_so_larger_means_a_smaller_blur(self, tmp_path):
        """docs/OM_Contrast_Algo.md says "/16 if vignetting still visible", so
        the number is the denominator, not the sigma. Reversing the sense here
        would make the control do the opposite of what its help text says."""
        from modules.optical.processing.contrast import flatfield, load, make_mask

        a = load(_synthetic_frame(tmp_path))
        aperture, _valid, ftype = make_mask(a)

        local = flatfield(a, aperture, ftype, ff_divisor=32.0)
        broad = flatfield(a, aperture, ftype, ff_divisor=2.0)

        # A more local background estimate flattens more of the gradient, so
        # the corrected frame's own spread is the smaller of the two.
        assert float(local[..., 1].std()) < float(broad[..., 1].std())

    def test_ff_divisor_reaches_analyse_frame(self, tmp_path):
        path = _synthetic_frame(tmp_path)
        default = analyse_frame(path, point=1, ref_label="2L")
        altered = analyse_frame(path, point=1, ref_label="2L", ff_divisor=24.0)

        assert altered.sigma_noise != default.sigma_noise

    def test_the_margin_override_applies_to_a_rectangular_frame(self, tmp_path):
        """Correction #2's point: a preset cannot know the frame type, so an
        override must reach a rectangular frame too, not only a circular one."""
        path = _synthetic_frame(tmp_path)
        default = analyse_frame(path, point=1, ref_label="2L")
        trimmed = analyse_frame(path, point=1, ref_label="2L", margin=0.25)

        assert int(trimmed.valid.sum()) < int(default.valid.sum())


class TestAbsoluteThreshold:
    """The fixed-contrast alternative to nsigma (v4.4.0)."""

    def test_unset_leaves_the_nsigma_path_untouched(self, tmp_path):
        path = _synthetic_frame(tmp_path)
        assert (analyse_frame(path, point=1, ref_label="2L").percentages
                == analyse_frame(path, point=1, ref_label="2L",
                                 abs_threshold=None).percentages)

    def test_a_scalar_places_both_cuts_symmetrically(self, tmp_path):
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L",
                              abs_threshold=5.0)
        assert frame.threshold_high == pytest.approx(frame.mode * 1.05)
        assert frame.threshold_low == pytest.approx(frame.mode * 0.95)

    def test_a_pair_places_them_independently(self, tmp_path):
        """2L->3L is one layer step up; 2L->substrate can be several down, so
        the two boundaries are not mirror images."""
        frame = analyse_frame(_synthetic_frame(tmp_path), point=1, ref_label="2L",
                              abs_threshold=(6.0, 4.25))
        assert frame.threshold_high == pytest.approx(frame.mode * 1.0425)
        assert frame.threshold_low == pytest.approx(frame.mode * 0.94)

    def test_the_cut_ignores_the_noise_width(self, tmp_path):
        """The point of the whole thing: a film whose domains widen both halves
        of its own histogram must not thereby raise its own threshold."""
        path = _synthetic_frame(tmp_path)
        loose = analyse_frame(path, point=1, ref_label="2L", nsigma=2.0,
                              abs_threshold=4.25)
        strict = analyse_frame(path, point=1, ref_label="2L", nsigma=9.0,
                               abs_threshold=4.25)
        assert loose.threshold_high == pytest.approx(strict.threshold_high)
        assert loose.percentages == strict.percentages
