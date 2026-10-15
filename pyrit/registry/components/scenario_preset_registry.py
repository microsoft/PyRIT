# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry owning scenario preset storage, validation, and version-checked resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models.catalog.scenario_preset import PresetEntry, PresetIssue, ScenarioPreset, StoredPreset
from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.registry import Registry
from pyrit.registry.registry_metadata import RegistryMetadata
from pyrit.registry.scenario_preset_storage import ScenarioPresetConflictError, ScenarioPresetStorage

if TYPE_CHECKING:
    from pyrit.registry.components.scenario_registry import ScenarioMetadata
    from pyrit.scenario.core.technique_tokens import TechniqueIssueKind

logger = logging.getLogger(__name__)


class ScenarioPresetNameRegisteredError(ValueError):
    """A preset name is owned by code in this deployment and cannot be stored."""

    def __init__(self, name: str) -> None:
        """
        Initialize the error with the name code already owns.

        Args:
            name (str): The preset name a document tried to claim.
        """
        self.name = name
        super().__init__(
            f"Scenario preset '{name}' is registered by code in this deployment and cannot be "
            f"saved as a document. Unlike a version conflict, re-reading will not clear this."
        )


@dataclass(frozen=True)
class ScenarioPresetMetadata(RegistryMetadata):
    """
    Metadata describing a buildable preset class.

    Placeholder for a catalog that is intentionally empty: a preset is a document, not a
    class this registry constructs. It carries only the common ``RegistryMetadata``
    fields.
    """


