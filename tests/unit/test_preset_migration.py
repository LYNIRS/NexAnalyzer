"""
Tests for the v1 -> v2 materials.json migration.

The single most valuable assertion here is `test_migrating_v1_reproduces_the
_committed_file`: the v1 array below is the store exactly as it stood before
v4.0.0, and the committed data/materials.json is what the migration actually
produced. If those two ever stop agreeing, the shipped file was edited by hand
in a way the migration cannot account for.
"""

import json

import pytest

from core.paths import DATA_DIR
from modules.spectra.io.preset_store import (
    SCHEMA_VERSION,
    load_store,
    migrate_legacy,
    save_presets,
)
from modules.spectra.models.preset import MaterialPreset, OpticalParams

#: data/materials.json as committed at v3.10.0, verbatim. Generated from the
#: file itself rather than retyped, so it cannot drift from what was migrated.
V1_STORE = [   {   'material_name': 'MoS2',
        'mode': 'Raman',
        'enabled': True,
        'x_range_enabled': False,
        'x_min': None,
        'x_max': None,
        'despike_threshold': 8.0,
        'baseline_algorithm': 'ALS',
        'baseline_degree': None,
        'baseline_lambda': 50000.0,
        'baseline_p': 0.01,
        'peak_templates': [   {   'peak_label': 'E2g',
                                  'center': 383.0,
                                  'center_tolerance': 5.0,
                                  'width_fwhm': 10.0,
                                  'shape': 0.3,
                                  'color': '#d62728'},
                              {   'peak_label': 'A1g',
                                  'center': 408.0,
                                  'center_tolerance': 5.0,
                                  'width_fwhm': 12.0,
                                  'shape': 0.3,
                                  'color': '#9467bd'}],
        'exclusion_ranges': '375-390; 400-415',
        'description': 'MoS2 E2g and A1g peaks'},
    {   'material_name': 'Silicon',
        'mode': 'Raman',
        'enabled': True,
        'x_range_enabled': False,
        'x_min': None,
        'x_max': None,
        'despike_threshold': 6.0,
        'baseline_algorithm': 'Polynomial',
        'baseline_degree': 5,
        'baseline_lambda': None,
        'baseline_p': None,
        'peak_templates': [   {   'peak_label': 'Si',
                                  'center': 520.0,
                                  'center_tolerance': 3.0,
                                  'width_fwhm': 8.0,
                                  'shape': 0.2,
                                  'color': '#2ca02c'}],
        'exclusion_ranges': None,
        'description': 'Silicon reference peak at 520 cm⁻¹'},
    {   'material_name': 'WSe2',
        'mode': 'PL',
        'enabled': True,
        'x_range_enabled': False,
        'x_min': None,
        'x_max': None,
        'despike_threshold': 8.0,
        'baseline_algorithm': 'ALS',
        'baseline_degree': None,
        'baseline_lambda': 50000.0,
        'baseline_p': 0.01,
        'peak_templates': [   {   'peak_label': 'Exciton',
                                  'center': 770.0,
                                  'center_tolerance': 15.0,
                                  'width_fwhm': 30.0,
                                  'shape': 0.3,
                                  'color': '#d62728'},
                              {   'peak_label': 'Trion',
                                  'center': 800.0,
                                  'center_tolerance': 15.0,
                                  'width_fwhm': 50.0,
                                  'shape': 0.3,
                                  'color': '#9467bd'}],
        'exclusion_ranges': '650-880',
        'description': 'WSe2 PL peaks'},
    {   'material_name': 'WSe2',
        'mode': 'Raman',
        'enabled': True,
        'x_range_enabled': True,
        'x_min': 6.0,
        'x_max': 400.0,
        'despike_threshold': 8.0,
        'baseline_algorithm': 'ALS',
        'baseline_degree': None,
        'baseline_lambda': 50000.0,
        'baseline_p': 0.02,
        'peak_templates': [   {   'peak_label': 'E2g+A1g',
                                  'center': 250.0,
                                  'center_tolerance': 7.0,
                                  'width_fwhm': 4.0,
                                  'shape': 0.3,
                                  'color': '#EF482E'},
                              {   'peak_label': '2LA',
                                  'center': 260.0,
                                  'center_tolerance': 7.0,
                                  'width_fwhm': 4.0,
                                  'shape': 0.3,
                                  'color': '#D5EF2E'},
                              {   'peak_label': 'B2g',
                                  'center': 310.0,
                                  'center_tolerance': 10.0,
                                  'width_fwhm': 2.0,
                                  'shape': 0.3,
                                  'color': '#2ED5EF'},
                              {   'peak_label': 'LB',
                                  'center': 30.0,
                                  'center_tolerance': 5.0,
                                  'width_fwhm': 2.0,
                                  'shape': 0.3,
                                  'color': '#482EEF'},
                              {   'peak_label': 'LA',
                                  'center': 135.0,
                                  'center_tolerance': 10.0,
                                  'width_fwhm': 15.0,
                                  'shape': 0.3,
                                  'color': '#76EC32'},
                              {   'peak_label': 'C',
                                  'center': 17.0,
                                  'center_tolerance': 5.0,
                                  'width_fwhm': 5.0,
                                  'shape': 0.3,
                                  'color': '#3276EC'},
                              {   'peak_label': 'center',
                                  'center': 0.0,
                                  'center_tolerance': 3.0,
                                  'width_fwhm': 5.0,
                                  'shape': 0.3,
                                  'color': '#3276EC'}],
        'exclusion_ranges': None,
        'description': 'WSe2 Raman'}]


