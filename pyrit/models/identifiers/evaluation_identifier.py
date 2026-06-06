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
  per-child rules via ``CHILD_EVAL_RULES`` and (optionally) a root-level
  ``OWN_RULE`` for leaf entities whose own params need filtering.
* ``ScorerEvaluationIdentifier`` — scorer-domain concrete subclass.
* ``AtomicAttackEvaluationIdentifier`` — attack-domain concrete subclass.
* ``ObjectiveTargetEvaluationIdentifier`` — leaf-target subclass used by the
  analytics layer to key cached results by behavioral target configuration.
"""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier, config_hash

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_strategy import AttackStrategy

# Behavioral params that define model output quality for scoring.
TARGET_EVAL_PARAMS: frozenset[str] = frozenset({"underlying_model_name", "temperature", "top_p"})
TARGET_EVAL_PARAM_FALLBACKS: dict[str, str] = {"underlying_model_name": "model_name"}


class ChildEvalRule(BaseModel):
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
    * ``param_fallbacks`` — maps a primary param key to a fallback key.
      When the primary key's value is falsy (empty string, ``None``, or
      missing), the fallback key's value from the component's raw params
      is used instead. This keeps fallback logic in the eval layer without
      changing full component hashes.  ``None`` means no fallbacks.
    * ``inner_child_name`` — if set, names the sub-child to "look through"
      when the child being processed is a wrapper component (e.g.,
      ``RoundRobinTarget``). The first item of that sub-child list is
      substituted before applying param filtering, so the eval hash
      matches the unwrapped inner target. ``None`` means no unwrapping.
    """

    model_config = ConfigDict(frozen=True)

    exclude: bool = False
    included_params: frozenset[str] | None = None
    included_item_values: dict[str, Any] | None = Field(default=None)
    param_fallbacks: dict[str, str] | None = Field(default=None)
    inner_child_name: str | None = Field(default=None)


