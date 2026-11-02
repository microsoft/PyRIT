# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import runpy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from build_scripts import evaluate_scorers
from pyrit import setup
from pyrit.exceptions.exception_classes import handle_bad_request_exception
from pyrit.memory import SQLiteMemory
from pyrit.models import Message, MessagePiece
from pyrit.registry import ScorerRegistry
from pyrit.score import (
    ScorerEvalDatasetFiles,
    ScorerMetrics,
    SelfAskRefusalScorer,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)


@pytest.fixture
def evaluation_path(tmp_path: Path) -> Path:
    (tmp_path / "synthetic.csv").write_text(
        "# dataset_version=1.0\nassistant_response,objective,human_score\n"
        "marker first,Synthetic objective,1\nmarker second,Synthetic objective,1\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("substring", "blocked_count", "blocked_trials", "excluded_count"),
    [
        ("marker", 0, (1, 2, 3), 0),
        ("marker", 1, (1, 2, 3), 1),
        ("marker", 2, (1, 2, 3), 2),
        ("marker", 1, (2,), 1),
        ("marker", 2, (2,), 2),
        ("absent", 2, (1, 2, 3), 0),
    ],
)
async def test_evaluate_scorers_handles_blocked_composite_judgments_async(
    *,
    substring: str,
    blocked_count: int,
    blocked_trials: tuple[int, ...],
    excluded_count: int,
    evaluation_path: Path,
    sqlite_instance: SQLiteMemory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = MockPromptTarget()
    refusal = SelfAskRefusalScorer(chat_target=target)
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[SubStringScorer(substring=substring), TrueFalseInverterScorer(scorer=refusal)],
    )
    scorer.evaluation_file_mapping = ScorerEvalDatasetFiles(
        human_labeled_datasets_files=["synthetic.csv"], result_file="metrics.jsonl"
    )
    registry = ScorerRegistry()
    registry.instances.register(scorer, name="composite", tags=["objective_composite"])
    trials = {"first": 0, "second": 0}

    async def send_async(*, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1].message_pieces[0]
        response_name = "first" if "marker first" in request.converted_value else "second"
        trials[response_name] += 1
        if trials[response_name] in blocked_trials and (
            blocked_count == 2 or (blocked_count == 1 and response_name == "first")
        ):
            return [
                handle_bad_request_exception(
                    response_text='{"error": {"code": "cyber_policy", "message": "Synthetic policy block"}}',
                    request=request,
                )
            ]
        return [
            MessagePiece(
                role="assistant",
                original_value='{"score_value": "False", "description": "Not a refusal", "rationale": "Synthetic"}',
                conversation_id=request.conversation_id,
            ).to_message()
        ]

    with (
        patch.object(evaluate_scorers, "initialize_pyrit_async", new_callable=AsyncMock),
        patch.object(ScorerRegistry, "get_registry_singleton", return_value=registry),
        patch("pyrit.score.scorer_evaluation.scorer_evaluator.SCORER_EVALS_PATH", evaluation_path),
        patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock, side_effect=send_async) as send,
    ):
        if excluded_count == 2:
            with pytest.raises(RuntimeError, match="Evaluation failed for 1 scorer"):
                await evaluate_scorers.evaluate_scorers(tags=["objective_composite"], max_concurrency=1)
        else:
            await evaluate_scorers.evaluate_scorers(tags=["objective_composite"], max_concurrency=1)
            assert send.await_count == 6
            send.reset_mock()
            await evaluate_scorers.evaluate_scorers(tags=["objective_composite"], max_concurrency=1)
            send.assert_not_awaited()

    assert trials == {"first": 3, "second": 3}
    assert refusal.raise_if_scorer_blocks is True
    assert registry.instances.get("composite") is scorer
    scores = await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True)
    judge_scores = [score for score in scores if score.scorer_class_identifier.class_name == "SelfAskRefusalScorer"]
    assert len(judge_scores) == 6
    assert sum(score.is_undetermined for score in judge_scores) == blocked_count * len(blocked_trials)
    for score in judge_scores:
        if score.is_undetermined:
            assert score.score_value is None
            assert score.observation_ids
            assert "blocked by content filtering" in score.score_rationale
    final_scores = [score for score in scores if score.scorer_class_identifier.class_name == "TrueFalseCompositeScorer"]
    assert len(final_scores) == 6
    assert sum(score.is_undetermined for score in final_scores) == excluded_count * len(blocked_trials)

    output = capsys.readouterr().out
    result_path = evaluation_path / "metrics.jsonl"
    if excluded_count == 2:
        assert not result_path.exists()
        assert "no determined scores" in output
        assert "Evaluation complete" not in output
    else:
        record = json.loads(await asyncio.to_thread(result_path.read_text, encoding="utf-8"))
        assert record["metrics"]["num_input_responses"] == 2
        assert record["metrics"]["num_responses"] == 2 - excluded_count
        assert record["metrics"]["num_scorer_trials"] == 3
        assert record["metrics"]["accuracy"] == (1.0 if substring == "marker" else 0.0)
        assert record["metrics"]["f1_score"] == (1.0 if substring == "marker" else 0.0)
        assert f"Coverage: {2 - excluded_count}/2 responses evaluated; {excluded_count} excluded." in output
        assert ("WARNING: Partial coverage" in output) == (excluded_count == 1)
        assert "Evaluation complete (new or cached metrics)." in output
        assert "complete and saved" not in output
        assert "Results saved to:" not in output


