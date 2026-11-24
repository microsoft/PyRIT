# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Generate promptfoo red-team plugin tests as objectives that carry the plugin's grading rubric."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  (Pydantic evaluates the ClassVar annotation at runtime)
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from pyrit.common.path import DATASETS_PATH
from pyrit.common.yaml_helper import safe_load_yaml
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.executor.promptgen import TargetObjectiveGenerator
from pyrit.models import FailsPromptfooRubric, SeedDataset, SeedObjective, SeedOrigin, SeedPrompt

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget


class PromptfooPlugin(BaseModel):
    """
    A promptfoo red-team plugin: the template that generates its tests and the rubric that grades them.

    Both templates use promptfoo's variables: ``purpose`` and, for generation, ``n``, the number of tests.

    Reference: [@promptfoo2025redteam]
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    PLUGINS_PATH: ClassVar[Path] = DATASETS_PATH / "promptfoo" / "plugins"

    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)
    generation_template: str = Field(min_length=1)
    rubric: str = Field(min_length=1)

    @classmethod
    def from_id(cls, plugin_id: str) -> PromptfooPlugin:
        """
        Load a bundled plugin by its promptfoo id.

        Args:
            plugin_id (str): The promptfoo plugin id, such as ``hallucination``.

        Returns:
            PromptfooPlugin: The plugin.

        Raises:
            ValueError: If no bundled plugin has this id.
        """
        available = cls.get_available_ids()
        if plugin_id not in available:
            raise ValueError(f"Unknown promptfoo plugin '{plugin_id}'. Available: {available}")
        path = cls.PLUGINS_PATH / f"{plugin_id}.yaml"
        return cls.model_validate(safe_load_yaml(path.read_text(encoding="utf-8")))

    @classmethod
    def get_available_ids(cls) -> list[str]:
        """
        List the ids of the bundled plugins.

        Returns:
            list[str]: The plugin ids, sorted.
        """
        return sorted(path.stem for path in cls.PLUGINS_PATH.glob("*.yaml"))

    def render_generation_prompt(self, *, purpose: str, count: int) -> SeedPrompt:
        """
        Render the generation template as the generator's system prompt.

        Args:
            purpose (str): What the application under test does.
            count (int): The number of tests to generate.

        Returns:
            SeedPrompt: The rendered prompt. It uses the generator's bundled response schema.
        """
        template = SeedPrompt(value=self.generation_template, data_type="text", name=f"promptfoo:{self.id}")
        return SeedPrompt(
            value=template.render_template_value(purpose=purpose, n=count),
            data_type="text",
            is_jinja_template=False,
        )

    def render_rubric(self, *, purpose: str) -> str:
        """
        Render the grading rubric for an application.

        Args:
            purpose (str): What the application under test does.

        Returns:
            str: The rubric text.
        """
        template = SeedPrompt(value=self.rubric, data_type="text", name=f"promptfoo:{self.id}:rubric")
        return template.render_template_value(purpose=purpose)


class PromptfooPluginProvider(SeedDatasetProvider):
    """
    Generate one promptfoo plugin's tests for an application and attach the plugin's rubric to each.

    Each test is a ``SeedObjective`` whose ``FailsPromptfooRubric`` condition carries the rubric rendered
    with the application's purpose, so ``PromptfooRubricScorer`` can grade the target's response.
    Every fetch generates a new batch.

    Reference: [@promptfoo2025redteam]
    """

    should_register = False

    def __init__(
        self,
        *,
        dataset_name: str,
        plugin: PromptfooPlugin,
        purpose: str,
        count: int,
        target: PromptTarget,
        timeout_seconds: float = 120,
    ) -> None:
        """
        Configure one plugin's generation for one application.

        Args:
            dataset_name (str): Destination dataset name, assigned to every seed.
            plugin (PromptfooPlugin): The plugin whose tests to generate.
            purpose (str): What the application under test does.
            count (int): The number of tests to generate.
            target (PromptTarget): The target that writes the tests.
            timeout_seconds (float): Generation deadline, including retry waits. Defaults to 120.

        Raises:
            ValueError: If the dataset name or purpose is empty, or the count is not positive.
        """
        if not dataset_name.strip():
            raise ValueError("dataset_name must be a non-empty string.")
        if not purpose.strip():
            raise ValueError("purpose must describe the application under test.")
        if isinstance(count, bool) or count < 1:
            raise ValueError("count must be a positive integer.")
        self._dataset_name = dataset_name
        self._plugin = plugin
        self._purpose = purpose
        self._count = count
        self._generator = TargetObjectiveGenerator(
            target=target,
            system_prompt=plugin.render_generation_prompt(purpose=purpose, count=count),
            timeout_seconds=timeout_seconds,
        )

    @property
    def dataset_name(self) -> str:
        """The destination name shared by every generated seed."""
        return self._dataset_name

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Generate the plugin's tests.

        Args:
            cache (bool): Accepted for provider compatibility; generated output is never cached.

        Returns:
            SeedDataset: One objective per test, each carrying the plugin's rendered rubric.
        """
        result = await self._generator.execute_async(instructions=self._purpose, count=self._count)
        condition = FailsPromptfooRubric(
            plugin_id=self._plugin.id, rubric=self._plugin.render_rubric(purpose=self._purpose)
        )
        return SeedDataset(
            dataset_name=self._dataset_name,
            seeds=[
                SeedObjective(
                    value=value,
                    dataset_name=self._dataset_name,
                    origin=SeedOrigin.GENERATED,
                    is_jinja_template=False,
                    harm_categories=[self._plugin.id],
                    source=self._plugin.source,
                    conditions=(condition,),
                    metadata={
                        "promptfoo_plugin": self._plugin.id,
                        "purpose": self._purpose,
                        "generation_conversation_id": result.conversation_id,
                    },
                )
                for value in result.objectives
            ],
        )
