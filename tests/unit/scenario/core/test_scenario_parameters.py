# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for Scenario custom parameter declaration, coercion, and validation (Stage 1b)."""

from typing import ClassVar, Literal
from unittest.mock import MagicMock

import pytest

from pyrit.models import ComponentIdentifier, Parameter
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.core import BaselineAttackPolicy, Scenario, ScenarioStrategy
from pyrit.score import Scorer

_TEST_SCORER_ID = ComponentIdentifier(class_name="MockScorer", class_module="tests.unit.scenarios")


def _make_scenario(*, declared_params: list[Parameter]) -> Scenario:
    """Build a minimal Scenario subclass that declares the given parameters.

    Each test gets its own subclass so declared-parameter state never leaks
    across tests.
    """
    params_to_declare = declared_params

    class _ParamTestStrategy(ScenarioStrategy):
        TEST = ("test", {"concrete"})
        ALL = ("all", {"all"})

        @classmethod
        def get_aggregate_tags(cls) -> set[str]:
            return {"all"}

    class _ParamTestScenario(Scenario):
        # No baseline in tests so atomic_attacks observations stay deterministic.
        BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

        @classmethod
        def supported_parameters(cls) -> list[Parameter]:
            return list(params_to_declare)

        async def _get_atomic_attacks_async(self):
            return []

    mock_scorer = MagicMock(spec=Scorer)
    mock_scorer.get_identifier.return_value = _TEST_SCORER_ID
    mock_scorer.get_scorer_metrics.return_value = None

    return _ParamTestScenario(
        version=1,
        strategy_class=_ParamTestStrategy,
        default_strategy=_ParamTestStrategy.ALL,
        default_dataset_config=DatasetConfiguration(),
        objective_scorer=mock_scorer,
    )


@pytest.mark.usefixtures("patch_central_database")
class TestSupportedParametersDefault:
    """The base Scenario.supported_parameters() returns an empty list by default."""

    def test_default_supported_parameters_is_empty(self) -> None:
        scenario = _make_scenario(declared_params=[])
        assert scenario.supported_parameters() == []

    def test_default_params_dict_is_empty(self) -> None:
        scenario = _make_scenario(declared_params=[])
        assert scenario.params == {}


@pytest.mark.usefixtures("patch_central_database")
class TestSetParamsFromArgsScalarCoercion:
    """Scalar type coercion via set_params_from_args."""

    def test_int_coercion_from_string(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": "10"})
        assert scenario.params == {"max_turns": 10}
        assert isinstance(scenario.params["max_turns"], int)

    def test_float_coercion_from_string(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="threshold", description="d", param_type=float)])
        scenario.set_params_from_args(args={"threshold": "0.75"})
        assert scenario.params == {"threshold": 0.75}

    def test_str_coercion(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="mode", description="d", param_type=str)])
        scenario.set_params_from_args(args={"mode": "fast"})
        assert scenario.params == {"mode": "fast"}

    def test_int_rejects_native_bool(self) -> None:
        """int(True) silently equals 1; we must reject this surprising coercion."""
        scenario = _make_scenario(declared_params=[Parameter(name="count", description="d", param_type=int)])
        with pytest.raises(ValueError, match="expects int but received a bool"):
            scenario.set_params_from_args(args={"count": True})

    def test_float_rejects_native_bool(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="rate", description="d", param_type=float)])
        with pytest.raises(ValueError, match="expects float but received a bool"):
            scenario.set_params_from_args(args={"rate": False})

    def test_int_coercion_failure(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="count", description="d", param_type=int)])
        with pytest.raises(ValueError, match="could not be coerced to int"):
            scenario.set_params_from_args(args={"count": "abc"})

    def test_param_type_none_stores_raw(self) -> None:
        """param_type=None preserves initializer-style raw storage."""
        scenario = _make_scenario(declared_params=[Parameter(name="opaque", description="d")])
        scenario.set_params_from_args(args={"opaque": ["a", "b"]})
        assert scenario.params == {"opaque": ["a", "b"]}


