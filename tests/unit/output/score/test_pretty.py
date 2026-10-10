# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from colorama import Fore, Style

from pyrit.models import Score, ScoreStatus
from pyrit.output.score.pretty import PrettyScorePrinter


async def test_render_async_supports_unknown_score_type() -> None:
    printer = PrettyScorePrinter(enable_colors=False)
    score = Score(score_value="opaque", score_type="unknown")

    output = await printer.render_async([score])

    assert "Type: unknown" in output
    assert "Value: opaque" in output


async def test_render_async_supports_undetermined_score() -> None:
    printer = PrettyScorePrinter(enable_colors=False)
    score = Score(score_value=None, score_type="true_false", status=ScoreStatus.UNDETERMINED)

    output = await printer.render_async([score])

    assert "Value: undetermined" in output


@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        (["refusal"], "refusal"),
        (["refusal", "scams"], "refusal, scams"),
        (["scams", "refusal", "scams"], "scams, refusal, scams"),
    ],
)
async def test_render_async_joins_score_categories(*, categories: list[str], expected: str) -> None:
    printer = PrettyScorePrinter(enable_colors=False)
    score = Score(score_value="true", score_type="true_false", score_category=categories)

    output = await printer.render_async([score])

    assert f"Category: {expected}" in output


@pytest.mark.parametrize("enable_colors", [False, True])
@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        (["refusal\nscams"], r"refusal\nscams"),
        (["refusal\r\nscams"], r"refusal\r\nscams"),
        (["refusal\rscams"], r"refusal\rscams"),
        (["\nrefusal\n\n"], r"\nrefusal\n\n"),
        (["refusal\nscams", "other\ncategory"], r"refusal\nscams, other\ncategory"),
        ([r"refusal\nscams"], r"refusal\nscams"),
    ],
)
async def test_write_async_keeps_category_line_breaks_on_one_line(
    *,
    categories: list[str],
    expected: str,
    enable_colors: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    printer = PrettyScorePrinter(enable_colors=enable_colors)
    score = Score(
        score_value="true",
        score_type="true_false",
        score_category=categories,
        score_metadata={"note": "unchanged"},
    )
    original = score.model_dump(mode="json")

    await printer.write_async([score])

    lines = capsys.readouterr().out.splitlines()
    category_line = f"      \u2022 Category: {expected}"
    if enable_colors:
        category_line = f"{Fore.LIGHTMAGENTA_EX}{category_line}{Style.RESET_ALL}"
    assert lines[1] == category_line
    assert len(lines) == 4
    assert score.model_dump(mode="json") == original


async def test_render_async_renders_missing_category_as_na() -> None:
    printer = PrettyScorePrinter(enable_colors=False)
    score = Score(score_value="true", score_type="true_false")

    output = await printer.render_async([score])

    assert "Category: N/A" in output
