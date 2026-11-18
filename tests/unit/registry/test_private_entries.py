# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The registry's access rule: a private entry exists only for its owner."""

from collections.abc import Iterator

import pytest

from pyrit.auth import caller_principal_scope
from pyrit.models import ComponentIdentifier, ComponentType, Identifiable
from pyrit.prompt_target import RoundRobinTarget
from pyrit.registry import (
    AttackTechniqueRegistry,
    DefaultInstanceRegistry,
    RegistryEntry,
    TargetRegistry,
    creator_metadata,
    get_entry_owner,
    is_private_entry,
    owner_metadata,
    record_private_references,
)
from pyrit.registry.instance_registry import (
    OWNER_METADATA_KEY,
    PRIVATE_METADATA_KEY,
    PrivateEntryReplacementError,
    RegistryNameTakenError,
)
from pyrit.registry.resolution import reference_name, resolve_reference_value
from unit.mocks import MockPromptTarget


class _Item(Identifiable):
    def __init__(self, value: str) -> None:
        self.value = value

    def _build_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(class_name="_Item", class_module="test", params={"value": self.value})


@pytest.fixture
def instances() -> DefaultInstanceRegistry[_Item]:
    registry: DefaultInstanceRegistry[_Item] = DefaultInstanceRegistry()
    registry.register(_Item("shared"), name="shared", tags=["team"])
    registry.register(
        _Item("alice"), name="alice-private", tags=["team"], metadata=owner_metadata("alice", private=True)
    )
    registry.register(_Item("alice-shared"), name="alice-shared", metadata=owner_metadata("alice", private=False))
    return registry


@pytest.fixture
def target_registry() -> Iterator[TargetRegistry]:
    TargetRegistry.reset_registry_singleton()
    yield TargetRegistry.get_registry_singleton()
    TargetRegistry.reset_registry_singleton()


@pytest.mark.parametrize(
    ("caller", "visible"),
    [
        ("alice", ["alice-private", "alice-shared", "shared"]),
        ("bob", ["alice-shared", "shared"]),
        (None, ["alice-shared", "shared"]),
    ],
)
def test_listings_show_private_entries_only_to_their_owner(
    instances: DefaultInstanceRegistry[_Item], caller: str | None, visible: list[str]
) -> None:
    with caller_principal_scope(caller):
        assert instances.get_names() == visible
        assert [entry.name for entry in instances.get_all_instances()] == visible
        assert len(instances) == len(visible)
        assert list(instances) == visible
        assert [entry.name for entry in instances.get_by_tag(tag="team")] == [
            name for name in visible if name in ("alice-private", "shared")
        ]
        assert [identifier.params["value"] for identifier in instances.list_metadata()] == [
            instances.get(name).value
            for name in visible  # type: ignore[union-attr]
        ]


@pytest.mark.parametrize("caller", ["bob", None])
def test_lookups_treat_another_callers_private_entry_as_missing(
    instances: DefaultInstanceRegistry[_Item], caller: str | None
) -> None:
    with caller_principal_scope(caller):
        assert instances.get("alice-private") is None
        assert instances.get_entry("alice-private") is None
        assert "alice-private" not in instances
        assert instances.unregister("alice-private") is None
        with pytest.raises(KeyError):
            instances.add_tags(name="alice-private", tags=["more"])

    with caller_principal_scope("alice"):
        assert instances.get("alice-private").value == "alice"  # type: ignore[union-attr]


def test_owner_is_recorded_separately_from_privacy(instances: DefaultInstanceRegistry[_Item]) -> None:
    with caller_principal_scope("alice"):
        private = instances.get_entry("alice-private")
        shared = instances.get_entry("alice-shared")
    assert private is not None and shared is not None
    assert (get_entry_owner(private), is_private_entry(private)) == ("alice", True)
    assert (get_entry_owner(shared), is_private_entry(shared)) == ("alice", False)
    with pytest.raises(ValueError, match="needs an owner"):
        owner_metadata(None, private=True)


@pytest.mark.parametrize(
    ("replace", "error_type"), [(False, RegistryNameTakenError), (True, PrivateEntryReplacementError)]
)
def test_a_name_another_caller_holds_is_taken_without_naming_the_owner(
    instances: DefaultInstanceRegistry[_Item], replace: bool, error_type: type[Exception]
) -> None:
    with caller_principal_scope("bob"), pytest.raises(error_type) as error:
        instances.register(_Item("bob"), name="alice-private", replace=replace)

    assert "alice" not in str(error.value).replace("alice-private", "")
    with caller_principal_scope("alice"):
        assert instances.get("alice-private").value == "alice"  # type: ignore[union-attr]


def test_every_owner_view_is_only_for_bookkeeping(instances: DefaultInstanceRegistry[_Item]) -> None:
    with caller_principal_scope("bob"):
        assert [entry.name for entry in instances.get_all_instances_of_every_owner()] == [
            "alice-private",
            "alice-shared",
            "shared",
        ]


