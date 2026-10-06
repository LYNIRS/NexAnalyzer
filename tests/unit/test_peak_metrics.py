"""Unit tests for modules.spectra.processing.peak_metrics (per-peak numbers + aggregation)."""

import numpy as np
import pytest

from modules.spectra.models.peak import FitResult, FittedPeak
from modules.spectra.processing.peak_metrics import (
    NEAR_ZERO_FRACTION,
    aggregate_fit_results,
    aggregate_raw_peak_stats,
    compute_peak_intensity_ratio,
    filter_fits_by_quality,
    iqr_mask,
    peak_intensity,
    peak_intensity_and_stderr,
    raw_peak_stats,
)


def _peak(label, center, intensity, width_fwhm, area=None):
    """A fitted peak whose component curve peaks at `intensity`.

    `area` is the area under the peak, a different quantity. It defaults to a
    value clearly unlike the intensity so that anything reading the wrong one
    fails loudly rather than looking plausible.
    """
    curve = np.zeros(10)
    curve[5] = intensity
    return FittedPeak(
        label=label, center=center, center_stderr=0.1,
        area=intensity * 7.0 if area is None else area,
        area_stderr=1.0,
        width_fwhm=width_fwhm, width_stderr=0.1,
        shape=0.3, component_curve=curve,
    )


def _fit_result(peaks, r_squared=0.99):
    return FitResult(
        success=True, fitted_peaks=peaks,
        total_fit_curve=np.zeros(10), residuals=np.zeros(10),
        chi_squared=1.0, r_squared=r_squared, convergence_time=0.1,
    )


class TestAggregateFitResults:
    def test_mean_and_std_across_multiple_fits(self):
        fits = [
            _fit_result([_peak("Exciton", 766.0, 10000.0, 25.0)]),
            _fit_result([_peak("Exciton", 768.0, 12000.0, 27.0)]),
        ]
        stats = aggregate_fit_results(list(enumerate(fits)))

        assert len(stats) == 1
        stat = stats[0]
        assert stat.label == "Exciton"
        assert stat.n == 2
        assert stat.center_mean == 767.0
        assert stat.center_std == np.std([766.0, 768.0], ddof=1)
        assert stat.intensity_mean == 11000.0
        assert stat.intensity_std == np.std([10000.0, 12000.0], ddof=1)

    def test_single_fit_has_zero_std(self):
        fits = [_fit_result([_peak("Si", 520.0, 5000.0, 8.0)])]
        stats = aggregate_fit_results(list(enumerate(fits)))

        assert stats[0].n == 1
        assert stats[0].center_std == 0.0
        assert stats[0].intensity_std == 0.0
        assert stats[0].fwhm_std == 0.0

    def test_multiple_labels_preserve_first_seen_order(self):
        fits = [
            _fit_result([_peak("Exciton", 766.0, 10000.0, 25.0), _peak("Trion", 785.0, 3000.0, 45.0)]),
            _fit_result([_peak("Exciton", 767.0, 11000.0, 26.0), _peak("Trion", 786.0, 3100.0, 46.0)]),
        ]
        stats = aggregate_fit_results(list(enumerate(fits)))

        assert [s.label for s in stats] == ["Exciton", "Trion"]

    def test_asymmetric_peak_presence_gives_correct_n_per_label(self):
        fits = [
            _fit_result([_peak("Exciton", 766.0, 10000.0, 25.0), _peak("Trion", 785.0, 3000.0, 45.0)]),
            _fit_result([_peak("Exciton", 767.0, 11000.0, 26.0)]),  # no Trion this point
        ]
        stats = {s.label: s for s in aggregate_fit_results(list(enumerate(fits)))}

        assert stats["Exciton"].n == 2
        assert stats["Trion"].n == 1

    def test_empty_input_returns_empty_list(self):
        assert aggregate_fit_results([]) == []

    def test_reports_intensity_not_the_area(self):
        """The two differ by FWHM x 1.064. Reporting area under a heading the
        CSV uses for intensity is what made the .pptx disagree with every other
        surface in the app."""
        fits = [_fit_result([_peak("LA", 130.0, intensity=36.0, width_fwhm=22.0, area=1800.0)])]

        assert aggregate_fit_results(list(enumerate(fits)))[0].intensity_mean == 36.0


