# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Regression for #3677: ``AdversarialBenchmark(use_cached=True)`` reuses a result only when the
adversarial chat that generated its simulated conversation has the same eval hash.

Offline: the adversarial chat and objective target are ``OpenAIChatTarget`` instances whose HTTP call
is replaced by canned replies, so their identifiers are real and no request leaves the process.
"""

import json

import pytest

from pyrit.models import AttackSeedGroup, JsonResponseConfig, MessagePiece, SeedObjective
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.scenarios.benchmark import AdversarialBenchmark
from pyrit.scenario.scenarios.benchmark.adversarial import _build_benchmark_technique
from pyrit.score import SubStringScorer
from pyrit.setup.initializers.techniques import build_technique_factories

ADVERSARIAL_NAME = "adversarial_under_test"
MARKER = "OFFLINE_SUCCESS"
_ENDPOINT = {"endpoint": "https://example.invalid/openai/v1", "api_key": "offline"}


class _OfflineChatTarget(OpenAIChatTarget):
    """OpenAIChatTarget without the HTTP call. Counts adversarial calls; the objective target echoes."""

    adversarial_calls = 0

    async def _send_prompt_to_target_async(self, *, normalized_conversation):
        request = normalized_conversation[-1]
        if self._temperature is None:  # objective target
            reply = request.get_value()
        else:
            _OfflineChatTarget.adversarial_calls += 1
            reply = f"attacker message {MARKER}"
            metadata = request.message_pieces[0].prompt_metadata
            if JsonResponseConfig.from_metadata(metadata=metadata).enabled:
                reply = json.dumps({"next_message": reply, "rationale": "", "last_response_summary": ""})
        conversation_id = request.message_pieces[0].conversation_id
        return [MessagePiece(role="assistant", original_value=reply, conversation_id=conversation_id).to_message()]


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


def _register_adversarial(*, temperature: float, max_requests_per_minute: int | None = None) -> None:
    instances = TargetRegistry.get_registry_singleton().instances
    if instances.get(ADVERSARIAL_NAME) is not None:
        instances.unregister(ADVERSARIAL_NAME)
    adversarial = _OfflineChatTarget(
        model_name="adversarial-model",
        temperature=temperature,
        max_requests_per_minute=max_requests_per_minute,
        **_ENDPOINT,
    )
    instances.register(adversarial, name=ADVERSARIAL_NAME)


async def _run_benchmark(*, objective_target: OpenAIChatTarget, use_cached: bool, pending: bool = False) -> list:
    scenario = AdversarialBenchmark(objective_scorer=SubStringScorer(substring=MARKER), use_cached=use_cached)
    scenario.set_params_from_args(
        args={
            "objective_target": objective_target,
            "adversarial_targets": [ADVERSARIAL_NAME],
            "scenario_techniques": [_build_benchmark_technique()("crescendo_simulated")],
            "dataset_config": DatasetAttackConfiguration(
                seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="placeholder objective")])], max_total=1
            ),
            "use_cached": use_cached,
        }
    )
    if pending:
        await scenario.create_pending_run_async()
    await scenario.initialize_async()
    _OfflineChatTarget.adversarial_calls = 0
    result = await scenario.run_async()
    return [attack_result for results in result.attack_results.values() for attack_result in results]


async def _cached_rerun(
    *, second_temperature: float, second_max_requests_per_minute: int | None = None, pending: bool = False
) -> tuple[list, list, int]:
    """Run at temperature 1.2 with caching off, re-register the same name, then run again with caching on."""
    objective_target = _OfflineChatTarget(model_name="objective-model", **_ENDPOINT)
    _register_adversarial(temperature=1.2)
    first = await _run_benchmark(objective_target=objective_target, use_cached=False)
    assert len(first) == 1
    assert _OfflineChatTarget.adversarial_calls > 0
    _register_adversarial(temperature=second_temperature, max_requests_per_minute=second_max_requests_per_minute)
    second = await _run_benchmark(objective_target=objective_target, use_cached=True, pending=pending)
    return first, second, _OfflineChatTarget.adversarial_calls


@pytest.mark.usefixtures("patch_central_database")
async def test_cache_does_not_reuse_results_from_a_different_adversarial_temperature():
    first, second, adversarial_calls = await _cached_rerun(second_temperature=0.2)

    assert [r.metadata.get("cached_from_attack_result_id") for r in second] == [None], (
        f"a result produced with the adversarial chat at temperature 1.2 ({first[0].attack_result_id}) "
        "was reused for the temperature 0.2 configuration"
    )
    assert adversarial_calls > 0


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("pending", [False, True])
async def test_cache_reuses_results_for_an_identical_adversarial_configuration(pending: bool) -> None:
    first, second, adversarial_calls = await _cached_rerun(second_temperature=1.2, pending=pending)

    assert [r.metadata.get("cached_from_attack_result_id") for r in second] == [first[0].attack_result_id]
    assert adversarial_calls == 0


@pytest.mark.usefixtures("patch_central_database")
async def test_cache_reuses_results_after_an_operational_only_adversarial_change():
    first, second, adversarial_calls = await _cached_rerun(second_temperature=1.2, second_max_requests_per_minute=10)

    assert [r.metadata.get("cached_from_attack_result_id") for r in second] == [first[0].attack_result_id]
    assert adversarial_calls == 0
