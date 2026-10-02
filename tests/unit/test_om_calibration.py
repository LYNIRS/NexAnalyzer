"""Unit tests for core.io.om_calibration (persisted magnification -> um/px)."""

from core.io.om_calibration import get_um_per_px, load_om_calibration, save_om_calibration


class TestCalibrationRoundTrip:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "om_calibration.json"
        save_om_calibration({"50x": 0.0893, "100x": 0.0447}, path=path)

        assert load_om_calibration(path=path) == {"50x": 0.0893, "100x": 0.0447}

    def test_overwrite_replaces_the_whole_mapping(self, tmp_path):
        path = tmp_path / "om_calibration.json"
        save_om_calibration({"50x": 0.0893}, path=path)
        save_om_calibration({"100x": 0.0447}, path=path)

        assert load_om_calibration(path=path) == {"100x": 0.0447}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert load_om_calibration(path=tmp_path / "does_not_exist.json") == {}

    def test_malformed_json_returns_empty_dict(self, tmp_path):
        path = tmp_path / "om_calibration.json"
        path.write_text("not valid json {{{")

        assert load_om_calibration(path=path) == {}

    def test_missing_calibration_key_returns_empty_dict(self, tmp_path):
        path = tmp_path / "om_calibration.json"
        path.write_text('{"schema_version": 1}')

        assert load_om_calibration(path=path) == {}

    def test_non_numeric_entries_are_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "om_calibration.json"
        path.write_text(
            '{"schema_version": 1, "calibration": {"50x": 0.0893, "100x": "n/a"}}'
        )

        assert load_om_calibration(path=path) == {"50x": 0.0893}

    def test_an_unmagnified_key_that_is_missing_reports_as_uncalibrated(self, tmp_path):
        path = tmp_path / "om_calibration.json"
        save_om_calibration({"50x": 0.0893}, path=path)

        assert load_om_calibration(path=path).get("100x") is None


class TestGetUmPerPx:
    """Magnification is a raw filename prefix (sample_scanner's "any other
    prefix") -- nothing normalises its case before a lookup, so "50X" from a
    folder and "50x" typed into the calibration table must still match. This
    is the defect: a folder named "50X_1.png" silently fell back to
    uncalibrated pixels against a "50x" calibration entry."""

    def test_exact_case_matches(self):
        assert get_um_per_px({"50x": 0.0893}, "50x") == 0.0893

    def test_mismatched_case_still_matches(self):
        assert get_um_per_px({"50x": 0.0893}, "50X") == 0.0893
        assert get_um_per_px({"50X": 0.0893}, "50x") == 0.0893

    def test_no_magnification_returns_none(self):
        assert get_um_per_px({"50x": 0.0893}, None) is None
        assert get_um_per_px({"50x": 0.0893}, "") is None

    def test_unlisted_magnification_returns_none(self):
        assert get_um_per_px({"50x": 0.0893}, "200x") is None

    def test_empty_calibration_returns_none(self):
        assert get_um_per_px({}, "50x") is None
