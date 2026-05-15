# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Convenience functions for one-line printing of attack results, scenario results, and scorer info."""

from pyrit.printer.sink import OutputFormat, Sink, StdoutSink, get_default_sink


async def print_attack_result_async(
    result: "AttackResult",  # noqa: F821
    *,
    format: OutputFormat = "pretty",
    sink: Sink | None = None,
    include_auxiliary_scores: bool = False,
    include_pruned_conversations: bool = False,
    include_adversarial_conversation: bool = False,
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
    """
    if format == "markdown":
        from pyrit.printer.attack_result.markdown import MarkdownAttackResultMemoryPrinter

        printer = MarkdownAttackResultMemoryPrinter(sink=sink or get_default_sink())
    else:
        from pyrit.printer.attack_result.pretty import PrettyAttackResultMemoryPrinter

        printer = PrettyAttackResultMemoryPrinter(sink=sink or get_default_sink(StdoutSink))

    await printer.write_async(
        result,
        include_auxiliary_scores=include_auxiliary_scores,
        include_pruned_conversations=include_pruned_conversations,
        include_adversarial_conversation=include_adversarial_conversation,
    )


async def print_scenario_result_async(
    result: "ScenarioResult",  # noqa: F821
    *,
    format: OutputFormat = "pretty",
    sink: Sink | None = None,
) -> None:
    """
    Print a scenario result in the specified format to the specified destination.

    Args:
        result (ScenarioResult): The scenario result to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Sink | None): Output sink. Defaults to StdoutSink.
    """
    if format == "pretty":
        from pyrit.printer.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

        printer = PrettyScenarioResultMemoryPrinter(sink=sink or get_default_sink(StdoutSink))
    else:
        raise ValueError(f"Unsupported format for scenario results: {format!r}. Only 'pretty' is available.")

    await printer.write_async(result)


async def print_scorer_async(
    *,
    scorer_identifier: "ComponentIdentifier",  # noqa: F821
    harm_category: str | None = None,
    format: OutputFormat = "pretty",
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
    """
    if format == "pretty":
        from pyrit.printer.scorer.pretty import PrettyScorerMemoryPrinter

        printer = PrettyScorerMemoryPrinter(sink=sink or get_default_sink(StdoutSink))
    else:
        raise ValueError(f"Unsupported format for scorer: {format!r}. Only 'pretty' is available.")

    await printer.write_async(scorer_identifier=scorer_identifier, harm_category=harm_category)


async def print_conversation_async(
    messages: "list[Message]",  # noqa: F821
    *,
    format: OutputFormat = "pretty",
    sink: Sink | None = None,
    include_scores: bool = False,
    include_reasoning_trace: bool = False,
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
    """
    if format == "pretty":
        from pyrit.printer.conversation.pretty import PrettyConversationMemoryPrinter

        printer = PrettyConversationMemoryPrinter(sink=sink or get_default_sink(StdoutSink))
    else:
        raise ValueError(f"Unsupported format for conversation: {format!r}. Only 'pretty' is available.")

    await printer.write_async(
        messages,
        include_scores=include_scores,
        include_reasoning_trace=include_reasoning_trace,
    )


async def print_score_async(
    scores: "list[Score]",  # noqa: F821
    *,
    format: OutputFormat = "pretty",
    sink: Sink | None = None,
) -> None:
    """
    Print a list of scores in the specified format.

    Args:
        scores (list[Score]): The scores to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Sink | None): Output sink. Defaults to StdoutSink.
    """
    if format == "pretty":
        from pyrit.printer.score.pretty import PrettyScorePrinter

        printer = PrettyScorePrinter(sink=sink or get_default_sink(StdoutSink))
    else:
        raise ValueError(f"Unsupported format for scores: {format!r}. Only 'pretty' is available.")

    await printer.write_async(scores)