def _v1(material_name: str, mode: str) -> dict:
    """One v1 entry by identity rather than by position."""
    return next(e for e in V1_STORE
                if e["material_name"] == material_name and e["mode"] == mode)


class TestMigrateLegacyIsPure:
    def test_it_takes_dicts_and_returns_dicts(self):
        entries, warnings = migrate_legacy(V1_STORE)

        assert all(isinstance(e, dict) for e in entries)
        assert all(isinstance(w, str) for w in warnings)

    def test_it_does_not_mutate_its_input(self):
        before = json.dumps(V1_STORE, sort_keys=True)
        migrate_legacy(V1_STORE)

        assert json.dumps(V1_STORE, sort_keys=True) == before

    def test_it_is_idempotent(self):
        once, _ = migrate_legacy(V1_STORE)
        twice, warnings = migrate_legacy(once)

        assert twice == once
        assert warnings == []


class TestMigrationMergesTechniques:
    def test_one_material_per_name_not_per_technique(self):
        entries, _ = migrate_legacy(V1_STORE)

        assert [e["material_name"] for e in entries] == ["MoS2", "Silicon", "WSe2"]
        assert len(V1_STORE) == 4, "four v1 entries became three materials"

    def test_both_techniques_land_in_their_own_block(self):
        entries, _ = migrate_legacy(V1_STORE)
        wse2 = next(e for e in entries if e["material_name"] == "WSe2")

        assert wse2["raman"]["x_min"] == 6.0
        assert wse2["raman"]["baseline_p"] == 0.02
        assert wse2["pl"]["exclusion_ranges"] == "650-880"
        assert wse2["pl"]["baseline_p"] == 0.01

    def test_mode_is_gone_from_every_block(self):
        entries, _ = migrate_legacy(V1_STORE)

        for entry in entries:
            for block in ("raman", "pl"):
                assert "mode" not in entry.get(block, {})

    def test_peak_templates_survive_byte_identical(self):
        entries, _ = migrate_legacy(V1_STORE)
        migrated = {}
        for entry in entries:
            for mode, block in (("Raman", "raman"), ("PL", "pl")):
                if block in entry:
                    migrated[(entry["material_name"], mode)] = entry[block]["peak_templates"]

        original = {(e["material_name"], e["mode"]): e["peak_templates"] for e in V1_STORE}
        assert migrated == original

    def test_descriptions_join_raman_first(self):
        """File order is PL-then-Raman for WSe2, so this catches ordering by
        arrival rather than by technique."""
        entries, warnings = migrate_legacy(V1_STORE)
        wse2 = next(e for e in entries if e["material_name"] == "WSe2")

        assert wse2["description"] == "WSe2 Raman / WSe2 PL peaks"
        assert any("joined" in w for w in warnings)

    def test_enabled_is_ored_not_anded(self):
        """A disabled PL entry must not switch off a working Raman one."""
        entries, _ = migrate_legacy([
            {**_v1("WSe2", "PL"), "enabled": False},
            _v1("WSe2", "Raman"),
        ])

        assert entries[0]["enabled"] is True

    def test_a_duplicate_technique_keeps_the_last(self):
        """Matching what v1's own loader did -- last key wins."""
        raman = _v1("WSe2", "Raman")
        entries, warnings = migrate_legacy(
            [{**raman, "despike_threshold": 3.5}, raman]
        )

        assert entries[0]["raman"]["despike_threshold"] == 8.0
        assert any("more than one" in w for w in warnings)


