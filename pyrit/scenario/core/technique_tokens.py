# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Technique tokens: the grammar scenario runs and presets are written in, and its resolution.

A token is a technique name optionally followed by colon-separated modifiers, as in
``role_play:converter.translation_spanish``. Both the grammar and the resolution live
here, in the package that owns the technique catalog, so that every caller reading a
stored token reaches the same answer. Splitting them produced a validator that compared
a whole token against the scenario's technique names and reported a token the launch
path resolves successfully as broken.

``resolve_technique_tokens`` collects problems instead of raising on the first one. A
launch wants to stop at the first bad token; a preset check wants to show the operator
everything that is wrong at once. Collecting serves both — a caller that wants the
launch behaviour raises on the first issue — whereas raising serves only one, which is
what pushed the second caller into writing its own weaker check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.converter import Converter
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique

CONVERTER_MODIFIER_PREFIX = "converter."

TechniqueIssueKind = Literal["unknown_technique", "unknown_converter", "malformed_modifier"]


class TechniqueToken(NamedTuple):
    """One parsed technique token."""

    base_name: str
    modifiers: tuple[str, ...]


def parse_technique_token(token: str) -> TechniqueToken:
    """
    Split one technique token into its technique name and its modifiers.

    Args:
        token (str): The token as written in a run request or a stored preset.

    Returns:
        TechniqueToken: The technique name and the modifiers that follow it, in token order.
    """
    base_name, _, remainder = token.partition(":")
    modifiers = tuple(modifier for modifier in remainder.split(":") if modifier)
    return TechniqueToken(base_name=base_name, modifiers=modifiers)


def converter_name_from_modifier(modifier: str) -> str | None:
    """
    Read the converter name out of a modifier.

    Args:
        modifier (str): One modifier from a technique token.

    Returns:
        str | None: The converter name, or None when the modifier is not a converter modifier.
    """
    if not modifier.startswith(CONVERTER_MODIFIER_PREFIX):
        return None
    return modifier[len(CONVERTER_MODIFIER_PREFIX) :]


@dataclass(frozen=True)
class TechniqueTokenIssue:
    """One reference in a technique token that this deployment cannot resolve."""

    kind: TechniqueIssueKind
    name: str
    message: str


@dataclass(frozen=True)
class TechniqueTokenResolution:
    """Everything a set of technique tokens resolved to, plus what did not resolve."""

    techniques: list[Any] = field(default_factory=list)
    technique_converters: dict[str, list[Converter]] = field(default_factory=dict)
    issues: list[TechniqueTokenIssue] = field(default_factory=list)


def resolve_technique_tokens(
    *,
    tokens: Sequence[str],
    technique_class: type[ScenarioTechnique],
    scenario_name: str,
) -> TechniqueTokenResolution:
    """
    Resolve technique tokens into technique members and their additive converters.

    Nothing is raised. A token that cannot be resolved contributes an issue and no
    technique, so a caller that must stop at the first problem raises on
    ``issues[0]`` and a caller that reports problems shows them all.

    Args:
        tokens (Sequence[str]): Tokens as written in a run request or a stored preset.
        technique_class (type[ScenarioTechnique]): The scenario's technique catalog.
        scenario_name (str): The scenario name, used only in messages.

    Returns:
        TechniqueTokenResolution: Resolved technique members, converters keyed by
        concrete technique name, and the references that did not resolve.
    """
    resolution = TechniqueTokenResolution()
    converter_instances: Any = None

    for token in tokens:
        base_name, modifiers = parse_technique_token(token)

        technique: ScenarioTechnique | None = None
        try:
            technique = technique_class(base_name)
        except ValueError:
            available = ", ".join(member.value for member in technique_class)
            resolution.issues.append(
                TechniqueTokenIssue(
                    kind="unknown_technique",
                    name=base_name,
                    message=(
                        f"Technique '{base_name}' not found for scenario '{scenario_name}'. Available: {available}"
                    ),
                )
            )

        converters: list[Converter] = []
        for modifier in modifiers:
            # The registry is reached only once a token actually carries a modifier, so
            # resolving plain techniques never constructs the converter registry.
            if converter_instances is None:
                from pyrit.registry import ConverterRegistry

                converter_instances = ConverterRegistry.get_registry_singleton().instances

            converter_name = converter_name_from_modifier(modifier)
            if converter_name is None:
                resolution.issues.append(
                    TechniqueTokenIssue(
                        kind="malformed_modifier",
                        name=modifier,
                        message=(
                            f"Unknown technique modifier '{modifier}' in '{token}'. "
                            f"Supported modifiers must use the '{CONVERTER_MODIFIER_PREFIX}' prefix "
                            f"(e.g. '{CONVERTER_MODIFIER_PREFIX}translation_spanish')."
                        ),
                    )
                )
                continue

            converter = converter_instances.get(converter_name)
            if converter is None:
                available_converters = converter_instances.get_names()
                available_text = ", ".join(available_converters) if available_converters else "(none registered)"
                resolution.issues.append(
                    TechniqueTokenIssue(
                        kind="unknown_converter",
                        name=converter_name,
                        message=(
                            f"Converter '{converter_name}' in '{token}' is not a registered converter "
                            f"instance. Available converters: {available_text}"
                        ),
                    )
                )
                continue

            converters.append(converter)

        if technique is None:
            continue

        resolution.techniques.append(technique)
        for concrete in technique_class.expand({technique}) if converters else ():
            resolution.technique_converters.setdefault(concrete.value, []).extend(converters)

    return resolution
