# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression coverage for AdversarialBenchmark cache identity."""

import json

import pytest

from pyrit.models import AttackSeedGroup, JsonResponseConfig, MessagePiece, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark, _build_benchmark_technique
from pyrit.score import SubStringScorer
from pyrit.setup.initializers.techniques import build_technique_factories

ADVERSARIAL_NAME = "adversarial_under_test"
MARKER = "OFFLINE_SUCCESS"


class _OfflineChatTarget(OpenAIChatTarget):
    """OpenAIChatTarget without HTTP calls for cache-identity regression coverage."""

    adversarial_calls = 0

    async def _send_prompt_to_target_async(self, *, normalized_conversation):
        request = normalized_conversation[-1]
        if self._temperature is None:
            reply = request.get_value()
        else:
            type(self).adversarial_calls += 1
            message = f"attacker message {MARKER}"
            metadata = request.message_pieces[0].prompt_metadata
            if JsonResponseConfig.from_metadata(metadata=metadata).enabled:
                message = json.dumps({"next_message": message, "rationale": "", "last_response_summary": ""})
            reply = message
        conversation_id = request.message_pieces[0].conversation_id
        return [MessagePiece(role="assistant", original_value=reply, conversation_id=conversation_id).to_message()]


_ENDPOINT = {"endpoint": "https://example.invalid/openai/v1", "api_key": "offline"}


@pytest.fixture(autouse=True)
def _technique_registry():
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_benchmark_technique.cache_clear()
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_benchmark_technique.cache_clear()


def _register_adversarial(*, temperature: float) -> None:
    instances = TargetRegistry.get_registry_singleton().instances
    if instances.get(ADVERSARIAL_NAME) is not None:
        instances.unregister(ADVERSARIAL_NAME)
    instances.register(
        _OfflineChatTarget(model_name="adversarial-model", temperature=temperature, **_ENDPOINT),
        name=ADVERSARIAL_NAME,
    )


async def _run_benchmark(*, objective_target: OpenAIChatTarget, use_cached: bool) -> list:
    scenario = AdversarialBenchmark(objective_scorer=SubStringScorer(substring=MARKER), use_cached=use_cached)
    scenario.set_params_from_args(
        args={
            "objective_target": objective_target,
            "adversarial_targets": [ADVERSARIAL_NAME],
            "scenario_techniques": [_build_benchmark_technique()("crescendo_simulated")],
            "dataset_config": DatasetAttackConfiguration(
                seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="placeholder objective")])],
                max_total=1,
            ),
            "use_cached": use_cached,
        }
    )
    await scenario.initialize_async()
    _OfflineChatTarget.adversarial_calls = 0
    result = await scenario.run_async()
    return [attack_result for results in result.attack_results.values() for attack_result in results]


async def _cached_rerun(*, second_temperature: float) -> tuple[list, list, int]:
    """Run, replace one registry entry, then run again with cache reuse enabled."""
    objective_target = _OfflineChatTarget(model_name="objective-model", **_ENDPOINT)
    _register_adversarial(temperature=1.2)
    first = await _run_benchmark(objective_target=objective_target, use_cached=False)
    assert len(first) == 1 and _OfflineChatTarget.adversarial_calls > 0
    _register_adversarial(temperature=second_temperature)
    second = await _run_benchmark(objective_target=objective_target, use_cached=True)
    return first, second, _OfflineChatTarget.adversarial_calls


@pytest.mark.usefixtures("patch_central_database")
async def test_cache_does_not_reuse_results_from_a_different_adversarial_temperature():
    first, second, adversarial_calls = await _cached_rerun(second_temperature=0.2)

    assert [result.metadata.get("cached_from_attack_result_id") for result in second] == [None], (
        f"a result produced with temperature 1.2 ({first[0].attack_result_id}) was reused for temperature 0.2"
    )
    assert adversarial_calls > 0


@pytest.mark.usefixtures("patch_central_database")
async def test_cache_reuses_results_for_an_identical_adversarial_configuration():
    first, second, adversarial_calls = await _cached_rerun(second_temperature=1.2)

    assert [result.metadata.get("cached_from_attack_result_id") for result in second] == [first[0].attack_result_id]
    assert adversarial_calls == 0
