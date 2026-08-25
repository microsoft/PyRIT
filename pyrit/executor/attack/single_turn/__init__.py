# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Singe turn attack strategies module."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.attack.single_turn.many_shot_jailbreak import ManyShotJailbreakAttack
    from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
    from pyrit.executor.attack.single_turn.single_turn_attack_strategy import (
        SingleTurnAttackContext,
        SingleTurnAttackStrategy,
    )
    from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "SingleTurnAttackStrategy": "pyrit.executor.attack.single_turn.single_turn_attack_strategy",
    "SingleTurnAttackContext": "pyrit.executor.attack.single_turn.single_turn_attack_strategy",
    "PromptSendingAttack": "pyrit.executor.attack.single_turn.prompt_sending",
    "ManyShotJailbreakAttack": "pyrit.executor.attack.single_turn.many_shot_jailbreak",
    "SkeletonKeyAttack": "pyrit.executor.attack.single_turn.skeleton_key",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