def test_visibility_key_names_the_caller_only_when_they_have_private_entries(
    instances: DefaultInstanceRegistry[_Item],
) -> None:
    with caller_principal_scope("alice"):
        assert instances.visibility_key == "alice"
        instances.unregister("alice-private")
        assert instances.visibility_key is None
    with caller_principal_scope("bob"):
        assert instances.visibility_key is None


def test_lookups_by_name_record_the_private_entries_a_build_uses(instances: DefaultInstanceRegistry[_Item]) -> None:
    with caller_principal_scope("alice"), record_private_references() as references:
        instances.get("shared")
        instances.get("alice-shared")
        instances.get_all_instances()
        assert references == {}
        instances.get("alice-private")
    assert references == {"alice-private": "entry"}


@pytest.mark.usefixtures("patch_central_database")
def test_name_references_resolve_only_the_callers_private_targets(target_registry: TargetRegistry) -> None:
    target_registry.instances.register(
        MockPromptTarget(), name="alice-target", metadata=owner_metadata("alice", private=True)
    )

    with caller_principal_scope("bob"), pytest.raises(ValueError, match="not found") as error:
        resolve_reference_value(component_type=ComponentType.TARGET, value="alice-target", owner="Scorer", name="chat")
    assert "alice-target" not in str(error.value).partition("Available")[2]

    with caller_principal_scope("alice"):
        assert resolve_reference_value(
            component_type=ComponentType.TARGET, value="alice-target", owner="Scorer", name="chat"
        )


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("by_hash", [False, True])
def test_resolve_source_finds_only_the_callers_private_source(target_registry: TargetRegistry, by_hash: bool) -> None:
    target = MockPromptTarget()
    target_registry.instances.register(target, name="alice-target", metadata=owner_metadata("alice", private=True))
    name = "renamed" if by_hash else "alice-target"
    identifier_hash = target.get_identifier().hash

    with caller_principal_scope("bob"), pytest.raises(ValueError, match="missing"):
        target_registry.resolve_source(name=name, identifier_hash=identifier_hash)

    with caller_principal_scope("alice"), record_private_references() as references:
        assert target_registry.resolve_source(name=name, identifier_hash=identifier_hash) is target
    assert references == {"alice-target": "target"}


@pytest.mark.usefixtures("patch_central_database")
def test_a_component_built_from_a_private_entry_is_private_to_its_creator(target_registry: TargetRegistry) -> None:
    target_registry.instances.register(MockPromptTarget(), name="mine", metadata=owner_metadata("alice", private=True))
    target_registry.instances.register(MockPromptTarget(), name="team")
    target_registry.instances.register(MockPromptTarget(), name="team-2")

    with caller_principal_scope("alice"):
        target_registry.create_named_instance(
            name="mixed", type_name="RoundRobinTarget", params={"targets": ["mine", "team"]}, external_input=True
        )
        target_registry.create_named_instance(
            name="shared-only",
            type_name="RoundRobinTarget",
            params={"targets": ["team", "team-2"]},
            external_input=True,
        )
        mixed = target_registry.instances.get_entry("mixed")
        shared_only = target_registry.instances.get_entry("shared-only")

    assert isinstance(mixed, RegistryEntry) and isinstance(mixed.instance, RoundRobinTarget)
    assert is_private_entry(mixed)
    assert shared_only is not None and not is_private_entry(shared_only)
    with caller_principal_scope("bob"):
        assert target_registry.instances.get("mixed") is None


def test_technique_catalogs_are_cached_per_view() -> None:
    AttackTechniqueRegistry.reset_registry_singleton()
    try:
        registry = AttackTechniqueRegistry.get_registry_singleton()
        with caller_principal_scope("alice"):
            alice = registry.catalog_revision
        with caller_principal_scope("bob"):
            bob = registry.catalog_revision
        assert alice == bob
        registry.instances._private_entry_counts["alice"] = 1
        with caller_principal_scope("alice"):
            assert registry.catalog_revision != bob
    finally:
        AttackTechniqueRegistry.reset_registry_singleton()


@pytest.mark.usefixtures("patch_central_database")
def test_finding_a_reference_name_records_no_private_entries(target_registry: TargetRegistry) -> None:
    target_registry.instances.register(
        MockPromptTarget(), name="aaa-private", metadata=owner_metadata("alice", private=True)
    )
    shared = MockPromptTarget()
    target_registry.instances.register(shared, name="zzz-shared")

    with caller_principal_scope("alice"), record_private_references() as references:
        assert reference_name(component_type=ComponentType.TARGET, value=shared) == "zzz-shared"

    assert references == {}