@pytest.mark.usefixtures("patch_central_database")
class TestSetParamsFromArgsBoolCoercion:
    """Boolean coercion handles strings and native bools, avoiding the type=bool footgun."""

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "Yes"])
    def test_truthy_strings(self, value: str) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="enabled", description="d", param_type=bool)])
        scenario.set_params_from_args(args={"enabled": value})
        assert scenario.params == {"enabled": True}

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "No"])
    def test_falsy_strings(self, value: str) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="enabled", description="d", param_type=bool)])
        scenario.set_params_from_args(args={"enabled": value})
        assert scenario.params == {"enabled": False}

    def test_native_bool_passes_through(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="enabled", description="d", param_type=bool)])
        scenario.set_params_from_args(args={"enabled": True})
        assert scenario.params == {"enabled": True}

    def test_invalid_bool_string_raises(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="enabled", description="d", param_type=bool)])
        with pytest.raises(ValueError, match="expects bool but received"):
            scenario.set_params_from_args(args={"enabled": "maybe"})


@pytest.mark.usefixtures("patch_central_database")
class TestSetParamsFromArgsListCoercion:
    """list[str] coercion."""

    def test_list_str_coercion(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="datasets", description="d", param_type=list[str])])
        scenario.set_params_from_args(args={"datasets": ["a", "b", "c"]})
        assert scenario.params == {"datasets": ["a", "b", "c"]}

    def test_list_str_coerces_non_string_elements(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="ids", description="d", param_type=list[str])])
        scenario.set_params_from_args(args={"ids": [1, 2, 3]})
        assert scenario.params == {"ids": ["1", "2", "3"]}

    def test_list_param_rejects_non_list_value(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="datasets", description="d", param_type=list[str])])
        with pytest.raises(ValueError, match="expects a list"):
            scenario.set_params_from_args(args={"datasets": "single"})

    def test_list_int_coerces_each_element(self) -> None:
        """list[int] is supported and coerces each element."""
        scenario = _make_scenario(declared_params=[Parameter(name="counts", description="d", param_type=list[int])])
        scenario.set_params_from_args(args={"counts": ["1", "2"]})
        assert scenario.params == {"counts": [1, 2]}

    def test_unsupported_list_element_type_raises(self) -> None:
        """A list of a non-scalar element type is rejected at declaration time."""
        scenario = _make_scenario(declared_params=[Parameter(name="tags", description="d", param_type=list[set[str]])])
        with pytest.raises(ValueError, match="unsupported.*param_type"):
            scenario.set_params_from_args(args={"tags": [{"a"}]})


@pytest.mark.usefixtures("patch_central_database")
class TestSetParamsFromArgsConstrainedScalars:
    """Constrained-scalar (Literal) membership validation."""

    def test_valid_choice_is_accepted(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="mode", description="d", param_type=Literal["fast", "slow"])]
        )
        scenario.set_params_from_args(args={"mode": "fast"})
        assert scenario.params == {"mode": "fast"}

    def test_invalid_choice_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="mode", description="d", param_type=Literal["fast", "slow"])]
        )
        with pytest.raises(ValueError, match="one of"):
            scenario.set_params_from_args(args={"mode": "medium"})

    def test_choices_validated_after_coercion(self) -> None:
        """A string '5' coerces to int 5, then is checked against the int Literal."""
        scenario = _make_scenario(
            declared_params=[Parameter(name="count", description="d", param_type=Literal[1, 5, 10])]
        )
        scenario.set_params_from_args(args={"count": "5"})
        assert scenario.params == {"count": 5}

    def test_list_literal_membership(self) -> None:
        """A list of a constrained scalar validates membership per element."""
        scenario = _make_scenario(
            declared_params=[Parameter(name="modes", description="d", param_type=list[Literal["a", "b"]])]
        )
        scenario.set_params_from_args(args={"modes": ["a", "b", "a"]})
        assert scenario.params == {"modes": ["a", "b", "a"]}


