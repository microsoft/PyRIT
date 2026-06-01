# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings
from datetime import datetime, timezone

from pyrit.memory.memory_models import AttackResultEntry
from pyrit.models import ComponentIdentifier, build_atomic_attack_identifier
from pyrit.models.attack_result import AttackOutcome, AttackResult
from pyrit.models.conversation_reference import ConversationReference, ConversationType
from pyrit.models.message_piece import MessagePiece
from pyrit.models.retry_event import RetryEvent
from pyrit.models.score import Score


class TestAttackResultDeprecation:
    """Tests for the AttackResult attack_identifier deprecation behaviour."""

    def _make_attack_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(class_name="TestAttack", class_module="tests.unit")

    def _make_atomic_identifier(self) -> ComponentIdentifier:
        attack_id = self._make_attack_identifier()
        return build_atomic_attack_identifier(attack_identifier=attack_id)

    # -- property deprecation -------------------------------------------------

    def test_attack_identifier_property_emits_deprecation_warning(self) -> None:
        """Accessing .attack_identifier should emit a DeprecationWarning."""
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            atomic_attack_identifier=self._make_atomic_identifier(),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = result.attack_identifier

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1, "Expected a DeprecationWarning from .attack_identifier"
        assert "attack_identifier" in str(deprecation_warnings[0].message).lower()

    def test_attack_identifier_property_returns_correct_value(self) -> None:
        """Accessing .attack_identifier should return the attack strategy child."""
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            atomic_attack_identifier=self._make_atomic_identifier(),
        )
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            value = result.attack_identifier

        assert value is not None
        assert value.class_name == "TestAttack"

    def test_attack_identifier_property_returns_none_when_unset(self) -> None:
        """Property returns None when atomic_attack_identifier is not set."""
        result = AttackResult(conversation_id="c1", objective="test")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert result.attack_identifier is None

    # -- get_attack_strategy_identifier (non-deprecated) ----------------------

    def test_get_attack_strategy_identifier_no_warning(self) -> None:
        """get_attack_strategy_identifier() must NOT emit a deprecation warning."""
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            atomic_attack_identifier=self._make_atomic_identifier(),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = result.get_attack_strategy_identifier()

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0, "get_attack_strategy_identifier should not warn"
        assert value is not None
        assert value.class_name == "TestAttack"

    def test_get_attack_strategy_identifier_returns_none_when_unset(self) -> None:
        result = AttackResult(conversation_id="c1", objective="test")
        assert result.get_attack_strategy_identifier() is None

    # -- backward-compat constructor ------------------------------------------

    def test_constructor_with_attack_identifier_kwarg_emits_warning(self) -> None:
        """Passing attack_identifier= to the constructor should emit DeprecationWarning."""
        attack_id = self._make_attack_identifier()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = AttackResult(
                conversation_id="c1",
                objective="test",
                attack_identifier=attack_id,
            )

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1, "Constructor should warn on attack_identifier="
        # The value should be promoted to atomic_attack_identifier
        assert result.atomic_attack_identifier is not None
        assert result.get_attack_strategy_identifier() == attack_id

    def test_constructor_attack_identifier_does_not_override_atomic(self) -> None:
        """If both are supplied, atomic_attack_identifier takes precedence."""
        attack_id = self._make_attack_identifier()
        atomic_id = self._make_atomic_identifier()
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = AttackResult(
                conversation_id="c1",
                objective="test",
                attack_identifier=attack_id,
                atomic_attack_identifier=atomic_id,
            )

        assert result.atomic_attack_identifier is atomic_id

    # -- construction without deprecated kwarg --------------------------------

    def test_constructor_with_atomic_attack_identifier_only(self) -> None:
        """Normal construction with atomic_attack_identifier should work with no warnings."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = AttackResult(
                conversation_id="c1",
                objective="test",
                atomic_attack_identifier=self._make_atomic_identifier(),
            )

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0
        assert result.get_attack_strategy_identifier() is not None

    def test_constructor_with_no_identifier_at_all(self) -> None:
        """Construction with neither identifier should be fine."""
        result = AttackResult(conversation_id="c1", objective="test")
        assert result.atomic_attack_identifier is None
        assert result.get_attack_strategy_identifier() is None


class TestAttackResultTimestamp:
    """Tests for the AttackResult.timestamp field and its round-trip through AttackResultEntry."""

    def test_timestamp_defaults_to_now_utc_when_not_set(self) -> None:
        """AttackResult constructed without a timestamp gets a tz-aware UTC default."""
        before = datetime.now(timezone.utc)
        result = AttackResult(conversation_id="c1", objective="test")
        after = datetime.now(timezone.utc)

        assert result.timestamp is not None
        assert result.timestamp.tzinfo is timezone.utc
        assert before <= result.timestamp <= after

    def test_timestamp_accepts_and_preserves_aware_datetime(self) -> None:
        """A tz-aware datetime passed to the constructor is stored as-is."""
        ts = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
        result = AttackResult(conversation_id="c1", objective="test", timestamp=ts)
        assert result.timestamp == ts

    def test_entry_preserves_timestamp_from_attack_result(self) -> None:
        """Constructing AttackResultEntry from an AttackResult preserves its timestamp."""
        persisted_ts = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
        original = AttackResult(
            conversation_id="c1",
            objective="test",
            timestamp=persisted_ts,
        )
        entry = AttackResultEntry(entry=original)
        assert entry.timestamp == persisted_ts

    def test_entry_falls_back_to_now_when_attack_result_timestamp_missing(self) -> None:
        """If AttackResult.timestamp is explicitly None, entry stamps datetime.now()."""
        original = AttackResult(conversation_id="c1", objective="test")
        original.timestamp = None  # type: ignore[assignment]

        before = datetime.now(timezone.utc)
        entry = AttackResultEntry(entry=original)
        after = datetime.now(timezone.utc)

        assert entry.timestamp is not None
        assert entry.timestamp.tzinfo is timezone.utc
        assert before <= entry.timestamp <= after

    def test_timestamp_roundtrips_through_attack_result_entry(self) -> None:
        """AttackResultEntry.timestamp is surfaced on the hydrated AttackResult."""
        original = AttackResult(
            conversation_id="c1",
            objective="test",
            outcome=AttackOutcome.SUCCESS,
        )
        entry = AttackResultEntry(entry=original)
        persisted_ts = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
        entry.timestamp = persisted_ts

        hydrated = entry.get_attack_result()

        assert hydrated.timestamp == persisted_ts

    def test_naive_entry_timestamp_is_normalized_to_utc_on_hydration(self) -> None:
        """SQLite returns naive datetimes; hydration must attach UTC tzinfo."""
        original = AttackResult(conversation_id="c1", objective="test")
        entry = AttackResultEntry(entry=original)
        entry.timestamp = datetime(2026, 4, 17, 12, 0, 0)  # noqa: DTZ001

        hydrated = entry.get_attack_result()

        assert hydrated.timestamp is not None
        assert hydrated.timestamp.tzinfo is timezone.utc
        assert hydrated.timestamp.replace(tzinfo=None) == datetime(2026, 4, 17, 12, 0, 0)  # noqa: DTZ001


class TestAttackResultErrorFields:
    """Tests for the error and retry fields on AttackResult."""

    def test_error_fields_default_to_none(self) -> None:
        """AttackResult without error fields defaults to None/empty."""
        result = AttackResult(conversation_id="c1", objective="test")
        assert result.error_message is None
        assert result.error_type is None
        assert result.error_traceback is None
        assert result.retry_events == []
        assert result.total_retries == 0

    def test_error_fields_set_correctly(self) -> None:
        """AttackResult stores error fields when provided."""
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            error_message="Connection refused",
            error_type="ConnectionError",
            error_traceback="Traceback (most recent call last):\n  ...",
            total_retries=3,
        )
        assert result.error_message == "Connection refused"
        assert result.error_type == "ConnectionError"
        assert "Traceback" in result.error_traceback
        assert result.total_retries == 3

    def test_retry_events_stored_on_result(self) -> None:
        """AttackResult stores retry events."""
        events = [
            RetryEvent(attempt_number=1, function_name="fn1", exception_type="TimeoutError"),
            RetryEvent(attempt_number=2, function_name="fn1", exception_type="TimeoutError"),
        ]
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            retry_events=events,
            total_retries=2,
        )
        assert len(result.retry_events) == 2
        assert result.retry_events[0].attempt_number == 1
        assert result.retry_events[1].attempt_number == 2


class TestAttackResultErrorRoundTrip:
    """Tests that error/retry fields survive the AttackResult -> AttackResultEntry -> AttackResult round-trip."""

    def test_error_fields_roundtrip(self) -> None:
        """Error fields are serialized to entry and deserialized back."""
        original = AttackResult(
            conversation_id="c1",
            objective="test",
            outcome=AttackOutcome.FAILURE,
            error_message="Rate limit hit",
            error_type="RateLimitError",
            error_traceback="Traceback...\n  File ...",
            total_retries=5,
        )
        entry = AttackResultEntry(entry=original)

        # Verify serialized values on entry
        assert entry.error_message == "Rate limit hit"
        assert entry.error_type == "RateLimitError"
        assert entry.error_traceback == "Traceback...\n  File ..."
        assert entry.total_retries == 5

        # Deserialize back
        hydrated = entry.get_attack_result()
        assert hydrated.error_message == "Rate limit hit"
        assert hydrated.error_type == "RateLimitError"
        assert hydrated.error_traceback == "Traceback...\n  File ..."
        assert hydrated.total_retries == 5

    def test_retry_events_roundtrip(self) -> None:
        """Retry events are serialized to JSON and deserialized back."""
        events = [
            RetryEvent(
                attempt_number=1,
                function_name="send_async",
                exception_type="TimeoutError",
                exception_message="timed out",
                component_role="target",
                component_name="AzureTarget",
                endpoint="https://api.azure.com",
                elapsed_seconds=5.5,
            ),
            RetryEvent(
                attempt_number=2,
                function_name="send_async",
                exception_type="RateLimitError",
                exception_message="429",
                elapsed_seconds=10.0,
            ),
        ]
        original = AttackResult(
            conversation_id="c1",
            objective="test",
            retry_events=events,
            total_retries=2,
        )
        entry = AttackResultEntry(entry=original)
        assert entry.retry_events_json is not None

        hydrated = entry.get_attack_result()
        assert len(hydrated.retry_events) == 2
        assert hydrated.retry_events[0].attempt_number == 1
        assert hydrated.retry_events[0].function_name == "send_async"
        assert hydrated.retry_events[0].exception_type == "TimeoutError"
        assert hydrated.retry_events[0].component_name == "AzureTarget"
        assert hydrated.retry_events[1].attempt_number == 2
        assert hydrated.retry_events[1].exception_type == "RateLimitError"
        assert hydrated.total_retries == 2

    def test_no_error_fields_roundtrip(self) -> None:
        """AttackResult without error fields round-trips cleanly."""
        original = AttackResult(
            conversation_id="c1",
            objective="test",
            outcome=AttackOutcome.SUCCESS,
        )
        entry = AttackResultEntry(entry=original)
        assert entry.error_message is None
        assert entry.error_type is None
        assert entry.retry_events_json is None
        assert entry.total_retries == 0

        hydrated = entry.get_attack_result()
        assert hydrated.error_message is None
        assert hydrated.error_type is None
        assert hydrated.retry_events == []
        assert hydrated.total_retries == 0

    def test_traceback_truncation(self) -> None:
        """Very long tracebacks are truncated to 10KB."""
        long_traceback = "x" * 20000
        original = AttackResult(
            conversation_id="c1",
            objective="test",
            error_traceback=long_traceback,
        )
        entry = AttackResultEntry(entry=original)
        assert len(entry.error_traceback) == 10240


def test_to_dict_from_dict_roundtrip():
    scorer_id = ComponentIdentifier(
        class_name="SelfAskTrueFalseScorer",
        class_module="pyrit.score",
    )
    target_id = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://api.example.com"},
    )
    attack_id = ComponentIdentifier(
        class_name="PromptSendingAttack",
        class_module="pyrit.executor.attack",
    )
    last_response = MessagePiece(
        id="12345678-aaaa-bbbb-cccc-123456789abc",
        role="assistant",
        original_value="Sure, here is the answer.",
        conversation_id="conv-1",
        sequence=1,
        timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        prompt_target_identifier=target_id,
        attack_identifier=attack_id,
    )
    last_score = Score(
        score_value="true",
        score_value_description="met objective",
        score_type="true_false",
        score_rationale="objective clearly met",
        scorer_class_identifier=scorer_id,
        message_piece_id="12345678-aaaa-bbbb-cccc-123456789abc",
        timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    original = AttackResult(
        conversation_id="conv-1",
        objective="Generate harmful content",
        attack_result_id="ar-001",
        atomic_attack_identifier=attack_id,
        last_response=last_response,
        last_score=last_score,
        executed_turns=5,
        execution_time_ms=2500,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Objective was achieved",
        timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        related_conversations={
            ConversationReference(
                conversation_id="conv-2",
                conversation_type=ConversationType.PRUNED,
                description="pruned branch",
            ),
            ConversationReference(
                conversation_id="conv-3",
                conversation_type=ConversationType.SCORE,
                description="scoring conversation",
            ),
        },
        metadata={"model": "gpt-4", "temperature": 0.7},
        labels={"category": "violence", "severity": "high"},
        error_message="partial error",
        error_type="RuntimeError",
        error_traceback="Traceback ...\n  File ...",
        retry_events=[
            RetryEvent(
                attempt_number=1,
                function_name="send_prompt",
                exception_type="TimeoutError",
                exception_message="Request timed out",
                component_role="target",
                component_name="OpenAIChatTarget",
                endpoint="https://api.example.com",
                elapsed_seconds=30.5,
                timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        total_retries=1,
    )
    roundtripped = AttackResult.from_dict(original.to_dict())
    assert original.to_dict() == roundtripped.to_dict()
