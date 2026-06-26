# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Identifiers module for PyRIT components."""

from pyrit.models.identifiers.atomic_attack_identifier import (
    AtomicAttackIdentifier,
)
from pyrit.models.identifiers.attack_identifier import AttackIdentifier
from pyrit.models.identifiers.attack_technique_identifier import AttackTechniqueIdentifier
from pyrit.models.identifiers.class_name_utils import (
    REGISTRY_NAME_PATTERN,
    class_name_to_snake_case,
    snake_case_to_class_name,
    validate_registry_name,
)
from pyrit.models.identifiers.component_identifier import ComponentIdentifier, Identifiable, config_hash
from pyrit.models.identifiers.converter_identifier import ConverterIdentifier
from pyrit.models.identifiers.evaluation_identifier import (
    TARGET_EVAL_PARAM_FALLBACKS,
    TARGET_EVAL_PARAMS,
    AtomicAttackEvaluationIdentifier,
    ChildEvalRule,
    EvaluationIdentifier,
    ObjectiveTargetEvaluationIdentifier,
    ScorerEvaluationIdentifier,
    compute_eval_hash,
    compute_inner_attack_eval_hash,
    derive_eval_config,
)
from pyrit.models.identifiers.evaluation_markers import EvalMarker, Evaluate, Exclude, Include, Unwrap
from pyrit.models.identifiers.identifier_filters import IdentifierFilter, IdentifierType
from pyrit.models.identifiers.param_markers import Param, ParamMarker
from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier
from pyrit.models.identifiers.seed_identifier import SeedIdentifier
from pyrit.models.identifiers.target_identifier import TargetIdentifier

__all__ = [
    "AtomicAttackEvaluationIdentifier",
    "AtomicAttackIdentifier",
    "AttackIdentifier",
    "AttackTechniqueIdentifier",
    "ChildEvalRule",
    "class_name_to_snake_case",
    "ComponentIdentifier",
    "compute_eval_hash",
    "compute_inner_attack_eval_hash",
    "ConverterIdentifier",
    "derive_eval_config",
    "EvalMarker",
    "Evaluate",
    "EvaluationIdentifier",
    "Exclude",
    "Identifiable",
    "Include",
    "ObjectiveTargetEvaluationIdentifier",
    "REGISTRY_NAME_PATTERN",
    "Param",
    "ParamMarker",
    "ScorerEvaluationIdentifier",
    "ScorerIdentifier",
    "SeedIdentifier",
    "snake_case_to_class_name",
    "TARGET_EVAL_PARAM_FALLBACKS",
    "TARGET_EVAL_PARAMS",
    "TargetIdentifier",
    "Unwrap",
    "validate_registry_name",
    "config_hash",
    "IdentifierFilter",
    "IdentifierType",
]
