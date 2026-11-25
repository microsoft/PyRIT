# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import inspect
from dataclasses import dataclass

from pyrit.models import ScorerCapabilityName
from pyrit.score.scorer import Scorer

#: True/false scorers that override ``_preserves_float_scale_value``, and when they keep the value.
#: A test requires this to name exactly the classes that override the hook.
_FLOAT_FEEDBACK_GUARANTEES: dict[str, str] = {
    "FloatScaleThresholdScorer": "always",
    "TrueFalseFallbackScorer": "if both scorers do",
}

_WRAPPED = "same as wrapped"


@dataclass(frozen=True)
class _ScorerInfo:
    """
    Lightweight, metadata-only description of a public scorer class.

    WARNING — internal helper. This class is intentionally private (leading underscore)
    and is not exported from ``pyrit.score``. Do not import it or build on it. It exists
    only to generate the scorer reference table in the documentation, analogous to
    ``get_converter_modalities`` for converters.

    It reads the class-level contracts that ``Scorer.capabilities`` derives from on a
    configured scorer (``CAPABILITIES``, ``SUPPORTED_SCORABLE_TYPES``, ``CONDITION_TYPE``,
    and the hooks a class overrides), and never instantiates a scorer, so it requires no
    credentials or network access. Some contracts depend on configuration, such as the
    scorers a wrapper holds or whether a message scorer's validator requires an objective,
    so a configured scorer's ``capabilities`` remain the authority.

    Attributes:
        name (str): The scorer class name (e.g. ``SelfAskRefusalScorer``).
        score_type (str): The score the scorer returns, either ``"true_false"`` or
            ``"float_scale"``, from the result family in ``CAPABILITIES``.
        uses_llm (bool | None): True when the scorer reasons about responses with a generative
            chat target (a "self-ask" scorer that accepts a ``chat_target`` argument). None when
            the scorer wraps other scorers and has no target of its own, so the answer is theirs.
            Note that some scorers call external classifier APIs (e.g. Azure Content
            Safety, Prompt Shield) without a generative LLM; those are ``uses_llm=False``.
        evidence (str): The ``Scorable`` types it accepts, without the ``Scorable`` suffix.
        conditions (str): The condition type it evaluates.
        float_feedback (str): Whether every complete true/false result keeps a float value
            in ``[0, 1]`` for attacks that rank results.
    """

    name: str
    score_type: str
    uses_llm: bool | None
    evidence: str
    conditions: str
    float_feedback: str


def _uses_chat_target(scorer_class: type[Scorer]) -> bool:
    """
    Determine whether a scorer accepts a ``chat_target`` constructor argument.

    Args:
        scorer_class (type[Scorer]): The scorer class to inspect.

    Returns:
        bool: True if ``chat_target`` is a parameter of ``__init__``.
    """
    try:
        signature = inspect.signature(scorer_class.__init__)
    except (TypeError, ValueError):
        return False
    return "chat_target" in signature.parameters


def _overrides(scorer_class: type[Scorer], *, name: str) -> bool:
    """
    Report whether a scorer class, or any class it inherits from, overrides a ``Scorer`` method.

    Args:
        scorer_class (type[Scorer]): The scorer class to inspect.
        name (str): The ``Scorer`` method name.

    Returns:
        bool: True when the method the class resolves is not the one ``Scorer`` defines.
    """
    return inspect.getattr_static(scorer_class, name) is not inspect.getattr_static(Scorer, name)


def _wraps_scorers(scorer_class: type[Scorer]) -> bool:
    return _overrides(scorer_class, name="_get_child_scorers")


def _uses_llm(scorer_class: type[Scorer]) -> bool | None:
    if _uses_chat_target(scorer_class):
        return True
    return None if _wraps_scorers(scorer_class) else False


def _score_type(scorer_class: type[Scorer]) -> str:
    """
    Name the result family a scorer class declares.

    Args:
        scorer_class (type[Scorer]): The scorer class to inspect.

    Returns:
        str: ``"true_false"`` or ``"float_scale"``.

    Raises:
        ValueError: If the class declares no result family in ``CAPABILITIES``.
    """
    if ScorerCapabilityName.TRUE_FALSE in scorer_class.CAPABILITIES:
        return "true_false"
    if ScorerCapabilityName.FLOAT_SCALE in scorer_class.CAPABILITIES:
        return "float_scale"
    raise ValueError(f"{scorer_class.__name__} declares no result family in CAPABILITIES.")


def _describe_evidence(scorer_class: type[Scorer]) -> str:
    if scorer_class.SUPPORTED_SCORABLE_TYPES:
        return ", ".join(sorted(t.__name__.removesuffix("Scorable") for t in scorer_class.SUPPORTED_SCORABLE_TYPES))
    return _WRAPPED if _wraps_scorers(scorer_class) else "none declared"


def _describe_conditions(scorer_class: type[Scorer]) -> str:
    if _wraps_scorers(scorer_class):
        return _WRAPPED
    if scorer_class.CONDITION_TYPE is not None:
        return scorer_class.CONDITION_TYPE.__name__
    if _overrides(scorer_class, name="_get_condition_type"):
        return "MatchesObjective if configured"
    return "none"


def _describe_float_feedback(scorer_class: type[Scorer]) -> str:
    if ScorerCapabilityName.TRUE_FALSE not in scorer_class.CAPABILITIES:
        return "not applicable"
    return _FLOAT_FEEDBACK_GUARANTEES.get(scorer_class.__name__, "no")


def _describe(scorer_class: type[Scorer]) -> _ScorerInfo:
    return _ScorerInfo(
        name=scorer_class.__name__,
        score_type=_score_type(scorer_class),
        uses_llm=_uses_llm(scorer_class),
        evidence=_describe_evidence(scorer_class),
        conditions=_describe_conditions(scorer_class),
        float_feedback=_describe_float_feedback(scorer_class),
    )


def _public_scorer_classes() -> list[type[Scorer]]:
    """
    List the concrete scorer classes exported from ``pyrit.score``.

    Returns:
        list[type[Scorer]]: The classes, in ``__all__`` order.
    """
    import pyrit.score as score_package

    classes: list[type[Scorer]] = []
    for name in score_package.__all__:
        obj = getattr(score_package, name, None)

        # Guard against entries that aren't genuine classes. A test elsewhere in the suite
        # may patch a ``pyrit.score`` export with a mock (e.g. ``autospec``/``spec=type``)
        # that reports ``isinstance(obj, type) is True`` yet makes ``issubclass`` raise
        # ``TypeError``; skip anything that isn't a real, concrete scorer subclass.
        try:
            if isinstance(obj, type) and issubclass(obj, Scorer) and not inspect.isabstract(obj):
                classes.append(obj)
        except TypeError:
            continue
    return classes


def get_scorer_info() -> list[_ScorerInfo]:
    """
    Describe every public, concrete scorer exported from ``pyrit.score``.

    Records each scorer's return type, whether it uses a generative chat target, the
    evidence it accepts, the condition type it evaluates, and whether its true/false
    results keep float feedback. Abstract bases and non-scorer exports are skipped, and
    no scorer is constructed.

    This is an internal helper used only to render the documentation's scorer reference
    table; see ``_ScorerInfo`` for why it should not be built upon.

    Returns:
        list[_ScorerInfo]: Scorers sorted by score type, then non-LLM scorers, LLM-based
            scorers, and scorers that wrap other scorers, then by name.
    """
    infos = [_describe(scorer_class) for scorer_class in _public_scorer_classes()]
    infos.sort(key=lambda info: (info.score_type, info.uses_llm is None, info.uses_llm is True, info.name))
    return infos