@pytest.mark.usefixtures("patch_central_database")
class TestDefaultMaterialization:
    """Defaults are materialized for params not supplied, with deep-copy."""

    def test_default_materialized_when_not_supplied(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={})
        assert scenario.params == {"max_turns": 5}

    def test_supplied_value_overrides_default(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": "10"})
        assert scenario.params == {"max_turns": 10}

    def test_mutable_default_is_deep_copied(self) -> None:
        """Two scenario instances must not share a mutable default list."""
        shared_default = ["x"]
        param = Parameter(name="items", description="d", default=shared_default)

        scenario_a = _make_scenario(declared_params=[param])
        scenario_b = _make_scenario(declared_params=[param])

        scenario_a.set_params_from_args(args={})
        scenario_b.set_params_from_args(args={})

        scenario_a.params["items"].append("y")
        # scenario_b's default must be untouched, and the original is too.
        assert scenario_b.params["items"] == ["x"]
        assert shared_default == ["x"]

    def test_default_none_materializes_as_none(self) -> None:
        """Parameters declared without an explicit default still appear in self.params (as None)
        so scenarios can rely on key presence."""
        scenario = _make_scenario(declared_params=[Parameter(name="optional", description="d", param_type=str)])
        scenario.set_params_from_args(args={})
        assert scenario.params == {"optional": None}

    def test_default_value_is_coerced_to_param_type(self) -> None:
        """A declared default value is coerced to param_type so user-supplied
        and default-supplied values share a type."""
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default="5")]
        )
        scenario.set_params_from_args(args={})
        assert scenario.params == {"max_turns": 5}
        assert isinstance(scenario.params["max_turns"], int)

    def test_default_list_value_is_coerced_per_item(self) -> None:
        """list[str] default deep-copies and re-coerces (a fresh list per instance)."""
        shared = ["a", "b"]
        scenario_a = _make_scenario(
            declared_params=[Parameter(name="tags", description="d", param_type=list[str], default=shared)]
        )
        scenario_b = _make_scenario(
            declared_params=[Parameter(name="tags", description="d", param_type=list[str], default=shared)]
        )
        scenario_a.set_params_from_args(args={})
        scenario_b.set_params_from_args(args={})
        scenario_a.params["tags"].append("c")
        assert scenario_b.params["tags"] == ["a", "b"]
        assert shared == ["a", "b"]


@pytest.mark.usefixtures("patch_central_database")
class TestParamValidation:
    """Unknown-key validation."""

    def test_unknown_param_raises(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="known", description="d", param_type=str)])
        with pytest.raises(ValueError, match="unknown parameter"):
            scenario.set_params_from_args(args={"bogus": "value"})

    def test_unknown_params_listed_together(self) -> None:
        """Multiple unknowns surface in a single error rather than failing on the first."""
        scenario = _make_scenario(declared_params=[Parameter(name="known", description="d", param_type=str)])
        with pytest.raises(ValueError, match="bogus1, bogus2"):
            scenario.set_params_from_args(args={"bogus1": "a", "bogus2": "b"})

    def test_reserved_version_param_raises(self) -> None:
        """A scenario cannot declare a param named ``version`` (owned by the identity)."""
        scenario = _make_scenario(declared_params=[Parameter(name="version", description="d", param_type=int)])
        with pytest.raises(ValueError, match="reserved parameter"):
            scenario.set_params_from_args(args={})


@pytest.mark.usefixtures("patch_central_database")
class TestDeclarationValidation:
    """_validate_declarations catches author mistakes on first set_params_from_args call."""

    def test_duplicate_name_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[
                Parameter(name="x", description="d", param_type=str),
                Parameter(name="x", description="d2", param_type=int),
            ]
        )
        with pytest.raises(ValueError, match="duplicate parameter name"):
            scenario.set_params_from_args(args={})

    def test_invalid_default_type_raises(self) -> None:
        """A default that fails coercion to its declared param_type is caught early."""
        scenario = _make_scenario(declared_params=[Parameter(name="x", description="d", param_type=int, default="abc")])
        with pytest.raises(ValueError, match="invalid default"):
            scenario.set_params_from_args(args={})

    def test_default_not_in_literal_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[
                Parameter(
                    name="mode",
                    description="d",
                    param_type=Literal["fast", "slow"],
                    default="medium",
                )
            ]
        )
        with pytest.raises(ValueError, match="invalid default"):
            scenario.set_params_from_args(args={})

    def test_unsupported_param_type_rejected_at_declaration(self) -> None:
        """An unsupported param_type (e.g. set[str]) fails at declaration time, not user time."""
        scenario = _make_scenario(declared_params=[Parameter(name="tags", description="d", param_type=set[str])])
        with pytest.raises(ValueError, match="unsupported.*param_type"):
            scenario.set_params_from_args(args={})

    def test_repeat_call_does_not_revalidate_declarations(self) -> None:
        """Once validated, a successful set_params_from_args should not re-run declaration checks.

        Observed behavior: a follow-up call with a different value for the same
        declared parameter succeeds, exercising coercion only — no re-declaration error.
        """
        scenario = _make_scenario(declared_params=[Parameter(name="x", description="d", param_type=int, default=5)])
        scenario.set_params_from_args(args={})
        assert scenario.params == {"x": 5}

        scenario.set_params_from_args(args={"x": "7"})
        assert scenario.params == {"x": 7}


