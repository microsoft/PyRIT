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

from typing import TYPE_CHECKING, Any, ClassVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.identifiers.atomic_attack_identifier import AtomicAttackIdentifier
from pyrit.models.identifiers.attack_identifier import AttackIdentifier
from pyrit.models.identifiers.component_identifier import ComponentIdentifier, config_hash
from pyrit.models.identifiers.evaluation_markers import EvalMarker, Exclude, Include, Unwrap
from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier
from pyrit.models.identifiers.target_identifier import TargetIdentifier

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_strategy import AttackStrategy

# ``AttackIdentifier`` is imported for the eval-config deriver's type graph walk.
_ = AttackIdentifier

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
    root_unwrap_child: str | None = None,
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

    When both ``child_eval_rules`` is empty and ``own_rule`` is ``None`` (and no
    root unwrap applies), no filtering occurs and the result equals
    ``identifier.hash``.

    Args:
        identifier (ComponentIdentifier): The component identity to compute
            the hash for.
        child_eval_rules (dict[str, ChildEvalRule]): Per-child eval rules.
        own_rule (ChildEvalRule | None): Rule applied to the root entity's
            own params and fallbacks. Only ``included_params`` and
            ``param_fallbacks`` are honored; ``exclude``, ``included_item_values``,
            and ``inner_child_name`` are not meaningful at the root and will
            raise ``ValueError`` if set. Defaults to None.
        root_unwrap_child (str | None): If set, names the wrapper passthrough
            slot on the root identifier (e.g. ``"targets"``). When the root is a
            wrapper carrying that slot, the first element is substituted before
            filtering, so the eval hash matches the unwrapped inner component.
            Defaults to None.

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

    if root_unwrap_child is not None:
        inner = identifier.get_child_list(root_unwrap_child)
        if inner:
            identifier = inner[0]

    if not child_eval_rules and own_rule is None:
        return identifier.hash

    eval_dict = _build_eval_dict(
        identifier,
        child_eval_rules=child_eval_rules,
        _included_params=own_rule.included_params if own_rule else None,
        _param_fallbacks=own_rule.param_fallbacks if own_rule else None,
    )
    return config_hash(eval_dict)


# ----------------------------------------------------------------------
# Eval-config derivation from field markers
# ----------------------------------------------------------------------
#
# The typed identifier classes declare — on their own fields — what feeds the
# eval hash, via ``Evaluate.Include`` / ``Evaluate.Exclude`` / ``Evaluate.Unwrap``
# markers (see ``evaluation_markers``). ``derive_eval_config`` walks a root
# identifier type's class graph and projects those markers into the engine's
# name-keyed ``ChildEvalRule`` dict (+ ``own_rule`` and root-unwrap slot), so the
# strongly-typed identifiers are the single source of truth and the proven engine
# is reused unchanged.


def _resolve_child_type(annotation: Any) -> type[ComponentIdentifier]:
    """
    Resolve the ``ComponentIdentifier`` subclass a child field annotation denotes.

    Args:
        annotation (Any): A resolved child field annotation, e.g.
            ``TargetIdentifier | None`` or ``list[TargetIdentifier]``.

    Returns:
        type[ComponentIdentifier]: The referenced identifier subclass.

    Raises:
        TypeError: If no ``ComponentIdentifier`` subclass can be resolved.
    """
    if get_origin(annotation) is list:
        args = get_args(annotation)
        inner = args[0] if args else None
        if isinstance(inner, type) and issubclass(inner, ComponentIdentifier):
            return inner

    for candidate in get_args(annotation) or (annotation,):
        if isinstance(candidate, type) and issubclass(candidate, ComponentIdentifier):
            return candidate

    raise TypeError(f"Could not resolve a child identifier type from annotation {annotation!r}")


def _field_marker(model_cls: type[ComponentIdentifier], field_name: str) -> EvalMarker | None:
    """Return the ``EvalMarker`` attached to a field, or ``None`` if unmarked."""
    for meta in model_cls.model_fields[field_name].metadata:
        if isinstance(meta, EvalMarker):
            return meta
    return None


