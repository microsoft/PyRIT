# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from pyrit.identifiers import ComponentIdentifier
from pyrit.memory.memory_models import (
    AttackResultEntry,
    ConversationMessageWithSimilarity,
    EmbeddingDataEntry,
    EmbeddingMessageWithSimilarity,
    PromptMemoryEntry,
    ScenarioResultEntry,
    ScoreEntry,
    SeedEntry,
    _ensure_utc,
)
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ConversationReference,
    ConversationType,
    MessagePiece,
    ScenarioIdentifier,
    ScenarioResult,
    Score,
    SeedObjective,
    SeedPrompt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message_piece(**overrides) -> MessagePiece:
    """Create a MessagePiece with sensible defaults; any keyword overrides a default field."""
    defaults = {
        "role": "user",
        "original_value": "hello",
        "converted_value": "hello converted",
        "conversation_id": str(uuid.uuid4()),
        "sequence": 0,
        "labels": {"label1": "value1"},
        "prompt_metadata": {"meta": "data"},
        "converter_identifiers": [ComponentIdentifier(class_name="NoOp", class_module="pyrit.converters")],
        "prompt_target_identifier": ComponentIdentifier(class_name="MockTarget", class_module="tests.mocks"),
        "attack_identifier": ComponentIdentifier(class_name="MockAttack", class_module="tests.mocks"),
        "original_value_data_type": "text",
        "converted_value_data_type": "text",
        "response_error": "none",
    }
    defaults.update(overrides)
    return MessagePiece(**defaults)


def _make_score(**overrides) -> Score:
    """Create a Score with sensible defaults; any keyword overrides a default field."""
    defaults = {
        "score_value": "0.9",
        "score_value_description": "High",
        "score_type": "float_scale",
        "score_category": ["test"],
        "score_rationale": "Good result",
        "score_metadata": {"key": "val"},
        "scorer_class_identifier": ComponentIdentifier(class_name="MockScorer", class_module="pyrit.score"),
        "message_piece_id": uuid.uuid4(),
        "objective": "test objective",
    }
    defaults.update(overrides)
    return Score(**defaults)


def _make_seed_prompt(**overrides) -> SeedPrompt:
    """Create a SeedPrompt with sensible defaults; any keyword overrides a default field."""
    defaults = {
        "value": "test seed value",
        "data_type": "text",
        "name": "test_seed",
        "dataset_name": "test_dataset",
        "harm_categories": ["hate"],
        "description": "a test seed",
        "authors": ["author1"],
        "groups": ["group1"],
        "source": "unit_test",
        "added_by": "tester",
    }
    defaults.update(overrides)
    return SeedPrompt(**defaults)


def _make_attack_result(**overrides) -> AttackResult:
    """Create an AttackResult with sensible defaults; any keyword overrides a default field."""
    defaults = {
        "conversation_id": str(uuid.uuid4()),
        "objective": "test objective",
        "atomic_attack_identifier": ComponentIdentifier(class_name="MockAttack", class_module="tests.mocks"),
        "executed_turns": 3,
        "execution_time_ms": 1500,
        "outcome": AttackOutcome.SUCCESS,
        "outcome_reason": "jailbreak achieved",
    }
    defaults.update(overrides)
    return AttackResult(**defaults)


# ---------------------------------------------------------------------------
# _ensure_utc
# ---------------------------------------------------------------------------


def test_ensure_utc_with_none():
    assert _ensure_utc(None) is None


def test_ensure_utc_naive_datetime_gets_utc():
    naive = datetime(2024, 1, 1, 12, 0, 0, tzinfo=None)  # noqa: DTZ001
    result = _ensure_utc(naive)
    assert result.tzinfo == timezone.utc
    assert result.year == 2024


def test_ensure_utc_aware_datetime_unchanged():
    aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    result = _ensure_utc(aware)
    assert result == aware
    assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# ConversationMessageWithSimilarity
# ---------------------------------------------------------------------------


def test_conversation_message_with_similarity_defaults():
    msg = ConversationMessageWithSimilarity(role="user", content="hi", metric="cosine")
    assert msg.score == 0.0
    assert msg.role == "user"


def test_conversation_message_with_similarity_forbids_extra():
    with pytest.raises(ValidationError):
        ConversationMessageWithSimilarity(role="user", content="hi", metric="cosine", unknown_field="x")


# ---------------------------------------------------------------------------
# EmbeddingMessageWithSimilarity
# ---------------------------------------------------------------------------


def test_embedding_message_with_similarity_defaults():
    uid = uuid.uuid4()
    msg = EmbeddingMessageWithSimilarity(uuid=uid, metric="cosine")
    assert msg.score == 0.0
    assert msg.uuid == uid


def test_embedding_message_with_similarity_forbids_extra():
    with pytest.raises(ValidationError):
        EmbeddingMessageWithSimilarity(uuid=uuid.uuid4(), metric="cosine", bad="x")


