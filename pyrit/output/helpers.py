# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Convenience functions for one-line printing of attack results, scenario results, and scorer info.

Printer classes are imported at module load, but the heavy ``CentralMemory`` dependency is
deferred inside each ``*MemoryPrinter`` constructor, so importing this module (or
``pyrit.output``) does not pull in the memory stack until a memory-backed printer is instantiated.
"""

import os

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import AttackResult, Message, Score
from pyrit.models.scenario_result import ScenarioResult
from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter
from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter
from pyrit.output.conversation.pretty import PrettyConversationMemoryPrinter
from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter
from pyrit.output.score.pretty import PrettyScorePrinter
from pyrit.output.scorer.pretty import PrettyScorerMemoryPrinter
from pyrit.output.sink import OutputFormat, Sink, StdoutSink, get_default_sink


async def output_attack_async(
    result: AttackResult,
    *,
    format: OutputFormat = "pretty",  # noqa: A002
    sink: Sink | None = None,
    include_auxiliary_scores: bool = False,
    include_pruned_conversations: bool = False,
    include_adversarial_conversation: bool = False,
    blur_images: bool = False,
    blur_radius: int = 20,
    blurred_dir: str | os.PathLike[str] | None = None,
) -> None:
    """
    Print an attack result in the specified format to the specified destination.

    Args:
        result (AttackResult): The attack result to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Sink | None): Output sink. Defaults to StdoutSink for "pretty"; auto-detects
            (IPythonMarkdownSink in notebooks, StdoutSink otherwise) for "markdown".
        include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
        include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
        include_adversarial_conversation (bool): Whether to include the adversarial conversation.
            Defaults to False.
        blur_images (bool): If True, apply a Gaussian blur to image outputs before
            rendering them. For "pretty" output, image bytes are blurred in-memory before
            display. For "markdown" output, a blurred file is written to disk and the
            markdown links to it instead of the original. The original image file is
            **not** modified and remains accessible on disk; this flag is intended to
            reduce reviewer exposure, not to enforce access control.
            If blurring fails for any reason (I/O error, decode error, etc.), a warning
            is logged and a plain-text link to the original is emitted instead of an
            inline image — the original is not silently rendered.
            Defaults to False.
        blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
            Defaults to 20.
        blurred_dir (str | PathLike | None): For "markdown" output, directory to write
            blurred copies into. Defaults to None (sibling of the original). Ignored
            when ``format != "markdown"``.
    """
    if format == "markdown":
        printer = MarkdownAttackResultMemoryPrinter(
            sink=sink or get_default_sink(),
            blur_images=blur_images,
            blur_radius=blur_radius,
            blurred_dir=blurred_dir,
        )
    else:
        printer = PrettyAttackResultMemoryPrinter(
            sink=sink or get_default_sink(StdoutSink),
            blur_images=blur_images,
            blur_radius=blur_radius,
        )

    await printer.write_async(
        result,
        include_auxiliary_scores=include_auxiliary_scores,
        include_pruned_conversations=include_pruned_conversations,
        include_adversarial_conversation=include_adversarial_conversation,
    )


async def output_scenario_async(
    result: ScenarioResult,
    *,
    format: OutputFormat = "pretty",  # noqa: A002
    sink: Sink | None = None,
) -> None:
    """
    Print a scenario result in the specified format to the specified destination.

    Args:
        result (ScenarioResult): The scenario result to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Sink | None): Output sink. Defaults to StdoutSink.

    Raises:
        ValueError: If ``format`` is not a supported value.
    """
    if format != "pretty":
        raise ValueError(f"Unsupported format for scenario results: {format!r}. Only 'pretty' is available.")

    printer = PrettyScenarioResultMemoryPrinter(sink=sink or get_default_sink(StdoutSink))
    await printer.write_async(result)


async def output_scorer_async(
    *,
    scorer_identifier: ComponentIdentifier,
    harm_category: str | None = None,
    format: OutputFormat = "pretty",  # noqa: A002
    sink: Sink | None = None,
) -> None:
    """
    Print scorer information in the specified format to the specified destination.

    Auto-detects scorer type: if harm_category is provided, renders harm
    metrics; otherwise renders objective metrics.

    Args:
        scorer_identifier (ComponentIdentifier): The scorer identifier.
        harm_category (str | None): The harm category. None for objective scorers.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Sink | None): Output sink. Defaults to StdoutSink.

    Raises:
        ValueError: If ``format`` is not a supported value.
    """
    if format != "pretty":
        raise ValueError(f"Unsupported format for scorer: {format!r}. Only 'pretty' is available.")

    printer = PrettyScorerMemoryPrinter(sink=sink or get_default_sink(StdoutSink))
    await printer.write_async(scorer_identifier=scorer_identifier, harm_category=harm_category)


async def output_conversation_async(
    messages: list[Message],
    *,
    format: OutputFormat = "pretty",  # noqa: A002
    sink: Sink | None = None,
    include_scores: bool = False,
    include_reasoning_trace: bool = False,
    blur_images: bool = False,
    blur_radius: int = 20,
) -> None:
    """
    Print a conversation message history in the specified format.

    Args:
        messages (list[Message]): The messages to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Sink | None): Output sink. Defaults to StdoutSink for "pretty", IPythonMarkdownSink
            for "markdown".
        include_scores (bool): Whether to include scores. Defaults to False.
        include_reasoning_trace (bool): Whether to include reasoning traces. Defaults to False.
        blur_images (bool): If True, apply a Gaussian blur to image outputs before
            rendering them. For "pretty" output (the only format supported here),
            image bytes are blurred in-memory before display. The original image file
            is **not** modified; this flag is intended to reduce reviewer exposure,
            not to enforce access control. If blurring fails for any reason, a warning
            is logged and the original is shown (pretty path only).
            Defaults to False.
        blur_radius (int): Gaussian blur radius applied when ``blur_images`` is True.
            Defaults to 20.

    Raises:
        ValueError: If ``format`` is not a supported value.
    """
    if format != "pretty":
        raise ValueError(f"Unsupported format for conversation: {format!r}. Only 'pretty' is available.")

    printer = PrettyConversationMemoryPrinter(
        sink=sink or get_default_sink(StdoutSink),
        blur_images=blur_images,
        blur_radius=blur_radius,
    )
    await printer.write_async(
        messages,
        include_scores=include_scores,
        include_reasoning_trace=include_reasoning_trace,
    )


async def output_score_async(
    scores: list[Score],
    *,
    format: OutputFormat = "pretty",  # noqa: A002
    sink: Sink | None = None,
) -> None:
    """
    Print a list of scores in the specified format.

    Args:
        scores (list[Score]): The scores to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Sink | None): Output sink. Defaults to StdoutSink.

    Raises:
        ValueError: If ``format`` is not a supported value.
    """
    if format != "pretty":
        raise ValueError(f"Unsupported format for scores: {format!r}. Only 'pretty' is available.")

    printer = PrettyScorePrinter(sink=sink or get_default_sink(StdoutSink))
    await printer.write_async(scores)