class TestMigrationRaisesRatherThanGuesses:
    def test_an_entry_carrying_both_shapes(self):
        mixed = {**_v1("MoS2", "Raman"), "raman": {"despike_threshold": 9.0}}

        with pytest.raises(ValueError, match="both shapes"):
            migrate_legacy([mixed])

    def test_a_half_migrated_file(self):
        new_shape = {"material_name": "MoS2", "enabled": True,
                     "raman": {"despike_threshold": 9.0}}

        with pytest.raises(ValueError, match="half migrated"):
            migrate_legacy([_v1("MoS2", "Raman"), new_shape])

    def test_a_duplicate_material_in_the_new_shape(self):
        entry = {"material_name": "MoS2", "enabled": True,
                 "raman": {"despike_threshold": 9.0}}

        with pytest.raises(ValueError, match="Duplicate material"):
            migrate_legacy([entry, dict(entry)])

    def test_an_entry_with_no_material_name(self):
        with pytest.raises(ValueError, match="no material_name"):
            migrate_legacy([{"mode": "Raman"}])

    def test_an_unrecognised_mode(self):
        with pytest.raises(ValueError, match="mode"):
            migrate_legacy([{**_v1("MoS2", "Raman"), "mode": "XRD"}])


class TestTheShippedStoreIsAlreadyV2:
    """What makes the loader shim deletable later."""

    def test_loading_it_migrates_nothing_and_warns_about_nothing(self):
        store = load_store()

        assert store.migrated is False
        assert store.warnings == []

    def test_it_declares_the_current_schema_version(self):
        with open(DATA_DIR / "materials.json", encoding="utf-8") as f:
            document = json.load(f)

        assert isinstance(document, dict)
        assert document["schema_version"] == SCHEMA_VERSION

    def test_every_material_validates(self):
        for name, preset in load_store().presets.items():
            assert preset.validate() == [], f"{name} does not validate"

    def test_migrating_v1_reproduces_the_committed_file(self):
        """The highest-value assertion in the change: the migration that
        actually ran lost nothing.

        What is compared is the measurement content -- peak templates, their
        centres and tolerances, and the processing settings. Two fields are
        excluded because they were deliberately edited after the migration and
        comparing them would assert the absence of changes that were made on
        purpose:

        - `optical`: v1 held no optical settings to carry, so WSe2's bilayer
          block was seeded afterwards.
        - `description`: at v5.0.0 the separate `WSe2-HA` entry was folded into
          `WSe2` and the old `WSe2` dropped, so the committed entry is the HA
          one renamed. Its peaks are identical -- that is exactly why the fold
          was safe, and it is what the assertion below still proves -- but it
          carries HA's description explaining its fixed-contrast optical block.

        MoS2's `raman` and `pl` are excluded as well: at v5.10.2 its Raman block
        was retuned and a PL block added, both fitted against HA1N01 and 02N01
        spectra, so its v1 Raman block no longer describes it. WSe2's `raman`
        and `pl` are excluded too: at v5.10.5 its Raman despike threshold went
        from 8 to 100, and at v5.10.3 its Exciton/Trion windows moved from
        770/800 to 755/780, because monolayer emission sat against the old
        windows' blue edges.
        """
        migrated, _ = migrate_legacy(V1_STORE)
        by_name = {e["material_name"]: e for e in migrated}
        post_migration_edits = ("optical", "description")
        retuned = {"MoS2": ("raman", "pl"), "WSe2": ("raman", "pl")}

        with open(DATA_DIR / "materials.json", encoding="utf-8") as f:
            committed = json.load(f)["materials"]

        for entry in committed:
            name = entry["material_name"]
            if name not in by_name:
                continue  # a material added since the migration
            skip = post_migration_edits + retuned.get(name, ())
            theirs = {k: v for k, v in entry.items() if k not in skip}
            mine = {k: v for k, v in by_name[name].items() if k not in skip}
            assert (MaterialPreset.from_dict(mine).to_dict()
                    == MaterialPreset.from_dict(theirs).to_dict()), name