class TestNearZeroFloor:
    """`relative_intensity_floor`: the PL summary table's copy of the PL
    quality figure's near-zero rule."""

    def _mos2(self):
        return [
            (1, _fit_result([_peak("A0", 668.0, 7000.0, 20.0), _peak("A-", 674.0, 3800.0, 36.0)])),
            # A0 fitted to nothing: 0.01 against A-'s 4300 -- its 645 nm centre
            # and 45 nm width describe nothing.
            (2, _fit_result([_peak("A0", 645.0, 0.01, 45.0), _peak("A-", 683.0, 4300.0, 29.0)])),
            (3, _fit_result([_peak("A0", 665.0, 2100.0, 20.0), _peak("A-", 674.0, 720.0, 37.0)])),
        ]

    def test_the_constant_is_the_one_the_pl_figure_uses(self):
        from modules.spectra.viz.peak_quality import PL_CLEANING
        assert PL_CLEANING.relative_intensity_floor == NEAR_ZERO_FRACTION == 0.02

    def test_off_by_default_so_raman_and_other_callers_are_unchanged(self):
        stats = {s.label: s for s in aggregate_fit_results(self._mos2())}

        assert stats["A0"].n == 3

    def test_a_near_zero_peak_is_skipped_whole(self):
        stats = {s.label: s for s in aggregate_fit_results(
            self._mos2(), relative_intensity_floor=NEAR_ZERO_FRACTION)}

        assert stats["A0"].n == 2
        assert stats["A0"].center_mean == pytest.approx(666.5)
        assert stats["A0"].fwhm_mean == pytest.approx(20.0)
        assert stats["A-"].n == 3

    def test_the_floor_is_relative_to_the_same_fit(self):
        """A dim spectrum's peaks are judged against that spectrum."""
        fits = [
            (1, _fit_result([_peak("X", 750.0, 20000.0, 30.0), _peak("T", 765.0, 5000.0, 45.0)])),
            (2, _fit_result([_peak("X", 751.0, 300.0, 30.0), _peak("T", 766.0, 100.0, 45.0)])),
        ]
        stats = {s.label: s for s in aggregate_fit_results(
            fits, relative_intensity_floor=NEAR_ZERO_FRACTION)}

        assert stats["X"].n == 2 and stats["T"].n == 2


class TestComputePeakIntensityRatio:
    def _pair(self, la_intensity, e2g_intensity, **kwargs):
        return _fit_result([
            _peak("LA", 130.0, la_intensity, 20.0, **kwargs),
            _peak("E2g+A1g", 250.0, e2g_intensity, 4.0),
        ])

    def test_uses_intensities_not_areas(self):
        """LA is ~6x broader than E2g+A1g here, so the area ratio and the
        intensity ratio are nothing like each other."""
        fits = [_fit_result([
            _peak("LA", 130.0, intensity=10.0, width_fwhm=20.0, area=900.0),
            _peak("E2g+A1g", 250.0, intensity=100.0, width_fwhm=4.0, area=300.0),
        ])]
        median, _mad, _n = compute_peak_intensity_ratio(fits, "LA", "E2g+A1g")

        assert median == 0.1  # intensities, 10/100
        assert median != 900.0 / 300.0  # not the area ratio

    def test_ratio_formed_per_point_not_from_the_two_means(self):
        fits = [self._pair(100.0, 200.0), self._pair(300.0, 100.0)]  # ratios 0.5 and 3.0
        median, _mad, n = compute_peak_intensity_ratio(fits, "LA", "E2g+A1g")

        assert n == 2
        assert median == np.median([0.5, 3.0])
        # mean(LA)/mean(E2g) would be 200/150 = 1.333
        assert median != (200.0 / 150.0)

    def test_median_resists_one_badly_fitted_point(self):
        """A ratio of two fitted quantities is exactly where one bad point
        drags a mean somewhere no measurement supports."""
        fits = [self._pair(50.0, 100.0), self._pair(50.0, 100.0),
                self._pair(50.0, 100.0), self._pair(500.0, 100.0)]
        median, _mad, n = compute_peak_intensity_ratio(fits, "LA", "E2g+A1g")

        assert n == 4
        assert median == 0.5           # the three consistent points
        assert np.mean([0.5, 0.5, 0.5, 5.0]) == 1.625  # what the mean would have said

    def test_spread_is_the_median_absolute_deviation(self):
        fits = [self._pair(40.0, 100.0), self._pair(50.0, 100.0), self._pair(70.0, 100.0)]
        _median, mad, _n = compute_peak_intensity_ratio(fits, "LA", "E2g+A1g")

        assert mad == np.median(np.abs(np.array([0.4, 0.5, 0.7]) - 0.5))

    def test_single_point_has_zero_spread(self):
        median, mad, n = compute_peak_intensity_ratio([self._pair(100.0, 200.0)], "LA", "E2g+A1g")

        assert (median, mad, n) == (0.5, 0.0, 1)

    def test_points_missing_either_peak_are_skipped(self):
        fits = [self._pair(100.0, 200.0), _fit_result([_peak("LA", 130.0, 300.0, 20.0)])]
        median, _mad, n = compute_peak_intensity_ratio(fits, "LA", "E2g+A1g")

        assert n == 1
        assert median == 0.5

    def test_a_zero_denominator_is_skipped_rather_than_dividing_by_zero(self):
        fits = [self._pair(100.0, 200.0), self._pair(100.0, 0.0)]
        median, _mad, n = compute_peak_intensity_ratio(fits, "LA", "E2g+A1g")

        assert n == 1
        assert median == 0.5

    def test_no_point_has_both_labels_returns_none(self):
        fits = [_fit_result([_peak("Exciton", 766.0, 10000.0, 25.0)])]

        assert compute_peak_intensity_ratio(fits, "LA", "E2g+A1g") is None

    def test_empty_input_returns_none(self):
        assert compute_peak_intensity_ratio([], "LA", "E2g+A1g") is None