@pytest.mark.usefixtures("patch_central_database")
async def test_evaluate_scorers_reports_unknown_legacy_coverage_async(
    *, evaluation_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scorer = SubStringScorer(substring="marker")
    scorer.evaluation_file_mapping = ScorerEvalDatasetFiles(
        human_labeled_datasets_files=["synthetic.csv"], result_file="metrics.jsonl"
    )
    registry = ScorerRegistry()
    registry.instances.register(scorer, name="legacy")
    with (
        patch.object(evaluate_scorers, "initialize_pyrit_async", new_callable=AsyncMock),
        patch.object(ScorerRegistry, "get_registry_singleton", return_value=registry),
        patch("pyrit.score.scorer_evaluation.scorer_evaluator.SCORER_EVALS_PATH", evaluation_path),
    ):
        await evaluate_scorers.evaluate_scorers()
        capsys.readouterr()
        result_path = evaluation_path / "metrics.jsonl"
        record = json.loads(await asyncio.to_thread(result_path.read_text, encoding="utf-8"))
        del record["metrics"]["num_input_responses"]
        legacy_record = json.dumps(record) + "\n"
        await asyncio.to_thread(result_path.write_text, legacy_record, encoding="utf-8")

        with patch.object(scorer, "score_async", new_callable=AsyncMock) as score:
            await evaluate_scorers.evaluate_scorers()
        score.assert_not_awaited()
        assert await asyncio.to_thread(result_path.read_text, encoding="utf-8") == legacy_record

    output = capsys.readouterr().out
    assert "Coverage: unknown; 2 evaluated, input/excluded counts unavailable." in output
    assert "2/2" not in output
    assert "Evaluation complete (new or cached metrics)." in output
    assert "Results saved to:" not in output


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("error", [None, ValueError("Invalid dataset")])
def test_evaluate_scorers_exits_nonzero_after_remaining_scorers(
    *, error: ValueError | None, capsys: pytest.CaptureFixture[str]
) -> None:
    failing = SubStringScorer(substring="failing")
    succeeding = SubStringScorer(substring="succeeding")
    registry = ScorerRegistry()
    registry.instances.register(failing, name="a_failing")
    registry.instances.register(succeeding, name="b_succeeding")
    metrics = ScorerMetrics(num_responses=1, num_input_responses=1, num_human_raters=1)
    with (
        patch.object(setup, "initialize_pyrit_async", new_callable=AsyncMock),
        patch.object(ScorerRegistry, "get_registry_singleton", return_value=registry),
        patch.object(failing, "evaluate_async", new_callable=AsyncMock, return_value=None, side_effect=error),
        patch.object(succeeding, "evaluate_async", new_callable=AsyncMock, return_value=metrics) as succeeding_eval,
        patch.object(sys, "argv", ["evaluate_scorers"]),
    ):
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(evaluate_scorers.__file__, run_name="__main__")

    assert exit_info.value.code == 1
    succeeding_eval.assert_awaited_once()
    output = capsys.readouterr().out
    assert ("Invalid dataset" if error else "Evaluation returned no metrics") in output
    assert "Coverage: 1/1 responses evaluated; 0 excluded." in output
    assert "Fatal error: Evaluation failed for 1 scorer(s): a_failing" in output
    assert "Results location:" not in output
