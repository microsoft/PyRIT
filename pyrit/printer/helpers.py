# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Convenience functions for one-line printing of attack results, scenario results, and scorer info."""

from pathlib import Path

from pyrit.printer.sink import OutputFormat, Sink, resolve_sink


async def print_attack_result_async(
    result: "AttackResult",  # noqa: F821
    *,
    format: OutputFormat = "pretty",
    sink: Path | str | Sink | None = None,
    include_auxiliary_scores: bool = False,
    include_pruned_conversations: bool = False,
    include_adversarial_conversation: bool = False,
) -> None:
    """
    Print an attack result in the specified format to the specified destination.

    Args:
        result (AttackResult): The attack result to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Path | str | Sink | None): Destination — None for stdout, path for file, or Sink instance.
        include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
        include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
        include_adversarial_conversation (bool): Whether to include the adversarial conversation.
            Defaults to False.
    """
    resolved_sink = resolve_sink(sink)

    if format == "markdown":
        from pyrit.printer.attack_result.markdown import MarkdownAttackResultMemoryPrinter

        printer = MarkdownAttackResultMemoryPrinter(sink=resolved_sink)
    else:
        from pyrit.printer.attack_result.pretty import PrettyAttackResultMemoryPrinter

        printer = PrettyAttackResultMemoryPrinter(sink=resolved_sink)

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
    sink: Path | str | Sink | None = None,
) -> None:
    """
    Print a scenario result in the specified format to the specified destination.

    Args:
        result (ScenarioResult): The scenario result to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Path | str | Sink | None): Destination — None for stdout, path for file, or Sink instance.
    """
    resolved_sink = resolve_sink(sink)

    if format == "pretty":
        from pyrit.printer.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

        printer = PrettyScenarioResultMemoryPrinter(sink=resolved_sink)
    else:
        raise ValueError(f"Unsupported format for scenario results: {format!r}. Only 'pretty' is available.")

    await printer.write_async(result)


async def print_scorer_async(
    *,
    scorer_identifier: "ComponentIdentifier",  # noqa: F821
    harm_category: str | None = None,
    format: OutputFormat = "pretty",
    sink: Path | str | Sink | None = None,
) -> None:
    """
    Print scorer information in the specified format to the specified destination.

    Auto-detects scorer type: if harm_category is provided, renders harm
    metrics; otherwise renders objective metrics.

    Args:
        scorer_identifier (ComponentIdentifier): The scorer identifier.
        harm_category (str | None): The harm category. None for objective scorers.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        sink (Path | str | Sink | None): Destination — None for stdout, path for file, or Sink instance.
    """
    resolved_sink = resolve_sink(sink)

    if format == "pretty":
        from pyrit.printer.scorer.pretty import PrettyScorerMemoryPrinter

        printer = PrettyScorerMemoryPrinter(sink=resolved_sink)
    else:
        raise ValueError(f"Unsupported format for scorer: {format!r}. Only 'pretty' is available.")

    await printer.write_async(scorer_identifier=scorer_identifier, harm_category=harm_category)