def _build_eval_dict(
    identifier: ComponentIdentifier,
    *,
    child_eval_rules: dict[str, ChildEvalRule],
    _included_params: frozenset[str] | None = None,
    _param_fallbacks: dict[str, str] | None = None,
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
        _included_params (frozenset[str] | None): Internal. If set, only
            include params whose keys are in this frozenset. Passed down from
            a parent rule's ``included_params``.
        _param_fallbacks (dict[str, str] | None): Internal. Maps a primary
            param key to a fallback key. When the primary value is falsy,
            the fallback key's value from raw params is used instead.
            Passed down from a parent rule's ``param_fallbacks``.

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

    # Apply fallbacks: when a primary param is missing or empty string,
    # substitute with the fallback key's value from the raw params.
    if _param_fallbacks:
        for primary_key, fallback_key in _param_fallbacks.items():
            primary_value = eval_dict.get(primary_key)
            if primary_value is None or primary_value == "":
                fallback_value = identifier.params.get(fallback_key)
                if fallback_value is not None and fallback_value != "":
                    eval_dict[primary_key] = fallback_value

    if identifier.children:
        eval_children: dict[str, Any] = {}
        for name in sorted(identifier.children):
            rule = child_eval_rules.get(name)

            if rule and rule.exclude:
                continue

            child_list = identifier.get_child_list(name)

            # Inner child lookup: if the rule names a sub-child (e.g., "targets"),
            # substitute the first item of that sub-child list. This lets wrapper
            # components (e.g., RoundRobinTarget) be "seen through".
            if rule and rule.inner_child_name:
                unwrapped: list[ComponentIdentifier] = []
                for c in child_list:
                    inner = c.get_child_list(rule.inner_child_name)
                    if inner:
                        unwrapped.append(inner[0])
                    else:
                        unwrapped.append(c)
                child_list = unwrapped

            # Filter list items by param-value match (e.g., only is_general_technique=True seeds)
            if rule and rule.included_item_values:
                required = rule.included_item_values
                child_list = [c for c in child_list if all(c.params.get(k) == v for k, v in required.items())]

            # For children with a rule, apply included_params and param_fallbacks;
            # otherwise None → all params kept, no fallbacks.
            child_included_params = rule.included_params if rule else None
            child_param_fallbacks = rule.param_fallbacks if rule else None
            hashes = [
                config_hash(
                    _build_eval_dict(
                        c,
                        child_eval_rules=child_eval_rules,
                        _included_params=child_included_params,
                        _param_fallbacks=child_param_fallbacks,
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
    own_rule: ChildEvalRule | None = None,
) -> str:
    """
    Compute a behavioral equivalence hash for evaluation grouping.

    Unlike ``ComponentIdentifier.hash`` (which includes all params of self and
    children), the eval hash applies per-child rules to strip operational params
    (like endpoint, max_requests_per_minute), exclude children entirely, or
    filter list items.  ``own_rule`` extends this to the root entity itself,
    which is required for leaf components (e.g., a target) whose own params
    need filtering and which have no relevant children to delegate to. This
    ensures the same logical configuration on different deployments produces
    the same eval hash.

    Children not listed in ``child_eval_rules`` receive full recursive treatment.

    When both ``child_eval_rules`` is empty and ``own_rule`` is ``None``, no
    filtering occurs and the result equals ``identifier.hash``.

    Args:
        identifier (ComponentIdentifier): The component identity to compute
            the hash for.
        child_eval_rules (dict[str, ChildEvalRule]): Per-child eval rules.
        own_rule (ChildEvalRule | None): Rule applied to the root entity's
            own params and fallbacks. Only ``included_params`` and
            ``param_fallbacks`` are honored; ``exclude``, ``included_item_values``,
            and ``inner_child_name`` are not meaningful at the root and will
            raise ``ValueError`` if set. Defaults to None.

    Returns:
        str: A hex-encoded SHA256 hash suitable for eval registry keying.

    Raises:
        RuntimeError: If the identifier's hash is None and no filtering is configured.
        ValueError: If ``own_rule`` carries fields that are not meaningful at the root.
    """
    if own_rule is not None:
        if own_rule.exclude:
            raise ValueError("own_rule.exclude is not meaningful at the root entity")
        if own_rule.included_item_values is not None:
            raise ValueError("own_rule.included_item_values is not meaningful at the root entity")
        if own_rule.inner_child_name is not None:
            raise ValueError("own_rule.inner_child_name is not meaningful at the root entity")

    if not child_eval_rules and own_rule is None:
        if identifier.hash is None:
            raise RuntimeError("hash should be set by __post_init__")
        return identifier.hash

    eval_dict = _build_eval_dict(
        identifier,
        child_eval_rules=child_eval_rules,
        _included_params=own_rule.included_params if own_rule else None,
        _param_fallbacks=own_rule.param_fallbacks if own_rule else None,
    )
    return config_hash(eval_dict)


class EvaluationIdentifier(ABC):
    """
    Wraps a ``ComponentIdentifier`` with domain-specific eval-hash configuration.

    Subclasses set ``CHILD_EVAL_RULES`` — a mapping of child names to
    ``ChildEvalRule`` instances that control how each child is treated during
    eval-hash computation.  Children not listed receive full recursive treatment.

    Leaf-entity subclasses (no relevant children to delegate to) may also set
    ``OWN_RULE`` to filter the root entity's own params.  See
    ``ObjectiveTargetEvaluationIdentifier`` for an example.

    The concrete ``eval_hash`` property delegates to the module-level
    ``compute_eval_hash`` free function.
    """

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]]
    OWN_RULE: ClassVar[ChildEvalRule | None] = None

    def __init__(self, identifier: ComponentIdentifier) -> None:
        """
        Wrap a ComponentIdentifier and resolve its eval hash.

        If the identifier carries an ``eval_hash`` (preserved from a prior
        DB round-trip or set by the scorer), that value is used directly.
        Otherwise the eval hash is computed from the identifier's params
        and children using the subclass's ``CHILD_EVAL_RULES`` and
        ``OWN_RULE``.
        """
        self._identifier = identifier
        if identifier.eval_hash is not None:
            self._eval_hash = identifier.eval_hash
        else:
            self._eval_hash = compute_eval_hash(
                identifier,
                child_eval_rules=self.CHILD_EVAL_RULES,
                own_rule=self.OWN_RULE,
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
    (``underlying_model_name``, ``temperature``, ``top_p``), so the same scorer
    configuration on different deployments produces the same eval hash.
    """

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {
        "prompt_target": ChildEvalRule(
            included_params=TARGET_EVAL_PARAMS,
            param_fallbacks=TARGET_EVAL_PARAM_FALLBACKS,
            inner_child_name="targets",
        ),
    }


class AtomicAttackEvaluationIdentifier(EvaluationIdentifier):
    """
    Evaluation identity for atomic attacks.

    Per-child rules:

    * ``seed_identifiers`` — excluded entirely (present for traceability only).
    * ``attack_technique`` — not listed, so fully included by default.
      Its nested children (``objective_target``, ``adversarial_chat``,
      ``objective_scorer``, ``technique_seeds``) are processed recursively
      using the same rules dict, so the rules below apply at any depth.
    * ``objective_target`` — include only ``temperature``.
    * ``adversarial_chat`` — include ``underlying_model_name``, ``temperature``, ``top_p``.
    * ``objective_scorer`` — excluded entirely.

    Non-target children (e.g., ``request_converters``, ``response_converters``,
    ``technique_seeds``) receive full recursive eval treatment.
    """

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {
        "objective_target": ChildEvalRule(
            included_params=frozenset({"temperature"}),
            inner_child_name="targets",
        ),
        "adversarial_chat": ChildEvalRule(
            included_params=TARGET_EVAL_PARAMS,
            param_fallbacks=TARGET_EVAL_PARAM_FALLBACKS,
        ),
        "objective_scorer": ChildEvalRule(exclude=True),
        "seed_identifiers": ChildEvalRule(exclude=True),
        # attack_technique: not listed in rules — fully included in eval hash.
        # technique_seeds (nested inside attack_technique): also not listed — fully included.
    }


class ObjectiveTargetEvaluationIdentifier(EvaluationIdentifier):
    """
    Evaluation identity for an objective target.

    Mirrors how ``ScorerEvaluationIdentifier`` filters its inner
    ``prompt_target`` child, except the target itself is the root of this
    identifier (it has no children carrying behavioral configuration).  The
    target's own params are filtered to the behavioral set
    (``underlying_model_name``, ``temperature``, ``top_p``) via ``OWN_RULE``,
    so the same logical target on different deployments produces the same
    eval hash.

    Wrapper targets (e.g., ``RoundRobinTarget``) are not unwrapped — the
    caller must pass the inner target's ``ComponentIdentifier`` directly if
    behavioral equivalence with the unwrapped form is desired.  This mirrors
    the constraint on ``OWN_RULE`` (no ``inner_child_name`` at the root).
    """

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {}
    OWN_RULE: ClassVar[ChildEvalRule | None] = ChildEvalRule(
        included_params=TARGET_EVAL_PARAMS,
        param_fallbacks=TARGET_EVAL_PARAM_FALLBACKS,
    )


def compute_inner_attack_eval_hash(*, attack: AttackStrategy) -> str:
    """
    Predict the eval hash the executor will stamp on persisted child rows
    for this attack.

    Mirrors the inner-attack write path so callers can look up historical
    results matching the same behavioral configuration *before* any row is
    written. Use this rather than reconstructing the recipe inline.

    Args:
        attack (AttackStrategy): Inner attack strategy.

    Returns:
        str: The eval hash that will appear on persisted child rows.
    """
    # Local import avoids a circular dependency inside the identifiers package.
    from pyrit.models.identifiers.atomic_attack_identifier import build_atomic_attack_identifier

    composite = build_atomic_attack_identifier(attack_identifier=attack.get_identifier())
    return AtomicAttackEvaluationIdentifier(composite).eval_hash
