# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Attack result printer classes."""

from pathlib import Path

from pyrit.printer.sink import OutputFormat, Sink, resolve_sink


async def print_attack_result_async(
    result: "AttackResult",  # noqa: F821
    *,
    format: OutputFormat = "pretty",
    to: Path | str | Sink | None = None,
    include_auxiliary_scores: bool = False,
    include_pruned_conversations: bool = False,
    include_adversarial_conversation: bool = False,
) -> None:
    """
    Print an attack result in the specified format to the specified destination.

    Args:
        result (AttackResult): The attack result to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        to (Path | str | Sink | None): Destination — None for stdout, path for file, or Sink instance.
        include_auxiliary_scores (bool): Whether to include auxiliary scores. Defaults to False.
        include_pruned_conversations (bool): Whether to include pruned conversations. Defaults to False.
        include_adversarial_conversation (bool): Whether to include the adversarial conversation.
            Defaults to False.
    """
    sink = resolve_sink(to)

    if format == "markdown":
        from pyrit.printer.attack_result.markdown import MarkdownAttackResultMemoryPrinter

        printer = MarkdownAttackResultMemoryPrinter(sink=sink)
    else:
        from pyrit.printer.attack_result.pretty import PrettyAttackResultMemoryPrinter

        printer = PrettyAttackResultMemoryPrinter(sink=sink)

    await printer.write_async(
        result,
        include_auxiliary_scores=include_auxiliary_scores,
        include_pruned_conversations=include_pruned_conversations,
        include_adversarial_conversation=include_adversarial_conversation,
    )