class TestRoundTrip:
    def test_absent_blocks_are_omitted_not_null(self, tmp_path):
        path = tmp_path / "materials.json"
        preset = MaterialPreset(material_name="OpticalOnly",
                                optical={"2L": OpticalParams(nsigma=5.0)})

        save_presets({"OpticalOnly": preset}, path=path)
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)["materials"][0]

        assert "raman" not in entry
        assert "pl" not in entry
        assert entry["optical"] == {"2L": {"nsigma": 5.0}}

    def test_unset_optical_fields_are_omitted_too(self):
        """So the defaults stay written down once, in contrast.py."""
        assert OpticalParams(nsigma=5.0).to_dict() == {"nsigma": 5.0}
        assert OpticalParams().to_dict() == {}
        assert OpticalParams().as_kwargs() == {}

    def test_mask_margin_maps_onto_analyse_frames_margin_keyword(self):
        params = OpticalParams(mask_margin=0.12, ff_divisor=16.0)

        assert params.as_kwargs() == {"margin": 0.12, "ff_divisor": 16.0}

    def test_marker_margin_sigma_round_trips_but_never_reaches_analyse_frame(self):
        """It tunes domain_size.measure_domains()'s watershed marker floor,
        not classify() -- as_kwargs() feeds analyse_frame directly, and that
        function has no such keyword."""
        params = OpticalParams(nsigma=4.0, marker_margin_sigma=0.2)

        assert params.to_dict() == {"nsigma": 4.0, "marker_margin_sigma": 0.2}
        assert "marker_margin_sigma" not in params.as_kwargs()
        assert OpticalParams.from_dict(params.to_dict()) == params

    def test_marker_margin_sigma_out_of_range_is_flagged(self):
        assert OpticalParams(marker_margin_sigma=0.2).validate() == []
        assert any(
            "marker_margin_sigma" in e
            for e in OpticalParams(marker_margin_sigma=-0.1).validate()
        )
        assert any(
            "marker_margin_sigma" in e
            for e in OpticalParams(marker_margin_sigma=5.1).validate()
        )

    def test_marker_min_distance_round_trips_but_never_reaches_analyse_frame(self):
        """Same shape as marker_margin_sigma immediately above it: it tunes
        domain_size.measure_domains()'s watershed marker spacing, not
        classify(), so as_kwargs() (which feeds analyse_frame directly) must
        not carry it."""
        params = OpticalParams(nsigma=4.0, marker_min_distance=25)

        assert params.to_dict() == {"nsigma": 4.0, "marker_min_distance": 25}
        assert "marker_min_distance" not in params.as_kwargs()
        assert OpticalParams.from_dict(params.to_dict()) == params

    def test_marker_min_distance_out_of_range_is_flagged(self):
        assert OpticalParams(marker_min_distance=25).validate() == []
        assert any(
            "marker_min_distance" in e
            for e in OpticalParams(marker_min_distance=0).validate()
        )
        assert any(
            "marker_min_distance" in e
            for e in OpticalParams(marker_min_distance=201).validate()
        )

    def test_mask_closing_radius_round_trips_but_never_reaches_analyse_frame(self):
        """Same shape as marker_margin_sigma/marker_min_distance above it: it
        tunes domain_size.measure_domains()'s mask closing, not classify(),
        so as_kwargs() must not carry it."""
        params = OpticalParams(nsigma=4.0, mask_closing_radius=3)

        assert params.to_dict() == {"nsigma": 4.0, "mask_closing_radius": 3}
        assert "mask_closing_radius" not in params.as_kwargs()
        assert OpticalParams.from_dict(params.to_dict()) == params

    def test_mask_closing_radius_out_of_range_is_flagged(self):
        assert OpticalParams(mask_closing_radius=3).validate() == []
        assert OpticalParams(mask_closing_radius=0).validate() == []
        assert any(
            "mask_closing_radius" in e
            for e in OpticalParams(mask_closing_radius=-1).validate()
        )
        assert any(
            "mask_closing_radius" in e
            for e in OpticalParams(mask_closing_radius=11).validate()
        )

    def test_an_unknown_layer_round_trips_and_is_flagged(self, tmp_path):
        """Dropping it on load would turn "I loaded the file" into "I deleted
        part of the file"."""
        path = tmp_path / "materials.json"
        preset = MaterialPreset(
            material_name="WSe2",
            optical={"2L": OpticalParams(nsigma=4.0),
                     "3L": OpticalParams(nsigma=6.0)},
        )

        save_presets({"WSe2": preset}, path=path)
        loaded = load_store(path=path).presets["WSe2"]

        assert loaded.optical["3L"].nsigma == 6.0
        assert any("unknown layer '3L'" in e for e in loaded.validate())

    def test_a_newer_schema_version_is_refused_rather_than_misread(self, tmp_path):
        path = tmp_path / "materials.json"
        path.write_text(json.dumps({"schema_version": 99, "materials": []}))

        with pytest.raises(ValueError, match="schema_version"):
            load_store(path=path)
