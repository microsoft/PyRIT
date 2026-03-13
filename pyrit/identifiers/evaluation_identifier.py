# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Evaluation identity and eval-hash computation.

This module provides:

* ``ChildEvalRule`` — per-child configuration for eval-hash filtering.
* ``_build_eval_dict`` — builds a filtered dict for eval-hash computation.
* ``compute_eval_hash`` — free function that computes a behavioral equivalence
  hash from a ``ComponentIdentifier``.
* ``EvaluationIdentifier`` — abstract base that wraps a ``ComponentIdentifier``
  with domain-specific eval-hash configuration.  Concrete subclasses declare
  per-child rules via a single ``CHILD_EVAL_RULES`` ClassVar.
* ``ScorerEvaluationIdentifier`` — scorer-domain concrete subclass.
* ``AtomicAttackEvaluationIdentifier`` — attack-domain concrete subclass.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional

from pyrit.identifiers.component_identifier import ComponentIdentifier, config_hash


@dataclass(frozen=True)
class ChildEvalRule:
    """
    Per-child configuration for eval-hash computation.

    Controls how a specific named child is treated when building the
    evaluation hash:

    * ``exclude`` — if ``True``, drop this child entirely from the hash.
    * ``included_params`` — if set, only include these param keys for this
      child (and its recursive descendants). ``None`` means all params.
    * ``included_item_values`` — for list-valued children, only include items
      whose ``params`` match **all** specified key-value pairs. ``None``
      means include all items.
    """

    exclude: bool = False
    included_params: Optional[frozenset[str]] = None
    included_item_values: Optional[dict[str, Any]] = field(default=None)


def _build_eval_dict(
    identifier: ComponentIdentifier,
    *,
    child_eval_rules: dict[str, ChildEvalRule],
    _included_params: Optional[frozenset[str]] = None,
) -> dict[str, Any]:
    """
    Build a filtered dictionary for eval-hash computation.

    Walks the ``ComponentIdentifier`` tree and applies per-child rules from
    ``child_eval_rules``.  Children not listed in the rules receive full
    recursive treatment (no filtering).

    Args:
        identifier (ComponentIdentifier): The component identity to process.
        child_eval_rules (dict[str, ChildEvalRule]): Per-child eval rules.
            Keys are child names; values describe how each child is filtered.
        _included_params (Optional[frozenset[str]]): Internal. If set, only
            include params whose keys are in this frozenset. Passed down from
            a parent rule's ``included_params``.

    Returns:
        dict[str, Any]: The filtered dictionary suitable for hashing.
    """
    eval_dict: dict[str, Any] = {
        ComponentIdentifier.KEY_CLASS_NAME: identifier.class_name,
        ComponentIdentifier.KEY_CLASS_MODULE: identifier.class_module,
    }

    eval_dict.update(
        {
            key: value
            for key, value in sorted(identifier.params.items())
            if value is not None and (_included_params is None or key in _included_params)
        }
    )

    if identifier.children:
        eval_children: dict[str, Any] = {}
        for name in sorted(identifier.children):
            rule = child_eval_rules.get(name)

            if rule and rule.exclude:
                continue

            child_list = identifier.get_child_list(name)

            # Filter list items by param-value match (e.g., only is_general_technique=True seeds)
            if rule and rule.included_item_values:
                required = rule.included_item_values
                child_list = [c for c in child_list if all(c.params.get(k) == v for k, v in required.items())]

            # For children with a rule, apply included_params; otherwise None → all params kept.
            child_included_params = rule.included_params if rule else None
            hashes = [
                config_hash(
                    _build_eval_dict(
                        c,
                        child_eval_rules=child_eval_rules,
                        _included_params=child_included_params,
                    )
                )
                for c in child_list
            ]
            eval_children[name] = hashes[0] if len(hashes) == 1 else hashes
        if eval_children:
            eval_dict["children"] = eval_children

    return eval_dict


def compute_eval_hash(
    identifier: ComponentIdentifier,
    *,
    child_eval_rules: dict[str, ChildEvalRule],
) -> str:
    """
    Compute a behavioral equivalence hash for evaluation grouping.

    Unlike ``ComponentIdentifier.hash`` (which includes all params of self and
    children), the eval hash applies per-child rules to strip operational params
    (like endpoint, max_requests_per_minute), exclude children entirely, or
    filter list items.  This ensures the same logical configuration on different
    deployments produces the same eval hash.

    Children not listed in ``child_eval_rules`` receive full recursive treatment.

    When ``child_eval_rules`` is empty, no filtering occurs and the result
    equals ``identifier.hash``.

    Args:
        identifier (ComponentIdentifier): The component identity to compute
            the hash for.
        child_eval_rules (dict[str, ChildEvalRule]): Per-child eval rules.

    Returns:
        str: A hex-encoded SHA256 hash suitable for eval registry keying.
    """
    if not child_eval_rules:
        return identifier.hash

    eval_dict = _build_eval_dict(
        identifier,
        child_eval_rules=child_eval_rules,
    )
    return config_hash(eval_dict)


class EvaluationIdentifier(ABC):
    """
    Wraps a ``ComponentIdentifier`` with domain-specific eval-hash configuration.

    Subclasses set ``CHILD_EVAL_RULES`` — a mapping of child names to
    ``ChildEvalRule`` instances that control how each child is treated during
    eval-hash computation.  Children not listed receive full recursive treatment.

    The concrete ``eval_hash`` property delegates to the module-level
    ``compute_eval_hash`` free function.
    """

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]]

    def __init__(self, identifier: ComponentIdentifier) -> None:
        """Wrap a ComponentIdentifier and eagerly compute its eval hash."""
        self._identifier = identifier
        self._eval_hash = compute_eval_hash(
            identifier,
            child_eval_rules=self.CHILD_EVAL_RULES,
        )

    @property
    def identifier(self) -> ComponentIdentifier:
        """The underlying component identity."""
        return self._identifier

    @property
    def eval_hash(self) -> str:
        """Behavioral equivalence hash for evaluation grouping."""
        return self._eval_hash


class ScorerEvaluationIdentifier(EvaluationIdentifier):
    """
    Evaluation identity for scorers.

    The ``prompt_target`` child is filtered to behavioral params only
    (``model_name``, ``temperature``, ``top_p``), so the same scorer
    configuration on different deployments produces the same eval hash.
    """

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {
        "prompt_target": ChildEvalRule(
            included_params=frozenset({"model_name", "temperature", "top_p"}),
        ),
    }


class AtomicAttackEvaluationIdentifier(EvaluationIdentifier):
    """
    Evaluation identity for atomic attacks.

    Per-child rules:

    * ``objective_target`` — include only ``temperature``.
    * ``adversarial_chat`` — include ``model_name``, ``temperature``, ``top_p``.
    * ``objective_scorer`` — excluded entirely.
    * ``seeds`` — include only items where ``is_general_technique=True``.

    Non-target children (e.g., ``request_converters``, ``response_converters``)
    receive full recursive eval treatment, meaning they fully contribute to
    the hash.
    """

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {
        "objective_target": ChildEvalRule(
            included_params=frozenset({"temperature"}),
        ),
        "adversarial_chat": ChildEvalRule(
            included_params=frozenset({"model_name", "temperature", "top_p"}),
        ),
        "objective_scorer": ChildEvalRule(exclude=True),
        "seeds": ChildEvalRule(
            included_item_values={"is_general_technique": True},
        ),
    }
