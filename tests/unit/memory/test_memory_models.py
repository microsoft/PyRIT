# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from pyrit.memory.memory_models import (
    AttackResultEntry,
    ConversationMessageWithSimilarity,
    EmbeddingDataEntry,
    EmbeddingMessageWithSimilarity,
    PromptMemoryEntry,
    ScenarioResultEntry,
    ScoreEntry,
    SeedEntry,
    UTCDateTime,
    _load_identifier,
)
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    MessagePiece,
    ScenarioResult,
    Score,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from unit.mocks import make_scenario_result

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
# UTCDateTime
# ---------------------------------------------------------------------------


def test_utcdatetime_attaches_utc_to_naive_datetime():
    naive = datetime(2024, 1, 1, 12, 0, 0, tzinfo=None)  # noqa: DTZ001
    result = UTCDateTime().process_result_value(naive, dialect=MagicMock())
    assert result is not None
    assert result.tzinfo == timezone.utc
    assert result.year == 2024


def test_utcdatetime_leaves_aware_datetime_unchanged():
    aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    result = UTCDateTime().process_result_value(aware, dialect=MagicMock())
    assert result == aware
    assert result.tzinfo == timezone.utc


def test_utcdatetime_passes_through_none():
    assert UTCDateTime().process_result_value(None, dialect=MagicMock()) is None


# ---------------------------------------------------------------------------
# Identifier (de)serialization helpers
# ---------------------------------------------------------------------------


def test_load_identifier_returns_none_for_falsy():
    assert _load_identifier(None) is None
    assert _load_identifier({}) is None


def test_dump_then_load_identifier_round_trips():
    identifier = ComponentIdentifier(class_name="MyConverter", class_module="pyrit.converters", pyrit_version="0.1.0")
    stored = identifier.model_dump()
    assert stored is not None
    loaded = _load_identifier(stored)
    assert loaded is not None
    assert loaded.class_name == "MyConverter"
    assert loaded.class_module == "pyrit.converters"


