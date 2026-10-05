# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import make_scenario_result

from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
    ScenarioResult,
    Score,
)
from pyrit.output.conversation.source import MemoryConversationSource
from pyrit.output.helpers import (
    output_attack_async,
    output_conversation_async,
    output_scenario_async,
    output_scenario_attacks_async,
    output_scenario_conversations_async,
    output_scenario_full_async,
    output_score_async,
    output_scorer_async,
)
from pyrit.output.scenario_result.json import JsonScenarioResultMemoryPrinter
from pyrit.output.sink import FileSink, IPythonMarkdownSink, StdoutSink, get_default_sink

# --- get_default_sink tests ---


def test_get_default_sink_no_default_returns_stdout_outside_notebook():
    sink = get_default_sink()
    assert isinstance(sink, StdoutSink)


def test_get_default_sink_explicit_default():
    sink = get_default_sink(IPythonMarkdownSink)
    assert isinstance(sink, IPythonMarkdownSink)


def test_get_default_sink_explicit_stdout():
    sink = get_default_sink(StdoutSink)
    assert isinstance(sink, StdoutSink)


@patch("pyrit.common.notebook_utils.is_in_ipython_session", return_value=True)
def test_get_default_sink_auto_detects_notebook(_mock):
    sink = get_default_sink()
    assert isinstance(sink, IPythonMarkdownSink)


# --- output_attack_async tests ---


