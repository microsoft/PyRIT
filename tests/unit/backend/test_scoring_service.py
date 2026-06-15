# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the scoring service.

Mocks ``ScorerRegistry``, ``CentralMemory``, and the per-scorer ``score_async`` to
exercise the orchestration logic in isolation.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.scoring import (
    CreateCustomScorerRequest,
    GeneralFloatScaleConfig,
    GeneralTrueFalseConfig,
    ScoreConversationRequest,
    ScoreMessageRequest,
    ThresholdWrapperConfig,
    UpdateCustomScorerRequest,
)
from pyrit.backend.services import scoring_service as scoring_service_module
from pyrit.backend.services.scoring_service import (
    ScoringService,
    get_scoring_service,
)
from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, build_atomic_attack_identifier
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.get_attack_results.return_value = []
    memory.get_conversation.return_value = []
    return memory


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.get.return_value = None
    registry.get_all_instances.return_value = []
    return registry


@pytest.fixture
def scoring_service(mock_memory, mock_registry):
    with (
        patch("pyrit.backend.services.scoring_service.CentralMemory") as mock_central,
        patch("pyrit.backend.services.scoring_service.ScorerRegistry") as mock_registry_cls,
    ):
        mock_central.get_memory_instance.return_value = mock_memory
        mock_registry_cls.get_registry_singleton.return_value = mock_registry
        # Bypass lru_cache so each test gets a fresh service instance bound to the mocks above.
        get_scoring_service.cache_clear()
        service = ScoringService()
        yield service
        get_scoring_service.cache_clear()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_attack_result(*, conversation_id: str = "conv-1", attack_result_id: str = "ar-1") -> AttackResult:
    target_identifier = ComponentIdentifier(
        class_name="TextTarget",
        class_module="pyrit.prompt_target",
    )
    now = datetime.now(timezone.utc)
    return AttackResult(
        conversation_id=conversation_id,
        objective="Test",
        atomic_attack_identifier=build_atomic_attack_identifier(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                children={"objective_target": target_identifier},
            ),
        ),
        outcome=AttackOutcome.UNDETERMINED,
        attack_result_id=attack_result_id,
        metadata={"created_at": now.isoformat(), "updated_at": now.isoformat()},
        labels={},
    )


def _make_piece(*, role: str = "assistant", piece_id: str | None = None) -> MagicMock:
    piece = MagicMock()
    piece.id = piece_id or uuid.uuid4()
    piece.role = role
    piece.api_role = "assistant" if role in ("assistant", "simulated_assistant") else role
    piece.scores = []
    return piece


def _make_message(pieces: list[MagicMock]) -> MagicMock:
    msg = MagicMock()
    msg.message_pieces = pieces
    return msg


def _make_pyrit_score(*, value: str = "true", category: str = "harm") -> MagicMock:
    score = MagicMock()
    score.id = uuid.uuid4()
    score.scorer_class_identifier = ComponentIdentifier(
        class_name="FakeScorer",
        class_module="tests",
    )
    score.score_type = "true_false"
    score.score_value = value
    score.score_category = [category]
    score.score_rationale = "because"
    score.timestamp = datetime.now(timezone.utc)
    return score


# --------------------------------------------------------------------------- #
# list_scorers_async
# --------------------------------------------------------------------------- #


