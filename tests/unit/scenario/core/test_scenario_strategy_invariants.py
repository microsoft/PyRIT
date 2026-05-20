# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared structural invariants for dynamically-generated ScenarioStrategy enums.

These tests verify that the strategy machinery works correctly for every
scenario that builds a strategy class via the technique registry. Adding a
new technique to the catalog should not require updating these tests.
"""

from unittest.mock import patch

import pytest

from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

# ---------------------------------------------------------------------------
# Synthetic many-shot examples — prevents reading the real JSON during tests
# ---------------------------------------------------------------------------
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(100)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registries():
    """Reset singletons and cached strategy classes between every test."""
    from pyrit.registry import TargetRegistry
    from pyrit.scenario.scenarios.airt.cyber import Cyber
    from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse

    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    Cyber._cached_strategy_class = None
    RapidResponse._cached_strategy_class = None
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    Cyber._cached_strategy_class = None
    RapidResponse._cached_strategy_class = None


@pytest.fixture(autouse=True)
def _patch_many_shot_load():
    """Prevent ManyShotJailbreakAttack from loading the full bundled dataset."""
    with patch(
        "pyrit.executor.attack.single_turn.many_shot_jailbreak.load_many_shot_jailbreaking_dataset",
        return_value=_MOCK_MANY_SHOT_EXAMPLES,
    ):
        yield


@pytest.fixture(autouse=True)
def _mock_runtime_env():
    """Provide minimal env vars so OpenAIChatTarget fallback doesn't fail."""
    with patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY": "test-key",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


# ---------------------------------------------------------------------------
# Parametrize: one entry per scenario that uses a dynamic strategy class
# ---------------------------------------------------------------------------


def _get_rapid_response_strategy():
    from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse

    return RapidResponse.get_strategy_class()


def _get_cyber_strategy():
    from pyrit.scenario.scenarios.airt.cyber import Cyber

    return Cyber.get_strategy_class()


SCENARIO_STRATEGY_BUILDERS = [
    pytest.param(_get_rapid_response_strategy, id="RapidResponse"),
    pytest.param(_get_cyber_strategy, id="Cyber"),
]


# ---------------------------------------------------------------------------
# Structural invariant tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_strategy_is_scenario_strategy_subclass(get_strategy):
    """Generated class must be a ScenarioStrategy subclass."""
    assert issubclass(get_strategy(), ScenarioStrategy)


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_has_at_least_one_technique(get_strategy):
    """Every scenario must have at least one non-aggregate technique."""
    strat = get_strategy()
    assert len(strat.get_all_strategies()) >= 1


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_has_all_aggregate(get_strategy):
    """Every scenario must include the ALL aggregate."""
    strat = get_strategy()
    assert "all" in strat.get_aggregate_tags()
    assert strat.ALL.value == "all"


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_member_count_is_techniques_plus_aggregates(get_strategy):
    """Total enum members = techniques + aggregates."""
    strat = get_strategy()
    techniques = strat.get_all_strategies()
    aggregates = strat.get_aggregate_strategies()
    assert len(list(strat)) == len(techniques) + len(aggregates)


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_values_are_unique(get_strategy):
    """No two members share a value."""
    strat = get_strategy()
    values = [s.value for s in strat]
    assert len(values) == len(set(values))


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_invalid_value_raises(get_strategy):
    """Constructing with a bogus value raises ValueError."""
    strat = get_strategy()
    with pytest.raises(ValueError):
        strat("nonexistent_strategy_xyzzy")


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_all_expands_to_every_technique(get_strategy):
    """ALL must expand to exactly the full set of non-aggregate techniques."""
    strat = get_strategy()
    expanded = strat.expand({strat.ALL})
    assert set(expanded) == set(strat.get_all_strategies())


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_each_aggregate_expands_to_nonempty_subset(get_strategy):
    """Every aggregate tag expands to a non-empty subset of techniques."""
    strat = get_strategy()
    all_techniques = set(strat.get_all_strategies())
    for aggregate in strat.get_aggregate_strategies():
        expanded = set(strat.expand({aggregate}))
        assert len(expanded) >= 1, f"Aggregate {aggregate.value!r} expanded to empty set"
        assert expanded <= all_techniques, f"Aggregate {aggregate.value!r} expanded outside technique set"


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_aggregates_are_disjoint_from_techniques(get_strategy):
    """Aggregate members and technique members don't overlap."""
    strat = get_strategy()
    agg_values = {s.value for s in strat.get_aggregate_strategies()}
    tech_values = {s.value for s in strat.get_all_strategies()}
    assert agg_values.isdisjoint(tech_values)


@pytest.mark.parametrize("get_strategy", SCENARIO_STRATEGY_BUILDERS)
def test_expanding_a_technique_returns_itself(get_strategy):
    """Expanding a single non-aggregate technique returns just that technique."""
    strat = get_strategy()
    for technique in strat.get_all_strategies():
        expanded = strat.expand({technique})
        assert expanded == [technique]
