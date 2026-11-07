# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from pyrit.common.path import DATASETS_PATH
from pyrit.memory import SQLiteMemory
from pyrit.models import SeedObjective, SeedPrompt


@pytest.mark.parametrize("seed_class", [SeedPrompt, SeedObjective])
@pytest.mark.parametrize("context", [None, "rainbows"])
async def test_bound_path_operation_roundtrips_sqlite_async(
    *, sqlite_instance: SQLiteMemory, seed_class: type[SeedPrompt | SeedObjective], context: str | None
) -> None:
    dataset_name = f"template_binding_{uuid4().hex}"
    seed = seed_class(
        value="Folder: {{ datasets_path.name }}. {% if context %}{{ context }}{% endif %}",
        data_type="text",
        is_jinja_template=True,
        dataset_name=dataset_name,
    )
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed], added_by="template_binding_integration")
    [stored] = await sqlite_instance.get_seeds_async(dataset_name=dataset_name)

    assert stored.value == seed.value
    assert stored.render_template_value(context=context) == f"Folder: {DATASETS_PATH.name}. {context or ''}"


@pytest.mark.parametrize(
    "path_parts",
    [
        ("executors", "crescendo", "crescendo_variant_1.yaml"),
        ("executors", "tree_of_attacks", "adversarial_system_prompt.yaml"),
    ],
)
@pytest.mark.parametrize("context", [None, "rainbows"])
async def test_attack_context_guard_roundtrips_sqlite_async(
    *, sqlite_instance: SQLiteMemory, path_parts: tuple[str, ...], context: str | None
) -> None:
    seed, expected = await asyncio.to_thread(
        _load_context_seed, path=DATASETS_PATH.joinpath(*path_parts), context=context
    )
    seed.dataset_name = f"context_guard_{uuid4().hex}"
    parameters: dict[str, str | None] = {name: f"<{name}>" for name in seed.parameters or []}
    parameters["conversation_context"] = context

    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed], added_by="template_binding_integration")
    [stored] = await sqlite_instance.get_seeds_async(dataset_name=seed.dataset_name)

    assert stored.render_template_value(**parameters) == expected
    assert ("PRIOR CONVERSATION CONTEXT" in expected.upper()) == (context is not None)


def _load_context_seed(*, path: Path, context: str | None) -> tuple[SeedPrompt, str]:
    seed = SeedPrompt.from_yaml_file(path)
    parameters: dict[str, str | None] = {name: f"<{name}>" for name in seed.parameters or []}
    parameters["conversation_context"] = context
    source = yaml.safe_load(path.read_text(encoding="utf-8"))["value"]
    return seed, SandboxedEnvironment(undefined=StrictUndefined).from_string(source).render(parameters)