class TestListScorers:
    async def test_returns_empty_when_no_scorers(self, scoring_service, mock_registry) -> None:
        mock_registry.get_all_instances.return_value = []

        result = await scoring_service.list_scorers_async()

        assert result.items == []

    async def test_returns_registered_scorers(self, scoring_service, mock_registry) -> None:
        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.scorer_type = "true_false"
        entry = MagicMock()
        entry.name = "my-scorer"
        entry.instance = scorer
        entry.tags = {"refusal": "", "best_refusal": ""}
        mock_registry.get_all_instances.return_value = [entry]

        result = await scoring_service.list_scorers_async()

        assert len(result.items) == 1
        item = result.items[0]
        assert item.scorer_registry_name == "my-scorer"
        assert item.score_type == "true_false"
        assert sorted(item.tags) == ["best_refusal", "refusal"]
        # MagicMock(spec=TrueFalseScorer) inherits TrueFalseScorer.__doc__,
        # so description should come from the real class docstring (first paragraph).
        assert item.description and len(item.description) > 0

    async def test_description_falls_back_to_none_when_class_has_no_docstring(
        self, scoring_service, mock_registry
    ) -> None:
        class _Undocumented:
            pass

        scorer = MagicMock()
        scorer.scorer_type = "true_false"
        scorer.__class__ = _Undocumented
        entry = MagicMock()
        entry.name = "undoc"
        entry.instance = scorer
        entry.tags = {}
        mock_registry.get_all_instances.return_value = [entry]

        result = await scoring_service.list_scorers_async()
        assert result.items[0].description is None
        assert result.items[0].tags == []

    async def test_uses_objective_is_read_from_scorer_instance(self, scoring_service, mock_registry) -> None:
        injecting = MagicMock(spec=TrueFalseScorer)
        injecting.scorer_type = "true_false"
        injecting.uses_objective = True
        injecting_entry = MagicMock()
        injecting_entry.name = "refusal"
        injecting_entry.instance = injecting
        injecting_entry.tags = {}

        non_injecting = MagicMock(spec=TrueFalseScorer)
        non_injecting.scorer_type = "true_false"
        non_injecting.uses_objective = False
        non_injecting_entry = MagicMock()
        non_injecting_entry.name = "substring"
        non_injecting_entry.instance = non_injecting
        non_injecting_entry.tags = {}

        mock_registry.get_all_instances.return_value = [injecting_entry, non_injecting_entry]

        result = await scoring_service.list_scorers_async()
        by_name = {item.scorer_registry_name: item for item in result.items}
        assert by_name["refusal"].uses_objective is True
        assert by_name["substring"].uses_objective is False


# --------------------------------------------------------------------------- #
# score_conversation_async
# --------------------------------------------------------------------------- #


