# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for Scenario custom parameter declaration, coercion, and validation (Stage 1b)."""

from unittest.mock import MagicMock

import pytest

from pyrit.common import Parameter
from pyrit.identifiers import ComponentIdentifier
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.core import Scenario, ScenarioStrategy
from pyrit.score import Scorer

_TEST_SCORER_ID = ComponentIdentifier(class_name="MockScorer", class_module="tests.unit.scenarios")


def _make_scenario(*, declared_params: list[Parameter]) -> Scenario:
    """Build a minimal Scenario subclass that declares the given parameters.

    Each test gets its own subclass so ``_declarations_validated`` state never
    leaks across tests.
    """
    params_to_declare = declared_params

    class _ParamTestStrategy(ScenarioStrategy):
        TEST = ("test", {"concrete"})
        ALL = ("all", {"all"})

        @classmethod
        def get_aggregate_tags(cls) -> set[str]:
            return {"all"}

    class _ParamTestScenario(Scenario):
        @classmethod
        def get_strategy_class(cls):
            return _ParamTestStrategy

        @classmethod
        def get_default_strategy(cls):
            return _ParamTestStrategy.ALL

        @classmethod
        def default_dataset_config(cls) -> DatasetConfiguration:
            return DatasetConfiguration()

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
        objective_scorer=mock_scorer,
        include_default_baseline=False,
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

    def test_unsupported_list_element_type_raises(self) -> None:
        """list[int] is rejected at declaration time (only list[str] is supported)."""
        scenario = _make_scenario(declared_params=[Parameter(name="counts", description="d", param_type=list[int])])
        with pytest.raises(ValueError, match="unsupported.*param_type"):
            scenario.set_params_from_args(args={"counts": [1, 2]})


@pytest.mark.usefixtures("patch_central_database")
class TestSetParamsFromArgsChoices:
    """choices validation."""

    def test_valid_choice_is_accepted(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="mode", description="d", param_type=str, choices=("fast", "slow"))]
        )
        scenario.set_params_from_args(args={"mode": "fast"})
        assert scenario.params == {"mode": "fast"}

    def test_invalid_choice_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="mode", description="d", param_type=str, choices=("fast", "slow"))]
        )
        with pytest.raises(ValueError, match="not in declared choices"):
            scenario.set_params_from_args(args={"mode": "medium"})

    def test_choices_validated_after_coercion(self) -> None:
        """A string '5' coerces to int 5, then is checked against int choices."""
        scenario = _make_scenario(
            declared_params=[Parameter(name="count", description="d", param_type=int, choices=(1, 5, 10))]
        )
        scenario.set_params_from_args(args={"count": "5"})
        assert scenario.params == {"count": 5}


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

    def test_default_none_is_not_materialized(self) -> None:
        """Parameters with default=None are not added to self.params."""
        scenario = _make_scenario(declared_params=[Parameter(name="optional", description="d", param_type=str)])
        scenario.set_params_from_args(args={})
        assert scenario.params == {}

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
    """Unknown / required-missing checks."""

    def test_unknown_param_raises(self) -> None:
        scenario = _make_scenario(declared_params=[Parameter(name="known", description="d", param_type=str)])
        with pytest.raises(ValueError, match="unknown parameter"):
            scenario.set_params_from_args(args={"bogus": "value"})

    def test_unknown_params_listed_together(self) -> None:
        """Multiple unknowns surface in a single error rather than failing on the first."""
        scenario = _make_scenario(declared_params=[Parameter(name="known", description="d", param_type=str)])
        with pytest.raises(ValueError, match="bogus1, bogus2"):
            scenario.set_params_from_args(args={"bogus1": "a", "bogus2": "b"})

    def test_missing_required_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="key", description="d", param_type=str, required=True)]
        )
        with pytest.raises(ValueError, match="missing required parameter"):
            scenario.set_params_from_args(args={})

    def test_required_supplied_passes(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="key", description="d", param_type=str, required=True)]
        )
        scenario.set_params_from_args(args={"key": "value"})
        assert scenario.params == {"key": "value"}


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

    def test_required_with_default_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="x", description="d", param_type=int, required=True, default=5)]
        )
        with pytest.raises(ValueError, match="required.*cannot have a default"):
            scenario.set_params_from_args(args={})

    def test_invalid_default_type_raises(self) -> None:
        """A default that fails coercion to its declared param_type is caught early."""
        scenario = _make_scenario(declared_params=[Parameter(name="x", description="d", param_type=int, default="abc")])
        with pytest.raises(ValueError, match="invalid default"):
            scenario.set_params_from_args(args={})

    def test_default_not_in_choices_raises(self) -> None:
        scenario = _make_scenario(
            declared_params=[
                Parameter(
                    name="mode",
                    description="d",
                    param_type=str,
                    default="medium",
                    choices=("fast", "slow"),
                )
            ]
        )
        with pytest.raises(ValueError, match="not in declared choices"):
            scenario.set_params_from_args(args={})

    def test_choices_on_list_param_rejected_at_declaration(self) -> None:
        """Combining `choices` with a list param_type is rejected pending semantic resolution.

        argparse's per-item choices for nargs='+' diverges from core's whole-list
        post-coercion check, so we forbid the combination at declaration time.
        """
        scenario = _make_scenario(
            declared_params=[Parameter(name="datasets", description="d", param_type=list[str], choices=("a", "b"))]
        )
        with pytest.raises(ValueError, match="choices on a list param_type"):
            scenario.set_params_from_args(args={})

    def test_unsupported_param_type_rejected_at_declaration(self) -> None:
        """An unsupported param_type (e.g. set[str]) fails at declaration time, not user time."""
        scenario = _make_scenario(declared_params=[Parameter(name="tags", description="d", param_type=set[str])])
        with pytest.raises(ValueError, match="unsupported.*param_type"):
            scenario.set_params_from_args(args={})

    def test_choices_not_coercible_to_param_type_raises(self) -> None:
        """A choices tuple with values that cannot be coerced to param_type fails fast."""
        scenario = _make_scenario(
            declared_params=[Parameter(name="count", description="d", param_type=int, choices=("a", "b"))]
        )
        with pytest.raises(ValueError, match="not coercible to"):
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

    def test_none_value_with_no_default_is_simply_absent(self) -> None:
        """If a param has no default and is not required, a None value yields no entry."""
        scenario = _make_scenario(declared_params=[Parameter(name="optional", description="d", param_type=str)])
        scenario.set_params_from_args(args={"optional": None})
        assert scenario.params == {}

    def test_none_value_for_required_param_raises(self) -> None:
        """A required param explicitly set to None is still missing, not satisfied."""
        scenario = _make_scenario(
            declared_params=[Parameter(name="key", description="d", param_type=str, required=True)]
        )
        with pytest.raises(ValueError, match="missing required parameter"):
            scenario.set_params_from_args(args={"key": None})


