"""Unit tests for modules.spectra.processing.fitting (Voigt fitting, peak auto-find)."""

import numpy as np
import pytest

from modules.spectra.processing.fitting import (
    auto_find_peaks,
    detect_overlapping_peaks,
    fit_voigt_peaks,
    voigt_fwhm,
    voigt_fwhm_stderr,
)
from modules.spectra.models.peak import MAX_PEAKS, PeakDefinition
from modules.spectra.models.preset import PeakTemplate


class TestFitVoigtPeaks:
    def test_single_peak_converges_with_good_fit(self, synthetic_spectrum):
        x, y = synthetic_spectrum(n_points=300, peaks=((1000.0, 1000.0, 50.0),))
        peak_defs = [PeakDefinition(center=990.0, intensity=1.0, width_fwhm=60.0)]

        result = fit_voigt_peaks(x, y, peak_defs, mode="Raman")

        assert result.success
        assert result.r_squared > 0.9
        assert len(result.fitted_peaks) == 1
        assert result.fitted_peaks[0].center == pytest.approx(1000.0, abs=10.0)

    def test_two_peaks_converges_with_good_fit(self, synthetic_spectrum):
        x, y = synthetic_spectrum(
            n_points=300,
            peaks=((800.0, 500.0, 40.0), (1300.0, 800.0, 60.0)),
        )
        peak_defs = [
            PeakDefinition(center=790.0, intensity=1.0, width_fwhm=45.0),
            PeakDefinition(center=1310.0, intensity=1.0, width_fwhm=65.0),
        ]

        result = fit_voigt_peaks(x, y, peak_defs, mode="Raman")

        assert result.success
        assert result.r_squared > 0.9
        assert len(result.fitted_peaks) == 2

    def test_empty_peak_table_raises(self, synthetic_spectrum):
        x, y = synthetic_spectrum()
        with pytest.raises(ValueError):
            fit_voigt_peaks(x, y, [], mode="Raman")

    def test_too_many_peaks_raises(self, synthetic_spectrum):
        x, y = synthetic_spectrum()
        peak_defs = [PeakDefinition(center=float(c), intensity=1.0, width_fwhm=20.0) for c in range(MAX_PEAKS + 1)]
        with pytest.raises(ValueError):
            fit_voigt_peaks(x, y, peak_defs, mode="Raman")

    def test_mismatched_lengths_raise(self):
        x = np.linspace(0, 10, 100)
        y = np.ones(50)
        peak_defs = [PeakDefinition(center=5.0, intensity=1.0, width_fwhm=1.0)]
        with pytest.raises(ValueError):
            fit_voigt_peaks(x, y, peak_defs, mode="Raman")


class TestAutoFindPeaks:
    def test_finds_single_real_peak(self, synthetic_spectrum):
        x, y = synthetic_spectrum(n_points=300, peaks=((1000.0, 1000.0, 50.0),))
        found = auto_find_peaks(x, y, mode="Raman", min_peaks=2, max_peaks=5)

        assert len(found) == 1
        assert found[0].center == pytest.approx(1000.0, abs=15.0)

    def test_fewer_real_peaks_than_min_peaks_does_not_crash(self, synthetic_spectrum):
        """Regression test for the auto_find_peaks clarity fix (fitting.py).

        min_peaks can't manufacture peaks that don't exist; the function
        should simply return however many real peaks it actually found.
        """
        x, y = synthetic_spectrum(n_points=300, peaks=((1000.0, 1000.0, 50.0),))
        found = auto_find_peaks(x, y, mode="Raman", min_peaks=3, max_peaks=5)

        assert len(found) == 1

    def test_caps_at_max_peaks(self, synthetic_spectrum):
        peaks = tuple((200.0 + i * 300.0, 500.0 + i * 50.0, 30.0) for i in range(6))
        x, y = synthetic_spectrum(n_points=600, x_range=(0.0, 2000.0), peaks=peaks, noise_std=0.0)

        found = auto_find_peaks(x, y, mode="Raman", min_peaks=1, max_peaks=3, prominence_threshold=0.01)

        assert len(found) <= 3

    def test_short_array_returns_empty(self):
        x = np.linspace(0, 10, 5)
        y = np.ones(5)
        assert auto_find_peaks(x, y) == []

    def test_no_peaks_returns_empty(self):
        x = np.linspace(0, 10, 200)
        y = np.ones(200)  # perfectly flat, no prominence anywhere
        assert auto_find_peaks(x, y, prominence_threshold=0.5) == []

    def test_result_sorted_by_center(self, synthetic_spectrum):
        x, y = synthetic_spectrum(
            n_points=400, x_range=(0.0, 2000.0),
            peaks=((1500.0, 800.0, 30.0), (500.0, 600.0, 30.0)),
        )
        found = auto_find_peaks(x, y, mode="Raman", min_peaks=1, max_peaks=5, prominence_threshold=0.1)

        centers = [p.center for p in found]
        assert centers == sorted(centers)


