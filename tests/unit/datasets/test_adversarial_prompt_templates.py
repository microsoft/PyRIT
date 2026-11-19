# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.models import SeedPrompt, get_common_json_schema

ADVERSARIAL_PROMPT_PATHS = [
    path
    for path in sorted(EXECUTOR_SEED_PROMPT_PATH.rglob("*.yaml"))
    if "response_json_schema_name: adversarial_chat" in path.read_text(encoding="utf-8")
]


def test_adversarial_prompt_catalog_is_not_empty() -> None:
    assert ADVERSARIAL_PROMPT_PATHS


@pytest.mark.parametrize(
    "path",
    ADVERSARIAL_PROMPT_PATHS,
    ids=[str(path.relative_to(EXECUTOR_SEED_PROMPT_PATH)) for path in ADVERSARIAL_PROMPT_PATHS],
)
def test_adversarial_prompt_uses_shared_schema_without_duplicate_format(path: Path) -> None:
    prompt = SeedPrompt.from_yaml_file(path)
    assert prompt.response_json_schema == get_common_json_schema("adversarial_chat")

    parameters = dict.fromkeys(prompt.parameters or [], "test context")
    parameters.update(objective="Test objective", max_turns=5)
    rendered = prompt.render_template_value(**parameters)
    assert "Test objective" in rendered
    assert "{{" not in rendered
    assert "{%" not in rendered
    for duplicate in (
        "response format",
        "format your response",
        "json object",
        "json schema",
        '"next_message"',
        '"last_response_summary"',
        '"rationale"',
    ):
        assert duplicate not in rendered.lower()


@pytest.mark.parametrize(
    ("relative_path", "guidance"),
    [
        (("red_teaming", "role_play", "role_play_trivia_game.yaml"), "point tallies as meta commentary"),
        (("red_teaming", "role_play", "role_play_movie_script.yaml"), "beat labels, director's notes"),
        (("red_teaming", "role_play", "role_play_video_game.yaml"), "game designer's voice"),
        (("red_teaming", "role_play", "role_play_persuasion.yaml"), "stays in the seminar frame"),
        (("red_teaming", "role_play", "role_play_persuasion_written.yaml"), "stays in the seminar frame"),
        (("red_teaming", "persuasion_deception", "persuasion_persona_generic.yaml"), "name it in `rationale`"),
        (("crescendo", "image_generation.yaml"), "structured audit"),
        (("crescendo", "image_generation.yaml"), "###Hard Crescendo Constraints###"),
        (("pair", "attacker_system_prompt.yaml"), "Examples of refinement reasoning"),
        (("tree_of_attacks", "adversarial_system_prompt.yaml"), "Examples of refinement reasoning"),
        (("tree_of_attacks", "image_generation.yaml"), "Put that analysis only in `rationale`"),
        (("simulated_target", "direct_next_message.yaml"), 'no "User:" prefix or surrounding quotes'),
        (("simulated_target", "role_play_next_message.yaml"), "stays inside the fiction"),
    ],
)
def test_adversarial_prompt_retains_content_guidance(relative_path: tuple[str, ...], guidance: str) -> None:
    prompt = SeedPrompt.from_yaml_file(EXECUTOR_SEED_PROMPT_PATH.joinpath(*relative_path))
    assert guidance in prompt.value
