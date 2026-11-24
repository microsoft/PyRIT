# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Opt-in techniques for testing tool-using agents."""

import asyncio

from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.executor.attack.multi_turn.simulated_conversation import runs_tools_locally
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, TargetRegistry
from pyrit.registry.instance_registry import refuse_saved_entry
from pyrit.scenario import AttackTechniqueFactory
from pyrit.setup.pyrit_initializer import PyRITInitializer


class ToolsInitializer(PyRITInitializer):
    """Register task-specific tool-use techniques with example call configuration."""

    async def initialize_async(self) -> None:
        """
        Register tool-use techniques using the configured adversarial chat.

        ``tool_priming_simulated`` is registered only when the adversarial chat runs tool calls, since
        a stand-in for the target is copied from it.
        """
        registry = AttackTechniqueRegistry.get_registry_singleton()
        # Returning early would leave a saved technique standing in for this one.
        refuse_saved_entry(registry.instances.get_entry("role_play_tool_calls"))
        refuse_saved_entry(registry.instances.get_entry("tool_priming_simulated"))
        adversarial_chat = TargetRegistry.get_registry_singleton().instances.get("adversarial_chat")
        if (
            "tool_priming_simulated" not in registry.instances
            and adversarial_chat is not None
            and runs_tools_locally(adversarial_chat)
        ):
            registry.register_from_factories(
                [
                    AttackTechniqueFactory.with_simulated_conversation(
                        name="tool_priming_simulated",
                        description=(
                            "Primes a tool-using target with a conversation in which a stand-in for it calls its "
                            "real tools, with results written by the adversarial chat to make the objective look "
                            "approved."
                        ),
                        use_target_tools=True,
                        num_turns=3,
                        technique_tags=["multi_turn", "tools"],
                    )
                ]
            )
        if "role_play_tool_calls" in registry.instances:
            return
        refuse_saved_entry(ConverterRegistry.get_registry_singleton().instances.get_entry("role_play_tool_calls"))
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