class TestDetectOverlappingPeaks:
    def test_no_warnings_for_well_separated_peaks(self):
        peaks = [
            PeakDefinition(center=100.0, intensity=1.0, width_fwhm=10.0, label="A"),
            PeakDefinition(center=500.0, intensity=1.0, width_fwhm=10.0, label="B"),
        ]
        assert detect_overlapping_peaks(peaks, merge_threshold=2.0) == []

    def test_warns_for_close_peaks(self):
        peaks = [
            PeakDefinition(center=100.0, intensity=1.0, width_fwhm=20.0, label="A"),
            PeakDefinition(center=105.0, intensity=1.0, width_fwhm=20.0, label="B"),
        ]
        warnings = detect_overlapping_peaks(peaks, merge_threshold=2.0)
        assert len(warnings) == 1
        assert "A" in warnings[0] and "B" in warnings[0]


class TestVoigtFwhm:
    """A Voigt's width comes from both of its components. Reporting
    2.355*sigma described only the Gaussian half and understated every width
    in the app by 60-110%."""

    def _measured_fwhm(self, sigma, gamma):
        """Half-maximum width read off an actual Voigt curve, on a grid fine
        enough that the reading is the reference and not the approximation."""
        from lmfit.models import VoigtModel

        x = np.linspace(-400.0, 400.0, 400001)
        curve = VoigtModel().eval(x=x, center=0.0, amplitude=1.0, sigma=sigma, gamma=gamma)
        above = np.where(curve >= curve.max() / 2.0)[0]
        return float(x[above[-1]] - x[above[0]])

    def test_pure_gaussian_reduces_to_the_gaussian_width(self):
        assert voigt_fwhm(10.0, 0.0) == pytest.approx(2.3548 * 10.0, rel=1e-3)

    def test_pure_lorentzian_reduces_to_twice_gamma(self):
        assert voigt_fwhm(0.0, 7.0) == pytest.approx(14.0, rel=1e-3)

    @pytest.mark.parametrize("sigma,gamma", [(2.0, 3.0), (9.0, 11.0), (1.9, 3.0), (0.5, 5.0), (5.0, 0.5)])
    def test_matches_the_measured_width_of_a_real_voigt_curve(self, sigma, gamma):
        assert voigt_fwhm(sigma, gamma) == pytest.approx(self._measured_fwhm(sigma, gamma), rel=0.005)

    def test_always_at_least_the_gaussian_only_value(self):
        """The old formula was a lower bound, never an estimate."""
        for sigma, gamma in ((2.0, 3.0), (9.0, 11.0), (0.7, 1.5)):
            assert voigt_fwhm(sigma, gamma) > 2.355 * sigma

    def test_a_fitted_peak_reports_the_width_of_the_curve_it_drew(self):
        """End to end: fit a synthetic peak, then check the reported FWHM
        against its own component curve."""
        x = np.linspace(900.0, 1100.0, 2000)
        from lmfit.models import VoigtModel
        y = VoigtModel().eval(x=x, center=1000.0, amplitude=5000.0, sigma=4.0, gamma=3.0)

        result = fit_voigt_peaks(x, y, [PeakDefinition(center=1000.0, intensity=1.0, width_fwhm=15.0)], mode="Raman")
        peak = result.fitted_peaks[0]

        curve = peak.component_curve
        above = np.where(curve >= curve.max() / 2.0)[0]
        measured = float(x[above[-1]] - x[above[0]])

        assert peak.width_fwhm == pytest.approx(measured, rel=0.02)


