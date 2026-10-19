# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario preset registry."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import Parameter
from pyrit.models.catalog.scenario_preset import ScenarioPreset
from pyrit.registry.components.converter_registry import ConverterRegistry
from pyrit.registry.components.scenario_preset_registry import (
    ScenarioPresetNameRegisteredError,
    ScenarioPresetRegistry,
)
from pyrit.registry.components.scenario_registry import ScenarioMetadata, ScenarioRegistry
from pyrit.registry.scenario_preset_storage import ScenarioPresetConflictError, ScenarioPresetStorage
from unit.mocks import make_technique_class

SCENARIO_NAME = "foundry.red_team_agent"


def _metadata(
    *,
    all_techniques: tuple[str, ...] = ("crescendo", "flip"),
    aggregate_techniques: tuple[str, ...] = (),
    supported_parameters: tuple[str, ...] = ("max_turns",),
    param_type: type = str,
    baseline_policy: str = "enabled",
) -> ScenarioMetadata:
    """Build real registry metadata carrying only the fields preset validation reads."""
    return ScenarioMetadata(
        "RedTeamAgentScenario",
        "pyrit.scenario.scenarios.foundry.red_team_agent",
        "Test scenario",
        SCENARIO_NAME,
        default_technique=all_techniques[0],
        all_techniques=all_techniques,
        technique_class=make_technique_class(techniques=all_techniques, aggregates=aggregate_techniques),
        aggregate_techniques=aggregate_techniques,
        default_datasets=("harmbench",),
        supported_parameters=tuple(
            Parameter(name=name, description="", param_type=param_type) for name in supported_parameters
        ),
        baseline_policy=baseline_policy,  # type: ignore[arg-type]
    )


def _preset(name: str = "quick_scan", **overrides: object) -> ScenarioPreset:
    """Build a preset that resolves cleanly against the fixture metadata."""
    fields: dict[str, object] = {"name": name, "scenario_name": SCENARIO_NAME}
    fields.update(overrides)
    return ScenarioPreset(**fields)  # type: ignore[arg-type]


@pytest.fixture
def registry(tmp_path: Path) -> ScenarioPresetRegistry:
    """Create a registry backed by an isolated preset directory."""
    return ScenarioPresetRegistry(storage=ScenarioPresetStorage(source=str(tmp_path)))


@pytest.fixture
def scenario_registry() -> Iterator[MagicMock]:
    """Patch scenario lookup so presets resolve against known metadata."""
    singleton = MagicMock(spec=ScenarioRegistry)
    singleton.get_registered_class_metadata.return_value = _metadata()
    with patch.object(ScenarioRegistry, "get_registry_singleton", return_value=singleton):
        yield singleton


@pytest.fixture
def converter_registry() -> Iterator[MagicMock]:
    """Patch converter lookup so no converter is registered unless a test says so."""
    singleton = MagicMock()
    singleton.instances.get.return_value = None
    with patch.object(ConverterRegistry, "get_registry_singleton", return_value=singleton):
        yield singleton


def _register_converters(converter_registry: MagicMock, *names: str) -> None:
    """Make only *names* resolve as registered converters."""
    converter_registry.instances.get.side_effect = lambda name: MagicMock() if name in names else None