@pytest.mark.usefixtures("patch_central_database")
class TestResumeParameterValidation:
    """Tests for Stage 5 resume validation against persisted scenario params."""

    @staticmethod
    def _make_stored_result(*, scenario_name: str, version: int, init_data):
        """Build a minimal ScenarioResult with a controlled identifier for resume tests."""
        from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult

        identifier = ScenarioIdentifier(
            name=scenario_name,
            description="",
            scenario_version=version,
            init_data=init_data,
        )
        target_id = ComponentIdentifier(class_name="MockTarget", class_module="tests.unit.scenarios")
        return ScenarioResult(
            scenario_identifier=identifier,
            objective_target_identifier=target_id,
            objective_scorer_identifier=_TEST_SCORER_ID,
            labels={},
            attack_results={},
            scenario_run_state="CREATED",
        )

    def test_matching_params_returns_true(self) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": 10})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, init_data={"max_turns": 10})
        assert scenario._validate_stored_scenario(stored_result=stored) is True

    def test_changed_param_returns_false_with_diff(self, caplog) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": 10})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, init_data={"max_turns": 5})
        with caplog.at_level("WARNING"):
            result = scenario._validate_stored_scenario(stored_result=stored)
        assert result is False
        # Diff log should reference the key but NOT the values (no leak).
        assert "changed: max_turns" in caplog.text
        assert "10" not in caplog.text
        assert "stored=5" not in caplog.text

    def test_added_param_returns_false(self, caplog) -> None:
        scenario = _make_scenario(
            declared_params=[
                Parameter(name="max_turns", description="d", param_type=int, default=5),
                Parameter(name="mode", description="d", param_type=str, default="fast"),
            ]
        )
        scenario.set_params_from_args(args={})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, init_data={"max_turns": 5})
        with caplog.at_level("WARNING"):
            result = scenario._validate_stored_scenario(stored_result=stored)
        assert result is False
        assert "added: mode" in caplog.text

    def test_legacy_init_data_none_matches_empty_params(self) -> None:
        """A pre-Stage-5 stored result has init_data=None; treat as empty for back-compat."""
        scenario = _make_scenario(declared_params=[])
        scenario.set_params_from_args(args={})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, init_data=None)
        assert scenario._validate_stored_scenario(stored_result=stored) is True

    def test_legacy_init_data_none_mismatches_populated_params(self, caplog) -> None:
        scenario = _make_scenario(
            declared_params=[Parameter(name="max_turns", description="d", param_type=int, default=5)]
        )
        scenario.set_params_from_args(args={"max_turns": 7})

        stored = self._make_stored_result(scenario_name=type(scenario).__name__, version=1, init_data=None)
        with caplog.at_level("WARNING"):
            result = scenario._validate_stored_scenario(stored_result=stored)
        assert result is False
        assert "added: max_turns" in caplog.text


@pytest.mark.usefixtures("patch_central_database")
class TestParamPersistenceJsonSafety:
    """Tests for the JSON-serializability check before persisting params."""

    def test_json_safe_scalar_passes(self) -> None:
        from pyrit.scenario.core.scenario import _assert_json_serializable

        _assert_json_serializable(params={"max_turns": 5, "mode": "fast", "datasets": ["a", "b"]})

    def test_non_json_safe_value_raises(self) -> None:
        from pyrit.scenario.core.scenario import _assert_json_serializable

        class _NotJsonable:
            pass

        with pytest.raises(ValueError, match="non-JSON-serializable"):
            _assert_json_serializable(params={"x": _NotJsonable()})