class TestQuantityNamingIsUnambiguous:
    """Two quantities, two names everywhere: the curve's maximum is
    "intensity", the area under it is "area". The .pptx once reported the area
    under a heading the CSV used for the maximum. Neither "amplitude" nor
    "height" names either quantity any more, so the two can't be silently
    swapped again."""

    def test_fitted_peak_names_neither_quantity_amplitude(self):
        peak = _peak("LA", 130.0, intensity=10.0, width_fwhm=20.0)

        assert not hasattr(peak, "amplitude")
        assert not hasattr(peak, "amplitude_stderr")
        assert peak.area == 70.0  # the integrated quantity, named for what it is

    def test_peak_stat_reports_intensity_under_that_name(self):
        fits = [_fit_result([_peak("LA", 130.0, intensity=36.0, width_fwhm=22.0)])]
        stat = aggregate_fit_results(list(enumerate(fits)))[0]

        assert not hasattr(stat, "amplitude_mean")
        assert not hasattr(stat, "height_mean")
        assert stat.intensity_mean == 36.0

    def test_peak_definition_names_its_ceiling_intensity_max(self):
        from modules.spectra.models.peak import PeakDefinition

        peak_def = PeakDefinition(center=130.0, intensity=1.0, width_fwhm=20.0)

        assert not hasattr(peak_def, "amplitude_max")
        assert not hasattr(peak_def, "height_max")
        assert hasattr(peak_def, "intensity_max")


class TestPeakIntensityAndStderr:
    def test_intensity_comes_from_the_component_curve_not_the_area(self):
        curve = np.array([0.0, 25.0, 100.0, 25.0, 0.0])
        peak = FittedPeak(
            label="LA", center=250.0, center_stderr=0.1,
            area=1000.0, area_stderr=50.0,
            width_fwhm=10.0, width_stderr=0.1, shape=0.3, component_curve=curve,
        )

        intensity, stderr = peak_intensity_and_stderr(peak)
        assert intensity == 100.0
        # 50 rescaled by the same intensity/area ratio = 100/1000
        assert stderr == 5.0
        assert peak_intensity(peak) == 100.0

    def test_falls_back_to_the_area_when_no_component_curve(self):
        peak = FittedPeak(
            label="LA", center=250.0, center_stderr=0.1,
            area=42.0, area_stderr=3.0,
            width_fwhm=10.0, width_stderr=0.1, shape=0.3, component_curve=None,
        )

        assert peak_intensity_and_stderr(peak) == (42.0, 3.0)

    def test_zero_area_gives_zero_stderr_rather_than_dividing_by_zero(self):
        peak = FittedPeak(
            label="Flat", center=250.0, center_stderr=0.1,
            area=0.0, area_stderr=5.0,
            width_fwhm=10.0, width_stderr=0.1, shape=0.3,
            component_curve=np.array([0.0, 1.0, 0.0]),
        )

        intensity, stderr = peak_intensity_and_stderr(peak)
        assert intensity == 1.0
        assert stderr == 0.0


class TestRawPeakStats:
    def test_measures_max_position_and_width_at_half_max(self):
        x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        y = np.array([0.0, 50.0, 100.0, 50.0, 0.0])

        stats = raw_peak_stats(x, y)
        assert stats.intensity == 100.0
        assert stats.center == 2.0
        assert stats.fwhm == 2.0  # x=1 to x=3 are >= half max

    def test_empty_spectrum_returns_none(self):
        assert raw_peak_stats(np.array([]), np.array([])) is None

    def test_non_positive_signal_has_no_measurable_width(self):
        x = np.array([0.0, 1.0, 2.0])
        y = np.array([0.0, 0.0, 0.0])

        stats = raw_peak_stats(x, y)
        assert stats.intensity == 0.0
        assert stats.fwhm is None