class TestVoigtFwhmStderr:
    def test_zero_uncertainty_in_gives_zero_out(self):
        assert voigt_fwhm_stderr(2.0, 3.0, 0.0, 0.0) == 0.0

    def test_anticorrelation_shrinks_the_uncertainty(self):
        """sigma and gamma trade off against each other, so each is poorly
        determined alone while their sum is not. Ignoring that is what quoted
        a width of 2.07 +/- 133.29."""
        quadrature = voigt_fwhm_stderr(2.0, 3.0, 1.0, 1.5, correlation=None)
        with_correl = voigt_fwhm_stderr(2.0, 3.0, 1.0, 1.5, correlation=-0.92)

        assert with_correl < quadrature

    def test_degenerate_widths_do_not_divide_by_zero(self):
        assert voigt_fwhm_stderr(0.0, 0.0, 1.0, 1.0) == 0.0

    def test_grows_with_the_input_uncertainty(self):
        small = voigt_fwhm_stderr(2.0, 3.0, 0.1, 0.1)
        large = voigt_fwhm_stderr(2.0, 3.0, 1.0, 1.0)

        assert large > small > 0


class TestIntensityMaxIsAnIntensity:
    """The ceiling on a peak is expressed in intensity -- the curve's
    maximum -- and only becomes an area on the way into lmfit. It was called
    amplitude_max, which read as lmfit's amplitude, an area, and was neither."""

    def test_auto_bound_is_five_times_the_data_maximum(self):
        peak = PeakDefinition(center=250.0, intensity=1.0, width_fwhm=4.0)
        peak.calculate_auto_bounds("Raman", x_range=(0.0, 400.0), y_max=120.0, spectral_resolution=0.5)

        assert peak.intensity_max == 5.0 * 120.0

    def test_the_old_names_are_gone(self):
        peak = PeakDefinition(center=250.0, intensity=1.0, width_fwhm=4.0)

        assert not hasattr(peak, "amplitude_max")
        assert not hasattr(peak, "height_max")

    def test_a_peak_may_reach_that_intensity_but_not_exceed_it(self):
        """End to end: a peak taller than the ceiling gets clipped to it."""
        x = np.linspace(200.0, 300.0, 800)
        from lmfit.models import VoigtModel
        y = VoigtModel().eval(x=x, center=250.0, amplitude=1000.0, sigma=2.0, gamma=1.0)

        result = fit_voigt_peaks(x, y, [PeakDefinition(center=250.0, intensity=1.0, width_fwhm=5.0)], mode="Raman")
        intensity = float(np.max(result.fitted_peaks[0].component_curve))

        assert intensity <= 5.0 * float(np.max(y)) * 1.001


