"""Unit tests for modules.spectra.io.preset_store (JSON-backed material preset storage)."""

import pytest

from modules.spectra.io.preset_store import load_presets, save_presets
from modules.spectra.models.preset import (
    MaterialPreset,
    PeakTemplate,
    TechniquePreset,
    parse_exclusion_ranges,
)


def _make_block():
    return TechniquePreset(
        x_range_enabled=False,
        x_min=None,
        x_max=None,
        despike_threshold=6.0,
        baseline_algorithm="Polynomial",
        baseline_degree=5,
        baseline_lambda=None,
        baseline_p=None,
        peak_templates=[
            PeakTemplate(peak_label="Si", center=520.0, center_tolerance=3.0,
                         width_fwhm=8.0, shape=0.2, color="#2ca02c")
        ],
    )


def _make_preset(material_name="Silicon", mode="Raman"):
    blocks = {"raman": None, "pl": None}
    blocks["raman" if mode == "Raman" else "pl"] = _make_block()
    return MaterialPreset(material_name=material_name, enabled=True, **blocks)


class TestLoadSaveRoundTrip:
    def test_round_trip_preserves_data(self, tmp_path):
        path = tmp_path / "materials.json"
        presets = {"Silicon": _make_preset()}

        save_presets(presets, path=path)
        loaded = load_presets(path=path)

        assert set(loaded.keys()) == {"Silicon"}
        block = loaded["Silicon"].block_for("Raman")
        assert block.despike_threshold == 6.0
        assert block.baseline_degree == 5
        assert len(block.peak_templates) == 1
        assert block.peak_templates[0].peak_label == "Si"
        assert loaded["Silicon"].block_for("PL") is None

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert load_presets(path=tmp_path / "does_not_exist.json") == {}

    def test_multiple_presets_sorted_on_save(self, tmp_path):
        path = tmp_path / "materials.json"
        presets = {
            "WSe2": _make_preset("WSe2", "Raman"),
            "MoS2": _make_preset("MoS2", "Raman"),
        }

        save_presets(presets, path=path)
        loaded = load_presets(path=path)

        assert set(loaded.keys()) == {"WSe2", "MoS2"}


class TestPeakTemplateHasNoIntensity:
    def test_intensity_field_removed(self):
        # v2.11.0: the initial-guess field was dropped from PeakTemplate since the fitter
        # auto-estimates it from data and never consults the preset value.
        template = PeakTemplate(peak_label="Si", center=520.0, center_tolerance=3.0,
                                 width_fwhm=8.0, shape=0.2, color="#2ca02c")
        assert not hasattr(template, "amplitude")
        assert not hasattr(template, "intensity")

    def test_to_peak_definition_still_produces_valid_intensity(self):
        template = PeakTemplate(peak_label="Si", center=520.0, center_tolerance=3.0,
                                 width_fwhm=8.0, shape=0.2, color="#2ca02c")
        peak_def = template.to_peak_definition(
            mode="Raman", x_range=(100.0, 1000.0), y_max=1000.0, spectral_resolution=1.0
        )
        assert peak_def.intensity > 0


class TestParseExclusionRanges:
    def test_parses_multiple_ranges(self):
        assert parse_exclusion_ranges("1200-1400; 2600-2800") == [(1200.0, 1400.0), (2600.0, 2800.0)]

    def test_none_or_empty_returns_empty_list(self):
        assert parse_exclusion_ranges(None) == []
        assert parse_exclusion_ranges("") == []

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            parse_exclusion_ranges("not-a-range-format-1")


class TestPeakLinks:
    def _linked_block(self, **trion_link):
        link = dict(linked_to="Exciton", energy_offset_meV=-30.0, max_intensity_ratio=1.0)
        link.update(trion_link)
        return TechniquePreset(
            baseline_algorithm="None (Skip)",
            peak_templates=[
                PeakTemplate(peak_label="Exciton", center=755.0, center_tolerance=15.0,
                             width_fwhm=30.0, shape=0.3, color="#d62728"),
                PeakTemplate(peak_label="Trion", center=769.0, center_tolerance=15.0,
                             width_fwhm=40.0, shape=0.3, color="#9467bd", **link),
            ],
        )

    def test_an_unlinked_template_serializes_as_before(self):
        # preset_staleness hashes to_dict(); new keys on every template would
        # mark every existing fit stale.
        template = _make_block().peak_templates[0]
        assert set(template.to_dict()) == {"peak_label", "center", "center_tolerance",
                                           "width_fwhm", "shape", "color"}

    def test_a_linked_template_round_trips_through_the_store(self, tmp_path):
        path = tmp_path / "materials.json"
        preset = MaterialPreset(material_name="WSe2 X-T linked", enabled=True, pl=self._linked_block())

        save_presets({"WSe2 X-T linked": preset}, path=path)
        trion = load_presets(path=path)["WSe2 X-T linked"].pl.peak_templates[1]

        assert (trion.linked_to, trion.energy_offset_meV, trion.max_intensity_ratio) == ("Exciton", -30.0, 1.0)

    def test_the_link_reaches_the_peak_definition(self):
        peak_def = self._linked_block().peak_templates[1].to_peak_definition(
            mode="PL", x_range=(650.0, 880.0), y_max=1000.0, spectral_resolution=0.2)
        assert (peak_def.linked_to, peak_def.energy_offset_meV, peak_def.max_intensity_ratio) == (
            "Exciton", -30.0, 1.0)

    def test_a_valid_link_validates(self):
        assert self._linked_block().validate("PL") == []

    @pytest.mark.parametrize("link, message", [
        (dict(linked_to="Exiton"), "exactly one other peak"),
        (dict(linked_to=None), "need 'linked to'"),
        (dict(energy_offset_meV=None, max_intensity_ratio=None), "sets neither"),
        (dict(max_intensity_ratio=-1.0), "must be > 0"),
    ])
    def test_a_bad_link_is_reported(self, link, message):
        errors = self._linked_block(**link).validate("PL")
        assert any(message in e for e in errors), errors

    def test_an_energy_offset_in_a_raman_block_is_reported(self):
        preset = MaterialPreset(material_name="X", enabled=True, raman=self._linked_block())
        assert any("PL only" in e for e in preset.validate())

    def test_the_committed_linked_preset_is_valid_and_linked(self):
        preset = load_presets()["WSe2 X-T linked"]
        assert preset.validate() == []
        trion = next(t for t in preset.pl.peak_templates if t.peak_label == "Trion")
        assert (trion.linked_to, trion.energy_offset_meV, trion.max_intensity_ratio) == ("Exciton", -30.0, 1.0)
