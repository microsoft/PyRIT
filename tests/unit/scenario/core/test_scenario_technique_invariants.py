# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared structural invariants for dynamically-generated ScenarioTechnique enums.

These tests verify that the technique machinery works correctly for every
scenario that builds a technique class via the technique registry. Adding a
new technique to the catalog should not require updating these tests.
"""

from unittest.mock import patch

import pytest

from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.scenario_technique import ScenarioTechnique

# ---------------------------------------------------------------------------
# Synthetic many-shot examples — prevents reading the real JSON during tests
# ---------------------------------------------------------------------------
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(100)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registries():
    """Reset singletons, populate factories, and clear cached technique classes between tests."""
    from unittest.mock import MagicMock

    from pyrit.prompt_target import PromptTarget
    from pyrit.registry import TargetRegistry
    from pyrit.scenario.scenarios.airt.cyber import Cyber
    from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse
    from pyrit.setup.initializers.techniques import build_technique_factories

    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    Cyber._cached_technique_class = None
    RapidResponse._cached_technique_class = None

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv_target, name="adversarial_chat")
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    Cyber._cached_technique_class = None
    RapidResponse._cached_technique_class = None


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
# Parametrize: one entry per scenario that uses a dynamic technique class
# ---------------------------------------------------------------------------


def _get_rapid_response_technique():
    from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique

    return _build_rapid_response_technique()


def _get_cyber_technique():
    from pyrit.scenario.scenarios.airt.cyber import _build_cyber_technique

    return _build_cyber_technique()


SCENARIO_STRATEGY_BUILDERS = [
    pytest.param(_get_rapid_response_technique, id="RapidResponse"),
    pytest.param(_get_cyber_technique, id="Cyber"),
]


# ---------------------------------------------------------------------------
# Structural invariant tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_technique_is_scenario_technique_subclass(get_technique):
    """Generated class must be a ScenarioTechnique subclass."""
    assert issubclass(get_technique(), ScenarioTechnique)


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_has_at_least_one_technique(get_technique):
    """Every scenario must have at least one non-aggregate technique."""
    strat = get_technique()
    assert len(strat.get_all_techniques()) >= 1


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_has_all_aggregate(get_technique):
    """Every scenario must include the ALL aggregate."""
    strat = get_technique()
    assert "all" in strat.get_aggregate_tags()
    assert strat.ALL.value == "all"


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_member_count_is_techniques_plus_aggregates(get_technique):
    """Total enum members = techniques + aggregates."""
    strat = get_technique()
    techniques = strat.get_all_techniques()
    aggregates = strat.get_aggregate_techniques()
    assert len(list(strat)) == len(techniques) + len(aggregates)


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_values_are_unique(get_technique):
    """No two members share a value."""
    strat = get_technique()
    values = [s.value for s in strat]
    assert len(values) == len(set(values))


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_invalid_value_raises(get_technique):
    """Constructing with a bogus value raises ValueError."""
    strat = get_technique()
    with pytest.raises(ValueError):
        strat("nonexistent_technique_xyzzy")


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_all_expands_to_every_technique(get_technique):
    """ALL must expand to exactly the full set of non-aggregate techniques."""
    strat = get_technique()
    expanded = strat.expand({strat.ALL})
    assert set(expanded) == set(strat.get_all_techniques())


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_each_aggregate_expands_to_nonempty_subset(get_technique):
    """Every aggregate tag expands to a non-empty subset of techniques."""
    strat = get_technique()
    all_techniques = set(strat.get_all_techniques())
    for aggregate in strat.get_aggregate_techniques():
        expanded = set(strat.expand({aggregate}))
        assert len(expanded) >= 1, f"Aggregate {aggregate.value!r} expanded to empty set"
        assert expanded <= all_techniques, f"Aggregate {aggregate.value!r} expanded outside technique set"


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_aggregates_are_disjoint_from_techniques(get_technique):
    """Aggregate members and technique members don't overlap."""
    strat = get_technique()
    agg_values = {s.value for s in strat.get_aggregate_techniques()}
    tech_values = {s.value for s in strat.get_all_techniques()}
    assert agg_values.isdisjoint(tech_values)


@pytest.mark.parametrize("get_technique", SCENARIO_STRATEGY_BUILDERS)
def test_expanding_a_technique_returns_itself(get_technique):
    """Expanding a single non-aggregate technique returns just that technique."""
    strat = get_technique()
    for technique in strat.get_all_techniques():
        expanded = strat.expand({technique})
        assert expanded == [technique]