class TestScoreConversation:
    async def test_raises_when_attack_missing(self, scoring_service, mock_memory) -> None:
        mock_memory.get_attack_results.return_value = []

        with pytest.raises(LookupError, match="not found"):
            await scoring_service.score_conversation_async(
                attack_result_id="missing",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_raises_when_conversation_not_in_attack(self, scoring_service, mock_memory) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result(conversation_id="conv-1")]

        with pytest.raises(ValueError, match="not part of attack"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="other-conv",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_raises_when_scorer_missing(self, scoring_service, mock_memory, mock_registry) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_registry.get.return_value = None

        with pytest.raises(ValueError, match="not registered"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="missing-scorer"),
            )

    async def test_raises_when_conversation_empty(self, scoring_service, mock_memory, mock_registry) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = []
        mock_registry.get.return_value = MagicMock(spec=TrueFalseScorer)

        with pytest.raises(ValueError, match="no messages to score"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_raises_when_last_message_has_no_assistant_turn(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [_make_message([_make_piece(role="user")])]
        mock_registry.get.return_value = MagicMock(spec=TrueFalseScorer)

        with pytest.raises(ValueError, match="no assistant message"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_last_message_scores_most_recent_assistant_turn(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        user_msg = _make_message([_make_piece(role="user")])
        first_assistant = _make_message([_make_piece(role="assistant")])
        last_assistant = _make_message([_make_piece(role="assistant")])
        trailing_user = _make_message([_make_piece(role="user")])
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [user_msg, first_assistant, user_msg, last_assistant, trailing_user]

        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.score_async = AsyncMock(return_value=[_make_pyrit_score()])
        mock_registry.get.return_value = scorer

        result = await scoring_service.score_conversation_async(
            attack_result_id="ar-1",
            conversation_id="conv-1",
            request=ScoreConversationRequest(scorer_registry_name="my-scorer", objective="be helpful"),
        )

        scorer.score_async.assert_awaited_once()
        kwargs = scorer.score_async.await_args.kwargs
        assert kwargs["message"] is last_assistant
        assert kwargs["objective"] == "be helpful"
        assert len(result.scores) == 1
        assert result.scores[0].score_value == "true"

    async def test_whole_conversation_wraps_scorer(self, scoring_service, mock_memory, mock_registry) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        # Whole-conv mode just hands the last message to the wrapped scorer; content doesn't matter.
        last = _make_message([_make_piece(role="assistant")])
        mock_memory.get_conversation.return_value = [last]

        scorer = MagicMock(spec=FloatScaleScorer)
        mock_registry.get.return_value = scorer

        with patch("pyrit.score.conversation_scorer.create_conversation_scorer") as mock_create:
            wrapped = MagicMock()
            wrapped.score_async = AsyncMock(return_value=[_make_pyrit_score()])
            mock_create.return_value = wrapped

            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="my-scorer", mode="whole_conversation"),
            )

            mock_create.assert_called_once_with(scorer=scorer)
            wrapped.score_async.assert_awaited_once()

    async def test_whole_conversation_rejects_unsupported_scorer(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [_make_message([_make_piece(role="assistant")])]
        mock_registry.get.return_value = MagicMock()  # Not a FloatScale/TrueFalse scorer.

        with pytest.raises(ValueError, match="FloatScaleScorer or TrueFalseScorer"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="my-scorer", mode="whole_conversation"),
            )


# --------------------------------------------------------------------------- #
# score_message_async
# --------------------------------------------------------------------------- #


class TestScoreMessage:
    async def test_scores_specific_piece(self, scoring_service, mock_memory, mock_registry) -> None:
        target_piece = _make_piece(role="assistant", piece_id="piece-target")
        other_piece = _make_piece(role="assistant", piece_id="piece-other")
        target_msg = _make_message([target_piece])
        other_msg = _make_message([other_piece])

        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [other_msg, target_msg]

        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.score_async = AsyncMock(return_value=[_make_pyrit_score()])
        mock_registry.get.return_value = scorer

        result = await scoring_service.score_message_async(
            attack_result_id="ar-1",
            conversation_id="conv-1",
            piece_id="piece-target",
            request=ScoreMessageRequest(scorer_registry_name="my-scorer"),
        )

        scorer.score_async.assert_awaited_once()
        assert scorer.score_async.await_args.kwargs["message"] is target_msg
        assert len(result.scores) == 1

    async def test_raises_when_piece_not_in_conversation(self, scoring_service, mock_memory, mock_registry) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [_make_message([_make_piece(role="assistant", piece_id="other")])]
        mock_registry.get.return_value = MagicMock(spec=TrueFalseScorer)

        with pytest.raises(LookupError, match="not part of conversation"):
            await scoring_service.score_message_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                piece_id="missing-piece",
                request=ScoreMessageRequest(scorer_registry_name="x"),
            )


# --------------------------------------------------------------------------- #
# Custom (user-created) scorers
# --------------------------------------------------------------------------- #


@pytest.fixture
def clear_custom_scorers():
    """Reset the module-level custom-scorer state before and after each test."""
    scoring_service_module._CUSTOM_SCORER_CONFIGS.clear()
    yield
    scoring_service_module._CUSTOM_SCORER_CONFIGS.clear()


@pytest.fixture
def custom_registry(mock_registry):
    """Configure the mocked registry so `name in registry` reads from a backing dict."""
    backing: dict[str, MagicMock] = {}
    mock_registry._registry_items = backing
    mock_registry._metadata_cache = MagicMock()
    mock_registry.__contains__ = lambda self, key: key in backing

    def _register_instance(instance, *, name, tags=None):
        backing[name] = instance

    def _get(name):
        return backing.get(name)

    def _get_all_instances():
        entries = []
        for n, inst in backing.items():
            entry = MagicMock()
            entry.name = n
            entry.instance = inst
            entry.tags = {}
            entries.append(entry)
        return entries

    mock_registry.register_instance = MagicMock(side_effect=_register_instance)
    mock_registry.get = MagicMock(side_effect=_get)
    mock_registry.get_all_instances = MagicMock(side_effect=_get_all_instances)
    return mock_registry


def _patch_default_target():
    """Helper: patch `_get_default_chat_target` to return a benign MagicMock."""
    return patch.object(ScoringService, "_get_default_chat_target", return_value=MagicMock())


class TestCreateCustomScorer:
    async def test_general_float_scale_registers_scorer(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        cfg = GeneralFloatScaleConfig(
            system_prompt_format_string="Score {prompt} from 0-10",
            category="harm",
            min_value=0,
            max_value=10,
        )
        with (
            _patch_default_target(),
            patch(
                "pyrit.score.float_scale.self_ask_general_float_scale_scorer.SelfAskGeneralFloatScaleScorer"
            ) as mock_cls,
        ):
            built = MagicMock(spec=FloatScaleScorer)
            built.scorer_type = "float_scale"
            built.uses_objective = True  # set by class default; service should override to False
            mock_cls.return_value = built

            response = await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="my_scale", config=cfg),
            )

        assert response.summary.scorer_registry_name == "my_scale"
        assert response.summary.editable is True
        assert response.summary.custom_config == cfg
        assert "my_scale" in scoring_service_module._CUSTOM_SCORER_CONFIGS
        custom_registry.register_instance.assert_called_once()
        mock_cls.assert_called_once()
        # min_value/max_value/category propagated
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["min_value"] == 0
        assert call_kwargs["max_value"] == 10
        assert call_kwargs["category"] == "harm"
        # requires_objective=False (default) → validator opts out and uses_objective is overridden to False
        validator = call_kwargs["validator"]
        assert validator._is_objective_required is False
        assert built.uses_objective is False
        assert response.summary.uses_objective is False

    async def test_general_float_scale_with_requires_objective(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        cfg = GeneralFloatScaleConfig(
            system_prompt_format_string="Objective: {objective}. Score {prompt}.",
            min_value=0,
            max_value=10,
            requires_objective=True,
        )
        with (
            _patch_default_target(),
            patch(
                "pyrit.score.float_scale.self_ask_general_float_scale_scorer.SelfAskGeneralFloatScaleScorer"
            ) as mock_cls,
        ):
            built = MagicMock(spec=FloatScaleScorer)
            built.scorer_type = "float_scale"
            built.uses_objective = False
            mock_cls.return_value = built

            response = await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="needs_obj", config=cfg),
            )

        validator = mock_cls.call_args.kwargs["validator"]
        assert validator._is_objective_required is True
        assert built.uses_objective is True
        assert response.summary.uses_objective is True

    async def test_general_true_false_registers_scorer(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        cfg = GeneralTrueFalseConfig(
            system_prompt_format_string="Is {prompt} bad?",
            score_aggregator="AND",
        )
        with (
            _patch_default_target(),
            patch(
                "pyrit.score.true_false.self_ask_general_true_false_scorer.SelfAskGeneralTrueFalseScorer"
            ) as mock_cls,
            patch("pyrit.score.true_false.true_false_score_aggregator.TrueFalseScoreAggregator") as mock_aggregator_ns,
        ):
            mock_aggregator_ns.AND = "AND_FUNC"
            built = MagicMock(spec=TrueFalseScorer)
            built.scorer_type = "true_false"
            built.uses_objective = True
            mock_cls.return_value = built

            response = await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="my_tf", config=cfg),
            )

        assert response.summary.scorer_registry_name == "my_tf"
        assert response.summary.editable is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["score_aggregator"] == "AND_FUNC"
        validator = call_kwargs["validator"]
        assert validator._is_objective_required is False
        assert built.uses_objective is False
        assert response.summary.uses_objective is False

    async def test_threshold_wrapper_registers_scorer(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        # Pre-seed the registry with a float-scale scorer to wrap.
        wrapped = MagicMock(spec=FloatScaleScorer)
        wrapped.scorer_type = "float_scale"
        wrapped.uses_objective = False
        custom_registry._registry_items["base_float"] = wrapped

        cfg = ThresholdWrapperConfig(
            wrapped_scorer_registry_name="base_float",
            threshold=0.75,
        )
        with patch("pyrit.score.true_false.float_scale_threshold_scorer.FloatScaleThresholdScorer") as mock_cls:
            built = MagicMock(spec=TrueFalseScorer)
            built.scorer_type = "true_false"
            built.uses_objective = False
            mock_cls.return_value = built

            response = await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="my_thresh", config=cfg),
            )

        assert response.summary.scorer_registry_name == "my_thresh"
        mock_cls.assert_called_once_with(scorer=wrapped, threshold=0.75)

    async def test_rejects_duplicate_name(self, scoring_service, custom_registry, clear_custom_scorers) -> None:
        # Pre-populate the registry with the same name.
        custom_registry._registry_items["taken"] = MagicMock()
        cfg = GeneralFloatScaleConfig(
            system_prompt_format_string="x",
            min_value=0,
            max_value=10,
        )
        with pytest.raises(ValueError, match="already registered"):
            await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="taken", config=cfg),
            )
        # No state pollution.
        assert "taken" not in scoring_service_module._CUSTOM_SCORER_CONFIGS

    async def test_rejects_max_value_not_greater_than_min(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        cfg = GeneralFloatScaleConfig(
            system_prompt_format_string="x",
            min_value=5,
            max_value=5,
        )
        with _patch_default_target(), pytest.raises(ValueError, match="max_value must be strictly greater"):
            await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="bad", config=cfg),
            )
        assert "bad" not in scoring_service_module._CUSTOM_SCORER_CONFIGS

    async def test_threshold_wrapper_rejects_missing_wrapped(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        cfg = ThresholdWrapperConfig(wrapped_scorer_registry_name="does_not_exist", threshold=0.5)
        with pytest.raises(ValueError, match="is not registered"):
            await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="thresh", config=cfg),
            )

    async def test_threshold_wrapper_rejects_non_float_scale_wrapped(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        wrapped = MagicMock(spec=TrueFalseScorer)
        custom_registry._registry_items["tf_scorer"] = wrapped
        cfg = ThresholdWrapperConfig(wrapped_scorer_registry_name="tf_scorer", threshold=0.5)
        with pytest.raises(ValueError, match="requires a FloatScaleScorer"):
            await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="thresh", config=cfg),
            )