@pytest.mark.usefixtures("patch_central_database")
class TestSetParamsFromArgsReplacement:
    """set_params_from_args replaces self.params wholesale (no merge)."""

    def test_subsequent_call_replaces_params(self) -> None:
        scenario = _make_scenario(
            declared_params=[
                Parameter(name="a", description="d", param_type=str, default="da"),
                Parameter(name="b", description="d", param_type=str, default="db"),
            ]
        )
        scenario.set_params_from_args(args={"a": "first"})
        assert scenario.params == {"a": "first", "b": "db"}

        scenario.set_params_from_args(args={"b": "second"})
        # 'a' is back to its default — confirms replacement, not merge.
        assert scenario.params == {"a": "da", "b": "second"}


@pytest.mark.usefixtures("patch_central_database")
class TestNoneIsAbsent:
    """Keys with ``None`` values (e.g. YAML ``null``) are treated as absent.

    Without this, ``str(None)`` produces the literal string ``"None"`` and
    other types raise confusing coercion errors. Stage 3 (YAML config load)
    needs this contract since users will write explicit ``null`` to mean
    "use the default."
    """

    def test_none_value_falls_through_to_default(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": None})
        assert scenario.params == {"max_turns": 5}

    def test_none_value_for_str_does_not_become_string_none(self) -> None:
        """``str(None) == 'None'`` would be a silent bug; treating None as absent avoids it."""
        scenario = _make_scenario(
            declared_params=[Parameter(name="mode", description="d", param_type=str, default="fast")]
        )
        scenario.set_params_from_args(args={"mode": None})
        assert scenario.params == {"mode": "fast"}

    def test_none_value_with_no_default_materializes_as_none(self) -> None:
        """A param with no declared default still materializes (as None) so scenarios can rely on key presence."""
        scenario = _make_scenario(declared_params=[Parameter(name="optional", description="d", param_type=str)])
        scenario.set_params_from_args(args={"optional": None})
        assert scenario.params == {"optional": None}


@pytest.mark.usefixtures("patch_central_database")
class TestResumeParameterValidation:
    """Tests for resume validation against a persisted scenario identifier (eval-hash based)."""

    _TARGET_ID = ComponentIdentifier(class_name="MockTarget", class_module="tests.unit.scenarios")

    @classmethod
    def _make_stored_result(cls, *, scenario_name: str, version: int, params):
        """Build a minimal ScenarioResult with a controlled scenario identifier for resume tests."""
        from tests.unit.mocks import make_scenario_result

        return make_scenario_result(
            scenario_name=scenario_name,
            scenario_version=version,
            params=params,
            objective_target_identifier=cls._TARGET_ID,
            objective_scorer_identifier=_TEST_SCORER_ID,
            labels={},
            attack_results={},
            scenario_run_state="CREATED",
        )

    @classmethod
    def _current_identifier(cls, *, scenario, version: int = 1, params):
        """Build the identifier that mirrors the current run for the given scenario."""
        from tests.unit.mocks import make_scenario_identifier

        return make_scenario_identifier(
            scenario_name=type(scenario).__name__,
            version=version,
            params=params,
            objective_target=cls._TARGET_ID,
            objective_scorer=_TEST_SCORER_ID,
        )

    def test_matching_params_returns_none(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": 10})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, params={"max_turns": 10})
        current = self._current_identifier(scenario=scenario, params={"max_turns": 10})
        # Match path: returns None and does not raise.
        assert scenario._validate_stored_scenario(stored_result=stored, current_identifier=current) is None

    def test_changed_param_raises_without_leaking_values(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": 10})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, params={"max_turns": 5})
        current = self._current_identifier(scenario=scenario, params={"max_turns": 10})
        with pytest.raises(ValueError, match="does not match the current") as exc_info:
            scenario._validate_stored_scenario(stored_result=stored, current_identifier=current)
        # Generic drift message never leaks the differing param values.
        assert "10" not in str(exc_info.value)
        assert "stored=5" not in str(exc_info.value)

    def test_added_param_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[
                Parameter(name="max_turns", description="d", param_type=int, default=5),
                Parameter(name="mode", description="d", param_type=str, default="fast"),
            ]
        )
        scenario.set_params_from_args(args={})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, params={"max_turns": 5})
        current = self._current_identifier(scenario=scenario, params={"max_turns": 5, "mode": "fast"})
        with pytest.raises(ValueError, match="does not match the current"):
            scenario._validate_stored_scenario(stored_result=stored, current_identifier=current)

    def test_resume_normalizes_json_drift_for_passthrough_tuples(self) -> None:
        """A tuple value under param_type=None matches a stored list (post-JSON round-trip)."""
        scenario = _make_scenario(declared_params=[Parameter(name="weights", description="d")])
        scenario.set_params_from_args(args={"weights": (0.5, 0.5)})

        # A stored value after a real DB round-trip would be a list, not a tuple. The
        # eval hash normalizes both sides through JSON before comparing.
        stored = self._make_stored_result(
            scenario_name=type(scenario).__name__, version=1, params={"weights": [0.5, 0.5]}
        )
        current = self._current_identifier(scenario=scenario, params={"weights": (0.5, 0.5)})
        assert scenario._validate_stored_scenario(stored_result=stored, current_identifier=current) is None

    def test_name_mismatch_raises(self) -> None:
        scenario = _make_scenario(declared_params=[])
        scenario.set_params_from_args(args={})

        stored = self._make_stored_result(scenario_name="OtherScenario", version=1, params={})
        current = self._current_identifier(scenario=scenario, params={})
        with pytest.raises(ValueError, match="does not match the current"):
            scenario._validate_stored_scenario(stored_result=stored, current_identifier=current)

    def test_version_mismatch_raises(self) -> None:
        scenario = _make_scenario(declared_params=[])
        scenario.set_params_from_args(args={})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=999, params={})
        current = self._current_identifier(scenario=scenario, version=1, params={})
        with pytest.raises(ValueError, match="does not match the current"):
            scenario._validate_stored_scenario(stored_result=stored, current_identifier=current)


@pytest.mark.usefixtures("patch_central_database")
class TestParamPersistenceJsonSafety:
    """Params flow into the scenario identifier, which enforces JSON-serializable values."""

    @staticmethod
    def _mock_target() -> MagicMock:
        target = MagicMock()
        target.get_identifier.return_value = ComponentIdentifier(class_name="MockTarget", class_module="test")
        return target

    async def test_json_safe_params_persist_on_init(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": 10})

        await scenario.initialize_async(objective_target=self._mock_target())

        stored = scenario._memory.get_scenario_results(scenario_result_ids=[scenario._scenario_result_id])[0]
        assert stored.scenario_identifier.params["max_turns"] == 10

    async def test_non_json_safe_value_raises(self) -> None:
        from pydantic import ValidationError

        class _NotJsonable:
            pass

        # param_type=None passes the raw value straight through set_params_from_args.
        scenario = _make_scenario(declared_params=[Parameter(name="blob", description="d")])
        scenario.set_params_from_args(args={"blob": _NotJsonable()})

        with pytest.raises(ValidationError):
            await scenario.initialize_async(objective_target=self._mock_target())
