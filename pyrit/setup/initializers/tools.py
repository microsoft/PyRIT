# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Opt-in techniques for testing tool-using agents."""

import asyncio

from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.setup.pyrit_initializer import PyRITInitializer


class ToolsInitializer(PyRITInitializer):
    """Register task-specific tool-use techniques with example call configuration."""

    async def initialize_async(self) -> None:
        """Register tool-use techniques using the configured adversarial chat."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        if "role_play_tool_calls" in registry.instances:
            return
        converter = await asyncio.to_thread(
            ConverterRegistry.get_registry_singleton().create_instance,
            "RolePlayToolCallConverter",
            converter_target="adversarial_chat",
            valid_call='refund {"order": "order-1", "amount": 25}',
            action_desired='refund {"order": "order-1", "amount": 80}',
        )
        ConverterRegistry.get_registry_singleton().instances.register(
            converter, name="role_play_tool_calls", tags=["tools"]
        )
        registry.register_from_factories(
            [
                AttackTechniqueFactory(
                    name="role_play_tool_calls",
                    attack_class=PromptSendingAttack,
                    description=(
                        "Writes one untrusted message so a desired tool action looks approved "
                        "beside a fixed authorized task."
                    ),
                    technique_tags=["single_turn", "tools"],
                    supports_additional_request_converters=True,
                    attack_kwargs={
                        "max_attempts_on_failure": 0,
                        "attack_converter_config": AttackConverterConfig(
                            request_converters=ConverterConfiguration.from_converters(converters=[converter])
                        ),
                    },
                )
            ]
        )
