# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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
