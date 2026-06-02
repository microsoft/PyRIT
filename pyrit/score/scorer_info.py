# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import inspect
from dataclasses import dataclass

from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


@dataclass(frozen=True)
class _ScorerInfo:
    """
    Lightweight, metadata-only description of a public scorer class.

    WARNING — temporary internal helper. This class is intentionally private (leading
    underscore) and is not exported from ``pyrit.score``. Do not import it or build on it.
    It exists only to generate the scorer reference table in the documentation until
    scorers gain a proper, first-class capability descriptor.

    Longer term we expect scorers to expose their own ``ScorerCapability`` (mirroring the
    capability model used by prompt targets) rather than having metadata inferred
    externally via introspection. When that lands, this module should be deleted and
    callers migrated to the capability API.

    Used to build the scorer reference table in the documentation, analogous to
    ``get_converter_modalities`` for converters. It is derived purely from class
    introspection (base class and ``__init__`` signature) and never instantiates a
    scorer, so it requires no credentials or network access.

    Attributes:
        name (str): The scorer class name (e.g. ``SelfAskRefusalScorer``).
        score_type (str): The score the scorer returns, either ``"true_false"`` or
            ``"float_scale"``.
        uses_llm (bool): True when the scorer reasons about responses with a generative
            chat target (a "self-ask" scorer that accepts a ``chat_target`` argument).
            Note that some scorers call external classifier APIs (e.g. Azure Content
            Safety, Prompt Shield) without a generative LLM; those are ``uses_llm=False``.
    """

    name: str
    score_type: str
    uses_llm: bool


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


def get_scorer_info() -> list[_ScorerInfo]:
    """
    Retrieve metadata for every public, concrete scorer exported from ``pyrit.score``.

    Iterates the package's public API, keeps concrete subclasses of ``TrueFalseScorer``
    or ``FloatScaleScorer``, and records each scorer's return type and whether it uses a
    generative chat target. Abstract bases and non-scorer exports are skipped.

    This is a temporary helper used only to render the documentation's scorer reference
    table; see ``_ScorerInfo`` for why it should not be built upon.

    Returns:
        list[_ScorerInfo]: Scorers sorted by score type, then LLM-based scorers last
            within each type, then by name.
    """
    import pyrit.score as score_package

    infos: list[_ScorerInfo] = []
    for name in score_package.__all__:
        obj = getattr(score_package, name, None)

        # Guard against entries that aren't genuine classes. A test elsewhere in the suite
        # may patch a ``pyrit.score`` export with a mock (e.g. ``autospec``/``spec=type``)
        # that reports ``isinstance(obj, type) is True`` yet makes ``issubclass`` raise
        # ``TypeError``; skip anything that isn't a real, concrete scorer subclass.
        try:
            if not isinstance(obj, type) or not issubclass(obj, Scorer) or inspect.isabstract(obj):
                continue

            if issubclass(obj, FloatScaleScorer):
                score_type = "float_scale"
            elif issubclass(obj, TrueFalseScorer):
                score_type = "true_false"
            else:
                continue
        except TypeError:
            continue

        infos.append(_ScorerInfo(name=name, score_type=score_type, uses_llm=_uses_chat_target(obj)))

    infos.sort(key=lambda info: (info.score_type, info.uses_llm, info.name))
    return infos