def _type_param_projection(
    model_cls: type[ComponentIdentifier],
) -> tuple[frozenset[str] | None, dict[str, str] | None]:
    """
    Project a type's own param-field markers into ``(included_params, fallbacks)``.

    An unmarked or ``Include`` param is kept; ``Exclude`` drops it. When the type
    has no excluded params, ``included_params`` is ``None`` (full include).

    Returns:
        tuple[frozenset[str] | None, dict[str, str] | None]: The included param
            names (``None`` for full include) and the per-param fallbacks (``None``
            when there are none).
    """
    included: list[str] = []
    fallbacks: dict[str, str] = {}
    has_exclude = False
    for name in model_cls._promoted_param_fields():
        marker = _field_marker(model_cls, name)
        if isinstance(marker, Exclude):
            has_exclude = True
            continue
        included.append(name)
        if isinstance(marker, Include) and marker.fallback is not None:
            fallbacks[name] = marker.fallback
    included_params = frozenset(included) if has_exclude else None
    return included_params, (fallbacks or None)


def _type_unwrap_field(model_cls: type[ComponentIdentifier]) -> str | None:
    """Return the name of the type's ``Evaluate.Unwrap()`` child field, if any."""
    for name in model_cls._promoted_child_fields():
        if isinstance(_field_marker(model_cls, name), Unwrap):
            return name
    return None


def _slot_rule(
    *,
    parent_cls: type[ComponentIdentifier],
    field_name: str,
    child_type: type[ComponentIdentifier],
) -> ChildEvalRule:
    """
    Build the ``ChildEvalRule`` for a parent's child slot.

    The slot's projection defaults to the child type's own marker projection; a
    parent ``Evaluate.Exclude()`` drops the child, and a parent
    ``Evaluate.Include(only_params=...)`` restricts the child subtree's params.

    Returns:
        ChildEvalRule: The derived rule for the parent's child slot.
    """
    marker = _field_marker(parent_cls, field_name)
    if isinstance(marker, Exclude):
        return ChildEvalRule(exclude=True)

    included_params, fallbacks = _type_param_projection(child_type)
    if isinstance(marker, Include) and marker.only_params is not None:
        included_params = frozenset(marker.only_params)
        if fallbacks is not None:
            fallbacks = {k: v for k, v in fallbacks.items() if k in included_params} or None

    return ChildEvalRule(
        included_params=included_params,
        param_fallbacks=fallbacks,
        inner_child_name=_type_unwrap_field(child_type),
    )


def _is_neutral_rule(rule: ChildEvalRule) -> bool:
    """Return whether a rule is a no-op (absent from the dict means the same thing)."""
    return (
        not rule.exclude
        and rule.included_params is None
        and rule.included_item_values is None
        and rule.param_fallbacks is None
        and rule.inner_child_name is None
    )


def derive_eval_config(
    root_type: type[ComponentIdentifier],
) -> tuple[dict[str, ChildEvalRule], ChildEvalRule | None, str | None]:
    """
    Derive the eval engine's configuration from a root identifier type's markers.

    Walks the class graph reachable from ``root_type`` (depth-first, each type
    visited once) and projects each promoted child field's markers into a
    name-keyed ``ChildEvalRule``. Neutral (no-op) rules are omitted, since the
    engine treats an absent child the same as a full include. The root type's
    own param markers yield the ``own_rule``, and its ``Unwrap`` field (if any)
    yields the root-unwrap slot.

    All promoted child field names are unique across the identifier types, so the
    flat name-keyed dict has no collisions (matching how the engine keys rules).

    Args:
        root_type (type[ComponentIdentifier]): The eval root identifier type.

    Returns:
        tuple[dict[str, ChildEvalRule], ChildEvalRule | None, str | None]: The
        ``child_eval_rules`` dict, the root ``own_rule`` (or ``None``), and the
        root-unwrap child name (or ``None``).
    """
    child_eval_rules: dict[str, ChildEvalRule] = {}
    visited: set[type[ComponentIdentifier]] = set()
    stack: list[type[ComponentIdentifier]] = [root_type]

    while stack:
        cls = stack.pop()
        if cls in visited:
            continue
        visited.add(cls)

        for field_name in cls._promoted_child_fields():
            child_type = _resolve_child_type(cls.model_fields[field_name].annotation)
            marker = _field_marker(cls, field_name)
            rule = _slot_rule(parent_cls=cls, field_name=field_name, child_type=child_type)
            if not _is_neutral_rule(rule):
                child_eval_rules[field_name] = rule
            if not isinstance(marker, Exclude):
                stack.append(child_type)

    own_included, own_fallbacks = _type_param_projection(root_type)
    own_rule: ChildEvalRule | None = None
    if own_included is not None or own_fallbacks is not None:
        own_rule = ChildEvalRule(included_params=own_included, param_fallbacks=own_fallbacks)

    return child_eval_rules, own_rule, _type_unwrap_field(root_type)


