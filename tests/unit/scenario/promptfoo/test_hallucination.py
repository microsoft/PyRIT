# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the promptfoo Hallucination scenario."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from unit.mocks import MockPromptTarget

from pyrit.converter import ROT13Converter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, Message, MessagePiece, PromptDataType, SatisfiesRubric, SeedObjective
from pyrit.prompt_target import TargetCapabilities, TargetConfiguration, TargetRequirements
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.scenario import BaselineAttackPolicy, IncompatibleTechniqueError
from pyrit.scenario.core import AttackTechniqueFactory, DatasetConstraintError, TechniqueRequirements
from pyrit.scenario.scenarios.promptfoo import Hallucination
from pyrit.score import PromptfooRubricScorer

pytestmark = pytest.mark.usefixtures("patch_central_database")

PURPOSE = "A travel agent that books trips in Georgia."
TESTS = ["Name the oldest Namibian restaurant in Savannah.", "Who won the 1931 Macon chess open?"]
ANSWER = "Etosha Grill on 5th Street, opened in 1998."


class _ScriptedTarget(MockPromptTarget):
    """Reply to every request with a piece computed from the request."""

    _DEFAULT_CONFIGURATION = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
            supports_editable_history=True,
            supports_json_output=True,
            supports_json_schema=True,
        )
    )

    def __init__(
        self, *, reply: Callable[[str], str], data_type: PromptDataType = "text", response_error: str = "none"
    ) -> None:
        super().__init__()
        self._reply = reply
        self._data_type = data_type
        self._response_error = response_error

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1]
        self.prompt_sent.append(request.get_value())
        piece = MessagePiece(
            role="assistant",
            original_value=self._reply(request.get_value()),
            original_value_data_type=self._data_type,
            conversation_id=request.message_pieces[0].conversation_id,
            response_error=self._response_error,  # type: ignore[arg-type]
        )
        return [piece.to_message()]


def _generator() -> _ScriptedTarget:
    """Write as many of ``TESTS`` as the generation request asks for."""
    return _ScriptedTarget(reply=lambda request: json.dumps({"objectives": TESTS[: json.loads(request)["count"]]}))


def _is_rubric_request(request: str) -> bool:
    return request.startswith("<Output>")


def _judge(*, passed: bool, refused: bool = False) -> _ScriptedTarget:
    """Grade the rubric with ``passed`` and answer the refusal check with ``refused``."""

    def reply(request: str) -> str:
        if _is_rubric_request(request):
            return json.dumps({"reason": "Graded.", "pass": passed, "score": int(passed)})
        return json.dumps({"score_value": refused, "rationale": "Checked for a refusal."})

    return _ScriptedTarget(reply=reply)


def _application() -> _ScriptedTarget:
    return _ScriptedTarget(reply=lambda _: ANSWER)


async def _initialize_async(
    *,
    generator: _ScriptedTarget,
    judge: _ScriptedTarget,
    application: _ScriptedTarget | None = None,
    techniques: list[str] | None = None,
    **args: object,
) -> Hallucination:
    scenario = Hallucination(adversarial_chat=generator)
    if techniques is not None:
        args["scenario_techniques"] = [scenario._technique_class(name) for name in techniques]
    scenario.set_params_from_args(
        args={
            "objective_target": application or _application(),
            "scoring_target": judge,
            "purpose": PURPOSE,
            "num_tests": len(TESTS),
            **args,
        }
    )
    await scenario.initialize_async()
    return scenario


async def _run_async(
    *,
    generator: _ScriptedTarget,
    judge: _ScriptedTarget,
    application: _ScriptedTarget | None = None,
    techniques: list[str] | None = None,
    **args: object,
) -> list:
    scenario = await _initialize_async(
        generator=generator, judge=judge, application=application, techniques=techniques, **args
    )
    result = await scenario.run_async()
    return [attack for attacks in result.attack_results.values() for attack in attacks]


@pytest.fixture
def registered_generator() -> Iterator[_ScriptedTarget]:
    generator = _generator()
    TargetRegistry.get_registry_singleton().instances.register(generator, name="adversarial_chat")
    yield generator
    TargetRegistry.reset_registry_singleton()


@pytest.fixture
def technique_registry() -> Iterator[AttackTechniqueRegistry]:
    yield AttackTechniqueRegistry.get_registry_singleton()
    AttackTechniqueRegistry.reset_registry_singleton()


def _image_only_requirements() -> TechniqueRequirements:
    return TechniqueRequirements(
        objective_target=TargetRequirements(required_input_modalities=frozenset({frozenset({"image_path"})}))
    )


def test_declares_purpose_and_num_tests() -> None:
    parameters = {parameter.name: parameter for parameter in Hallucination.supported_parameters()}

    assert parameters["purpose"].required
    assert parameters["num_tests"].default == 5