class TestUpdateCustomScorer:
    async def test_replaces_instance_and_preserves_name(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        original_cfg = GeneralFloatScaleConfig(
            system_prompt_format_string="orig",
            min_value=0,
            max_value=10,
        )
        new_cfg = GeneralFloatScaleConfig(
            system_prompt_format_string="updated",
            min_value=0,
            max_value=100,
            category="bias",
        )

        with (
            _patch_default_target(),
            patch(
                "pyrit.score.float_scale.self_ask_general_float_scale_scorer.SelfAskGeneralFloatScaleScorer"
            ) as mock_cls,
        ):
            orig_built = MagicMock(spec=FloatScaleScorer)
            orig_built.scorer_type = "float_scale"
            orig_built.uses_objective = False
            updated_built = MagicMock(spec=FloatScaleScorer)
            updated_built.scorer_type = "float_scale"
            updated_built.uses_objective = False
            mock_cls.side_effect = [orig_built, updated_built]

            await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="ed", config=original_cfg),
            )
            response = await scoring_service.update_custom_scorer_async(
                scorer_id="ed",
                request=UpdateCustomScorerRequest(config=new_cfg),
            )

        assert response.summary.scorer_registry_name == "ed"
        assert response.summary.custom_config == new_cfg
        # New instance replaced the old one in the registry.
        assert custom_registry._registry_items["ed"] is updated_built
        assert scoring_service_module._CUSTOM_SCORER_CONFIGS["ed"] == new_cfg

    async def test_rejects_non_custom_name(self, scoring_service, custom_registry, clear_custom_scorers) -> None:
        custom_registry._registry_items["builtin"] = MagicMock(spec=TrueFalseScorer)
        cfg = GeneralTrueFalseConfig(system_prompt_format_string="x")
        with pytest.raises(ValueError, match="not a user-created scorer"):
            await scoring_service.update_custom_scorer_async(
                scorer_id="builtin",
                request=UpdateCustomScorerRequest(config=cfg),
            )