class ScenarioPresetRegistry(Registry["ScenarioPreset", ScenarioPresetMetadata]):
    """
    The single owner of scenario preset storage, validation, and resolution.

    A preset reaches the registry one of two ways. A **stored** preset is a JSON document
    an operator can edit, move between deployments, and delete. A **registered** preset is
    contributed in-process by code, lives only as long as the process, and can be neither
    edited nor deleted — which is the point when a configuration is meant to stay fixed.
    ``PresetEntry.origin`` tells a caller which it holds, since only one supports a write.
    A stored document wins a name collision: the operator's data outranks a code default.

    A preset is a document rather than a component class, so the buildable class catalog
    inherited from ``Registry`` stays empty and ``_discover`` registers nothing. What the
    base contributes is the shared singleton machinery every registry is reached through,
    alongside the ``instances`` container holding the registered half.

    **Stored presets are read through on every call; nothing caches them.** The version
    guarding every write is ``sha256`` of the stored bytes, so a cached copy could only
    describe what this process last read or wrote.
    """

    def __init__(self, *, storage: ScenarioPresetStorage | None = None) -> None:
        """
        Initialize the registry without touching storage.

        Args:
            storage (ScenarioPresetStorage | None): Storage to read and write presets
                through. Defaults to the standard directory, constructed on first use so
                that merely importing the registry never creates a directory.
        """
        super().__init__()
        self._storage = storage
        self.instances: InstanceRegistry[ScenarioPreset] = DefaultInstanceRegistry(instance_type=ScenarioPreset)

    def _discover(self) -> None:
        """Register no classes: a preset is a document, not a class this registry builds."""

    def _metadata_class(self) -> type[ScenarioPresetMetadata]:
        """Return ``ScenarioPresetMetadata``; unused while the buildable catalog is empty."""
        return ScenarioPresetMetadata

    @property
    def source(self) -> str:
        """Credential-free storage source presets are read from and written to."""
        return self._get_storage().display_source

    def configure_source(self, source: str | None) -> None:
        """
        Point the registry at a local directory or Azure Blob source.

        Args:
            source (str | None): The configured source, or None to use the default
                directory under the PyRIT configuration path.
        """
        self._storage = ScenarioPresetStorage(source=source)

    def scan_stored_presets(self) -> int:
        """
        Read every stored preset once so unusable documents are reported at startup.

        ``list_presets`` skips a document it cannot parse, so without this a broken
        preset stays invisible until someone wonders where it went.

        The pass checks the schema only: resolving a preset needs scenario metadata, and
        building that instantiates every registered scenario class, which this early in
        boot has neither its environment nor its memory nor its techniques. Failures are
        logged rather than raised so unusable operator data cannot stop boot.

        Returns:
            int: The number of presets that parsed cleanly.
        """
        try:
            presets = self._get_storage().list_presets()
        except Exception:
            logger.exception("Could not read scenario presets at startup; the preset library may be unavailable.")
            return 0

        logger.info(f"Loaded {len(presets)} scenario preset(s) from {self.source}.")
        return len(presets)

    def register_preset(self, *, preset: ScenarioPreset, tags: dict[str, str] | list[str] | None = None) -> None:
        """
        Contribute one preset from code, for the lifetime of this process.

        Nothing is written, so the preset cannot be edited or deleted through the
        registry. Use ``ensure_preset`` instead to seed a library the operator then owns.

        A document already stored under this name shadows the registration and is warned
        about here rather than on every read; one appearing afterwards cannot be caught.

        Args:
            preset (ScenarioPreset): The preset, registered under its own ``name``.
            tags (dict[str, str] | list[str] | None): Tags for discovery through ``instances``.

        Raises:
            ValueError: If a preset is already registered under this name.
        """
        try:
            self.instances.register(preset, name=preset.name, tags=tags)
        except ValueError as exc:
            raise ValueError(f"Scenario preset '{preset.name}' is already registered.") from exc

        if self._stored_version(name=preset.name) is not None:
            logger.warning(
                f"Scenario preset '{preset.name}' was registered but a stored document of the same "
                f"name already exists; the stored document takes precedence."
            )

    def list_presets(self) -> list[PresetEntry]:
        """
        Read every preset this deployment offers, skipping documents that cannot be parsed.

        Returns:
            list[PresetEntry]: Stored and registered presets sorted by name, with a stored
            document shadowing a registered preset of the same name.
        """
        entries = {
            name: PresetEntry(preset=stored.preset, origin="stored", version=stored.version)
            for name, stored in self._get_storage().list_presets().items()
        }
        for entry in self.instances.get_all_instances():
            entries.setdefault(entry.name, PresetEntry(preset=entry.instance, origin="registered"))
        return [entries[name] for name in sorted(entries)]

    def get_preset(self, *, name: str) -> PresetEntry | None:
        """
        Read one preset together with the version needed to save an edit to it.

        Args:
            name (str): The preset name.

        Returns:
            PresetEntry | None: The preset, or None if it is absent or malformed. The
            version is set only for a stored preset, because only a stored preset can be
            written back.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        stored = self._get_storage().load_preset(name)
        if stored is not None:
            return PresetEntry(preset=stored.preset, origin="stored", version=stored.version)

        registered = self.instances.get(name)
        return PresetEntry(preset=registered, origin="registered") if registered is not None else None

    def get_preset_version(self, *, name: str) -> str | None:
        """
        Read the version of one stored document without parsing it.

        This is the recovery path: a document that cannot be parsed is unreachable
        through ``get_preset``, so exposing its version lets a caller overwrite the
        broken file instead of leaving the name permanently unusable.

        Args:
            name (str): The preset name.

        Returns:
            str | None: The document version, or None if no document is stored. A
            registered preset has no document and so no version.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        return self._get_storage().get_preset_version(name)

    def save_preset(self, *, preset: ScenarioPreset, expected_version: str | None) -> StoredPreset:
        """
        Persist one preset, refusing a write that would discard a concurrent edit.

        Unresolvable references are reported by ``check_preset`` rather than rejected
        here, so a preset authored against one deployment stays savable on another. A
        name already registered by code is refused: one name has one owner, which keeps
        the stored-shadows-registered rule a recovery path rather than a workflow.

        Args:
            preset (ScenarioPreset): The preset to persist.
            expected_version (str | None): None to create a preset that must not already
                exist, or the version returned when the edited preset was read.

        Returns:
            StoredPreset: The persisted preset and its new version.

        Raises:
            ScenarioPresetConflictError: If the stored version does not match *expected_version*.
            ScenarioPresetNameRegisteredError: If code already registered this name.
            ValueError: If the preset name is not a legal preset name.
        """
        try:
            self.instances.validate_name_available(preset.name)
        except ValueError as exc:
            raise ScenarioPresetNameRegisteredError(preset.name) from exc

        stored = self._get_storage().save_preset(preset=preset, expected_version=expected_version)
        logger.info(f"Saved scenario preset '{preset.name}'.")
        return stored

    def ensure_preset(self, *, preset: ScenarioPreset) -> bool:
        """
        Persist one preset only if nothing is stored under its name.

        This is how code seeds a library the operator then owns: create-if-absent rather
        than a write, so a preset they have since edited or replaced is left alone. The
        create and the existence test are the same conditional write, so two workers
        starting together cannot both decide the name is free.

        Args:
            preset (ScenarioPreset): The preset to persist if its name is free.

        Returns:
            bool: True if this call created the preset, False if one was already available.

        Raises:
            ValueError: If the preset name is not a legal preset name.
        """
        if preset.name in self.instances:
            logger.debug(f"Scenario preset '{preset.name}' is already registered; not storing a document.")
            return False

        try:
            self.save_preset(preset=preset, expected_version=None)
        except (ScenarioPresetConflictError, ScenarioPresetNameRegisteredError):
            logger.debug(f"Scenario preset '{preset.name}' already exists; leaving the stored preset alone.")
            return False
        return True

    def delete_preset(self, *, name: str) -> bool:
        """
        Delete one stored preset, reporting whether it was there to delete.

        The delete is unconditional: a version guards an edit from discarding content,
        and a delete has none to lose. The answer comes from the delete itself rather
        than a preceding existence check, so when two callers race to remove the same
        preset exactly one is told it did.

        Args:
            name (str): The preset name.

        Returns:
            bool: True if a document was stored under *name*, False if nothing was. A
            preset registered by code is not a document and is never deleted here.

        Raises:
            ValueError: If *name* is not a legal preset name.
            TimeoutError: If a local delete could not acquire the document lock.
        """
        deleted = self._get_storage().delete_preset(name)
        if deleted:
            logger.info(f"Deleted scenario preset '{name}'.")
        return deleted

    def resolve_preset(self, *, name: str, expected_version: str | None) -> PresetEntry | None:
        """
        Read one preset for launch, confirming it is the version the caller reviewed.

        The comparison is against a fresh read, because an edit landing between the
        preview and the launch is the case the check exists to catch. A registered preset
        has no version, so a caller supplying one read a document that has since been
        deleted, and the mismatch is reported rather than ignored.

        Args:
            name (str): The preset name.
            expected_version (str | None): The version the caller read, or None to accept
                whatever is available now.

        Returns:
            PresetEntry | None: The preset, or None if nothing usable is available under
            *name*.

        Raises:
            ScenarioPresetConflictError: If the available version does not match
                *expected_version*.
            ValueError: If *name* is not a legal preset name.
        """
        stored = self._get_storage().load_preset(name)
        if stored is not None:
            if expected_version is not None and expected_version != stored.version:
                raise ScenarioPresetConflictError(
                    name=name, expected_version=expected_version, actual_version=stored.version
                )
            return PresetEntry(preset=stored.preset, origin="stored", version=stored.version)

        registered = self.instances.get(name)
        if registered is None:
            return None

        if expected_version is not None:
            raise ScenarioPresetConflictError(name=name, expected_version=expected_version, actual_version=None)
        return PresetEntry(preset=registered, origin="registered")

    def check_preset(self, *, preset: ScenarioPreset) -> list[PresetIssue]:
        """
        Check one preset against this deployment's registered scenarios and converters.

        This is the only validation path: a preset is resolved by the same code the
        launch path resolves it with, so the two answers cannot drift apart.

        Results are advisory. Nothing here rejects a preset.

        Args:
            preset (ScenarioPreset): The preset to check.

        Returns:
            list[PresetIssue]: Advisory issues, empty when the preset resolves here.
        """
        from pyrit.registry.components.scenario_registry import ScenarioRegistry

        metadata = ScenarioRegistry.get_registry_singleton().get_registered_class_metadata(preset.scenario_name)
        if metadata is None:
            return [
                PresetIssue(
                    field="scenario_name",
                    message=f"Scenario '{preset.scenario_name}' is not registered in this deployment.",
                )
            ]

        return [
            *_technique_issues(preset=preset, metadata=metadata),
            *_parameter_issues(preset=preset, metadata=metadata),
            *_forbidden_baseline_issues(preset=preset, metadata=metadata),
        ]

    def _get_storage(self) -> ScenarioPresetStorage:
        """
        Return storage, constructing the default backend on first use.

        Returns:
            ScenarioPresetStorage: The configured storage backend.
        """
        if self._storage is None:
            self._storage = ScenarioPresetStorage()
        return self._storage

    def _stored_version(self, *, name: str) -> str | None:
        """
        Read a stored document's version for a warning, reporting None if storage fails.

        Args:
            name (str): The preset name.

        Returns:
            str | None: The document version, or None if nothing is stored or storage
            could not be reached.
        """
        try:
            return self._get_storage().get_preset_version(name)
        except Exception:
            logger.debug(f"Could not check storage for an existing scenario preset '{name}'.", exc_info=True)
            return None