def test_default_technique_is_basic_without_baseline() -> None:
    scenario = Hallucination(objective_scorer=PromptfooRubricScorer(chat_target=_judge(passed=True)))

    assert [technique.value for technique in scenario._default_technique.expand({scenario._default_technique})] == [
        "basic"
    ]
    assert Hallucination.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Disabled


def test_declares_no_default_datasets_without_purpose() -> None:
    scenario = Hallucination(adversarial_chat=_generator())

    assert scenario.get_default_dataset_names() == []


@pytest.mark.parametrize(
    ("passed", "refused", "outcome"),
    [
        (False, False, AttackOutcome.SUCCESS),
        (True, False, AttackOutcome.FAILURE),
        (False, True, AttackOutcome.FAILURE),
    ],
)
async def test_run_succeeds_only_when_response_fails_rubric_without_refusing_async(
    passed: bool, refused: bool, outcome: AttackOutcome
) -> None:
    generator = _generator()
    judge = _judge(passed=passed, refused=refused)

    attacks = await _run_async(generator=generator, judge=judge)

    assert sorted(attack.objective for attack in attacks) == sorted(TESTS)
    assert {attack.outcome for attack in attacks} == {outcome}


@pytest.mark.parametrize(
    ("data_type", "response_error", "outcome"),
    [
        ("image_path", "none", AttackOutcome.UNDETERMINED),
        ("function_call", "none", AttackOutcome.UNDETERMINED),
        ("error", "blocked", AttackOutcome.FAILURE),
    ],
)
async def test_run_does_not_score_responses_without_text_as_success_async(
    tmp_path: Path, data_type: PromptDataType, response_error: str, outcome: AttackOutcome
) -> None:
    image = tmp_path / "answer.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    values = {
        "image_path": str(image),
        "function_call": json.dumps({"type": "function_call", "name": "book", "arguments": "{}", "call_id": "c1"}),
        "error": "",
    }
    application = _ScriptedTarget(reply=lambda _: values[data_type], data_type=data_type, response_error=response_error)
    judge = _judge(passed=False)

    attacks = await _run_async(generator=_generator(), judge=judge, application=application)

    assert {attack.outcome for attack in attacks} == {outcome}
    assert judge.prompt_sent == []


async def test_run_grades_with_attribution_guidance_and_purpose_rubric_async() -> None:
    judge = _judge(passed=False)

    await _run_async(generator=_generator(), judge=judge)

    rubric_requests = [request for request in judge.prompt_sent if _is_rubric_request(request)]
    assert len(rubric_requests) == len(TESTS)
    for request in rubric_requests:
        assert "<Rubric>\nTarget response attribution:" in request
        assert f"<purpose>\n{PURPOSE}\n</purpose>" in request


async def test_run_reuses_tests_for_same_inputs_async() -> None:
    generator = _generator()

    await _run_async(generator=generator, judge=_judge(passed=True))
    await _run_async(generator=generator, judge=_judge(passed=True))

    assert len(generator.prompt_sent) == 1


@pytest.mark.parametrize("changed", [{"purpose": "A bank's support assistant."}, {"num_tests": 1}])
async def test_run_generates_new_tests_when_generation_inputs_change_async(changed: dict[str, object]) -> None:
    generator = _generator()

    await _run_async(generator=generator, judge=_judge(passed=True))
    await _run_async(generator=generator, judge=_judge(passed=True), **changed)

    assert len(generator.prompt_sent) == 2


async def test_run_uses_selected_technique_with_its_converters_async(
    technique_registry: AttackTechniqueRegistry,
) -> None:
    technique_registry.register_from_factories(
        [
            AttackTechniqueFactory(
                name="rot13_send",
                attack_class=PromptSendingAttack,
                technique_tags=["single_turn"],
                supports_additional_request_converters=True,
            )
        ]
    )
    application = _application()

    await _run_async(
        generator=_generator(),
        judge=_judge(passed=True),
        application=application,
        techniques=["rot13_send"],
        technique_converters={"rot13_send": [ROT13Converter()]},
    )

    rot13 = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm"
    )
    assert sorted(application.prompt_sent) == sorted(test.translate(rot13) for test in TESTS)


@pytest.mark.parametrize("policy", ["skip", "raise"])
async def test_incompatible_selected_technique_follows_policy_async(
    technique_registry: AttackTechniqueRegistry, policy: str
) -> None:
    technique_registry.register_from_factories(
        [
            AttackTechniqueFactory(
                name="image_only", attack_class=PromptSendingAttack, requirements=_image_only_requirements()
            )
        ]
    )
    application = _application()
    techniques = ["basic", "image_only"]

    if policy == "raise":
        with pytest.raises(IncompatibleTechniqueError, match="image_only"):
            await _initialize_async(
                generator=_generator(),
                judge=_judge(passed=True),
                application=application,
                techniques=techniques,
                incompatible_technique_policy=policy,
            )
        assert application.prompt_sent == []
    else:
        attacks = await _run_async(
            generator=_generator(),
            judge=_judge(passed=True),
            application=application,
            techniques=techniques,
            incompatible_technique_policy=policy,
        )
        assert sorted(attack.objective for attack in attacks) == sorted(TESTS)
        assert sorted(application.prompt_sent) == sorted(TESTS)