class TestPresetCrud:
    """CRUD behavior over the configured storage source."""

    def test_list_presets_is_empty_when_nothing_is_stored(self, registry: ScenarioPresetRegistry) -> None:
        assert registry.list_presets() == []

    def test_list_presets_is_sorted_by_name(self, registry: ScenarioPresetRegistry) -> None:
        for name in ("zebra", "alpha", "middle"):
            registry.save_preset(preset=_preset(name), expected_version=None)

        assert [stored.preset.name for stored in registry.list_presets()] == ["alpha", "middle", "zebra"]

    def test_get_preset_returns_none_when_not_stored(self, registry: ScenarioPresetRegistry) -> None:
        assert registry.get_preset(name="missing") is None

    def test_saved_preset_round_trips_every_field(self, registry: ScenarioPresetRegistry) -> None:
        preset = _preset(
            description="Nightly smoke",
            author="tester",
            techniques=["crescendo"],
            dataset_names=["harmbench"],
            max_dataset_size=25,
            dataset_filters={"harm_categories": ["violence"]},
            include_baseline=True,
            scenario_params={"max_turns": 3},
        )

        registry.save_preset(preset=preset, expected_version=None)
        read_back = registry.get_preset(name=preset.name)

        assert read_back is not None
        assert read_back.preset == preset

    def test_update_with_the_version_from_a_read_succeeds(self, registry: ScenarioPresetRegistry) -> None:
        created = registry.save_preset(preset=_preset(), expected_version=None)

        updated = registry.save_preset(preset=_preset(description="changed"), expected_version=created.version)

        assert updated.preset.description == "changed"
        assert updated.version != created.version

    def test_update_with_a_stale_version_conflicts(self, registry: ScenarioPresetRegistry) -> None:
        created = registry.save_preset(preset=_preset(), expected_version=None)
        registry.save_preset(preset=_preset(description="first"), expected_version=created.version)

        with pytest.raises(ScenarioPresetConflictError):
            registry.save_preset(preset=_preset(description="second"), expected_version=created.version)

    def test_create_over_an_existing_preset_conflicts(self, registry: ScenarioPresetRegistry) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        with pytest.raises(ScenarioPresetConflictError):
            registry.save_preset(preset=_preset(), expected_version=None)

    def test_delete_removes_the_stored_preset(self, registry: ScenarioPresetRegistry) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        assert registry.delete_preset(name="quick_scan") is True
        assert registry.get_preset(name="quick_scan") is None

    def test_delete_reports_a_missing_preset_rather_than_succeeding_silently(
        self, registry: ScenarioPresetRegistry
    ) -> None:
        assert registry.delete_preset(name="missing") is False

    def test_only_the_delete_that_removed_the_preset_reports_that_it_did(
        self, registry: ScenarioPresetRegistry
    ) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        assert registry.delete_preset(name="quick_scan") is True
        assert registry.delete_preset(name="quick_scan") is False

    def test_delete_does_not_probe_storage_before_removing(self, registry: ScenarioPresetRegistry) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        with patch.object(
            ScenarioPresetStorage, "get_preset_version", side_effect=AssertionError("storage was probed")
        ):
            assert registry.delete_preset(name="quick_scan") is True

    def test_delete_removes_a_document_that_cannot_be_parsed(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

        assert registry.delete_preset(name="broken") is True
        assert not (tmp_path / "broken.json").exists()

    def test_get_preset_version_reaches_a_document_that_cannot_be_parsed(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

        version = registry.get_preset_version(name="broken")

        assert version is not None
        assert registry.get_preset(name="broken") is None
        assert registry.save_preset(preset=_preset("broken"), expected_version=version).preset.name == "broken"

    def test_an_illegal_name_is_refused_rather_than_escaping_the_source(self, registry: ScenarioPresetRegistry) -> None:
        with pytest.raises(ValueError):
            registry.get_preset(name="../escape")


class TestEnsurePreset:
    """Code contributes a preset without overwriting what the operator has since done to it."""

    def test_the_preset_is_created_when_the_name_is_free(self, registry: ScenarioPresetRegistry) -> None:
        assert registry.ensure_preset(preset=_preset()) is True
        assert registry.get_preset(name="quick_scan") is not None

    def test_an_existing_preset_is_left_exactly_as_the_operator_left_it(self, registry: ScenarioPresetRegistry) -> None:
        registry.save_preset(preset=_preset(techniques=["flip"]), expected_version=None)

        assert registry.ensure_preset(preset=_preset(techniques=["crescendo"])) is False

        stored = registry.get_preset(name="quick_scan")
        assert stored is not None
        assert stored.preset.techniques == ["flip"]

    def test_repeating_the_call_is_not_an_error(self, registry: ScenarioPresetRegistry) -> None:
        assert registry.ensure_preset(preset=_preset()) is True
        assert registry.ensure_preset(preset=_preset()) is False

    def test_an_illegal_name_is_still_refused(self, registry: ScenarioPresetRegistry) -> None:
        with pytest.raises(ValueError):
            registry.ensure_preset(preset=_preset("../escape"))


class TestRegisteredPresets:
    """Code contributes a preset for this process only, and the operator's document wins."""

    def test_a_registered_preset_is_readable_and_reports_no_version(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset())

        entry = registry.get_preset(name="quick_scan")

        assert entry is not None
        assert entry.origin == "registered"
        assert entry.version is None

    def test_a_registered_preset_has_no_document_version(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset())

        assert registry.get_preset_version(name="quick_scan") is None

    def test_listing_merges_both_origins_in_name_order(self, registry: ScenarioPresetRegistry) -> None:
        registry.save_preset(preset=_preset("stored_one"), expected_version=None)
        registry.register_preset(preset=_preset("code_one"))

        listed = registry.list_presets()

        assert [(entry.preset.name, entry.origin) for entry in listed] == [
            ("code_one", "registered"),
            ("stored_one", "stored"),
        ]

    def test_a_stored_document_shadows_a_registered_preset_in_a_listing(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        registry.register_preset(preset=_preset(techniques=["crescendo"]))
        (tmp_path / "quick_scan.json").write_text(
            json.dumps({"scenario_name": SCENARIO_NAME, "techniques": ["flip"]}), encoding="utf-8"
        )

        entries = {entry.preset.name: entry for entry in registry.list_presets()}

        assert entries["quick_scan"].origin == "stored"
        assert entries["quick_scan"].preset.techniques == ["flip"]

    def test_a_stored_document_shadows_a_registered_preset_on_read(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        registry.register_preset(preset=_preset(techniques=["crescendo"]))
        (tmp_path / "quick_scan.json").write_text(
            json.dumps({"scenario_name": SCENARIO_NAME, "techniques": ["flip"]}), encoding="utf-8"
        )

        entry = registry.get_preset(name="quick_scan")

        assert entry is not None
        assert entry.origin == "stored"
        assert entry.preset.techniques == ["flip"]

    def test_registering_a_shadowed_name_warns_so_the_collision_is_not_silent(
        self, registry: ScenarioPresetRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        with caplog.at_level(logging.WARNING):
            registry.register_preset(preset=_preset())

        assert "takes precedence" in caplog.text

    def test_registering_the_same_name_twice_is_refused(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset())

        with pytest.raises(ValueError, match="quick_scan"):
            registry.register_preset(preset=_preset())

    def test_saving_over_a_registered_name_is_refused(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset())

        with pytest.raises(ScenarioPresetNameRegisteredError) as caught:
            registry.save_preset(preset=_preset(), expected_version=None)

        assert caught.value.name == "quick_scan"
        assert registry.get_preset_version(name="quick_scan") is None

    def test_a_registered_name_and_an_illegal_name_raise_distinguishable_types(
        self, registry: ScenarioPresetRegistry
    ) -> None:
        """A caller mapping these to different statuses must not have to match on the message."""
        registry.register_preset(preset=_preset())

        with pytest.raises(ScenarioPresetNameRegisteredError):
            registry.save_preset(preset=_preset(), expected_version=None)

        illegal_name = _preset(name="placeholder").model_copy(update={"name": "not/a/legal/name"})
        with pytest.raises(ValueError) as illegal:
            registry.save_preset(preset=illegal_name, expected_version=None)

        assert not isinstance(illegal.value, ScenarioPresetNameRegisteredError)

    def test_a_registered_name_is_not_reported_as_a_version_conflict(self, registry: ScenarioPresetRegistry) -> None:
        """Re-reading clears a version conflict but never clears this, so the types stay apart."""
        registry.register_preset(preset=_preset())

        with pytest.raises(ScenarioPresetNameRegisteredError) as caught:
            registry.save_preset(preset=_preset(), expected_version=None)

        assert not isinstance(caught.value, ScenarioPresetConflictError)
        assert isinstance(caught.value, ValueError)

    def test_ensuring_a_registered_name_writes_nothing(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset())

        assert registry.ensure_preset(preset=_preset()) is False
        assert registry.get_preset_version(name="quick_scan") is None

    def test_deleting_a_registered_preset_removes_nothing(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset())

        assert registry.delete_preset(name="quick_scan") is False
        assert registry.get_preset(name="quick_scan") is not None

    def test_resolving_a_registered_preset_needs_no_version(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset())

        resolved = registry.resolve_preset(name="quick_scan", expected_version=None)

        assert resolved is not None
        assert resolved.origin == "registered"

    def test_resolving_a_registered_preset_with_a_version_conflicts(self, registry: ScenarioPresetRegistry) -> None:
        created = registry.save_preset(preset=_preset(), expected_version=None)
        registry.delete_preset(name="quick_scan")
        registry.register_preset(preset=_preset())

        with pytest.raises(ScenarioPresetConflictError):
            registry.resolve_preset(name="quick_scan", expected_version=created.version)

    def test_registered_presets_carry_identifier_metadata(self, registry: ScenarioPresetRegistry) -> None:
        registry.register_preset(preset=_preset(), tags=["starter"])

        assert [entry.name for entry in registry.instances.get_by_tag(tag="starter")] == ["quick_scan"]
        assert len(registry.instances.list_metadata()) == 1


class TestReadThrough:
    """Every call reflects storage as it is now, not as this process last saw it."""

    def test_a_preset_written_by_another_process_is_visible_immediately(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        assert registry.list_presets() == []

        (tmp_path / "external.json").write_text(json.dumps({"scenario_name": SCENARIO_NAME}), encoding="utf-8")

        assert [stored.preset.name for stored in registry.list_presets()] == ["external"]
        assert registry.get_preset(name="external") is not None

    def test_a_preset_deleted_by_another_process_disappears_immediately(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        (tmp_path / "quick_scan.json").unlink()

        assert registry.get_preset(name="quick_scan") is None
        assert registry.list_presets() == []

    def test_a_hand_edit_changes_the_version_the_registry_reports(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        created = registry.save_preset(preset=_preset(), expected_version=None)

        (tmp_path / "quick_scan.json").write_text(
            json.dumps({"scenario_name": SCENARIO_NAME, "description": "edited by hand"}), encoding="utf-8"
        )

        read_back = registry.get_preset(name="quick_scan")
        assert read_back is not None
        assert read_back.version != created.version
        assert read_back.preset.description == "edited by hand"

    def test_a_save_is_refused_after_a_hand_edit_the_caller_never_saw(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        created = registry.save_preset(preset=_preset(), expected_version=None)

        (tmp_path / "quick_scan.json").write_text(
            json.dumps({"scenario_name": SCENARIO_NAME, "description": "edited by hand"}), encoding="utf-8"
        )

        with pytest.raises(ScenarioPresetConflictError):
            registry.save_preset(preset=_preset(description="from the stale editor"), expected_version=created.version)


class TestVersionCheckedResolution:
    """A launch runs the configuration the operator confirmed, or it does not run."""

    def test_resolve_returns_the_preset_when_the_version_still_matches(self, registry: ScenarioPresetRegistry) -> None:
        created = registry.save_preset(preset=_preset(), expected_version=None)

        resolved = registry.resolve_preset(name="quick_scan", expected_version=created.version)

        assert resolved is not None
        assert resolved.preset == created.preset

    def test_resolve_without_an_expected_version_accepts_whatever_is_stored(
        self, registry: ScenarioPresetRegistry
    ) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        resolved = registry.resolve_preset(name="quick_scan", expected_version=None)

        assert resolved is not None

    def test_resolve_conflicts_when_the_preset_changed_after_it_was_previewed(
        self, registry: ScenarioPresetRegistry
    ) -> None:
        created = registry.save_preset(preset=_preset(), expected_version=None)
        registry.save_preset(preset=_preset(description="changed"), expected_version=created.version)

        with pytest.raises(ScenarioPresetConflictError):
            registry.resolve_preset(name="quick_scan", expected_version=created.version)

    def test_resolve_conflicts_on_a_hand_edit_rather_than_launching_an_unconfirmed_configuration(
        self, registry: ScenarioPresetRegistry, tmp_path: Path
    ) -> None:
        created = registry.save_preset(preset=_preset(techniques=["crescendo"]), expected_version=None)

        (tmp_path / "quick_scan.json").write_text(
            json.dumps({"scenario_name": SCENARIO_NAME, "techniques": ["flip"]}), encoding="utf-8"
        )

        with pytest.raises(ScenarioPresetConflictError):
            registry.resolve_preset(name="quick_scan", expected_version=created.version)

    def test_resolve_returns_none_when_no_preset_is_stored(self, registry: ScenarioPresetRegistry) -> None:
        assert registry.resolve_preset(name="missing", expected_version=None) is None

    def test_resolve_reports_absence_before_checking_the_version(self, registry: ScenarioPresetRegistry) -> None:
        assert registry.resolve_preset(name="missing", expected_version="stale") is None


@pytest.mark.usefixtures("converter_registry")
class TestAdvisoryValidation:
    """Unresolvable references are reported, never enforced."""

    def test_a_resolvable_preset_has_no_issues(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        preset = _preset(techniques=["crescendo"], scenario_params={"max_turns": 2})

        assert registry.check_preset(preset=preset) == []

    def test_an_unregistered_scenario_is_reported(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        scenario_registry.get_registered_class_metadata.return_value = None

        issues = registry.check_preset(preset=_preset())

        assert [issue.field for issue in issues] == ["scenario_name"]
        assert "not registered" in issues[0].message

    def test_an_unresolvable_preset_is_still_saved(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        scenario_registry.get_registered_class_metadata.return_value = None
        preset = _preset()

        registry.save_preset(preset=preset, expected_version=None)

        assert registry.get_preset(name=preset.name) is not None
        assert registry.check_preset(preset=preset) != []

    def test_unknown_techniques_are_reported(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        issues = registry.check_preset(preset=_preset(techniques=["crescendo", "not_a_technique"]))

        assert [issue.field for issue in issues] == ["techniques"]
        assert "not_a_technique" in issues[0].message
        assert "crescendo" not in issues[0].message

    def test_an_aggregate_technique_is_not_reported_as_unknown(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        scenario_registry.get_registered_class_metadata.return_value = _metadata(aggregate_techniques=("all",))

        assert registry.check_preset(preset=_preset(techniques=["all"])) == []

    def test_a_converter_modifier_does_not_make_a_known_technique_look_unknown(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock, converter_registry: MagicMock
    ) -> None:
        _register_converters(converter_registry, "translation_spanish")

        issues = registry.check_preset(preset=_preset(techniques=["crescendo:converter.translation_spanish"]))

        assert issues == []

    def test_a_converter_this_deployment_has_not_registered_is_reported(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        issues = registry.check_preset(preset=_preset(techniques=["crescendo:converter.translation_spanish"]))

        assert [issue.field for issue in issues] == ["techniques"]
        assert "translation_spanish" in issues[0].message

    def test_a_modifier_the_launch_path_cannot_parse_is_reported(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        issues = registry.check_preset(preset=_preset(techniques=["crescendo:scorer.refusal"]))

        assert [issue.field for issue in issues] == ["techniques"]
        assert "scorer.refusal" in issues[0].message

    def test_undeclared_scenario_parameters_are_reported(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        issues = registry.check_preset(preset=_preset(scenario_params={"max_turns": "1", "mystery": 2}))

        assert [issue.field for issue in issues] == ["scenario_params"]
        assert "unknown parameter(s): mystery" in issues[0].message

    def test_a_declared_parameter_the_scenario_cannot_coerce_is_reported(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        scenario_registry.get_registered_class_metadata.return_value = _metadata(param_type=int)

        issues = registry.check_preset(preset=_preset(scenario_params={"max_turns": "banana"}))

        assert [issue.field for issue in issues] == ["scenario_params"]
        assert "max_turns" in issues[0].message
        assert "banana" in issues[0].message

    def test_a_baseline_the_scenario_forbids_is_reported(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        scenario_registry.get_registered_class_metadata.return_value = _metadata(baseline_policy="forbidden")

        issues = registry.check_preset(preset=_preset(include_baseline=True))

        assert [issue.field for issue in issues] == ["include_baseline"]

    def test_an_omitted_baseline_is_not_reported_when_the_scenario_forbids_one(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        scenario_registry.get_registered_class_metadata.return_value = _metadata(baseline_policy="forbidden")

        assert registry.check_preset(preset=_preset()) == []

    def test_every_kind_of_unresolvable_reference_is_reported_together(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        scenario_registry.get_registered_class_metadata.return_value = _metadata(baseline_policy="forbidden")
        preset = _preset(
            techniques=["not_a_technique", "crescendo:converter.missing", "crescendo:scorer.refusal"],
            scenario_params={"mystery": 1},
            include_baseline=True,
        )

        issues = registry.check_preset(preset=preset)

        assert [issue.field for issue in issues] == [
            "techniques",
            "techniques",
            "techniques",
            "scenario_params",
            "include_baseline",
        ]


class TestStartupScan:
    """Boot surfaces unusable documents without doing the work a launch would."""

    def test_it_counts_the_presets_that_parsed(self, registry: ScenarioPresetRegistry) -> None:
        for name in ("alpha", "beta"):
            registry.save_preset(preset=_preset(name), expected_version=None)

        assert registry.scan_stored_presets() == 2

    def test_a_malformed_document_is_reported_without_failing_the_pass(
        self, registry: ScenarioPresetRegistry, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry.save_preset(preset=_preset("healthy"), expected_version=None)
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

        with caplog.at_level("WARNING"):
            loaded = registry.scan_stored_presets()

        assert loaded == 1
        assert "broken" in caplog.text

    def test_a_document_that_fails_the_preset_schema_is_reported(
        self, registry: ScenarioPresetRegistry, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "typo.json").write_text(
            json.dumps({"scenario_name": SCENARIO_NAME, "technqiues": ["crescendo"]}), encoding="utf-8"
        )

        with caplog.at_level("WARNING"):
            loaded = registry.scan_stored_presets()

        assert loaded == 0
        assert "typo" in caplog.text

    def test_unreadable_storage_does_not_fail_boot(
        self, registry: ScenarioPresetRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage = MagicMock(spec=ScenarioPresetStorage)
        storage.list_presets.side_effect = OSError("source is unreachable")
        registry._storage = storage

        with caplog.at_level("ERROR"):
            assert registry.scan_stored_presets() == 0

        assert "Could not read scenario presets" in caplog.text

    def test_it_does_not_build_scenario_metadata(
        self, registry: ScenarioPresetRegistry, scenario_registry: MagicMock
    ) -> None:
        registry.save_preset(preset=_preset(), expected_version=None)

        registry.scan_stored_presets()

        assert scenario_registry.get_registered_class_metadata.call_count == 0


class TestSourceConfiguration:
    """The registry owns which source presets are read from and written to."""

    def test_configure_source_points_at_the_given_directory(self, tmp_path: Path) -> None:
        instance = ScenarioPresetRegistry()

        instance.configure_source(str(tmp_path))

        assert instance.source == str(tmp_path)

    def test_configure_source_replaces_an_earlier_source(self, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        instance = ScenarioPresetRegistry(storage=ScenarioPresetStorage(source=str(first)))
        instance.save_preset(preset=_preset(), expected_version=None)

        instance.configure_source(str(second))

        assert instance.source == str(second)
        assert instance.list_presets() == []

    def test_constructing_the_registry_does_not_touch_storage(self) -> None:
        with patch.object(ScenarioPresetStorage, "__init__", side_effect=AssertionError("storage was constructed")):
            ScenarioPresetRegistry()

    def test_the_singleton_is_shared(self) -> None:
        ScenarioPresetRegistry.reset_registry_singleton()
        try:
            first = ScenarioPresetRegistry.get_registry_singleton()
            second = ScenarioPresetRegistry.get_registry_singleton()

            assert first is second
        finally:
            ScenarioPresetRegistry.reset_registry_singleton()
