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
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.models.literals import SeedType  # noqa: TC001  (runtime-required by Pydantic field annotations)
from pyrit.models.seeds.attack_seed_group import AttackSeedGroup
from pyrit.models.seeds.seed import (  # AwareDatetimeUTC is runtime-required by Pydantic
    AwareDatetimeUTC,
    Seed,
)
from pyrit.models.seeds.seed_group import (  # runtime-required by Pydantic field annotations
    PROMPT_ONLY_SEED_KEYS,
    SeedGroup,
    SeedUnion,
)
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pydantic.types import PositiveInt

logger = logging.getLogger(__name__)

# Dataset-level defaults that get merged into each dict seed when missing on the seed.
# date_added/added_by/metadata are intentionally excluded — per-seed Pydantic defaults
# (default_factory) are the source of truth.
_SCALAR_DEFAULT_KEYS = ("name", "description", "source")
_LIST_DEFAULT_KEYS = ("harm_categories", "authors", "groups")


def _merge_unique(left: Any, right: Any) -> list[str]:
    """
    Concatenate two list-or-str inputs into a deterministic, order-preserving deduped list.

    Treats ``None`` as empty, accepts bare strings as single-element lists, and preserves the
    order of first occurrence (left first, then any new items from right). Used instead of
    ``utils.combine_list`` because the latter goes through ``set()`` and is nondeterministic
    across processes for non-trivial inputs.

    Args:
        left: First list (or string) of values; falsy values are treated as empty.
        right: Second list (or string) of values; falsy values are treated as empty.

    Returns:
        list[str]: Deduplicated concatenation, preserving first-occurrence order.
    """

    def _as_list(v: Any) -> list[str]:
        if not v:
            return []
        return [v] if isinstance(v, str) else list(v)

    seen: set[str] = set()
    result: list[str] = []
    for item in _as_list(left) + _as_list(right):
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class SeedDataset(BaseModel):
    """
    SeedDataset manages seed prompts plus optional top-level defaults.
    Prompts are stored as a Sequence[Seed], so references to prompt properties
    are straightforward (e.g. ds.seeds[0].value).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    data_type: str | None = "text"
    name: str | None = None
    dataset_name: str | None = None
    harm_categories: list[str] | None = None
    description: str | None = None
    authors: list[str] | None = Field(default_factory=list)
    groups: list[str] | None = Field(default_factory=list)
    source: str | None = None
    date_added: AwareDatetimeUTC | None = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    added_by: str | None = None
    # The default seed type for items that don't specify their own ("prompt", "objective", ...).
    seed_type: SeedType | None = None

    # The actual prompts
    seeds: list[SeedUnion]

    @model_validator(mode="before")
    @classmethod
    def _build_seeds(cls, data: Any) -> Any:
        """
        Merge dataset-level defaults into each dict seed and normalize for the discriminator.

        Concrete Seed instances pass through unchanged. For dict seeds:

        - ``seed_type`` defaults to the dataset's ``seed_type`` or ``"prompt"``.
        - ``is_jinja_template`` (construction-time flag, popped from the dataset) is propagated.
        - Scalar defaults (name, dataset_name, description, source) fall back to the dataset's
          when the seed has none.
        - List defaults (harm_categories, authors, groups) are concatenated with deterministic
          order-preserving dedup (dataset values first, then seed-only additions).
        - For prompts: ``data_type`` falls back to the dataset's; ``role`` defaults to ``"user"``.
        - For objective/simulated_conversation: ``data_type``/``role``/``sequence``/
          ``parameters`` are stripped — they aren't valid fields on those classes and a
          dataset-level value (e.g. ``data_type: image_path``) would otherwise be rejected.

        Returns:
            The data with normalized ``seeds`` (passes through unchanged if not a dict).

        Raises:
            ValueError: If the dataset has no seeds or contains an unsupported seed entry.
        """
        if not isinstance(data, dict):
            return data

        data = dict(data)
        is_jinja_template = data.pop("is_jinja_template", False)
        raw_seeds = data.get("seeds")
        if not raw_seeds:
            raise ValueError("SeedDataset cannot be empty.")

        default_seed_type = data.get("seed_type") or "prompt"
        default_data_type = data.get("data_type") or "text"
        default_dataset_name = data.get("dataset_name") or data.get("name")

        normalized: list[Any] = []
        for p in raw_seeds:
            if isinstance(p, Seed):
                normalized.append(p)
                continue
            if not isinstance(p, dict):
                raise ValueError(
                    "Seeds should be dicts or Seed objects (SeedPrompt, SeedObjective, SeedSimulatedConversation)."
                )

            p = dict(p)
            seed_type = p.setdefault("seed_type", default_seed_type)
            p["is_jinja_template"] = is_jinja_template

            for key in _SCALAR_DEFAULT_KEYS:
                if not p.get(key) and data.get(key) is not None:
                    p[key] = data.get(key)
            if not p.get("dataset_name") and default_dataset_name is not None:
                p["dataset_name"] = default_dataset_name

            for key in _LIST_DEFAULT_KEYS:
                p[key] = _merge_unique(data.get(key), p.get(key))

            if seed_type == "prompt":
                if not p.get("data_type"):
                    p["data_type"] = default_data_type
                p.setdefault("role", "user")
            else:
                # Non-prompt seeds narrow data_type to Literal["text"] and don't have
                # role/sequence/parameters fields. Drop those so dataset-level defaults
                # don't bleed in and trip extra="forbid" on the leaf class.
                for prompt_only in PROMPT_ONLY_SEED_KEYS:
                    p.pop(prompt_only, None)

            normalized.append(p)

        data["seeds"] = normalized
        return data

    @classmethod
    def from_yaml_file(cls, file: str | Path) -> SeedDataset:
        """
        Create a SeedDataset from a YAML file, marking nested seeds as trusted templates.

        Thin shim that delegates to
        ``pyrit.models.seeds.yaml_seed_loader.load_seed_dataset_from_yaml``; file I/O and
        the ``is_jinja_template`` trust marker live in the loader module.

        Args:
            file: The input file path.

        Returns:
            SeedDataset: The loaded dataset.

        Raises:
            FileNotFoundError: If the path does not resolve to an existing file.
            ValueError: If the YAML file is invalid or empty.
        """
        # Deferred import: yaml_seed_loader imports SeedDataset at module load, so importing
        # it at the top of this module would create a circular import.
        from pyrit.models.seeds.yaml_seed_loader import load_seed_dataset_from_yaml

        return load_seed_dataset_from_yaml(file)

    def get_values(
        self,
        *,
        first: PositiveInt | None = None,
        last: PositiveInt | None = None,
        harm_categories: Sequence[str] | None = None,
    ) -> Sequence[str]:
        """
        Extract and return prompt values from the dataset.

        Args:
            first (int | None): If provided, values from the first N prompts are included.
            last (int | None): If provided, values from the last N prompts are included.
            harm_categories (Sequence[str] | None): If provided, only prompts containing at least one of
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

    def get_random_values(self, *, number: PositiveInt, harm_categories: Sequence[str] | None = None) -> Sequence[str]:
        """
        Extract and return random prompt values from the dataset.

        Args:
            number (int): The number of random prompt values to return.
            harm_categories (Sequence[str] | None): If provided, only prompts containing at least one of
                these harm categories are included.

        Returns:
            Sequence[str]: A list of prompt values.

        """
        prompts = self.get_values(harm_categories=harm_categories)
        return random.sample(prompts, min(len(prompts), number))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeedDataset:
        """
        Build a SeedDataset, assigning per-seed ``prompt_group_id`` by alias.

        Default merging now lives in ``_build_seeds`` so direct construction and
        ``from_dict`` produce equivalent results. This method handles the YAML-only
        concerns: rejecting pre-set ``prompt_group_id`` on input seeds and resolving
        ``prompt_group_alias`` into a shared ``prompt_group_id``.

        Args:
            data (dict[str, Any]): Dataset payload with top-level defaults and seed entries.

        Returns:
            SeedDataset: Constructed dataset.

        Raises:
            ValueError: If any seed entry includes a pre-set ``prompt_group_id``.
        """
        data = dict(data)

        # Shallow-copy each dict seed so alias resolution doesn't mutate caller-owned dicts;
        # non-dict seeds (e.g. Seed instances) pass through untouched.
        seeds_data: list[Any] = [dict(seed) if isinstance(seed, dict) else seed for seed in data.get("seeds", [])]

        dict_seeds = [s for s in seeds_data if isinstance(s, dict)]
        for seed in dict_seeds:
            if "prompt_group_id" in seed:
                raise ValueError("prompt_group_id should not be set in seed data")
        cls._set_seed_group_id_by_alias(dict_seeds)

        data["seeds"] = seeds_data
        return cls.model_validate(data)

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
        SeedGroup or AttackSeedGroup instances.

        For each group, this method first attempts to create a AttackSeedGroup
        (which has attack-specific properties like objective). If validation fails,
        it falls back to a basic SeedGroup.

        Args:
            seeds: A list of Seed objects.

        Returns:
            A list of SeedGroup or AttackSeedGroup objects, with seeds grouped by
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

        # Create SeedGroup or AttackSeedGroup instances from grouped seeds
        seed_groups: list[SeedGroup] = []
        for group_seeds in grouped_seeds.values():
            if len(group_seeds) > 1:
                group_seeds.sort(key=lambda s: getattr(s, "sequence", 0))

            # Try to create a AttackSeedGroup first; fall back to SeedGroup if validation fails
            try:
                attack_group = AttackSeedGroup(seeds=cast("list[SeedUnion]", group_seeds))
                seed_groups.append(attack_group)
            except ValueError:
                seed_groups.append(SeedGroup(seeds=cast("list[SeedUnion]", group_seeds)))

        return seed_groups

    @property
    def prompts(self) -> Sequence[SeedPrompt]:
        """
        All prompt-type seeds.

        Returns:
            Sequence[SeedPrompt]: Prompt seeds in this dataset.

        """
        return [s for s in self.seeds if isinstance(s, SeedPrompt)]

    @property
    def objectives(self) -> Sequence[SeedObjective]:
        """
        All objective-type seeds.

        Returns:
            Sequence[SeedObjective]: Objective seeds in this dataset.

        """
        return [s for s in self.seeds if isinstance(s, SeedObjective)]

    @property
    def seed_groups(self) -> Sequence[SeedGroup]:
        """
        The seeds grouped by their prompt_group_id.

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
