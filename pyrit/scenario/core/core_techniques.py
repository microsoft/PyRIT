# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared AttackTechniqueFactory builders for common attack techniques.

These functions return ``AttackTechniqueFactory`` instances that can be
used by any scenario.  Each factory captures technique-specific defaults
at registration time; runtime parameters (``objective_target``) and
optional overrides (``attack_scoring_config_override``, etc.) are
provided when ``factory.create()`` is called during scenario execution.

Scenarios expose available factories via the overridable
``Scenario.get_attack_technique_factories()`` classmethod.
"""

from pyrit.executor.attack import (
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    RolePlayPaths,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


def prompt_sending_factory() -> AttackTechniqueFactory:
    """Create a factory for ``PromptSendingAttack`` (single-turn, no converter)."""
    return AttackTechniqueFactory(attack_class=PromptSendingAttack)


def role_play_factory(
    *,
    role_play_path: str | None = None,
) -> AttackTechniqueFactory:
    """
    Create a factory for ``RolePlayAttack`` (single-turn with role-play converter).

    Args:
        role_play_path: Path to the role-play YAML definition.
            Defaults to ``RolePlayPaths.MOVIE_SCRIPT``.
    """
    kwargs: dict[str, object] = {
        "role_play_definition_path": role_play_path or RolePlayPaths.MOVIE_SCRIPT.value,
    }
    return AttackTechniqueFactory(attack_class=RolePlayAttack, attack_kwargs=kwargs)


def many_shot_factory() -> AttackTechniqueFactory:
    """Create a factory for ``ManyShotJailbreakAttack`` (multi-turn)."""
    return AttackTechniqueFactory(attack_class=ManyShotJailbreakAttack)


def tap_factory() -> AttackTechniqueFactory:
    """Create a factory for ``TreeOfAttacksWithPruningAttack`` (multi-turn)."""
    return AttackTechniqueFactory(attack_class=TreeOfAttacksWithPruningAttack)