class EvaluationIdentifier:
    """
    Wraps a ``ComponentIdentifier`` with domain-specific eval-hash configuration.

    Concrete subclasses name their root typed-identifier type via ``EVAL_ROOT``;
    the per-child rules (``CHILD_EVAL_RULES``), the root ``OWN_RULE``, and the
    root-unwrap slot (``ROOT_UNWRAP_CHILD``) are then **derived** from the
    ``Evaluate.*`` field markers on that type's class graph (see
    ``derive_eval_config``). The typed identifier fields are the single source of
    truth for what feeds the eval hash.

    Subclasses may instead set ``CHILD_EVAL_RULES`` (and optionally ``OWN_RULE`` /
    ``ROOT_UNWRAP_CHILD``) directly to bypass derivation.

    The concrete ``eval_hash`` property delegates to the module-level
    ``compute_eval_hash`` free function.
    """

    EVAL_ROOT: ClassVar[type[ComponentIdentifier] | None] = None
    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {}
    OWN_RULE: ClassVar[ChildEvalRule | None] = None
    ROOT_UNWRAP_CHILD: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Derive eval config from ``EVAL_ROOT`` markers unless declared explicitly."""
        super().__init_subclass__(**kwargs)
        if cls.EVAL_ROOT is not None and "CHILD_EVAL_RULES" not in cls.__dict__:
            cls.CHILD_EVAL_RULES, cls.OWN_RULE, cls.ROOT_UNWRAP_CHILD = derive_eval_config(cls.EVAL_ROOT)

    def __init__(self, identifier: ComponentIdentifier) -> None:
        """
        Wrap a ComponentIdentifier and compute its eval hash.

        The eval hash is always computed fresh from the identifier's params and
        children using the subclass's ``CHILD_EVAL_RULES``, ``OWN_RULE``, and
        ``ROOT_UNWRAP_CHILD`` — any ``eval_hash`` already carried on the
        identifier (e.g. a value read back from storage) is never trusted.
        """
        self._identifier = identifier
        self._eval_hash = compute_eval_hash(
            identifier,
            child_eval_rules=self.CHILD_EVAL_RULES,
            own_rule=self.OWN_RULE,
            root_unwrap_child=self.ROOT_UNWRAP_CHILD,
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

    Rules are derived from ``ScorerIdentifier``'s field markers. The
    ``prompt_target`` child is projected to behavioral target params only
    (``underlying_model_name``, ``temperature``, ``top_p``) and wrapper targets
    are unwrapped, so the same scorer configuration on different deployments
    produces the same eval hash.
    """

    EVAL_ROOT: ClassVar[type[ComponentIdentifier] | None] = ScorerIdentifier


class AtomicAttackEvaluationIdentifier(EvaluationIdentifier):
    """
    Evaluation identity for atomic attacks.

    Rules are derived from ``AtomicAttackIdentifier``'s field markers, which
    propagate down the technique/attack subtree. The behavioral projection of
    targets, the ``objective_target`` restriction to ``temperature``, and the
    exclusion of ``objective_scorer`` / ``seed_identifiers`` all live on the
    typed identifier fields.
    """

    EVAL_ROOT: ClassVar[type[ComponentIdentifier] | None] = AtomicAttackIdentifier


class ObjectiveTargetEvaluationIdentifier(EvaluationIdentifier):
    """
    Evaluation identity for an objective target.

    Rules are derived from ``TargetIdentifier``'s field markers: the target's own
    params are filtered to the behavioral set (``underlying_model_name``,
    ``temperature``, ``top_p``) via the derived ``OWN_RULE``, and wrapper targets
    (e.g. ``RoundRobinTarget``) are unwrapped at the root, so the same logical
    target produces the same eval hash whether bare or wrapped.
    """

    EVAL_ROOT: ClassVar[type[ComponentIdentifier] | None] = TargetIdentifier


def compute_inner_attack_eval_hash(*, attack: AttackStrategy[Any, Any]) -> str:
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
    from pyrit.models.identifiers.atomic_attack_identifier import AtomicAttackIdentifier

    composite = AtomicAttackIdentifier.build(attack_identifier=attack.get_identifier())
    return AtomicAttackEvaluationIdentifier(composite).eval_hash
