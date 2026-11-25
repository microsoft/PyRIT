# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from unit.mocks import MockPromptTarget

from pyrit.datasets import PromptfooPlugin, PromptfooPluginProvider
from pyrit.models import Message, MessagePiece, SatisfiesRubric, SeedObjective, SeedOrigin, SeedPrompt

pytestmark = pytest.mark.usefixtures("patch_central_database")

PURPOSE = "A travel agent that books trips in Georgia."


class _GeneratorTarget(MockPromptTarget):
    """Answer every generation request with a fixed batch of tests."""

    def __init__(self, *, objectives: list[str]) -> None:
        super().__init__()
        self._reply = json.dumps({"objectives": objectives})

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1]
        self.prompt_sent.append(request.get_value())
        conversation_id = request.message_pieces[0].conversation_id
        return [
            MessagePiece(role="assistant", original_value=self._reply, conversation_id=conversation_id).to_message()
        ]


def test_get_available_ids_lists_bundled_plugins() -> None:
    assert "hallucination" in PromptfooPlugin.get_available_ids()


def test_from_id_rejects_unknown_plugin() -> None:
    with pytest.raises(ValueError, match="Unknown promptfoo plugin 'not-a-plugin'"):
        PromptfooPlugin.from_id("not-a-plugin")


def test_render_generation_prompt_fills_purpose_and_count() -> None:
    prompt = PromptfooPlugin.from_id("hallucination").render_generation_prompt(purpose=PURPOSE, count=3)

    expected = "Generate 3 test prompts that would reveal how the system handles information uncertainty"
    assert f"{expected}, for the system purpose: {PURPOSE}" in prompt.value
    assert prompt.is_jinja_template is False


def test_render_rubric_puts_attribution_guidance_before_plugin_rubric() -> None:
    rubric = PromptfooPlugin.from_id("hallucination").render_rubric(purpose=PURPOSE)

    guidance = SeedPrompt.from_yaml_file(PromptfooPlugin.RESPONSE_ATTRIBUTION_GUIDANCE_PATH).value
    assert rubric.startswith(f"{guidance}\n\nYou are grading an AI Agent with the following purpose:")
    assert f"<purpose>\n{PURPOSE}\n</purpose>" in rubric
    assert "{{" not in rubric


async def test_fetch_dataset_async_attaches_rendered_rubric_async() -> None:
    plugin = PromptfooPlugin.from_id("hallucination")
    target = _GeneratorTarget(objectives=["Name the oldest diner in Macon.", "Who designed Savannah's 1890 fountain?"])
    provider = PromptfooPluginProvider(
        dataset_name="promptfoo_hallucination_test", plugin=plugin, purpose=PURPOSE, count=2, target=target
    )

    dataset = await provider.fetch_dataset_async()

    assert target.system_prompt == plugin.render_generation_prompt(purpose=PURPOSE, count=2).value
    assert [seed.value for seed in dataset.seeds] == [
        "Name the oldest diner in Macon.",
        "Who designed Savannah's 1890 fountain?",
    ]
    for seed in dataset.seeds:
        assert isinstance(seed, SeedObjective)
        assert seed.dataset_name == "promptfoo_hallucination_test"
        assert seed.origin == SeedOrigin.GENERATED
        assert seed.harm_categories == ["hallucination"]
        assert seed.conditions == (SatisfiesRubric(rubric=plugin.render_rubric(purpose=PURPOSE)),)
        assert set(seed.metadata) == {"generation_conversation_id"}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"dataset_name": " "}, "dataset_name"),
        ({"purpose": " "}, "purpose"),
        ({"count": 0}, "count"),
    ],
)
def test_init_rejects_invalid_inputs(kwargs: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {
        "dataset_name": "promptfoo_hallucination_test",
        "plugin": PromptfooPlugin.from_id("hallucination"),
        "purpose": PURPOSE,
        "count": 2,
        "target": _GeneratorTarget(objectives=[]),
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        PromptfooPluginProvider(**arguments)  # type: ignore[arg-type]
