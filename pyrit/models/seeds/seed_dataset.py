# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedDataset - Container for managing collections of seeds with top-level defaults.
"""

from __future__ import annotations

import logging
import random
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

from pyrit.common import utils
from pyrit.common.utils import verify_and_resolve_path
from pyrit.models.literals import SeedType  # noqa: TC001  (runtime-required by Pydantic field annotations)
from pyrit.models.seeds.seed import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    Seed,
    coerce_str_to_list,
)
from pyrit.models.seeds.seed_attack_group import SeedAttackGroup
from pyrit.models.seeds.seed_group import SeedGroup
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt
from pyrit.models.seeds.seed_simulated_conversation import SeedSimulatedConversation

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pydantic.types import PositiveInt

logger = logging.getLogger(__name__)


class SeedDataset(BaseModel):
    """
    SeedDataset manages seed prompts plus optional top-level defaults.
    Prompts are stored as a Sequence[Seed], so references to prompt properties
    are straightforward (e.g. ds.seeds[0].value).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    data_type: Optional[str] = "text"
    name: Optional[str] = None
    dataset_name: Optional[str] = None
    harm_categories: Optional[list[str]] = None
    description: Optional[str] = None
    authors: Optional[list[str]] = Field(default_factory=list)
    groups: Optional[list[str]] = Field(default_factory=list)
    source: Optional[str] = None
    date_added: Optional[datetime] = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    added_by: Optional[str] = None
    # The default seed type for items that don't specify their own ("prompt", "objective", ...).
    seed_type: Optional[SeedType] = None

    # The actual prompts
    seeds: list[SerializeAsAny[Seed]]

    @model_validator(mode="before")
    @classmethod
    def _build_seeds(cls, data: Any) -> Any:
        """
        Convert dict seed entries into concrete Seed subclasses, merging dataset-level defaults.

        ``is_jinja_template`` is a construction-time flag (consumed here, not stored) that marks
        seed values as trusted Jinja2 templates.

        Returns:
            Any: The input data with ``seeds`` replaced by built Seed instances.

        Raises:
            ValueError: If the dataset has no seeds.
        """
        if not isinstance(data, dict):
            return data

        data = dict(data)
        is_jinja_template = data.pop("is_jinja_template", False)
        raw_seeds = data.get("seeds")
        if not raw_seeds:
            raise ValueError("SeedDataset cannot be empty.")

        default_data_type = data.get("data_type", "text")
        default_name = data.get("name")
        default_dataset_name = data.get("dataset_name")
        default_description = data.get("description")
        default_source = data.get("source")
        dataset_seed_type = data.get("seed_type")

        built: list[Seed] = []
        for p in raw_seeds:
            if isinstance(p, dict):
                p_seed_type = p.get("seed_type", dataset_seed_type)

                base_params: dict[str, Any] = {
                    "value_sha256": p.get("value_sha256"),
                    "id": uuid.uuid4(),
                    "name": p.get("name") or default_name,
                    "dataset_name": p.get("dataset_name") or default_dataset_name or default_name,
                    "harm_categories": p.get("harm_categories", []),
                    "description": p.get("description") or default_description,
                    "authors": p.get("authors", []),
                    "groups": p.get("groups", []),
                    "source": p.get("source") or default_source,
                    "date_added": p.get("date_added"),
                    "added_by": p.get("added_by"),
                    "metadata": p.get("metadata", {}),
                    "prompt_group_id": p.get("prompt_group_id"),
                    "is_jinja_template": is_jinja_template,
                }

                if p_seed_type == "simulated_conversation":
                    _adv_path = p.get("adversarial_chat_system_prompt_path")
                    _sim_path = p.get("simulated_target_system_prompt_path")
                    _sc_kwargs: dict[str, Any] = {**base_params, "num_turns": p.get("num_turns", 3)}
                    if _adv_path is not None:
                        _sc_kwargs["adversarial_chat_system_prompt_path"] = str(_adv_path)
                    if _sim_path is not None:
                        _sc_kwargs["simulated_target_system_prompt_path"] = str(_sim_path)
                    built.append(SeedSimulatedConversation(**_sc_kwargs))
                elif p_seed_type == "objective":
                    base_params["value"] = p["value"]
                    built.append(SeedObjective(**base_params))
                else:  # prompt
                    base_params["value"] = p["value"]
                    built.append(
                        SeedPrompt(
                            **base_params,
                            data_type=p.get("data_type") or default_data_type,
                            role=p.get("role", "user"),
                            sequence=p.get("sequence", 0),
                            parameters=p.get("parameters") or [],
                        )
                    )
            elif isinstance(p, (SeedPrompt, SeedObjective, SeedSimulatedConversation)):
                built.append(p)
            else:
                raise ValueError(
                    "Seeds should be dicts or Seed objects (SeedPrompt, SeedObjective, SeedSimulatedConversation)."
                )

        data["seeds"] = built
        for key in ("harm_categories", "authors", "groups"):
            data[key] = coerce_str_to_list(data.get(key))
        data["authors"] = data.get("authors") or []
        data["groups"] = data.get("groups") or []
        data["date_added"] = data.get("date_added") or datetime.now(tz=timezone.utc)
        return data

    @classmethod
    def from_yaml_file(cls, file: Union[str, Path]) -> SeedDataset:
        """
        Create a SeedDataset from a YAML file, marking nested seeds as trusted templates.

        Args:
            file: The input file path.

        Returns:
            SeedDataset: The loaded dataset.

        Raises:
            ValueError: If the YAML file is invalid.
        """
        file = verify_and_resolve_path(file)
        try:
            yaml_data = yaml.safe_load(file.read_text("utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML file '{file}': {exc}") from exc

        if yaml_data is None:
            raise ValueError(f"YAML file '{file}' is empty.")

        yaml_data["is_jinja_template"] = True
        return cls.from_dict(yaml_data)

    def get_values(
        self,
        *,
        first: Optional[PositiveInt] = None,
        last: Optional[PositiveInt] = None,
        harm_categories: Optional[Sequence[str]] = None,
    ) -> Sequence[str]:
        """
        Extract and return prompt values from the dataset.

        Args:
            first (Optional[int]): If provided, values from the first N prompts are included.
            last (Optional[int]): If provided, values from the last N prompts are included.
            harm_categories (Optional[Sequence[str]]): If provided, only prompts containing at least one of
                these harm categories are included.

        Returns:
            Sequence[str]: A list of prompt values.

        """
        # Filter by harm categories if specified
        seeds = self.seeds
        if harm_categories:
            seeds = [
                seed
                for seed in seeds
                if seed.harm_categories and any(cat in seed.harm_categories for cat in harm_categories)
            ]

        values = [seed.value for seed in seeds]

        if first is None and last is None:
            return values
        if first is not None and last is not None and first + last >= len(values):
            return values  # simply return all values in case of an overlap

        first_part = values[:first] if first is not None else []
        last_part = values[-last:] if last else []

        return first_part + last_part

    def get_random_values(
        self, *, number: PositiveInt, harm_categories: Optional[Sequence[str]] = None
    ) -> Sequence[str]:
        """
        Extract and return random prompt values from the dataset.

        Args:
            number (int): The number of random prompt values to return.
            harm_categories (Optional[Sequence[str]]): If provided, only prompts containing at least one of
                these harm categories are included.

        Returns:
            Sequence[str]: A list of prompt values.

        """
        prompts = self.get_values(harm_categories=harm_categories)
        return random.sample(prompts, min(len(prompts), number))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeedDataset:
        """
        Build a SeedDataset by merging top-level defaults into each item in `seeds`.

        Args:
            data (Dict[str, Any]): Dataset payload with top-level defaults and seed entries.

        Returns:
            SeedDataset: Constructed dataset with merged defaults.

        Raises:
            ValueError: If any seed entry includes a pre-set prompt_group_id.

        """
        # Pop out the seeds section
        seeds_data = data.pop("seeds", [])

        dataset_defaults = data  # everything else is top-level

        merged_seeds: list[dict[str, Any]] = []
        for p in seeds_data:
            # Merge dataset-level fields with the prompt-level fields
            merged = utils.combine_dict(dataset_defaults, p)

            merged["harm_categories"] = utils.combine_list(
                dataset_defaults.get("harm_categories", []),
                p.get("harm_categories", []),
            )

            merged["authors"] = utils.combine_list(
                dataset_defaults.get("authors", []),
                p.get("authors", []),
            )

            merged["groups"] = utils.combine_list(
                dataset_defaults.get("groups", []),
                p.get("groups", []),
            )

            if "data_type" not in merged:
                merged["data_type"] = dataset_defaults.get("data_type", "text")

            merged_seeds.append(merged)

        for seed in merged_seeds:
            if "prompt_group_id" in seed:
                raise ValueError("prompt_group_id should not be set in seed data")

        SeedDataset._set_seed_group_id_by_alias(seed_prompts=merged_seeds)

        # Now create the dataset with the newly merged prompt dicts
        return cls.model_validate({"seeds": merged_seeds, **dataset_defaults})

    def render_template_value(self, **kwargs: object) -> None:
        """
        Render seed values as templates using provided parameters.

        Args:
            kwargs:Key-value pairs to replace in the SeedDataset value.

        Raises:
            ValueError: If parameters are missing or invalid in the template.

        """
        for seed in self.seeds:
            seed.value = seed.render_template_value(**kwargs)

    @staticmethod
    def _set_seed_group_id_by_alias(seed_prompts: Sequence[dict[str, object]]) -> None:
        """
        Set all seed_group_ids based on prompt_group_alias matches.

        This is important so the prompt_group_alias can be set in yaml to group prompts
        """
        alias_to_group_id = {}

        for prompt in seed_prompts:
            alias = prompt.get("prompt_group_alias")
            if alias:
                if alias not in alias_to_group_id:
                    alias_to_group_id[alias] = uuid.uuid4()
                prompt["prompt_group_id"] = alias_to_group_id[alias]
            else:
                prompt["prompt_group_id"] = uuid.uuid4()

    @staticmethod
    def group_seed_prompts_by_prompt_group_id(seeds: Sequence[Seed]) -> Sequence[SeedGroup]:
        """
        Group the given list of seeds by prompt_group_id and create
        SeedGroup or SeedAttackGroup instances.

        For each group, this method first attempts to create a SeedAttackGroup
        (which has attack-specific properties like objective). If validation fails,
        it falls back to a basic SeedGroup.

        Args:
            seeds: A list of Seed objects.

        Returns:
            A list of SeedGroup or SeedAttackGroup objects, with seeds grouped by
            prompt_group_id. Each group will be ordered by the sequence number of
            the seeds, if available.

        """
        # Group seeds by `prompt_group_id`
        grouped_seeds: dict[uuid.UUID, list[Seed]] = defaultdict(list)
        for seed in seeds:
            if seed.prompt_group_id:
                grouped_seeds[seed.prompt_group_id].append(seed)
            else:
                grouped_seeds[uuid.uuid4()].append(seed)

        # Create SeedGroup or SeedAttackGroup instances from grouped seeds
        seed_groups: list[SeedGroup] = []
        for group_seeds in grouped_seeds.values():
            if len(group_seeds) > 1:
                group_seeds.sort(key=lambda s: s.sequence if hasattr(s, "sequence") else 0)

            # Try to create a SeedAttackGroup first; fall back to SeedGroup if validation fails
            try:
                attack_group = SeedAttackGroup(seeds=group_seeds)
                seed_groups.append(attack_group)
            except ValueError:
                seed_groups.append(SeedGroup(seeds=group_seeds))

        return seed_groups

    @property
    def prompts(self) -> Sequence[SeedPrompt]:
        """
        Return all prompt-type seeds.

        Returns:
            Sequence[SeedPrompt]: Prompt seeds in this dataset.

        """
        return [s for s in self.seeds if isinstance(s, SeedPrompt)]

    @property
    def objectives(self) -> Sequence[SeedObjective]:
        """
        Return all objective-type seeds.

        Returns:
            Sequence[SeedObjective]: Objective seeds in this dataset.

        """
        return [s for s in self.seeds if isinstance(s, SeedObjective)]

    @property
    def seed_groups(self) -> Sequence[SeedGroup]:
        """
        Returns the seeds grouped by their prompt_group_id.

        Returns:
            Sequence[SeedGroup]: A list of SeedGroup objects, with seeds grouped by prompt_group_id.

        """
        return self.group_seed_prompts_by_prompt_group_id(self.seeds)

    def __repr__(self) -> str:
        """
        Return a concise representation of the dataset.

        Returns:
            str: Dataset summary string.

        """
        return f"<SeedDataset(seeds={len(self.seeds)} seeds)>"