class TestPresetCenterToleranceSurvivesTheFit:
    """The preset owns the peak's position, so the tolerance it states is the
    tolerance the fitter uses.

    Until v4.0.0 it wasn't: `fit_voigt_peaks` re-ran `calculate_auto_bounds`
    unconditionally, which rewrote center_min/center_max with the mode default
    of +/-5 cm-1. Five of WSe2's seven peaks were fitted against a tolerance
    they never asked for, and LA -- preset tolerance 10 -- railed against the
    +/-5 wall it was given instead.
    """

    def test_a_wider_preset_tolerance_is_not_narrowed_to_the_mode_default(self):
        template = PeakTemplate(peak_label="LA", center=135.0, center_tolerance=10.0,
                                width_fwhm=15.0, shape=0.5, color="#1f77b4")
        peak = template.to_peak_definition(
            mode="Raman", x_range=(0.0, 400.0), y_max=100.0, spectral_resolution=0.5
        )

        assert (peak.center_min, peak.center_max) == (125.0, 145.0)

    def test_a_narrower_preset_tolerance_is_not_widened_either(self):
        template = PeakTemplate(peak_label="center", center=50.0, center_tolerance=3.0,
                                width_fwhm=5.0, shape=0.5, color="#1f77b4")
        peak = template.to_peak_definition(
            mode="Raman", x_range=(0.0, 400.0), y_max=100.0, spectral_resolution=0.5
        )

        assert (peak.center_min, peak.center_max) == (47.0, 53.0)

    def test_the_bounds_survive_fit_voigt_peaks_re_running_them(self):
        """The regression itself: bounds must still be the preset's *after* a fit."""
        x = np.linspace(0.0, 400.0, 1200)
        from lmfit.models import VoigtModel
        y = VoigtModel().eval(x=x, center=135.0, amplitude=1000.0, sigma=4.0, gamma=2.0)

        template = PeakTemplate(peak_label="LA", center=135.0, center_tolerance=10.0,
                                width_fwhm=15.0, shape=0.5, color="#1f77b4")
        peak = template.to_peak_definition(
            mode="Raman", x_range=(x.min(), x.max()), y_max=y.max(),
            spectral_resolution=float(np.median(np.diff(x)))
        )

        fit_voigt_peaks(x, y, [peak], mode="Raman")

        assert (peak.center_min, peak.center_max) == (125.0, 145.0)

    def test_a_peak_with_no_tolerance_of_its_own_still_takes_the_mode_default(self):
        raman = PeakDefinition(center=250.0, intensity=1.0, width_fwhm=4.0)
        raman.calculate_auto_bounds("Raman", (0.0, 400.0), 100.0, 0.5)
        assert (raman.center_min, raman.center_max) == (245.0, 255.0)

        pl = PeakDefinition(center=770.0, intensity=1.0, width_fwhm=30.0)
        pl.calculate_auto_bounds("PL", (700.0, 900.0), 100.0, 0.5)
        assert (pl.center_min, pl.center_max) == (740.0, 800.0)

    def test_the_tolerance_is_clamped_to_the_spectrum_range(self):
        """A tolerance wider than the data can't invent bounds outside it."""
        template = PeakTemplate(peak_label="C", center=17.0, center_tolerance=25.0,
                                width_fwhm=5.0, shape=0.5, color="#1f77b4")
        peak = template.to_peak_definition(
            mode="Raman", x_range=(-6.0, 400.0), y_max=100.0, spectral_resolution=0.5
        )

        assert peak.center_min == -6.0
        assert peak.center_max == 42.0


class TestCenterBoundsCannotInvert:
    """Clamping the tolerance window to the data range must not produce
    center_min > center_max.

    WSe2's "center" template sits at 0 cm-1 while the same preset crops the
    spectrum to x_min=6, so the window [-3, 3] clamps to min=6.67, max=3.00.
    lmfit accepts an inverted bound silently and returns a center outside both
    of them, so the Sample Report quoted a position that was not a position.
    """

    RANGE = (6.674, 399.867)

    def _peak_at(self, center, tolerance):
        peak = PeakDefinition(center=center, intensity=1.0, width_fwhm=5.0,
                              center_tolerance=tolerance)
        peak.calculate_auto_bounds("Raman", self.RANGE, 100.0, 0.2)
        return peak

    def test_a_peak_below_the_data_pins_to_the_lower_edge(self):
        peak = self._peak_at(0.0, 3.0)

        assert peak.center_min == peak.center_max == self.RANGE[0]

    def test_a_peak_above_the_data_pins_to_the_upper_edge(self):
        peak = self._peak_at(500.0, 3.0)

        assert peak.center_min == peak.center_max == self.RANGE[1]

    def test_a_window_that_merely_overlaps_the_edge_is_clamped_not_pinned(self):
        """Partial overlap is ordinary clamping and must keep its width."""
        peak = self._peak_at(8.0, 5.0)

        assert (peak.center_min, peak.center_max) == (self.RANGE[0], 13.0)

    def test_the_fit_starts_inside_the_bounds_and_stays_there(self):
        """End to end: an off-range peak must not come back outside the data."""
        x = np.linspace(*RANGE_FOR_FIT, 1500)
        from lmfit.models import VoigtModel
        y = VoigtModel().eval(x=x, center=250.0, amplitude=1000.0, sigma=2.0, gamma=1.0)

        off_range = PeakDefinition(center=0.0, intensity=1.0, width_fwhm=5.0,
                                   center_tolerance=3.0)
        real = PeakDefinition(center=250.0, intensity=1.0, width_fwhm=5.0,
                              center_tolerance=7.0)

        result = fit_voigt_peaks(x, y, [off_range, real], mode="Raman")

        assert result.success
        fitted = result.fitted_peaks[0].center
        assert x.min() <= fitted <= x.max()