@pytest.mark.parametrize(
    ("caller", "requested", "references", "expected"),
    [
        ("alice", None, {}, {OWNER_METADATA_KEY: "alice", PRIVATE_METADATA_KEY: False}),
        ("alice", False, {}, {OWNER_METADATA_KEY: "alice", PRIVATE_METADATA_KEY: False}),
        ("alice", True, {}, {OWNER_METADATA_KEY: "alice", PRIVATE_METADATA_KEY: True}),
        ("alice", None, {"mine": "target"}, {OWNER_METADATA_KEY: "alice", PRIVATE_METADATA_KEY: True}),
        (None, None, {}, {}),
        (None, False, {}, {}),
    ],
)
def test_components_are_shared_unless_made_private_or_built_on_a_private_entry(
    caller: str | None, requested: bool | None, references: dict[str, str], expected: dict[str, object]
) -> None:
    with caller_principal_scope(caller):
        assert creator_metadata(name="new", requested_private=requested, private_references=references) == expected


def test_a_component_built_on_a_private_entry_cannot_be_shared() -> None:
    with caller_principal_scope("alice"), pytest.raises(ValueError, match="built on your private target 'mine'"):
        creator_metadata(name="new", requested_private=False, private_references={"mine": "target"})


def test_a_private_component_needs_a_caller() -> None:
    with pytest.raises(PermissionError, match="signed-in caller"):
        creator_metadata(name="new", requested_private=True, private_references={})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("name", "tags"), [("Secret_Plan", []), ("mine", ["Hidden_Tag"])])
def test_a_technique_clash_with_a_hidden_technique_names_no_one_elses_selector(name: str, tags: list[str]) -> None:
    from pyrit.executor.attack import PromptSendingAttack
    from pyrit.scenario.core import AttackTechniqueFactory

    AttackTechniqueRegistry.reset_registry_singleton()
    try:
        instances = AttackTechniqueRegistry.get_registry_singleton().instances
        hidden = AttackTechniqueFactory(
            name="secret_plan", attack_class=PromptSendingAttack, technique_tags=["hidden_tag"]
        )
        instances.register(hidden, name="secret_plan", metadata=owner_metadata("alice", private=True))
        factory = AttackTechniqueFactory(name=name, attack_class=PromptSendingAttack, technique_tags=tags)

        with caller_principal_scope("bob"), pytest.raises(RegistryNameTakenError) as error:
            instances.register_runtime(factory)

        assert "secret_plan" not in str(error.value)
        assert "hidden_tag" not in str(error.value)
        assert "is taken" in str(error.value)
    finally:
        AttackTechniqueRegistry.reset_registry_singleton()


@pytest.mark.usefixtures("patch_central_database")
def test_techniques_of_different_owners_can_share_a_tag() -> None:
    from pyrit.executor.attack import PromptSendingAttack
    from pyrit.scenario.core import AttackTechniqueFactory

    AttackTechniqueRegistry.reset_registry_singleton()
    try:
        instances = AttackTechniqueRegistry.get_registry_singleton().instances
        hidden = AttackTechniqueFactory(
            name="secret_plan", attack_class=PromptSendingAttack, technique_tags=["shared_tag"]
        )
        instances.register(hidden, name="secret_plan", metadata=owner_metadata("alice", private=True))

        with caller_principal_scope("bob"):
            instances.register_runtime(
                AttackTechniqueFactory(name="mine", attack_class=PromptSendingAttack, technique_tags=["shared_tag"])
            )
            assert instances.get("mine") is not None
        instances.register_runtime(
            AttackTechniqueFactory(name="configured", attack_class=PromptSendingAttack, technique_tags=["shared_tag"])
        )
        assert instances.get("configured") is not None
    finally:
        AttackTechniqueRegistry.reset_registry_singleton()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("name", "tags"), [("mine", ["Shared_Tag"]), ("mine", ["secret_plan"]), ("shared_tag", [])])
def test_a_hidden_technique_still_blocks_name_and_spelling_clashes(name: str, tags: list[str]) -> None:
    from pyrit.executor.attack import PromptSendingAttack
    from pyrit.scenario.core import AttackTechniqueFactory

    AttackTechniqueRegistry.reset_registry_singleton()
    try:
        instances = AttackTechniqueRegistry.get_registry_singleton().instances
        hidden = AttackTechniqueFactory(
            name="secret_plan", attack_class=PromptSendingAttack, technique_tags=["shared_tag"]
        )
        instances.register(hidden, name="secret_plan", metadata=owner_metadata("alice", private=True))

        with caller_principal_scope("bob"), pytest.raises(RegistryNameTakenError, match="is taken"):
            instances.register_runtime(
                AttackTechniqueFactory(name=name, attack_class=PromptSendingAttack, technique_tags=tags)
            )
    finally:
        AttackTechniqueRegistry.reset_registry_singleton()


def test_replacing_a_name_a_private_entry_holds_reports_a_clear_conflict(
    instances: DefaultInstanceRegistry[_Item],
) -> None:
    with pytest.raises(PrivateEntryReplacementError, match="a user's private entry uses") as error:
        instances.register(_Item("configured"), name="alice-private", replace=True)

    assert "alice" not in str(error.value).replace("alice-private", "")
    with caller_principal_scope("alice"):
        assert instances.get("alice-private").value == "alice"  # type: ignore[union-attr]
