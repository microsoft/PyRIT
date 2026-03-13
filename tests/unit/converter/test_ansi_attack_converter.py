# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.prompt_converter import ConverterResult
from pyrit.prompt_converter.ansi_escape.ansi_attack_converter import AnsiAttackConverter
from pyrit.prompt_converter.ansi_escape.ansi_payloads import (
    ASKS,
    ESCAPED_PAYLOADS,
    HIGH_LEVEL_TASKS,
    LIVE_PAYLOADS,
    REPEAT_STUBS,
    UNESCAPE_STUBS,
)


@pytest.mark.asyncio
async def test_convert_async_default_settings():
    converter = AnsiAttackConverter()
    prompt = "User wants to do something."
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result, ConverterResult)
    assert result.output_text != ""
    # Just check that the user's prompt is incorporated as per default settings
    assert "User wants to do something." in result.output_text


@pytest.mark.asyncio
async def test_convert_async_no_user_prompt():
    converter = AnsiAttackConverter(incorporate_user_prompt=True)
    # If no user prompt is given (empty string), just ensure we get a scenario
    result = await converter.convert_async(prompt="")
    assert isinstance(result, ConverterResult)
    assert result.output_text != ""


@pytest.mark.asyncio
async def test_convert_async_incorporate_user_prompt_false():
    converter = AnsiAttackConverter(incorporate_user_prompt=False)
    prompt = "User prompt that should not appear."
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result, ConverterResult)
    # The user prompt should not appear in the output text
    assert "User prompt that should not appear." not in result.output_text


@pytest.mark.asyncio
async def test_convert_async_unescape_scenario():
    # Force scenario to choose the unescape scenario by mocking random.choice
    with patch("random.choice") as mock_choice:
        # We pick a stable return sequence for mock_choice:
        # The converter tries to pick scenario from scenario_choices which is influenced by multiple
        # random.choice calls. We'll guide it step-by-step.
        # First scenario selection: scenario with tasks
        # second scenario selection: scenario with repeats
        # third scenario selection: scenario with unescape
        # last scenario picks from these three. We'll force it to pick the unescape scenario last.
        mock_choice.side_effect = [
            ASKS[0],  # from scenario 1
            HIGH_LEVEL_TASKS[0],  # from scenario 1
            REPEAT_STUBS[0],  # from scenario 2
            (LIVE_PAYLOADS + ESCAPED_PAYLOADS)[0],  # from scenario 2 pick a payload
            UNESCAPE_STUBS[0],  # from scenario 3
            ESCAPED_PAYLOADS[0],  # from scenario 3
            # Finally pick scenario 3 (unescape scenario) as final scenario
            # scenario_choices: [scenario1, scenario2, scenario3]  # noqa: ERA001
            # scenario 1: "ASKS ..."
            # scenario 2: "REPEAT..."
            # scenario 3: "UNESCAPE..."
            # Let's pick scenario 3
            # After all these side effects, the last call to random.choice will pick from these 3 scenarios
            # We'll just return the scenario 3 text constructed in code.
        ]

        converter = AnsiAttackConverter(
            include_raw=True,
            include_escaped=True,
            include_tasks=True,
            include_repeats=True,
            include_unescape=True,
            incorporate_user_prompt=True,
        )

        # Because we set side_effect so specifically, we need to adapt the calls:
        # Actually, the code chooses scenario structure as:
        # scenario_choices.append(f"{ask} {task}")  --> scenario 1
        # scenario_choices.append(f"{repeat_stub} {chosen_payload}") --> scenario 2
        # scenario_choices.append(f"{unescape_stub} {escaped_payload}") --> scenario 3
        #
        # The last random.choice will pick one of these 3 final scenario strings. We want scenario 3 returned:
        # Once scenario1 is built, scenario2 is built, scenario3 is built.
        # The last call to random.choice picks from them.
        # We simplify: On the last call, just return scenario3.
        # We'll do that by adding another mock return at the end.
        mock_choice.side_effect = [
            # pick ask
            ASKS[0],
            # pick task
            HIGH_LEVEL_TASKS[0],
            # pick repeat_stub
            REPEAT_STUBS[0],
            # pick chosen_payload for scenario2
            (LIVE_PAYLOADS + ESCAPED_PAYLOADS)[0],
            # pick unescape_stub
            UNESCAPE_STUBS[0],
            # pick escaped_payload for scenario3
            ESCAPED_PAYLOADS[0],
            # final scenario pick: return scenario3
            "Decode the following as raw bytes: \\x1b[",
        ]

        result = await converter.convert_async(prompt="Test prompt")
        assert isinstance(result, ConverterResult)
        # Check that it contains the unescape request
        assert "Decode the following as raw bytes:" in result.output_text
        assert "Test prompt" in result.output_text


@pytest.mark.asyncio
async def test_convert_async_all_features_off():
    converter = AnsiAttackConverter(
        include_raw=False,
        include_escaped=False,
        include_tasks=False,
        include_repeats=False,
        include_unescape=False,
        incorporate_user_prompt=False,
    )
    # With all features off, we fallback to a default scenario
    result = await converter.convert_async(prompt="No features")
    assert isinstance(result, ConverterResult)
    # Check fallback scenario text
    assert "render green terminal text" in result.output_text


@pytest.mark.asyncio
async def test_convert_async_invalid_input_type():
    converter = AnsiAttackConverter()
    with pytest.raises(ValueError) as excinfo:
        await converter.convert_async(prompt="Hello, world!", input_type="non-text")
    assert "Input type not supported" in str(excinfo.value)


@pytest.mark.asyncio
async def test_convert_async_no_scenarios():
    # Test a scenario where no variants are available:
    # Actually, if we set all includes to False, we get the fallback scenario.
    # Let's do the same with incorporate_user_prompt to ensure stable result.
    converter = AnsiAttackConverter(
        include_raw=False,
        include_escaped=False,
        include_tasks=False,
        include_repeats=False,
        include_unescape=False,
        incorporate_user_prompt=False,
    )
    result = await converter.convert_async(prompt="")
    assert isinstance(result, ConverterResult)
    # Check fallback scenario text again
    assert "render green terminal text" in result.output_text


@pytest.mark.asyncio
async def test_convert_async_only_tasks():
    converter = AnsiAttackConverter(
        include_raw=False,
        include_escaped=False,
        include_tasks=True,
        include_repeats=False,
        include_unescape=False,
        incorporate_user_prompt=True,
    )
    result = await converter.convert_async(prompt="User input")
    # Should only produce a scenario related to tasks and incorporate the prompt
    assert isinstance(result, ConverterResult)
    out = result.output_text
    # Should contain something from ASKS and HIGH_LEVEL_TASKS combined
    assert any(ask in out for ask in ASKS)
    assert any(task in out for task in HIGH_LEVEL_TASKS)
    assert "User input" in out