@patch("pyrit.output.helpers.PrettyAttackResultMemoryPrinter")
async def test_output_attack_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_attack_async(result)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.helpers.MarkdownAttackResultMemoryPrinter")
async def test_output_attack_async_markdown_auto_detects_sink(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_attack_async(result, format="markdown")

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    # Outside a notebook, auto-detect falls back to StdoutSink
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.helpers.PrettyAttackResultMemoryPrinter")
async def test_output_attack_async_explicit_sink(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()
    custom_sink = StdoutSink()

    await output_attack_async(result, sink=custom_sink)

    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["sink"] is custom_sink


# --- output_scenario_async tests ---


@patch("pyrit.output.helpers.PrettyScenarioResultMemoryPrinter")
async def test_output_scenario_async_pretty(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_scenario_async(result)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once_with(result)


@patch("pyrit.output.helpers.PrettyScenarioResultMemoryPrinter")
async def test_output_scenario_async_forwards_sort_groups_by_success_rate(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_scenario_async(result, sort_groups_by_success_rate=True)

    assert mock_cls.call_args.kwargs["sort_groups_by_success_rate"] is True
    mock_printer.write_async.assert_called_once_with(result)


@patch("pyrit.output.helpers.PrettyAttackResultMemoryPrinter")
async def test_output_attack_async_forwards_reasoning_summaries(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_attack_async(result, include_reasoning_summaries=True)

    assert mock_printer.write_async.call_args.kwargs["include_reasoning_summaries"] is True


async def test_output_scenario_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_scenario_async(AsyncMock(), format="markdown")


# --- output_scorer_async tests ---


@patch("pyrit.output.helpers.PrettyScorerMemoryPrinter")
async def test_output_scorer_async_pretty(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scorer_id = MagicMock()

    await output_scorer_async(scorer_identifier=scorer_id)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once_with(scorer_identifier=scorer_id, harm_category=None)


@patch("pyrit.output.helpers.PrettyScorerMemoryPrinter")
async def test_output_scorer_async_with_harm_category(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scorer_id = MagicMock()

    await output_scorer_async(scorer_identifier=scorer_id, harm_category="hate_speech")

    mock_printer.write_async.assert_called_once_with(scorer_identifier=scorer_id, harm_category="hate_speech")


async def test_output_scorer_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_scorer_async(scorer_identifier=AsyncMock(), format="markdown")


# --- output_conversation_async tests ---


@patch("pyrit.output.helpers.PrettyConversationMemoryPrinter")
async def test_output_conversation_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    messages = [MagicMock()]

    await output_conversation_async(messages)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.helpers.PrettyConversationMemoryPrinter")
async def test_output_conversation_async_with_scores(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    messages = [MagicMock()]

    await output_conversation_async(messages, include_scores=True)

    mock_printer.write_async.assert_called_once_with(messages, include_scores=True, include_reasoning_summaries=False)


async def test_output_conversation_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_conversation_async([AsyncMock()], format="markdown")


# --- output_score_async tests ---


@patch("pyrit.output.helpers.PrettyScorePrinter")
async def test_output_score_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scores = [MagicMock()]

    await output_score_async(scores)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once_with(scores)


async def test_output_score_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_score_async([AsyncMock()], format="markdown")


# --- json-branch tests (each routes to the Json* memory printer) ---


@patch("pyrit.output.helpers.JsonScenarioResultMemoryPrinter")
async def test_output_scenario_async_json(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer

    await output_scenario_async(MagicMock(), format="json")

    mock_cls.assert_called_once()
    assert isinstance(mock_cls.call_args[1]["sink"], StdoutSink)
    mock_printer.write_async.assert_awaited_once()


@patch("pyrit.output.helpers.JsonScenarioResultMemoryPrinter")
async def test_output_scenario_attacks_async_json(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer

    await output_scenario_attacks_async(MagicMock(), format="json")

    mock_cls.assert_called_once()
    assert isinstance(mock_cls.call_args[1]["sink"], StdoutSink)
    mock_printer.write_async.assert_awaited_once()


@patch("pyrit.output.scorer.json.JsonScorerMemoryPrinter")
async def test_output_scorer_async_json(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer

    await output_scorer_async(scorer_identifier=MagicMock(), format="json")

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_awaited_once()


@patch("pyrit.output.helpers.JsonConversationMemoryPrinter")
async def test_output_conversation_async_json(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer

    await output_conversation_async([MagicMock()], format="json")

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_awaited_once()


@patch("pyrit.output.score.json.JsonScorePrinter")
async def test_output_score_async_json(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer

    await output_score_async([MagicMock()], format="json")

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_awaited_once()


# --- scenario conversations / full report helpers (memory-backed) ---

OBJECTIVE_SCORER = ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests")
REFUSAL_SCORER = ComponentIdentifier(class_name="RefusalScorer", class_module="tests")


async def _seed_attack_async(memory: MemoryInterface, *, objective: str, response: str) -> AttackResult:
    conversation_id = str(uuid.uuid4())
    user_piece = MessagePiece(role="user", original_value=objective, conversation_id=conversation_id)
    assistant_piece = MessagePiece(role="assistant", original_value=response, conversation_id=conversation_id)
    await memory.add_message_to_memory_async(request=Message(message_pieces=[user_piece]))
    await memory.add_message_to_memory_async(request=Message(message_pieces=[assistant_piece]))
    # The auxiliary score is stored first so selection can't rely on list order.
    await memory.add_scores_to_memory_async(
        scores=[
            Score(
                score_type="true_false",
                score_value="true",
                score_rationale="auxiliary: refused",
                message_piece_id=assistant_piece.id,
                scorer_class_identifier=REFUSAL_SCORER,
            ),
            Score(
                score_type="true_false",
                score_value="false",
                score_rationale="objective: not achieved",
                message_piece_id=assistant_piece.id,
                scorer_class_identifier=OBJECTIVE_SCORER,
            ),
        ]
    )
    return AttackResult(conversation_id=conversation_id, objective=objective, outcome=AttackOutcome.FAILURE)


@pytest.fixture
async def seeded_attacks(sqlite_instance) -> dict[str, list[AttackResult]]:
    return {
        "tech_a": [
            await _seed_attack_async(sqlite_instance, objective="objective one", response="response one"),
            await _seed_attack_async(sqlite_instance, objective="objective two", response="response two"),
        ],
        "tech_b": [
            await _seed_attack_async(sqlite_instance, objective="objective <b>three</b>", response="response three")
        ],
    }


@pytest.fixture
def scenario_result(seeded_attacks) -> ScenarioResult:
    return make_scenario_result(attack_results=seeded_attacks, objective_scorer_identifier=OBJECTIVE_SCORER)


def _all_attacks(result: ScenarioResult) -> list[AttackResult]:
    return [attack for attacks in result.attack_results.values() for attack in attacks]


def _piece_scores(conversation: dict) -> list[list[tuple[str, str]]]:
    return [
        [(score["scorer"], score["score_value"]) for score in piece.get("scores", [])]
        for message in conversation["messages"]
        for piece in message["pieces"]
    ]


@pytest.mark.parametrize("fmt", ["pretty", "markdown", "html"])
async def test_output_scenario_conversations_async_rejects_non_json_format(fmt):
    with pytest.raises(ValueError, match="Unsupported format for scenario conversations"):
        await output_scenario_conversations_async(MagicMock(), format=fmt)


@pytest.mark.parametrize("fmt", ["pretty", "markdown"])
async def test_output_scenario_full_async_rejects_unsupported_format(fmt):
    with pytest.raises(ValueError, match="Unsupported format for full scenario report"):
        await output_scenario_full_async(MagicMock(), format=fmt)


async def test_output_scenario_full_async_html_requires_sink():
    with pytest.raises(ValueError, match="requires an explicit sink"):
        await output_scenario_full_async(MagicMock(), format="html")


@pytest.mark.parametrize("helper", [output_scenario_conversations_async, output_scenario_full_async])
async def test_scenario_report_helpers_reject_negative_limit(helper):
    with pytest.raises(ValueError, match="limit must be zero or greater"):
        await helper(make_scenario_result(attack_results={}), limit=-1)


@pytest.mark.parametrize("helper", [output_scenario_conversations_async, output_scenario_full_async])
async def test_scenario_report_helpers_with_nothing_selected_skip_memory(helper, capsys):
    attacks = {"tech_a": [AttackResult(conversation_id="conv-1", objective="objective")]}

    with patch.object(CentralMemory, "get_memory_instance") as get_memory_instance:
        await helper(make_scenario_result(attack_results=attacks), limit=0)

    get_memory_instance.assert_not_called()
    assert json.loads(capsys.readouterr().out)["conversations"] == []


@pytest.mark.usefixtures("patch_central_database")
async def test_output_scenario_conversations_async_reads_memory_and_keeps_objective_score(scenario_result, capsys):
    await output_scenario_conversations_async(scenario_result)

    document = json.loads(capsys.readouterr().out)
    assert document["view"] == "conversations"
    assert document["scenario_result_id"] == str(scenario_result.id)
    conversations = document["conversations"]
    assert [entry["id"] for entry in conversations] == [a.attack_result_id for a in _all_attacks(scenario_result)]
    assert [entry["technique"] for entry in conversations] == ["tech_a", "tech_a", "tech_b"]
    first_messages = conversations[0]["messages"]
    assert [message["role"] for message in first_messages] == ["user", "assistant"]
    assert first_messages[1]["pieces"][0]["original_value"] == "response one"
    for entry in conversations:
        assert _piece_scores(entry) == [[], [("ObjectiveScorer", "false")]]


@pytest.mark.usefixtures("patch_central_database")
async def test_output_scenario_conversations_async_filters_ids_before_limit(scenario_result, tmp_path):
    _, second, third = _all_attacks(scenario_result)
    path = tmp_path / "conversations.json"

    with patch.object(
        MemoryConversationSource, "get_messages_async", autospec=True, return_value=[]
    ) as get_messages_async:
        await output_scenario_conversations_async(
            scenario_result,
            attack_result_ids=[third.attack_result_id, second.attack_result_id],
            limit=1,
            sink=FileSink(path=path),
        )

    document = json.loads(path.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in document["conversations"]] == [second.attack_result_id]
    assert [call.kwargs["conversation_id"] for call in get_messages_async.call_args_list] == [second.conversation_id]


@pytest.mark.usefixtures("patch_central_database")
async def test_output_scenario_conversations_async_includes_every_attack_by_default(capsys):
    attacks = [AttackResult(conversation_id=str(uuid.uuid4()), objective=f"objective {i}") for i in range(6)]

    await output_scenario_conversations_async(make_scenario_result(attack_results={"tech_a": attacks}))

    conversations = json.loads(capsys.readouterr().out)["conversations"]
    assert [entry["id"] for entry in conversations] == [attack.attack_result_id for attack in attacks]
    assert all(entry["messages"] == [] for entry in conversations)


async def test_output_scenario_conversations_async_scores_duplicated_conversation(
    sqlite_instance, patch_central_database, capsys
):
    original = await _seed_attack_async(sqlite_instance, objective="objective one", response="response one")
    duplicate = AttackResult(
        conversation_id=await sqlite_instance.duplicate_conversation_async(conversation_id=original.conversation_id),
        objective="objective one",
    )
    result = make_scenario_result(attack_results={"tech_a": [duplicate]}, objective_scorer_identifier=OBJECTIVE_SCORER)

    await output_scenario_conversations_async(result)

    conversation = json.loads(capsys.readouterr().out)["conversations"][0]
    assert _piece_scores(conversation) == [[], [("ObjectiveScorer", "false")]]


@pytest.mark.usefixtures("patch_central_database")
async def test_output_scenario_conversations_async_without_objective_scorer_omits_scores(seeded_attacks, capsys):
    result = make_scenario_result(attack_results=seeded_attacks, objective_scorer_identifier=None)

    await output_scenario_conversations_async(result)

    for entry in json.loads(capsys.readouterr().out)["conversations"]:
        assert _piece_scores(entry) == [[], []]


async def test_output_conversation_async_json_still_shows_every_score(sqlite_instance, patch_central_database, capsys):
    attack = await _seed_attack_async(sqlite_instance, objective="objective one", response="response one")
    messages = list(await sqlite_instance.get_conversation_messages_async(conversation_id=attack.conversation_id))

    await output_conversation_async(messages, format="json", include_scores=True)

    assistant_piece = json.loads(capsys.readouterr().out)[1]["pieces"][0]
    assert sorted(score["scorer"] for score in assistant_piece["scores"]) == ["ObjectiveScorer", "RefusalScorer"]


@pytest.mark.usefixtures("patch_central_database")
async def test_output_scenario_full_async_json_keeps_whole_scenario_overview(scenario_result, capsys):
    expected_overview = JsonScenarioResultMemoryPrinter().build(scenario_result, view="overview")

    await output_scenario_full_async(scenario_result, limit=1)

    document = json.loads(capsys.readouterr().out)
    assert document["view"] == "full"
    assert document["overview"] == expected_overview
    assert len(document["conversations"]) == 1
    assert _piece_scores(document["conversations"][0]) == [[], [("ObjectiveScorer", "false")]]


@pytest.mark.usefixtures("patch_central_database")
async def test_output_scenario_full_async_html_writes_report(scenario_result, tmp_path, capsys):
    path = tmp_path / "report.html"

    await output_scenario_full_async(scenario_result, format="html", sink=FileSink(path=path))

    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "response three" in html
    assert "objective: not achieved" in html
    assert "auxiliary: refused" not in html
    assert capsys.readouterr().out == ""
