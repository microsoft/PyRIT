# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Identifiers module for PyRIT components."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.identifiers.atomic_attack_identifier import AtomicAttackIdentifier
    from pyrit.models.identifiers.attack_identifier import AttackIdentifier
    from pyrit.models.identifiers.attack_technique_identifier import AttackTechniqueIdentifier
    from pyrit.models.identifiers.class_name_utils import (
        REGISTRY_NAME_PATTERN,
        class_name_to_snake_case,
        snake_case_to_class_name,
        validate_registry_name,
    )
    from pyrit.models.identifiers.component_identifier import (
        ComponentIdentifier,
        Identifiable,
        JSONValue,
        config_hash,
    )
    from pyrit.models.identifiers.converter_identifier import ConverterIdentifier
    from pyrit.models.identifiers.evaluation_identifier import (
        TARGET_EVAL_PARAM_FALLBACKS,
        TARGET_EVAL_PARAMS,
        AtomicAttackEvaluationIdentifier,
        ChildEvalRule,
        EvaluationIdentifier,
        ObjectiveTargetEvaluationIdentifier,
        ScenarioEvaluationIdentifier,
        ScorerEvaluationIdentifier,
        compute_eval_hash,
        compute_inner_attack_eval_hash,
        derive_eval_config,
    )
    from pyrit.models.identifiers.evaluation_markers import EvalMarker, Evaluate, Exclude, Include, Unwrap
    from pyrit.models.identifiers.identifier_filters import IdentifierFilter, IdentifierType
    from pyrit.models.identifiers.identifier_projection import project_behavioral_identity
    from pyrit.models.identifiers.param_markers import Param, ParamMarker
    from pyrit.models.identifiers.scenario_identifier import ScenarioIdentifier
    from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier
    from pyrit.models.identifiers.seed_identifier import SeedIdentifier, compute_seed_group_hash
    from pyrit.models.identifiers.target_identifier import TargetIdentifier

_LAZY_EXPORTS: dict[str, str] = {
    "AtomicAttackEvaluationIdentifier": "pyrit.models.identifiers.evaluation_identifier",
    "AtomicAttackIdentifier": "pyrit.models.identifiers.atomic_attack_identifier",
    "AttackIdentifier": "pyrit.models.identifiers.attack_identifier",
    "AttackTechniqueIdentifier": "pyrit.models.identifiers.attack_technique_identifier",
    "ChildEvalRule": "pyrit.models.identifiers.evaluation_identifier",
    "class_name_to_snake_case": "pyrit.models.identifiers.class_name_utils",
    "ComponentIdentifier": "pyrit.models.identifiers.component_identifier",
    "compute_eval_hash": "pyrit.models.identifiers.evaluation_identifier",
    "compute_inner_attack_eval_hash": "pyrit.models.identifiers.evaluation_identifier",
    "ConverterIdentifier": "pyrit.models.identifiers.converter_identifier",
    "derive_eval_config": "pyrit.models.identifiers.evaluation_identifier",
    "EvalMarker": "pyrit.models.identifiers.evaluation_markers",
    "Evaluate": "pyrit.models.identifiers.evaluation_markers",
    "EvaluationIdentifier": "pyrit.models.identifiers.evaluation_identifier",
    "Exclude": "pyrit.models.identifiers.evaluation_markers",
    "Identifiable": "pyrit.models.identifiers.component_identifier",
    "Include": "pyrit.models.identifiers.evaluation_markers",
    "JSONValue": "pyrit.models.identifiers.component_identifier",
    "ObjectiveTargetEvaluationIdentifier": "pyrit.models.identifiers.evaluation_identifier",
    "REGISTRY_NAME_PATTERN": "pyrit.models.identifiers.class_name_utils",
    "Param": "pyrit.models.identifiers.param_markers",
    "ParamMarker": "pyrit.models.identifiers.param_markers",
    "ScenarioEvaluationIdentifier": "pyrit.models.identifiers.evaluation_identifier",
    "ScorerEvaluationIdentifier": "pyrit.models.identifiers.evaluation_identifier",
    "ScorerIdentifier": "pyrit.models.identifiers.scorer_identifier",
    "ScenarioIdentifier": "pyrit.models.identifiers.scenario_identifier",
    "SeedIdentifier": "pyrit.models.identifiers.seed_identifier",
    "compute_seed_group_hash": "pyrit.models.identifiers.seed_identifier",
    "snake_case_to_class_name": "pyrit.models.identifiers.class_name_utils",
    "TARGET_EVAL_PARAM_FALLBACKS": "pyrit.models.identifiers.evaluation_identifier",
    "TARGET_EVAL_PARAMS": "pyrit.models.identifiers.evaluation_identifier",
    "TargetIdentifier": "pyrit.models.identifiers.target_identifier",
    "Unwrap": "pyrit.models.identifiers.evaluation_markers",
    "validate_registry_name": "pyrit.models.identifiers.class_name_utils",
    "config_hash": "pyrit.models.identifiers.component_identifier",
    "IdentifierFilter": "pyrit.models.identifiers.identifier_filters",
    "IdentifierType": "pyrit.models.identifiers.identifier_filters",
    "project_behavioral_identity": "pyrit.models.identifiers.identifier_projection",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public identifier export on first access.

    Args:
        name (str): The requested public name.

    Returns:
        object: The resolved export.
    """
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    """Return package attributes, including unresolved exports."""
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
