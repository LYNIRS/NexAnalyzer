"""Unit tests for core.io.report_settings (persisted per-installation QC
Report sidecar: default material, the domain-size measurement opt-in, and
the size-population split opt-in)."""

from core.io.report_settings import (
    load_default_material,
    load_measure_domain_size,
    load_split_size_populations,
    save_default_material,
    save_measure_domain_size,
    save_split_size_populations,
)


class TestDefaultMaterialRoundTrip:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_default_material("WSe2", path=path)

        assert load_default_material(path=path) == "WSe2"

    def test_overwrite_updates_value(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_default_material("WSe2", path=path)
        save_default_material("MoS2", path=path)

        assert load_default_material(path=path) == "MoS2"

    def test_missing_file_returns_none(self, tmp_path):
        assert load_default_material(path=tmp_path / "does_not_exist.json") is None

    def test_malformed_json_returns_none(self, tmp_path):
        path = tmp_path / "report_settings.json"
        path.write_text("not valid json {{{")

        assert load_default_material(path=path) is None

    def test_missing_key_returns_none(self, tmp_path):
        path = tmp_path / "report_settings.json"
        path.write_text("{}")

        assert load_default_material(path=path) is None


class TestMeasureDomainSizeRoundTrip:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_measure_domain_size(True, path=path)

        assert load_measure_domain_size(path=path) is True

    def test_overwrite_updates_value(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_measure_domain_size(True, path=path)
        save_measure_domain_size(False, path=path)

        assert load_measure_domain_size(path=path) is False

    def test_missing_file_defaults_to_false(self, tmp_path):
        """Domain-size analysis roughly doubles optical analysis time, so an
        unset installation must not silently opt in."""
        assert load_measure_domain_size(path=tmp_path / "does_not_exist.json") is False

    def test_malformed_json_defaults_to_false(self, tmp_path):
        path = tmp_path / "report_settings.json"
        path.write_text("not valid json {{{")

        assert load_measure_domain_size(path=path) is False

    def test_missing_key_defaults_to_false(self, tmp_path):
        path = tmp_path / "report_settings.json"
        path.write_text("{}")

        assert load_measure_domain_size(path=path) is False


class TestSplitSizePopulationsRoundTrip:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_split_size_populations(True, path=path)

        assert load_split_size_populations(path=path) is True

    def test_overwrite_updates_value(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_split_size_populations(True, path=path)
        save_split_size_populations(False, path=path)

        assert load_split_size_populations(path=path) is False

    def test_missing_file_defaults_to_false(self, tmp_path):
        """The split has fired on frames with no real second population at
        all, so an unset installation must not silently opt in."""
        assert load_split_size_populations(path=tmp_path / "does_not_exist.json") is False

    def test_malformed_json_defaults_to_false(self, tmp_path):
        path = tmp_path / "report_settings.json"
        path.write_text("not valid json {{{")

        assert load_split_size_populations(path=path) is False

    def test_missing_key_defaults_to_false(self, tmp_path):
        path = tmp_path / "report_settings.json"
        path.write_text("{}")

        assert load_split_size_populations(path=path) is False


class TestBothSettingsShareOneFileWithoutClobbering:
    """These settings live in the same JSON sidecar. A save that overwrote
    the whole document instead of merging into it would silently erase
    whichever setting was saved first -- the defect this guards."""

    def test_saving_material_after_domain_size_keeps_both(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_measure_domain_size(True, path=path)
        save_default_material("WSe2", path=path)

        assert load_default_material(path=path) == "WSe2"
        assert load_measure_domain_size(path=path) is True

    def test_saving_domain_size_after_material_keeps_both(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_default_material("WSe2", path=path)
        save_measure_domain_size(True, path=path)

        assert load_default_material(path=path) == "WSe2"
        assert load_measure_domain_size(path=path) is True

    def test_saving_all_three_in_sequence_keeps_all_three(self, tmp_path):
        path = tmp_path / "report_settings.json"
        save_default_material("WSe2", path=path)
        save_measure_domain_size(True, path=path)
        save_split_size_populations(True, path=path)

        assert load_default_material(path=path) == "WSe2"
        assert load_measure_domain_size(path=path) is True
        assert load_split_size_populations(path=path) is True
