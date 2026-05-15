# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario result printer classes."""

from pathlib import Path

from pyrit.printer.sink import OutputFormat, Sink, resolve_sink


async def print_scenario_result_async(
    result: "ScenarioResult",  # noqa: F821
    *,
    format: OutputFormat = "pretty",
    to: Path | str | Sink | None = None,
) -> None:
    """
    Print a scenario result in the specified format to the specified destination.

    Args:
        result (ScenarioResult): The scenario result to print.
        format (OutputFormat): Output format — "pretty" or "markdown". Defaults to "pretty".
        to (Path | str | Sink | None): Destination — None for stdout, path for file, or Sink instance.
    """
    sink = resolve_sink(to)

    if format == "pretty":
        from pyrit.printer.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

        printer = PrettyScenarioResultMemoryPrinter(sink=sink)
    else:
        raise ValueError(f"Unsupported format for scenario results: {format!r}. Only 'pretty' is available.")

    await printer.write_async(result)