class _Spectrum:
    """Just the `processed_data.X/.Y` that aggregate_raw_peak_stats reads."""

    class _Data:
        def __init__(self, x, y):
            self.X, self.Y = x, y

    def __init__(self, x, y):
        self.processed_data = self._Data(x, y)


def _pl_spectrum(peak_intensity_value, center, half_width=1.0):
    """A triangular emission peak of the given height at the given center."""
    x = np.arange(center - 4.0, center + 4.01, 1.0)
    y = np.clip(peak_intensity_value * (1.0 - np.abs(x - center) / (2.0 * half_width)), 0.0, None)
    return _Spectrum(x, y)


class TestAggregateRawPeakStats:
    """The empirical PL row: measured off the processed spectrum, no fit."""

    def test_labelled_raw_so_it_matches_the_other_surfaces(self):
        stat = aggregate_raw_peak_stats([_pl_spectrum(1000.0, 766.0)])

        assert stat.label == "Raw"

    def test_averages_intensity_and_center_across_points(self):
        stats = aggregate_raw_peak_stats([
            _pl_spectrum(1000.0, 766.0),
            _pl_spectrum(2000.0, 768.0),
        ])

        assert stats.n == 2
        assert stats.intensity_mean == 1500.0
        assert stats.center_mean == 767.0
        assert stats.intensity_std == np.std([1000.0, 2000.0], ddof=1)
        assert stats.center_std == np.std([766.0, 768.0], ddof=1)

    def test_single_point_has_no_spread(self):
        stat = aggregate_raw_peak_stats([_pl_spectrum(1000.0, 766.0)])

        assert stat.n == 1
        assert stat.intensity_std == 0.0
        assert stat.center_std == 0.0
        assert stat.fwhm_std == 0.0

    def test_measures_a_width_at_half_maximum(self):
        stat = aggregate_raw_peak_stats([_pl_spectrum(1000.0, 766.0, half_width=1.0)])

        assert stat.fwhm_mean is not None
        assert stat.fwhm_mean > 0.0

    def test_unmeasurable_width_is_none_not_zero(self):
        """A flat spectrum has no half-maximum crossing. Reporting 0.0 would
        print a fake measurement; the renderer dashes a None instead."""
        flat = _Spectrum(np.arange(5.0), np.zeros(5))
        stat = aggregate_raw_peak_stats([flat])

        assert stat is not None          # the tallest point still exists
        assert stat.fwhm_mean is None
        assert stat.fwhm_std is None

    def test_width_is_none_when_only_some_points_could_be_measured(self):
        """Averaging the measurable subset while `n` claims the full count
        would report a width the sample never had."""
        stats = aggregate_raw_peak_stats([
            _pl_spectrum(1000.0, 766.0),
            _Spectrum(np.arange(5.0), np.zeros(5)),
        ])

        assert stats.n == 2
        assert stats.fwhm_mean is None

    def test_reads_processed_data_so_it_is_comparable_to_fitted_intensities(self):
        """The layer the fit sees, not the raw file: a baseline-corrected
        spectrum, so no baseline offset inflates the reported intensity."""
        spectrum = _pl_spectrum(1000.0, 766.0)
        spectrum.processed_data.Y = spectrum.processed_data.Y + 0.0  # already corrected

        assert aggregate_raw_peak_stats([spectrum]).intensity_mean == 1000.0

    def test_no_spectra_returns_none(self):
        assert aggregate_raw_peak_stats([]) is None

    def test_empty_spectra_return_none(self):
        assert aggregate_raw_peak_stats([_Spectrum(np.array([]), np.array([]))]) is None


class TestIqrMask:
    """The outlier cut inherited from the WSe2 analysis scripts."""

    def test_keeps_everything_when_there_is_no_spread(self):
        """The property that makes this safe on one-spectrum-per-point samples:
        no spread means no basis for calling anything an outlier, so nothing is
        dropped and existing reports keep their numbers."""
        assert iqr_mask(np.array([5.0])).all()
        assert iqr_mask(np.array([5.0, 5.0, 5.0])).all()

    def test_drops_a_value_outside_the_fences(self):
        values = np.array([10.0, 10.1, 9.9, 10.2, 9.8, 500.0])

        mask = iqr_mask(values)

        assert not mask[-1]
        assert mask[:-1].all()

    def test_empty_input_gives_empty_mask(self):
        assert iqr_mask(np.array([])).shape == (0,)