def test_load_identifier_injects_pyrit_version():
    identifier = ComponentIdentifier(class_name="MyConverter", class_module="pyrit.converters", pyrit_version="0.1.0")
    stored = identifier.model_dump()
    loaded = _load_identifier(stored, pyrit_version="9.9.9")
    assert loaded is not None
    assert loaded.pyrit_version == "9.9.9"


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

    def test_str_renders_role_and_value(self):
        piece = _make_message_piece()
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

    def test_init_from_seed_objective(self):
        obj = SeedObjective(
            value="objective text",
            name="obj1",
            dataset_name="ds",
            added_by="tester",
        )
        entry = SeedEntry(entry=obj)
        assert entry.seed_type == "objective"

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

    # ---- response_json_schema persistence ---------------------------------

    def test_roundtrip_seed_prompt_preserves_inline_response_json_schema(self):
        """Inline ``response_json_schema`` round-trips through ``SeedEntry``."""
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}, "y": {"type": "integer"}},
            "required": ["x"],
        }
        seed = _make_seed_prompt(response_json_schema=schema)
        entry = SeedEntry(entry=seed)
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedPrompt)
        assert recovered.response_json_schema == schema

    def test_roundtrip_seed_prompt_with_named_schema_kwarg(self):
        """Named-schema construction round-trips as the resolved body."""
        from pyrit.models.json_schema_definition import get_common_json_schema

        expected = get_common_json_schema("true_false_with_rationale")
        seed = _make_seed_prompt(response_json_schema_name="true_false_with_rationale")
        entry = SeedEntry(entry=seed)
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedPrompt)
        assert recovered.response_json_schema == expected
        # InitVar is never persisted as a name; only the resolved body survives.
        assert "response_json_schema_name" not in recovered.__dict__

    def test_seed_prompt_without_schema_does_not_leak_reserved_key(self):
        """A SeedPrompt without a schema must NOT carry the reserved key out of the DB."""
        from pyrit.models import SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY

        seed = _make_seed_prompt(metadata={"format": "png"})
        entry = SeedEntry(entry=seed)
        assert entry.prompt_metadata == {"format": "png"}
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedPrompt)
        assert recovered.response_json_schema is None
        assert SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY not in (recovered.metadata or {})
        assert (recovered.metadata or {}).get("format") == "png"

    def test_seed_prompt_forged_reserved_key_is_stripped_on_save(self):
        """A caller-forged reserved key in metadata is dropped, not persisted."""
        from pyrit.models import SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY

        forged = {
            SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY: "evil",
            "keep": "me",
        }
        seed = _make_seed_prompt(metadata=forged)
        entry = SeedEntry(entry=seed)
        assert SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY not in entry.prompt_metadata
        assert entry.prompt_metadata.get("keep") == "me"
        recovered = entry.get_seed()
        assert recovered.response_json_schema is None

    def test_roundtrip_seed_objective_strips_reserved_key(self):
        """SeedObjective doesn't have a schema field, but the reserved key must still be stripped."""
        from pyrit.models import SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY

        obj = SeedObjective(
            value="objective text",
            name="obj1",
            dataset_name="ds",
            added_by="tester",
            metadata={
                SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY: "sneaky",
                "owned": "by-caller",
            },
        )
        entry = SeedEntry(entry=obj)
        assert SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY not in entry.prompt_metadata
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedObjective)
        assert SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY not in (recovered.metadata or {})
        assert (recovered.metadata or {}).get("owned") == "by-caller"

    def test_roundtrip_seed_simulated_conversation_strips_reserved_key(self):
        """SeedSimulatedConversation also has no schema field; reserved key must still be stripped."""
        from pyrit.models import SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY

        config = SeedSimulatedConversation(
            num_turns=3,
            adversarial_chat_system_prompt_path="/path/to/adversarial.yaml",
            simulated_target_system_prompt_path="/path/to/target.yaml",
            metadata={
                SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY: "sneaky",
                "owned": "by-caller",
            },
        )
        entry = SeedEntry(entry=config)
        assert SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY not in entry.prompt_metadata
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedSimulatedConversation)
        assert SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY not in (recovered.metadata or {})
        assert (recovered.metadata or {}).get("owned") == "by-caller"

    def test_corrupt_reserved_key_unpack_returns_no_schema(self):
        """A malformed JSON-encoded schema in the DB must round-trip as no schema, with clean metadata."""
        from pyrit.models import SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY

        seed = _make_seed_prompt()
        entry = SeedEntry(entry=seed)
        # Simulate corruption that bypasses the write-time pack: write garbage directly.
        entry.prompt_metadata = {
            SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY: "{not valid json",
            "keep": "me",
        }
        recovered = entry.get_seed()
        assert isinstance(recovered, SeedPrompt)
        # The reserved key never leaks back out, and the rest of the metadata survives.
        assert SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY not in (recovered.metadata or {})
        assert (recovered.metadata or {}).get("keep") == "me"
        assert recovered.response_json_schema is None

    def test_non_json_serializable_schema_raises_descriptive_error(self):
        """A schema containing non-JSON-native values must raise with context, not a bare TypeError."""
        # ``set`` is not JSON-serializable; json.dumps would normally raise a bare
        # ``TypeError: Object of type set is not JSON serializable`` with no hint
        # about which seed produced it.
        seed = _make_seed_prompt(name="bad-schema-seed", response_json_schema={"enum": {1, 2, 3}})
        with pytest.raises(TypeError, match="bad-schema-seed.*not JSON-serializable"):
            SeedEntry(entry=seed)


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

    def test_get_attack_result_prefers_atomic_over_stale_attack_identifier(self):
        """When atomic_attack_identifier and attack_identifier disagree, atomic wins."""
        correct_attack_id = ComponentIdentifier(class_name="CorrectAttack", class_module="pyrit.backend")
        atomic_id = AtomicAttackIdentifier.build(attack_identifier=correct_attack_id)
        ar = _make_attack_result(atomic_attack_identifier=atomic_id)
        entry = AttackResultEntry(entry=ar)

        # Simulate a stale attack_identifier column (as if it wasn't updated)
        stale_id = ComponentIdentifier(class_name="StaleAttack", class_module="pyrit.backend")
        entry.attack_identifier = stale_id.model_dump()

        round_tripped = entry.get_attack_result()
        strategy = round_tripped.get_attack_strategy_identifier()
        assert strategy is not None
        assert strategy.class_name == "CorrectAttack"


# ---------------------------------------------------------------------------
# ScenarioResultEntry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioResultEntry:
    def _make_scenario_result(self, **overrides) -> ScenarioResult:
        defaults = {
            "scenario_name": "test_scenario",
            "scenario_description": "desc",
            "objective_target_identifier": ComponentIdentifier(class_name="MockTarget", class_module="tests.mocks"),
            "attack_results": {},
            "objective_scorer_identifier": ComponentIdentifier(class_name="MockScorer", class_module="pyrit.score"),
            "scenario_run_state": "COMPLETED",
            "labels": {"env": "test"},
            "number_tries": 1,
            "completion_time": datetime.now(tz=timezone.utc),
        }
        defaults.update(overrides)
        return make_scenario_result(**defaults)

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
        assert recovered.scenario_name == "test_scenario"
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
