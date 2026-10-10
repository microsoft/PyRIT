# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment

from pyrit.models import SeedPrompt


def _render_system_prompt_template(
    *,
    system_prompt_template: SeedPrompt | str | None,
    default_template_path: Path,
    render_params: Mapping[str, Any],
    required_parameters: Sequence[str],
) -> SeedPrompt:
    if system_prompt_template is None:
        template = SeedPrompt.from_yaml_file(default_template_path)
    elif isinstance(system_prompt_template, SeedPrompt):
        template = system_prompt_template
    elif isinstance(system_prompt_template, str):
        template = SeedPrompt(value=system_prompt_template, data_type="text", is_jinja_template=True)
    else:
        raise TypeError("system_prompt_template must be a SeedPrompt, str, or None.")

    parsed_template = SandboxedEnvironment().parse(template.value)
    template_parameters = meta.find_undeclared_variables(parsed_template)
    missing_parameters = sorted(set(required_parameters) - template_parameters)
    if missing_parameters:
        missing = ", ".join(missing_parameters)
        raise ValueError(f"System prompt template must reference these parameters: {missing}.")

    rendered_value = template.render_template_value(**render_params)
    return template.model_copy(update={"value": rendered_value})