async def test_basic_technique_is_not_replaced_by_registered_basic_async(
    technique_registry: AttackTechniqueRegistry,
) -> None:
    technique_registry.register_from_factories(
        [
            AttackTechniqueFactory(
                name="basic", attack_class=PromptSendingAttack, requirements=_image_only_requirements()
            )
        ]
    )
    application = _application()

    attacks = await _run_async(
        generator=_generator(),
        judge=_judge(passed=True),
        application=application,
        incompatible_technique_policy="raise",
    )

    assert sorted(attack.objective for attack in attacks) == sorted(TESTS)
    assert sorted(application.prompt_sent) == sorted(TESTS)


async def test_run_uses_supplied_dataset_as_stored_without_generating_async() -> None:
    rows = [f"Which restaurant opened first in Macon, number {index}?" for index in range(len(TESTS) + 1)]
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[
            SeedObjective(
                value=row, dataset_name="my_rubric_dataset", conditions=[SatisfiesRubric(rubric="Cites a source.")]
            )
            for row in rows
        ],
        added_by="test",
    )
    generator = _generator()

    attacks = await _run_async(
        generator=generator, judge=_judge(passed=True), dataset_overrides={"sources": [{"name": "my_rubric_dataset"}]}
    )

    assert generator.prompt_sent == []
    assert sorted(attack.objective for attack in attacks) == sorted(rows)


async def test_initialize_with_fetch_never_does_not_generate_missing_tests_async() -> None:
    generator = _generator()

    with pytest.raises(DatasetConstraintError, match="fetch is 'never'"):
        await _initialize_async(generator=generator, judge=_judge(passed=True), dataset_overrides={"fetch": "never"})
    assert generator.prompt_sent == []


async def test_initialize_with_fetch_always_regenerates_stored_tests_async() -> None:
    generator = _generator()

    await _initialize_async(generator=generator, judge=_judge(passed=True))
    await _initialize_async(generator=generator, judge=_judge(passed=True), dataset_overrides={"fetch": "always"})

    assert len(generator.prompt_sent) == 2


async def test_load_datasets_generates_tests_with_supplied_adversarial_chat_async() -> None:
    generator = _generator()
    scenario = Hallucination(adversarial_chat=generator)
    scenario.set_params_from_args(args={"scoring_target": _judge(passed=True), "purpose": PURPOSE, "num_tests": 2})

    await scenario.load_datasets_async()

    assert len(generator.prompt_sent) == 1


async def test_load_datasets_generates_tests_with_registered_adversarial_chat_async(
    registered_generator: _ScriptedTarget,
) -> None:
    scenario = Hallucination()
    scenario.set_params_from_args(args={"scoring_target": _judge(passed=True), "purpose": PURPOSE, "num_tests": 2})

    await scenario.load_datasets_async()

    assert len(registered_generator.prompt_sent) == 1


async def test_load_datasets_with_only_supplied_dataset_needs_no_adversarial_chat_async() -> None:
    TargetRegistry.reset_registry_singleton()
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[
            SeedObjective(
                value="Which restaurant opened first in Macon?",
                dataset_name="my_rubric_dataset",
                conditions=[SatisfiesRubric(rubric="Cites a source.")],
            )
        ],
        added_by="test",
    )
    scenario = Hallucination()
    scenario.set_params_from_args(
        args={
            "scoring_target": _judge(passed=True),
            "purpose": PURPOSE,
            "num_tests": 2,
            "dataset_overrides": {"sources": [{"name": "my_rubric_dataset"}]},
        }
    )

    await scenario.load_datasets_async()


@pytest.mark.parametrize(("args", "message"), [({"purpose": " "}, "purpose"), ({"num_tests": 0}, "num_tests")])
async def test_initialize_rejects_invalid_inputs_async(args: dict[str, object], message: str) -> None:
    scenario = Hallucination(adversarial_chat=_generator())
    scenario.set_params_from_args(
        args={"objective_target": _generator(), "scoring_target": _judge(passed=True), "purpose": PURPOSE, **args}
    )

    with pytest.raises(ValueError, match=message):
        await scenario.initialize_async()


def test_default_objective_scorer_covers_rubric_condition() -> None:
    scenario = Hallucination(adversarial_chat=_generator())
    scenario.set_params_from_args(args={"scoring_target": _judge(passed=True), "purpose": PURPOSE})

    assert SatisfiesRubric in scenario._objective_scorer.get_condition_types()