class TestDeleteCustomScorer:
    async def test_removes_from_registry_and_config_dict(
        self, scoring_service, custom_registry, clear_custom_scorers
    ) -> None:
        cfg = GeneralTrueFalseConfig(system_prompt_format_string="x")
        with (
            _patch_default_target(),
            patch(
                "pyrit.score.true_false.self_ask_general_true_false_scorer.SelfAskGeneralTrueFalseScorer"
            ) as mock_cls,
        ):
            built = MagicMock(spec=TrueFalseScorer)
            built.scorer_type = "true_false"
            built.uses_objective = False
            mock_cls.return_value = built
            await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="goner", config=cfg),
            )

        assert "goner" in custom_registry._registry_items
        assert "goner" in scoring_service_module._CUSTOM_SCORER_CONFIGS

        await scoring_service.delete_custom_scorer_async(scorer_id="goner")

        assert "goner" not in custom_registry._registry_items
        assert "goner" not in scoring_service_module._CUSTOM_SCORER_CONFIGS
        assert custom_registry._metadata_cache is None

    async def test_rejects_non_custom_name(self, scoring_service, custom_registry, clear_custom_scorers) -> None:
        custom_registry._registry_items["builtin"] = MagicMock(spec=TrueFalseScorer)
        with pytest.raises(ValueError, match="not a user-created scorer"):
            await scoring_service.delete_custom_scorer_async(scorer_id="builtin")
        # Built-in remains in the registry.
        assert "builtin" in custom_registry._registry_items


