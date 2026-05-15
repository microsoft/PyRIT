# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scorer printer classes."""

from pathlib import Path

from pyrit.printer.sink import OutputFormat, Sink, resolve_sink


async def print_scorer_async(
    *,
    scorer_identifier: "ComponentIdentifier",  # noqa: F821
    harm_category: str | None = None,
    format: OutputFormat = "pretty",
    to: Path | str | Sink | None = None,
) -> None:
    """
    Print scorer information in the specified format to the specified destination.

    Auto-detects scorer type: if harm_category is provided, renders harm
    metrics; otherwise renders objective metrics.

    Args:
        scorer_identifier (ComponentIdentifier): The scorer identifier.
        harm_category (str | None): The harm category. None for objective scorers.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        to (Path | str | Sink | None): Destination — None for stdout, path for file, or Sink instance.
    """
    sink = resolve_sink(to)

    if format == "pretty":
        from pyrit.printer.scorer.pretty import PrettyScorerMemoryPrinter

        printer = PrettyScorerMemoryPrinter(sink=sink)
    else:
        raise ValueError(f"Unsupported format for scorer: {format!r}. Only 'pretty' is available.")

    await printer.write_async(scorer_identifier=scorer_identifier, harm_category=harm_category)