class TestFitResultRSquaredRange:
    """R-squared is 1 - SS_res/SS_tot, so only the upper bound is real.

    Bounding it below at 0 rejected the ordinary outcome of fitting a preset
    against the wrong material and reported it as a validation failure whose
    suggested remedy -- check the initial guesses -- pointed away from the
    actual problem. It was inconsistent too: the same mistake with the Silicon
    preset scores +0.00008 and always passed.
    """

    def test_accepts_a_negative_r_squared(self):
        fit = _fit_result([_peak("E2g", 383.0, 0.001, 10.0)], r_squared=-0.11)

        assert fit.r_squared == -0.11

    def test_accepts_a_perfect_fit(self):
        assert _fit_result([_peak("Si", 520.0, 100.0, 5.0)], r_squared=1.0).r_squared == 1.0

    def test_rejects_above_one(self):
        with pytest.raises(ValueError, match="r_squared must be <= 1"):
            _fit_result([_peak("Si", 520.0, 100.0, 5.0)], r_squared=1.0001)


class TestFilterFitsByQuality:
    def test_drops_fits_at_or_below_the_r_squared_gate(self):
        good = _fit_result([_peak("Si", 520.0, 100.0, 5.0)], r_squared=0.95)
        bad = _fit_result([_peak("Si", 520.0, 100.0, 5.0)], r_squared=0.4)

        kept = filter_fits_by_quality([(1, good), (2, bad)])

        assert [point for point, _ in kept] == [1]

    def test_boundary_value_is_excluded(self):
        edge = _fit_result([_peak("Si", 520.0, 100.0, 5.0)], r_squared=0.5)

        assert filter_fits_by_quality([(1, edge)]) == []

    def test_preserves_order_and_point_pairing(self):
        fits = [(p, _fit_result([_peak("Si", 520.0, 100.0, 5.0)])) for p in (3, 1, 2)]

        assert [point for point, _ in filter_fits_by_quality(fits)] == [3, 1, 2]

    def test_drops_a_negative_r_squared_fit(self):
        """The case the gate exists for, which it could not previously see.

        A preset aimed at the wrong material converges onto empty spectrum:
        the areas collapse to ~zero and R-squared goes slightly negative
        (MoS2's 383/408 peaks on real WSe2 data score about -0.11). FitResult
        used to reject that value outright, so the fit never reached this gate
        -- it surfaced as "Peak fitting failed: r_squared must be in [0, 1]"
        instead.
        """
        collapsed = _fit_result([_peak("E2g", 383.0, 0.001, 10.0)], r_squared=-0.11)

        assert filter_fits_by_quality([(1, collapsed)]) == []


class TestPerPointOutlierRemoval:
    """Aggregation cleans within a grid point, never across the wafer."""

    def _at(self, point, center):
        return (point, _fit_result([_peak("Si", center, 100.0, 5.0)]))

    def test_an_outlier_within_one_point_is_excluded_from_the_mean(self):
        fits = [self._at(1, c) for c in (520.0, 520.1, 519.9, 520.2, 519.8, 600.0)]

        stat = aggregate_fit_results(fits)[0]

        assert stat.center_mean == pytest.approx(520.0, abs=0.05)

    def test_n_counts_measurements_taken_not_survivors(self):
        """`n` says how many spectra contributed the peak. Per-metric cuts move
        the mean, not the count, so one number doesn't become three."""
        fits = [self._at(1, c) for c in (520.0, 520.1, 519.9, 520.2, 519.8, 600.0)]

        assert aggregate_fit_results(fits)[0].n == 6

    def test_position_to_position_variation_survives_cleaning(self):
        """One spectrum per point across nine points: the spread between points
        is the signal the report exists to show. Cleaning the pooled set would
        delete it, so cleaning has to happen inside a point."""
        fits = [self._at(p, 520.0 + p) for p in range(1, 10)]

        stat = aggregate_fit_results(fits)[0]

        assert stat.n == 9
        assert stat.center_std == pytest.approx(np.std(
            [520.0 + p for p in range(1, 10)], ddof=1
        ))

    def test_many_spectra_at_one_point_aggregate_to_that_point(self):
        fits = [self._at(1, 520.0), self._at(1, 521.0), self._at(2, 530.0)]

        stat = aggregate_fit_results(fits)[0]

        assert stat.n == 3
        assert stat.center_mean == pytest.approx((520.0 + 521.0 + 530.0) / 3)