RANGE_FOR_FIT = (6.674, 399.867)


# ---------------------------------------------------------------------------
# Linked peaks: a trion tied to its exciton by energy and/or intensity.
# ---------------------------------------------------------------------------

def _pl_voigt(x, center, intensity, sigma, gamma):
    """A Voigt with the given maximum (intensity), as the fitter reports it."""
    from lmfit.lineshapes import voigt
    peak = voigt(np.array([center]), 1.0, center, sigma, gamma)[0]
    return intensity * voigt(x, 1.0, center, sigma, gamma) / peak


def _exciton_trion(x_ratio=0.4, exciton=760.0, offset_meV=-30.0):
    from modules.spectra.processing.fitting import linked_center
    x = np.linspace(680.0, 860.0, 900)
    trion = linked_center(exciton, offset_meV)
    y = _pl_voigt(x, exciton, 1000.0, 7.0, 4.0) + _pl_voigt(x, trion, 1000.0 * x_ratio, 9.0, 5.0)
    return x, y


def _linked_defs(offset_meV=-30.0, ratio=1.0, trion_start=780.0):
    return [
        PeakDefinition(center=755.0, intensity=1.0, width_fwhm=30.0, label="Exciton", shape=0.3,
                       center_tolerance=15.0),
        PeakDefinition(center=trion_start, intensity=1.0, width_fwhm=40.0, label="Trion", shape=0.3,
                       center_tolerance=15.0, linked_to="Exciton",
                       energy_offset_meV=offset_meV, max_intensity_ratio=ratio),
    ]