def _technique_issues(*, preset: ScenarioPreset, metadata: ScenarioMetadata) -> list[PresetIssue]:
    """
    Report technique references this deployment cannot resolve.

    The resolution is the launch path's own, called on the scenario's technique catalog
    rather than on the name tuples projected from it, so a preset reported as broken is
    one the launch path would reject.

    Issues are grouped by kind because an operator repairing a preset wants one line
    naming every unknown technique, not one line per token.

    Args:
        preset (ScenarioPreset): The preset to check.
        metadata (ScenarioMetadata): Registry metadata for the scenario it names.

    Returns:
        list[PresetIssue]: One issue per kind of unresolvable reference, or an empty list.
    """
    if not preset.techniques:
        return []

    from pyrit.scenario.core.technique_tokens import CONVERTER_MODIFIER_PREFIX, resolve_technique_tokens

    resolution = resolve_technique_tokens(
        tokens=preset.techniques,
        technique_class=metadata.technique_class,
        scenario_name=metadata.registry_name,
    )

    descriptions: tuple[tuple[TechniqueIssueKind, str], ...] = (
        ("unknown_technique", f"Scenario '{metadata.registry_name}' does not define"),
        ("unknown_converter", "This deployment has no registered converter named"),
        ("malformed_modifier", f"Technique modifiers must use the '{CONVERTER_MODIFIER_PREFIX}' prefix; got"),
    )

    issues: list[PresetIssue] = []
    for kind, description in descriptions:
        names = sorted({issue.name for issue in resolution.issues if issue.kind == kind})
        if names:
            issues.append(PresetIssue(field="techniques", message=f"{description}: {', '.join(names)}."))
    return issues