class TestListScorersWithCustom:
    async def test_marks_user_created_as_editable(self, scoring_service, custom_registry, clear_custom_scorers) -> None:
        # Pre-seed a built-in scorer (no entry in _CUSTOM_SCORER_CONFIGS).
        builtin = MagicMock(spec=TrueFalseScorer)
        builtin.scorer_type = "true_false"
        builtin.uses_objective = False
        custom_registry._registry_items["builtin_one"] = builtin

        # Then create a custom one.
        cfg = GeneralFloatScaleConfig(system_prompt_format_string="x", min_value=0, max_value=10)
        with (
            _patch_default_target(),
            patch(
                "pyrit.score.float_scale.self_ask_general_float_scale_scorer.SelfAskGeneralFloatScaleScorer"
            ) as mock_cls,
        ):
            built = MagicMock(spec=FloatScaleScorer)
            built.scorer_type = "float_scale"
            built.uses_objective = False
            mock_cls.return_value = built
            await scoring_service.create_custom_scorer_async(
                request=CreateCustomScorerRequest(name="user_one", config=cfg),
            )

        response = await scoring_service.list_scorers_async()
        by_name = {item.scorer_registry_name: item for item in response.items}

        assert by_name["builtin_one"].editable is False
        assert by_name["builtin_one"].custom_config is None
        assert by_name["user_one"].editable is True
        assert by_name["user_one"].custom_config == cfg


class TestGetDefaultChatTarget:
    def test_returns_first_preferred_target(self) -> None:
        from pyrit.prompt_target import PromptChatTarget

        preferred = MagicMock(spec=PromptChatTarget)
        target_registry = MagicMock()

        def _get(name):
            return preferred if name == "azure_openai_gpt4o_temp9" else None

        target_registry.get = MagicMock(side_effect=_get)
        target_registry.get_all_instances = MagicMock(return_value=[])

        with patch(
            "pyrit.registry.TargetRegistry.get_registry_singleton",
            return_value=target_registry,
        ):
            result = ScoringService._get_default_chat_target()

        assert result is preferred

    def test_falls_back_to_first_chat_capable(self) -> None:
        from pyrit.prompt_target import PromptChatTarget

        fallback = MagicMock(spec=PromptChatTarget)
        non_chat = MagicMock()  # not a PromptChatTarget
        target_registry = MagicMock()
        target_registry.get = MagicMock(return_value=None)
        entry_bad = MagicMock()
        entry_bad.instance = non_chat
        entry_good = MagicMock()
        entry_good.instance = fallback
        target_registry.get_all_instances = MagicMock(return_value=[entry_bad, entry_good])

        with patch(
            "pyrit.registry.TargetRegistry.get_registry_singleton",
            return_value=target_registry,
        ):
            result = ScoringService._get_default_chat_target()

        assert result is fallback

    def test_raises_when_no_chat_target_registered(self) -> None:
        target_registry = MagicMock()
        target_registry.get = MagicMock(return_value=None)
        target_registry.get_all_instances = MagicMock(return_value=[])

        with patch(
            "pyrit.registry.TargetRegistry.get_registry_singleton",
            return_value=target_registry,
        ):
            with pytest.raises(ValueError, match="No PromptChatTarget"):
                ScoringService._get_default_chat_target()
