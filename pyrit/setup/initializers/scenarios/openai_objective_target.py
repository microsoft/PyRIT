# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
OpenAI Objective Target Scenario Configuration.

This simply sets the target to use OpenAIChatTarget with basic settings.

It will likely need to be modified based on the target you are testing. But this will work
with OpenAI targets if you set OPENAI_CLI_ENDPOINT
"""

import os

from pyrit.common.apply_defaults import set_default_value
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import Scenario
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer


class ScenarioObjectiveTargetInitializer(PyRITInitializer):
    """Configure a simple objective target for use in PyRIT scenarios."""

    @property
    def name(self) -> str:
        """Return the display name of this initializer."""
        return "Simple Objective Target Configuration for Scenarios"

    @property
    def execution_order(self) -> int:
        """Should be executed after most initializers."""
        return 10

    @property
    def description(self) -> str:
        """Describe the objective target configuration of this initializer."""
        return (
            "This configuration sets up a simple objective target for scenarios "
            "using OpenAIChatTarget with basic settings. It initializes an openAI chat target "
            "using the OPENAI_CLI_ENDPOINT and OPENAI_CLI_KEY environment variables."
        )

    @property
    def required_env_vars(self) -> list[str]:
        """Get list of required environment variables."""
        return [
            "DEFAULT_OPENAI_FRONTEND_ENDPOINT",
            "DEFAULT_OPENAI_FRONTEND_KEY",
        ]

    async def initialize_async(self) -> None:
        """Set default objective target for scenarios that accept them."""
        objective_target = OpenAIChatTarget(
            endpoint=os.getenv("DEFAULT_OPENAI_FRONTEND_ENDPOINT"),
            api_key=os.getenv("DEFAULT_OPENAI_FRONTEND_KEY"),
            model_name=os.getenv("DEFAULT_OPENAI_FRONTEND_MODEL"),
        )

        set_default_value(
            class_type=Scenario,
            parameter_name="objective_target",
            value=objective_target,
        )