def _parameter_issues(*, preset: ScenarioPreset, metadata: ScenarioMetadata) -> list[PresetIssue]:
    """
    Report scenario parameters this scenario will not accept.

    This runs the resolution the launch path runs rather than comparing names against
    the declaration, because a declared parameter carrying a value it cannot be coerced
    to passes a name comparison and then fails the run.

    Resolution stops at the first value it cannot coerce, so at most one coercion
    failure is reported per check; undeclared names are still reported together.

    Args:
        preset (ScenarioPreset): The preset to check.
        metadata (ScenarioMetadata): Registry metadata for the scenario it names.

    Returns:
        list[PresetIssue]: A single issue describing what will not resolve, or an empty list.
    """
    if not preset.scenario_params:
        return []

    from pyrit.registry.resolution import resolve_declared_params

    try:
        resolve_declared_params(
            declared=list(metadata.supported_parameters),
            raw_args=preset.scenario_params,
            owner=f"Scenario '{metadata.registry_name}'",
        )
    except ValueError as error:
        return [PresetIssue(field="scenario_params", message=str(error))]
    return []


def _forbidden_baseline_issues(*, preset: ScenarioPreset, metadata: ScenarioMetadata) -> list[PresetIssue]:
    """
    Report a baseline request the scenario forbids.

    Args:
        preset (ScenarioPreset): The preset to check.
        metadata (ScenarioMetadata): Registry metadata for the scenario it names.

    Returns:
        list[PresetIssue]: A single issue when the scenario forbids a requested baseline.
    """
    if not preset.include_baseline or metadata.baseline_policy != "forbidden":
        return []

    return [
        PresetIssue(
            field="include_baseline",
            message=f"Scenario '{metadata.registry_name}' does not support a baseline run.",
        )
    ]