class TestLinkedPeaks:
    def test_linked_center_is_an_energy_shift(self):
        from modules.spectra.processing.fitting import HC_EV_NM, linked_center
        trion = linked_center(760.0, -30.0)
        assert (HC_EV_NM / 760.0 - HC_EV_NM / trion) * 1000 == pytest.approx(30.0, abs=1e-9)
        assert trion > 760.0  # lower energy, longer wavelength
        assert linked_center(trion, 30.0) == pytest.approx(760.0, abs=1e-9)

    def test_an_energy_link_recovers_a_30_meV_pair_exactly(self):
        from modules.spectra.processing.fitting import HC_EV_NM
        from modules.spectra.processing.peak_metrics import peak_intensity
        x, y = _exciton_trion(x_ratio=0.4)

        result = fit_voigt_peaks(x, y, _linked_defs(), mode="PL")

        assert result.success and result.r_squared > 0.999
        exciton, trion = result.fitted_peaks
        assert exciton.center == pytest.approx(760.0, abs=0.3)
        assert (HC_EV_NM / exciton.center - HC_EV_NM / trion.center) * 1000 == pytest.approx(30.0, abs=1e-6)
        assert peak_intensity(trion) / peak_intensity(exciton) == pytest.approx(0.4, abs=0.05)
        assert trion.constraint == "E = Exciton − 30 meV; I ≤ 1 × Exciton"
        assert exciton.constraint == ""

    def test_the_intensity_cap_holds_when_the_data_wants_more(self):
        from modules.spectra.processing.peak_metrics import peak_intensity
        x, y = _exciton_trion(x_ratio=2.5)  # trion 2.5x brighter than the exciton

        result = fit_voigt_peaks(x, y, _linked_defs(ratio=1.0), mode="PL")

        assert result.success
        exciton, trion = result.fitted_peaks
        # The cap is on the analytic height; peak_intensity() is the component
        # curve's maximum on the data grid, which can fall short of the height
        # by a little more for one peak than the other (here 0.2 nm steps).
        assert peak_intensity(trion) <= peak_intensity(exciton) * (1 + 1e-3)
        assert trion.constraint.endswith("(at cap)")

    def test_an_intensity_only_link_leaves_the_centre_free_in_its_window(self):
        x, y = _exciton_trion(x_ratio=0.4)
        defs = _linked_defs(offset_meV=None, ratio=1.0)

        result = fit_voigt_peaks(x, y, defs, mode="PL")

        trion = result.fitted_peaks[1]
        assert defs[1].center_min <= trion.center <= defs[1].center_max
        assert trion.constraint.startswith("I ≤ 1 × Exciton")

    def test_a_linked_fit_runs_from_several_starts_and_a_free_one_once(self, monkeypatch):
        import lmfit.model
        from modules.spectra.processing.fitting import LINKED_START_FRACTIONS
        calls = []
        original = lmfit.model.Model.fit

        def counting(self, *a, **kw):
            calls.append(1)
            return original(self, *a, **kw)

        monkeypatch.setattr(lmfit.model.Model, "fit", counting)
        x, y = _exciton_trion()

        fit_voigt_peaks(x, y, _linked_defs(), mode="PL")
        assert len(calls) == 1 + len(LINKED_START_FRACTIONS)

        calls.clear()
        free = [PeakDefinition(center=755.0, intensity=1.0, width_fwhm=30.0, label="Exciton", center_tolerance=15.0),
                PeakDefinition(center=785.0, intensity=1.0, width_fwhm=40.0, label="Trion", center_tolerance=15.0)]
        fit_voigt_peaks(x, y, free, mode="PL")
        assert len(calls) == 1

    def test_on_a_single_line_the_exciton_keeps_it(self):
        # One line and no trion: the intensity cap forbids the trion taking
        # the line, so the exciton must sit on it and the trion stay weaker.
        from modules.spectra.processing.peak_metrics import peak_intensity
        x = np.linspace(680.0, 860.0, 900)
        y = _pl_voigt(x, 766.0, 900.0, 9.0, 8.0)

        result = fit_voigt_peaks(x, y, _linked_defs(), mode="PL")

        exciton, trion = result.fitted_peaks
        assert exciton.center == pytest.approx(766.0, abs=1.0)
        assert peak_intensity(trion) <= peak_intensity(exciton)

    @pytest.mark.parametrize("mutate, message", [
        (lambda d: setattr(d[1], "linked_to", "Nothing"), "exactly one other peak"),
        (lambda d: setattr(d[1], "linked_to", "Trion"), "linked to itself"),
        (lambda d: (setattr(d[0], "linked_to", "Trion"), setattr(d[0], "max_intensity_ratio", 1.0)),
         "itself linked"),
        (lambda d: setattr(d[1], "max_intensity_ratio", 0.0), "must be > 0"),
    ])
    def test_a_bad_link_is_refused(self, mutate, message):
        x, y = _exciton_trion()
        defs = _linked_defs()
        mutate(defs)
        with pytest.raises(ValueError, match=message):
            fit_voigt_peaks(x, y, defs, mode="PL")

    def test_an_energy_offset_is_pl_only(self):
        x, y = _exciton_trion()
        with pytest.raises(ValueError, match="PL only"):
            fit_voigt_peaks(x, y, _linked_defs(), mode="Raman")