# ---------------------------------------------------------------------------
# PromptMemoryEntry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestPromptMemoryEntry:
    def test_init_from_message_piece(self):
        piece = _make_message_piece()
        entry = PromptMemoryEntry(entry=piece)
        assert entry.id == piece.id
        assert entry.role == "user"
        assert entry.original_value == "hello"
        assert entry.converted_value == "hello converted"
        assert entry.original_value_data_type == "text"
        assert entry.response_error == "none"

    def test_init_stores_converter_identifiers_as_dicts(self):
        piece = _make_message_piece()
        entry = PromptMemoryEntry(entry=piece)
        assert isinstance(entry.converter_identifiers, list)
        assert isinstance(entry.converter_identifiers[0], dict)

    def test_init_with_no_attack_identifier(self):
        piece = _make_message_piece(attack_identifier=None)
        entry = PromptMemoryEntry(entry=piece)
        assert entry.attack_identifier == {}

    def test_init_with_no_target_identifier(self):
        piece = _make_message_piece(prompt_target_identifier=None)
        entry = PromptMemoryEntry(entry=piece)
        assert entry.prompt_target_identifier == {}

    def test_roundtrip_get_message_piece(self):
        piece = _make_message_piece()
        entry = PromptMemoryEntry(entry=piece)
        # Simulate relationship loading
        entry.scores = []
        recovered = entry.get_message_piece()
        assert recovered.original_value == piece.original_value
        assert recovered.converted_value == piece.converted_value
        assert recovered.conversation_id == piece.conversation_id
        assert isinstance(recovered.converter_identifiers[0], ComponentIdentifier)

    def test_str_with_target_identifier(self):
        piece = _make_message_piece()
        entry = PromptMemoryEntry(entry=piece)
        s = str(entry)
        assert "MockTarget" in s
        assert "user" in s

    def test_str_without_target_identifier(self):
        piece = _make_message_piece(prompt_target_identifier=None)
        entry = PromptMemoryEntry(entry=piece)
        s = str(entry)
        assert "user" in s


# ---------------------------------------------------------------------------
# ScoreEntry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestScoreEntry:
    def test_init_from_score(self):
        score = _make_score()
        entry = ScoreEntry(entry=score)
        assert entry.id == score.id
        assert entry.score_value == "0.9"
        assert entry.score_type == "float_scale"
        assert entry.objective == "test objective"
        # backward compat: task == objective
        assert entry.task == "test objective"

    def test_roundtrip_get_score(self):
        score = _make_score()
        entry = ScoreEntry(entry=score)
        recovered = entry.get_score()
        assert recovered.score_value == score.score_value
        assert recovered.score_type == score.score_type
        assert isinstance(recovered.scorer_class_identifier, ComponentIdentifier)

    def test_to_dict(self):
        score = _make_score()
        entry = ScoreEntry(entry=score)
        d = entry.to_dict()
        assert d["score_value"] == "0.9"
        assert d["score_type"] == "float_scale"
        assert "id" in d
        assert "timestamp" in d


# ---------------------------------------------------------------------------
# EmbeddingDataEntry
# ---------------------------------------------------------------------------


def test_embedding_data_entry_str():
    entry = EmbeddingDataEntry()
    uid = uuid.uuid4()
    entry.id = uid
    assert str(entry) == str(uid)


# ---------------------------------------------------------------------------
# SeedEntry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestSeedEntry:
    def test_init_from_seed_prompt(self):
        seed = _make_seed_prompt()
        entry = SeedEntry(entry=seed)
        assert entry.value == "test seed value"
        assert entry.dataset_name == "test_dataset"
        assert entry.seed_type == "prompt"
        assert entry.is_objective is False

    def test_init_from_seed_objective(self):
        obj = SeedObjective(
            value="objective text",
            name="obj1",
            dataset_name="ds",
            added_by="tester",
        )
        entry = SeedEntry(entry=obj)
        assert entry.seed_type == "objective"
        assert entry.is_objective is True

    def test_roundtrip_seed_prompt(self):
        seed = _make_seed_prompt()
        entry = SeedEntry(entry=seed)
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedPrompt)
        assert recovered.value == "test seed value"
        assert recovered.dataset_name == "test_dataset"

    def test_roundtrip_seed_objective(self):
        obj = SeedObjective(
            value="objective text",
            name="obj1",
            dataset_name="ds",
            added_by="tester",
        )
        entry = SeedEntry(entry=obj)
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedObjective)
        assert recovered.value == "objective text"

    def test_seed_prompt_preserves_parameters(self):
        seed = _make_seed_prompt(parameters=["param1", "param2"])
        entry = SeedEntry(entry=seed)
        assert entry.parameters == ["param1", "param2"]


