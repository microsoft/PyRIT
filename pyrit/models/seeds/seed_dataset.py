# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedDataset - Container for managing collections of seeds with top-level defaults.
"""

from __future__ import annotations

import logging
import random
import uuid
import warnings
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Union

from pydantic.types import PositiveInt

from pyrit.common import utils
from pyrit.common.yaml_loadable import YamlLoadable
from pyrit.models.literals import PromptDataType, SeedType
from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_attack_group import SeedAttackGroup
from pyrit.models.seeds.seed_group import SeedGroup
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt
from pyrit.models.seeds.seed_simulated_conversation import SeedSimulatedConversation

logger = logging.getLogger(__name__)


class SeedDataset(YamlLoadable):
    """
    SeedDataset manages seed prompts plus optional top-level defaults.
    Prompts are stored as a Sequence[Seed], so references to prompt properties
    are straightforward (e.g. ds.seeds[0].value).
    """

    data_type: Optional[str]
    name: Optional[str]
    dataset_name: Optional[str]
    harm_categories: Optional[Sequence[str]]
    description: Optional[str]
    authors: Optional[Sequence[str]]
    groups: Optional[Sequence[str]]
    source: Optional[str]
    date_added: Optional[datetime]
    added_by: Optional[str]

    # Now the actual prompts
    seeds: Sequence["Seed"]

    def __init__(
        self,
        *,
        seeds: Optional[Union[Sequence[Dict[str, Any]], Sequence[Seed]]] = None,
        data_type: Optional[PromptDataType] = "text",
        name: Optional[str] = None,
        dataset_name: Optional[str] = None,
        harm_categories: Optional[Sequence[str]] = None,
        description: Optional[str] = None,
        authors: Optional[Sequence[str]] = None,
        groups: Optional[Sequence[str]] = None,
        source: Optional[str] = None,
        date_added: Optional[datetime] = None,
        added_by: Optional[str] = None,
        seed_type: Optional[SeedType] = None,
        is_objective: bool = False,  # Deprecated in 0.13.0: Use seed_type="objective" instead
    ):
        """
        Initialize the dataset.
        Typically, you'll call from_dict or from_yaml_file so that top-level defaults
        are merged into each seed. If you're passing seeds directly, they can be
        either a list of Seed objects or seed dictionaries (which then get
        converted to Seed objects).

        Args:
            seeds: List of seed dictionaries or Seed objects.
            data_type: Default data type for seeds.
            name: Name of the dataset.
            dataset_name: Dataset name for categorization.
            harm_categories: List of harm categories.
            description: Description of the dataset.
            authors: List of authors.
            groups: List of groups.
            source: Source of the dataset.
            date_added: Date when the dataset was added.
            added_by: User who added the dataset.
            seed_type: The type of seeds in this dataset ("prompt", "objective", or "simulated_conversation").
            is_objective: Deprecated in 0.13.0. Use seed_type="objective" instead.
        """
        if not seeds:
            raise ValueError("SeedDataset cannot be empty.")

        # Emit deprecation warning for legacy is_objective parameter
        if is_objective:
            warnings.warn(
                "is_objective parameter is deprecated since 0.13.0. Use seed_type='objective' instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        input_seeds = seeds

        # Store top-level fields
        self.data_type = data_type
        self.name = name
        self.dataset_name = dataset_name

        self.harm_categories = harm_categories
        self.description = description
        self.authors = authors or []
        self.groups = groups or []
        self.source = source
        self.date_added = date_added or datetime.now()
        self.added_by = added_by

        # Convert any dictionaries in `seeds` to SeedPrompt and/or SeedObjective objects
        self.seeds = []
        for p in input_seeds:
            if isinstance(p, dict):
                # Support new seed_type field with backward compatibility for deprecated is_objective
                p_seed_type = p.get("seed_type", seed_type)
                p_is_objective = p.get("is_objective", is_objective)

                # Emit deprecation warning if is_objective is used in dict
                if "is_objective" in p and p["is_objective"]:
                    warnings.warn(
                        "is_objective in seed dict is deprecated since 0.13.0. Use seed_type='objective' instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    # Only error if seed_type is explicitly set to a conflicting value
                    if p_seed_type is not None and p_seed_type != "objective":
                        raise ValueError("Conflicting seed_type and is_objective values.")

                effective_type: SeedType = "prompt"
                if p_seed_type == "objective" or (p_is_objective):
                    effective_type = "objective"
                elif p_seed_type == "simulated_conversation":
                    effective_type = "simulated_conversation"
                elif p_seed_type == "prompt":
                    effective_type = "prompt"

                # Extract common base parameters (from Seed base class) with dataset defaults.
                # Note: If Seed base class param names change, update here too.
                # SeedSimulatedConversation computes its own value, so we don't require it.
                base_params = {
                    "value_sha256": p.get("value_sha256"),
                    "id": uuid.uuid4(),
                    "name": p.get("name") or self.name,
                    "dataset_name": p.get("dataset_name") or self.dataset_name or self.name,
                    "harm_categories": p.get("harm_categories", []),
                    "description": p.get("description") or self.description,
                    "authors": p.get("authors", []),
                    "groups": p.get("groups", []),
                    "source": p.get("source") or self.source,
                    "date_added": p.get("date_added"),
                    "added_by": p.get("added_by"),
                    "metadata": p.get("metadata", {}),
                    "prompt_group_id": p.get("prompt_group_id"),
                }

                if effective_type == "simulated_conversation":
                    self.seeds.append(
                        SeedSimulatedConversation(
                            **base_params,
                            num_turns=p.get("num_turns", 3),
                            adversarial_chat_system_prompt_path=p.get("adversarial_chat_system_prompt_path"),
                            simulated_target_system_prompt_path=p.get("simulated_target_system_prompt_path"),
                        )
                    )
                elif effective_type == "objective":
                    # SeedObjective inherits data_type="text" from base Seed property
                    base_params["value"] = p["value"]
                    self.seeds.append(SeedObjective(**base_params))
                else:  # prompt
                    base_params["value"] = p["value"]
                    self.seeds.append(
                        SeedPrompt(
                            **base_params,
                            data_type=p.get("data_type") or self.data_type,
                            role=p.get("role", "user"),
                            sequence=p.get("sequence", 0),
                            parameters=p.get("parameters", {}),
                        )
                    )
            elif isinstance(p, (SeedPrompt, SeedObjective, SeedSimulatedConversation)):
                self.seeds.append(p)
            else:
                raise ValueError(
                    "Seeds should be dicts or Seed objects (SeedPrompt, SeedObjective, SeedSimulatedConversation)."
                )

    def get_values(
        self,
        *,
        first: Optional[PositiveInt] = None,
        last: Optional[PositiveInt] = None,
        harm_categories: Optional[Sequence[str]] = None,
    ) -> Sequence[str]:
        """
        Extracts and returns a list of prompt values from the dataset. By default, returns all of them.

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
        if first and last and first + last >= len(values):
            return values  # simply return all values in case of an overlap

        first_part = values[:first] if first is not None else []
        last_part = values[-last:] if last is not None else []

        return first_part + last_part

    def get_random_values(
        self, *, number: PositiveInt, harm_categories: Optional[Sequence[str]] = None
    ) -> Sequence[str]:
        """
        Extracts and returns a list of random prompt values from the dataset.

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
    def from_dict(cls, data: Dict[str, Any]) -> SeedDataset:
        """
        Builds a SeedDataset by merging top-level defaults into each item in 'seeds'.
        """
        # Pop out the seeds section
        seeds_data = data.pop("seeds", [])

        dataset_defaults = data  # everything else is top-level

        merged_seeds = []
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
        return cls(seeds=merged_seeds, **dataset_defaults)

    def render_template_value(self, **kwargs: object) -> None:
        """
        Renders self.value as a template, applying provided parameters in kwargs.

        Args:
            kwargs:Key-value pairs to replace in the SeedDataset value.

        Returns:
            None

        Raises:
            ValueError: If parameters are missing or invalid in the template.
        """
        for seed in self.seeds:
            seed.value = seed.render_template_value(**kwargs)

    @staticmethod
    def _set_seed_group_id_by_alias(seed_prompts: Sequence[dict[str, object]]) -> None:
        """
        Sets all seed_group_ids based on prompt_group_alias matches.

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
        Groups the given list of Seeds by their prompt_group_id and creates
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
        grouped_seeds: Dict[uuid.UUID, list[Seed]] = defaultdict(list)
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
        return [s for s in self.seeds if isinstance(s, SeedPrompt)]

    @property
    def objectives(self) -> Sequence[SeedObjective]:
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
        return f"<SeedDataset(seeds={len(self.seeds)} seeds)>"
