# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Frozen evaluation rules for saved objective-target analytics keys."""

from __future__ import annotations

from typing import ClassVar

from pyrit.models import ChildEvalRule, ComponentIdentifier, TargetIdentifier, compute_eval_hash


class ObjectiveTargetAnalyticsIdentityV1:
    """
    The objective-target evaluation identity used for both writes and historical backfills.

    Targets with a model name group by the behavioral evaluation rules (model, temperature,
    top_p). Targets without one (HTTP, Gandalf and other non-LLM targets) group by their
    content hash instead, so distinct endpoints or levels stay apart. That fallback is a
    pre-release correction to v1, applied by revision ``3f6a9d2c8b1e``.

    Keep these rules unchanged when ``Evaluate`` markers evolve. A future rule
    change needs a new versioned column and migration so stored chart identities
    never acquire a new meaning merely because code was upgraded.
    """

    VERSION: ClassVar[str] = "v1"
    _PARAMS: ClassVar[frozenset[str]] = frozenset({"underlying_model_name", "temperature", "top_p"})
    _FALLBACKS: ClassVar[dict[str, str]] = {"underlying_model_name": "model_name"}
    _CHILD_RULES: ClassVar[dict[str, ChildEvalRule]] = {
        "targets": ChildEvalRule(
            included_params=_PARAMS,
            param_fallbacks=_FALLBACKS,
            inner_child_name="targets",
        )
    }
    _OWN_RULE: ClassVar[ChildEvalRule] = ChildEvalRule(included_params=_PARAMS, param_fallbacks=_FALLBACKS)

    @classmethod
    def hash(cls, *, identifier: ComponentIdentifier) -> str:
        """
        Compute the frozen v1 equivalence hash, including target-wrapper unwrapping.

        Args:
            identifier (ComponentIdentifier): The recorded objective-target snapshot.

        Returns:
            str: The 64-character v1 evaluation hash.
        """
        target = TargetIdentifier.from_component_identifier(identifier)
        evaluated = cls._evaluated_target(target=target)
        if not cls._has_model_name(target=evaluated):
            # The behavioral rules only see model settings, so they would merge every
            # model-less target of a class into one group.
            return evaluated.hash
        return compute_eval_hash(
            target,
            child_eval_rules=cls._CHILD_RULES,
            own_rule=cls._OWN_RULE,
            root_unwrap_child="targets",
        )

    @staticmethod
    def _evaluated_target(*, target: ComponentIdentifier) -> ComponentIdentifier:
        """
        Return the target the rules evaluate: a wrapper's first inner target, or the target itself.

        Args:
            target (ComponentIdentifier): The recorded objective target.

        Returns:
            ComponentIdentifier: The unwrapped target.
        """
        inner = target.get_child_list("targets")
        return inner[0] if inner else target

    @classmethod
    def _has_model_name(cls, *, target: ComponentIdentifier) -> bool:
        """
        Check for a nonempty underlying model name or its ``model_name`` fallback.

        Args:
            target (ComponentIdentifier): The unwrapped target.

        Returns:
            bool: True if the target records a model name the rules can group by.
        """
        return any(str(target.params.get(name) or "").strip() for name in ("underlying_model_name", "model_name"))

    @classmethod
    def from_atomic_document(cls, *, document: dict[str, object] | None) -> str | None:
        """
        Resolve a target from either supported saved attack nesting layout.

        Args:
            document (dict[str, object] | None): The stored atomic-attack identifier.

        Returns:
            str | None: The v1 evaluation hash, or None if no objective target was recorded.
        """
        if not document:
            return None
        atomic = ComponentIdentifier.model_validate(document)
        technique = atomic.get_child("attack_technique")
        attack = technique.get_child("attack") if technique is not None else atomic.get_child("attack")
        target = attack.get_child("objective_target") if attack is not None else None
        return cls.hash(identifier=target) if target is not None else None