# ---------------------------------------------------------------------------
# AttackResultEntry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestAttackResultEntry:
    def test_init_from_attack_result(self):
        result = _make_attack_result()
        entry = AttackResultEntry(entry=result)
        assert entry.objective == "test objective"
        assert entry.executed_turns == 3
        assert entry.execution_time_ms == 1500
        assert entry.outcome == "success"
        assert entry.outcome_reason == "jailbreak achieved"

    def test_init_with_pruned_conversations(self):
        refs = {
            ConversationReference(
                conversation_id="conv1",
                conversation_type=ConversationType.PRUNED,
                description="pruned",
            )
        }
        result = _make_attack_result(related_conversations=refs)
        entry = AttackResultEntry(entry=result)
        assert entry.pruned_conversation_ids == ["conv1"]

    def test_init_with_adversarial_conversations(self):
        refs = {
            ConversationReference(
                conversation_id="adv1",
                conversation_type=ConversationType.ADVERSARIAL,
                description="adversarial",
            )
        }
        result = _make_attack_result(related_conversations=refs)
        entry = AttackResultEntry(entry=result)
        assert entry.adversarial_chat_conversation_ids == ["adv1"]

    def test_get_id_as_uuid_valid(self):
        obj = MagicMock()
        obj.id = str(uuid.uuid4())
        result = AttackResultEntry._get_id_as_uuid(obj)
        assert isinstance(result, uuid.UUID)

    def test_get_id_as_uuid_none(self):
        assert AttackResultEntry._get_id_as_uuid(None) is None

    def test_get_id_as_uuid_invalid(self):
        obj = MagicMock()
        obj.id = "not-a-uuid"
        assert AttackResultEntry._get_id_as_uuid(obj) is None

    def test_filter_json_serializable_metadata_empty(self):
        assert AttackResultEntry.filter_json_serializable_metadata({}) == {}
        assert AttackResultEntry.filter_json_serializable_metadata(None) == {}

    def test_filter_json_serializable_metadata_mixed(self):
        metadata = {
            "str_val": "hello",
            "int_val": 42,
            "non_serializable": MagicMock(),
        }
        result = AttackResultEntry.filter_json_serializable_metadata(metadata)
        assert "str_val" in result
        assert "int_val" in result
        assert "non_serializable" not in result


# ---------------------------------------------------------------------------
# ScenarioResultEntry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioResultEntry:
    def _make_scenario_result(self, **overrides) -> ScenarioResult:
        defaults = {
            "scenario_identifier": ScenarioIdentifier(name="test_scenario", description="desc"),
            "objective_target_identifier": ComponentIdentifier(class_name="MockTarget", class_module="tests.mocks"),
            "attack_results": {},
            "objective_scorer_identifier": ComponentIdentifier(class_name="MockScorer", class_module="pyrit.score"),
            "scenario_run_state": "COMPLETED",
            "labels": {"env": "test"},
            "number_tries": 1,
            "completion_time": datetime.now(tz=timezone.utc),
        }
        defaults.update(overrides)
        return ScenarioResult(**defaults)

    def test_init_from_scenario_result(self):
        sr = self._make_scenario_result()
        entry = ScenarioResultEntry(entry=sr)
        assert entry.scenario_name == "test_scenario"
        assert entry.scenario_description == "desc"
        assert entry.scenario_run_state == "COMPLETED"
        assert entry.labels == {"env": "test"}

    def test_roundtrip_get_scenario_result(self):
        sr = self._make_scenario_result()
        entry = ScenarioResultEntry(entry=sr)
        recovered = entry.get_scenario_result()
        assert recovered.scenario_identifier.name == "test_scenario"
        assert recovered.scenario_run_state == "COMPLETED"
        # attack_results should be empty after roundtrip (populated by memory_interface)
        assert recovered.attack_results == {}

    def test_get_conversation_ids_by_attack_name(self):
        attack_result = _make_attack_result()
        sr = self._make_scenario_result(attack_results={"attack1": [attack_result]})
        entry = ScenarioResultEntry(entry=sr)
        conv_ids = entry.get_conversation_ids_by_attack_name()
        assert "attack1" in conv_ids
        assert len(conv_ids["attack1"]) == 1

    def test_get_conversation_ids_by_attack_name_multiple_attacks(self):
        result_a = _make_attack_result()
        result_b = _make_attack_result()
        result_c = _make_attack_result()
        sr = self._make_scenario_result(attack_results={"attack1": [result_a, result_b], "attack2": [result_c]})
        entry = ScenarioResultEntry(entry=sr)
        conv_ids = entry.get_conversation_ids_by_attack_name()
        assert len(conv_ids["attack1"]) == 2
        assert len(conv_ids["attack2"]) == 1

    def test_str(self):
        sr = self._make_scenario_result()
        entry = ScenarioResultEntry(entry=sr)
        s = str(entry)
        assert "test_scenario" in s

    def test_init_with_empty_attack_results(self):
        sr = self._make_scenario_result(attack_results={})
        entry = ScenarioResultEntry(entry=sr)
        conv_ids = entry.get_conversation_ids_by_attack_name()
        assert conv_ids == {}
