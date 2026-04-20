# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random
from typing import Any, Optional

from pyrit.common.path import JAILBREAK_TEMPLATES_PATH
from pyrit.models import SeedPrompt


class TextJailBreak:
    """
    A class that manages jailbreak datasets (like DAN, etc.).
    """

    def __init__(
        self,
        *,
        template_path: Optional[str] = None,
        template_file_name: Optional[str] = None,
        string_template: Optional[str] = None,
        random_template: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Initialize a Jailbreak instance with exactly one template source.

        Args:
            template_path (str, optional): Full path to a YAML template file.
            template_file_name (str, optional): Name of a template file in datasets/jailbreak directory.
            string_template (str, optional): A string template to use directly.
            random_template (bool, optional): Whether to use a random template from datasets/jailbreak.
            **kwargs: Additional parameters to apply to the template. The 'prompt' parameter will be preserved
                     for later use in get_jailbreak().

        Raises:
            ValueError: If more than one template source is provided or if no template source is provided.
        """
        # Track the template source for error reporting
        self.template_source: str = "<unknown>"
        # Count how many template sources are provided
        template_sources = [template_path, template_file_name, string_template, random_template]
        provided_sources = [source for source in template_sources if source]

        if len(provided_sources) != 1:
            raise ValueError(
                "Exactly one of template_path, template_file_name, string_template, or random_template must be provided"
            )

        if template_path:
            self.template = SeedPrompt.from_yaml_file(template_path)
            self.template_source = str(template_path)
        elif string_template:
            self.template = SeedPrompt(value=string_template)
            self.template_source = "<string_template>"
        else:
            # Get all yaml files in the jailbreak directory and its subdirectories
            jailbreak_dir = JAILBREAK_TEMPLATES_PATH
            # Get all yaml files but exclude those in multi_parameter subdirectory
            yaml_files = [f for f in jailbreak_dir.rglob("*.yaml") if "multi_parameter" not in f.parts]
            if not yaml_files:
                raise ValueError(
                    "No YAML templates found in jailbreak directory (excluding multi_parameter subdirectory)"
                )

            if template_file_name:
                matching_files = [f for f in yaml_files if f.name == template_file_name]
                if not matching_files:
                    raise ValueError(
                        f"Template file '{template_file_name}' not found in jailbreak directory or its subdirectories"
                    )
                if len(matching_files) > 1:
                    raise ValueError(f"Multiple files named '{template_file_name}' found in jailbreak directory")
                self.template = SeedPrompt.from_yaml_file(matching_files[0])
                self.template_source = str(matching_files[0])
            else:
                while True:
                    random_template_path = random.choice(yaml_files)
                    self.template = SeedPrompt.from_yaml_file(random_template_path)

                    if self.template.parameters == ["prompt"]:
                        self.template_source = str(random_template_path)
                        # Validate template renders correctly by test-rendering with dummy prompt
                        try:
                            self.template.render_template_value(prompt="test")
                            break
                        except ValueError as e:
                            # Template has syntax errors - fail fast with clear error
                            raise ValueError(f"Invalid jailbreak template '{random_template_path}': {str(e)}") from e

        # Validate that all required parameters (except 'prompt') are provided in kwargs
        required_params = [p for p in self.template.parameters if p != "prompt"]
        missing_params = [p for p in required_params if p not in kwargs]
        if missing_params:
            raise ValueError(
                f"Template requires parameters that were not provided: {missing_params}. "
                f"Required parameters (excluding 'prompt'): {required_params}"
            )

        # Apply any kwargs to the template, preserving the prompt parameter for later use
        if kwargs:
            kwargs.pop("prompt", None)
            # Apply remaining kwargs to the template while preserving template variables
            self.template.value = self.template.render_template_value_silent(**kwargs)

    def get_jailbreak_system_prompt(self) -> str:
        """
        Get the jailbreak template as a system prompt without a specific user prompt.

        Returns:
            str: The rendered jailbreak template with an empty prompt parameter.
        """
        return self.get_jailbreak(prompt="")

    def get_jailbreak(self, prompt: str) -> str:
        """
        Render the jailbreak template with the provided user prompt.

        Args:
            prompt (str): The user prompt to insert into the jailbreak template.

        Returns:
            str: The rendered jailbreak template with the prompt parameter filled in.

        Raises:
            ValueError: If the template fails to render.
        """
        return self.template.render_template_value(prompt=prompt)
