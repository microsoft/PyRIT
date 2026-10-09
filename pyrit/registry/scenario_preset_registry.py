# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registry owning scenario preset storage, validation, and version-checked resolution."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from pyrit.models.catalog.scenario_preset import PresetIssue, ScenarioPreset, StoredPreset
from pyrit.registry.scenario_preset_storage import ScenarioPresetConflictError, ScenarioPresetStorage

if TYPE_CHECKING:
    from pyrit.registry.components.scenario_registry import ScenarioMetadata

logger = logging.getLogger(__name__)


class ScenarioPresetRegistry:
    """
    The single owner of scenario preset storage, validation, and resolution.

    A preset is inert JSON rather than a component class, so this registry deliberately
    does not extend ``Registry``. ``pyrit.registry.components`` is documented as the home
    for class catalogs that build instances from classes; a catalog of documents shares
    the vocabulary but none of the machinery, and inheriting it would leave the build,
    identifier, and discovery surface permanently unimplemented.

    **Storage is read through on every call. This registry is not an instance cache.**
    Every lookup goes to storage and reflects what is stored right now. That is a
    deliberate decision, not an oversight, and these are the reasons:

    - The version that guards every write is ``sha256`` of the stored bytes, chosen so
      that "an edit made by hand or by another tool invalidates it too". A version
      computed from cached bytes can only describe what this process last read or wrote,
      so a cache defeats the mechanism by construction rather than merely aging it.
    - Multiple workers and multiple replicas are a supported topology; the backend
      probes ``WEB_CONCURRENCY``, ``UVICORN_WORKERS``, ``PYRIT_API_WORKERS``, and
      ``PYRIT_REPLICAS`` precisely because more than one process may be serving. Each
      process would hold its own cache, so a preset edited through one worker would keep
      launching at its old configuration through another.
    - ``resolve_preset`` compares a client-supplied ``expected_version`` against the
      stored version to confirm the operator is launching the configuration they
      reviewed. Served from a cache, that comparison tests the cache against itself and
      always passes, and PyRIT would launch a configuration nobody confirmed.
    - ``get_preset_version`` exists to recover a document that cannot be parsed, and
      ``list_presets`` deliberately omits those documents. Neither is reachable from a
      map of successfully parsed instances.

    A caching preset registry was previously removed for these reasons. Reintroducing
    one would reintroduce the same defects, so it is not an optimization left for later.
    """

    _singleton: ScenarioPresetRegistry | None = None
    _singleton_lock = threading.Lock()

    def __init__(self, *, storage: ScenarioPresetStorage | None = None) -> None:
        """
        Initialize the registry without touching storage.

        Args:
            storage (ScenarioPresetStorage | None): Storage to read and write presets
                through. Defaults to the standard directory, constructed on first use so
                that merely importing the registry never creates a directory.
        """
        self._storage = storage

    @classmethod
    def get_registry_singleton(cls) -> ScenarioPresetRegistry:
        """
        Get the process-wide preset registry.

        Returns:
            ScenarioPresetRegistry: The singleton registry.
        """
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

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

    def validate_stored_presets(self) -> int:
        """
        Read stored presets once so unusable documents are reported at startup.

        Without this pass a preset that cannot be parsed stays invisible until someone
        opens the library, because ``list_presets`` skips it by design. Reading the
        source at boot moves every such failure into the startup log, named and with its
        reason, which is where an operator looks when a preset they wrote is missing.

        This validates each document against the full ``ScenarioPreset`` schema. It
        deliberately does **not** check whether a preset resolves against this
        deployment's scenarios, converters, and parameters, which is what
        ``check_preset`` does, for three independent reasons:

        - Reading scenario metadata builds it for the whole catalog, and building it
          instantiates every registered scenario class. That is unbounded work on the
          boot path, proportional to the number of scenarios rather than to the number
          of presets.
        - That build requires resolved environment variables, an initialized
          ``CentralMemory``, and a populated ``AttackTechniqueRegistry``. None of them is
          established when the preset source is configured, so the pass would not merely
          be slow, it would raise.
        - Initializers that register scenarios have not run yet at that point, so every
          preset naming a scenario they contribute would be reported as unresolvable.
          Reporting a healthy preset as broken is worse than reporting nothing.

        Failures are logged rather than raised. A library of presets is operator data,
        and unusable operator data must never stop the service that would let the
        operator repair it.

        Returns:
            int: The number of presets that loaded cleanly.
        """
        try:
            presets = self._get_storage().list_presets()
        except Exception:
            logger.exception("Could not read scenario presets at startup; the preset library may be unavailable.")
            return 0

        logger.info(f"Loaded {len(presets)} scenario preset(s) from {self.source}.")
        return len(presets)

    def list_presets(self) -> list[StoredPreset]:
        """
        Read every stored preset, skipping any that cannot be parsed.

        Returns:
            list[StoredPreset]: Stored presets and their versions, sorted by preset name.
        """
        stored = self._get_storage().list_presets()
        return [stored[name] for name in sorted(stored)]

    def get_preset(self, *, name: str) -> StoredPreset | None:
        """
        Read one stored preset together with the version needed to save an edit to it.

        Args:
            name (str): The preset name.

        Returns:
            StoredPreset | None: The preset and its version, or None if it is absent or
            malformed.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        return self._get_storage().load_preset(name)

    def get_preset_version(self, *, name: str) -> str | None:
        """
        Read the version of one stored document without parsing it.

        A document that cannot be parsed is otherwise unreachable, so exposing its
        version lets a caller offer to overwrite the broken file instead of leaving the
        name permanently unusable.

        Args:
            name (str): The preset name.

        Returns:
            str | None: The document version, or None if no document is stored.

        Raises:
            ValueError: If *name* is not a legal preset name.
        """
        return self._get_storage().get_preset_version(name)

    def save_preset(self, *, preset: ScenarioPreset, expected_version: str | None) -> StoredPreset:
        """
        Persist one preset, refusing a write that would discard a concurrent edit.

        References this deployment cannot resolve are not rejected here. A preset
        authored against one deployment must remain savable on another, so unresolvable
        references are reported by ``check_preset`` instead.

        Args:
            preset (ScenarioPreset): The preset to persist.
            expected_version (str | None): None to create a preset that must not already
                exist, or the version returned when the edited preset was read.

        Returns:
            StoredPreset: The persisted preset and its new version.

        Raises:
            ScenarioPresetConflictError: If the stored version does not match *expected_version*.
            ValueError: If the preset name is not a legal preset name.
        """
        stored = self._get_storage().save_preset(preset=preset, expected_version=expected_version)
        logger.info(f"Saved scenario preset '{preset.name}'.")
        return stored

    def delete_preset(self, *, name: str) -> bool:
        """
        Delete one stored preset, reporting whether it was there to delete.

        The answer comes from the delete itself, so it stays correct under concurrency:
        when two callers race to remove the same preset, exactly one is told it removed
        it. An existence check followed by a delete would tell both that they did, and
        would report a preset created between the two steps as missing.

        Args:
            name (str): The preset name.

        Returns:
            bool: True if a document was stored under *name*, False if nothing was.

        Raises:
            ValueError: If *name* is not a legal preset name.
            TimeoutError: If a local delete could not acquire the document lock.
        """
        deleted = self._get_storage().delete_preset(name)
        if deleted:
            logger.info(f"Deleted scenario preset '{name}'.")
        return deleted

    def resolve_preset(self, *, name: str, expected_version: str | None) -> StoredPreset | None:
        """
        Read one preset for launch, confirming it is the version the caller reviewed.

        The comparison is against a fresh read rather than anything this process is
        holding. An edit landing between the preview and the launch is the case the
        check exists to catch, so the stored document is the only thing it can be
        compared against.

        Args:
            name (str): The preset name.
            expected_version (str | None): The version the caller read, or None to accept
                whatever is stored now.

        Returns:
            StoredPreset | None: The preset and its version, or None if nothing usable is
            stored under *name*.

        Raises:
            ScenarioPresetConflictError: If the stored version does not match *expected_version*.
            ValueError: If *name* is not a legal preset name.
        """
        stored = self._get_storage().load_preset(name)
        if stored is None:
            return None

        if expected_version is not None and expected_version != stored.version:
            raise ScenarioPresetConflictError(
                name=name, expected_version=expected_version, actual_version=stored.version
            )
        return stored

    def check_preset(self, *, preset: ScenarioPreset) -> list[PresetIssue]:
        """
        Check one preset against this deployment's registered scenarios and converters.

        This is the only validation path. A preset is checked with the same token grammar
        and the same registries the launch path uses, so a preset reported as runnable
        runs and a preset reported as broken is broken. Re-deriving any part of that
        check elsewhere would let the two answers drift apart, which is how an operator
        ends up being told a working preset is invalid.

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
            *_unknown_technique_issues(preset=preset, metadata=metadata),
            *_unknown_parameter_issues(preset=preset, metadata=metadata),
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


def _technique_issues(*, names: list[str], description: str) -> list[PresetIssue]:
    """
    Build the at-most-one issue describing a kind of unresolvable technique reference.

    Args:
        names (list[str]): The offending names, possibly with repeats.
        description (str): What is wrong with them, phrased to read before a name list.

    Returns:
        list[PresetIssue]: A single issue, or an empty list when nothing was offending.
    """
    if not names:
        return []
    return [PresetIssue(field="techniques", message=f"{description}: {', '.join(sorted(set(names)))}.")]


def _unknown_technique_issues(*, preset: ScenarioPreset, metadata: ScenarioMetadata) -> list[PresetIssue]:
    """
    Report techniques and converter modifiers this deployment cannot resolve.

    Tokens are parsed with the same grammar the launch path uses, because comparing a
    whole token against the scenario's technique names would report a runnable preset
    such as ``role_play:converter.translation_spanish`` as broken.

    Args:
        preset (ScenarioPreset): The preset to check.
        metadata (ScenarioMetadata): Registry metadata for the scenario it names.

    Returns:
        list[PresetIssue]: One issue per kind of unresolvable reference, or an empty list.
    """
    if not preset.techniques:
        return []

    from pyrit.registry.components.converter_registry import ConverterRegistry
    from pyrit.scenario.core._technique_tokens import (
        CONVERTER_MODIFIER_PREFIX,
        converter_name_from_modifier,
        parse_technique_token,
    )

    known = set(metadata.all_techniques) | set(metadata.aggregate_techniques)
    registered_converters = ConverterRegistry.get_registry_singleton().instances
    unknown_techniques: list[str] = []
    unknown_converters: list[str] = []
    malformed_modifiers: list[str] = []

    for token in preset.techniques:
        base_name, modifiers = parse_technique_token(token)
        if base_name not in known:
            unknown_techniques.append(base_name)
        for modifier in modifiers:
            converter_name = converter_name_from_modifier(modifier)
            if converter_name is None:
                malformed_modifiers.append(modifier)
            elif registered_converters.get(converter_name) is None:
                unknown_converters.append(converter_name)

    return [
        *_technique_issues(
            names=unknown_techniques, description=f"Scenario '{metadata.registry_name}' does not define"
        ),
        *_technique_issues(names=unknown_converters, description="This deployment has no registered converter named"),
        *_technique_issues(
            names=malformed_modifiers,
            description=f"Technique modifiers must use the '{CONVERTER_MODIFIER_PREFIX}' prefix; got",
        ),
    ]


def _unknown_parameter_issues(*, preset: ScenarioPreset, metadata: ScenarioMetadata) -> list[PresetIssue]:
    """
    Report scenario parameters the scenario does not declare.

    Args:
        preset (ScenarioPreset): The preset to check.
        metadata (ScenarioMetadata): Registry metadata for the scenario it names.

    Returns:
        list[PresetIssue]: One issue naming every undeclared parameter, or an empty list.
    """
    if not preset.scenario_params:
        return []

    declared = {parameter.name for parameter in metadata.supported_parameters}
    unknown = [name for name in preset.scenario_params if name not in declared]
    if not unknown:
        return []

    return [
        PresetIssue(
            field="scenario_params",
            message=f"Scenario '{metadata.registry_name}' does not declare: {', '.join(sorted(unknown))}.",
        )
    ]


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
