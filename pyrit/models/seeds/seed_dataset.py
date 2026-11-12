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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.common import combine_list
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
from pyrit.models.seeds.seed_objective import SeedObjective, build_objective_decomposition_tree
from pyrit.models.seeds.seed_prompt import SeedPrompt
from pyrit.models.seeds.seed_simulated_conversation import SeedSimulatedConversation
from pyrit.models.seeds.seed_simulated_tool_history import SeedSimulatedToolHistory

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pydantic.types import NonNegativeInt

    from pyrit.models.decomposition import DecompositionTree

logger = logging.getLogger(__name__)


def _validate_count(*, name: str, count: object) -> None:
    """
    Reject a prompt count that is not a non-negative integer.

    The count annotations on the public methods are not enforced at runtime, and a
    negative count would silently slice from the other end of the prompt list.

    Args:
        name (str): The parameter name, used in the error message.
        count (object): The value to validate.

    Raises:
        ValueError: If ``count`` is a bool, not an integer, or negative.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {count!r}")


# Dataset-level defaults that get merged into each dict seed when missing on the seed.
# date_added/added_by/metadata are intentionally excluded — per-seed Pydantic defaults
# (default_factory) are the source of truth.
_SCALAR_DEFAULT_KEYS = ("name", "description", "source")
_LIST_DEFAULT_KEYS = ("harm_categories", "authors", "groups")


class SeedDataset(BaseModel):
    """
    SeedDataset manages seed prompts plus optional top-level defaults.
    Prompts are stored as a Sequence[Seed], so references to prompt properties
    are straightforward (e.g. ds.seeds[0].value).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # YAML fields that nest decomposed objectives under their parent.
    SUB_OBJECTIVES_KEY: ClassVar[str] = "sub_objectives"
    SUB_OBJECTIVE_KEY_FIELD: ClassVar[str] = "key"

    data_type: str | None = "text"
    name: str | None = None
    dataset_name: str | None = None
    harm_categories: list[str] | None = None
    description: str | None = None
    authors: list[str] | None = Field(default_factory=list)
    groups: list[str] | None = Field(default_factory=list)
    source: str | None = None
    date_added: AwareDatetimeUTC | None = Field(default_factory=lambda: datetime.now(tz=UTC))
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
          Bare empty strings are treated as missing metadata.
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
        for p in cls._flatten_sub_objectives(raw_seeds):
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
                p[key] = combine_list(data.get(key) or None, p.get(key) or None)

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
    def from_yaml_file(cls, file: str | Path, *, default_dataset_name: str | None = None) -> SeedDataset:
        """
        Create a SeedDataset from a YAML file, marking nested seeds as trusted templates.

        Thin shim that delegates to
        ``pyrit.models.seeds.yaml_seed_loader.load_seed_dataset_from_yaml``; file I/O and
        the ``is_jinja_template`` trust marker live in the loader module.

        Args:
            file: The input file path.
            default_dataset_name: Dataset name used when the YAML declares neither
                ``dataset_name`` nor ``name``.

        Returns:
            SeedDataset: The loaded dataset.

        Raises:
            FileNotFoundError: If the path does not resolve to an existing file.
            ValueError: If the YAML file is invalid or empty.
        """
        # Deferred import: yaml_seed_loader imports SeedDataset at module load, so importing
        # it at the top of this module would create a circular import.
        from pyrit.models.seeds.yaml_seed_loader import load_seed_dataset_from_yaml

        return load_seed_dataset_from_yaml(file, default_dataset_name=default_dataset_name)

    def get_values(
        self,
        *,
        first: NonNegativeInt | None = None,
        last: NonNegativeInt | None = None,
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

        Raises:
            ValueError: If ``first`` or ``last`` is not a non-negative integer.
        """
        for name, count in (("first", first), ("last", last)):
            if count is not None:
                _validate_count(name=name, count=count)

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
        self, *, number: NonNegativeInt, harm_categories: Sequence[str] | None = None
    ) -> Sequence[str]:
        """
        Extract and return random prompt values from the dataset.

        Args:
            number (int): The number of random prompt values to return.
            harm_categories (Sequence[str] | None): If provided, only prompts containing at least one of
                these harm categories are included.

        Returns:
            Sequence[str]: A list of prompt values.

        Raises:
            ValueError: If ``number`` is not a non-negative integer.
        """
        _validate_count(name="number", count=number)
        prompts = self.get_values(harm_categories=harm_categories)
        return random.sample(prompts, min(len(prompts), number))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Build a SeedDataset, assigning per-seed ``prompt_group_id`` by alias.

        Default merging now lives in ``_build_seeds`` so direct construction and
        ``from_dict`` produce equivalent results. This method handles the YAML-only
        concerns: rejecting pre-set ``prompt_group_id`` on input seeds and resolving
        ``prompt_group_alias`` into a shared ``prompt_group_id``.

        Args:
            data (dict[str, Any]): Dataset payload with top-level defaults and seed entries.

        Returns:
            Self: Constructed dataset.

        Raises:
            ValueError: If any seed entry includes a pre-set ``prompt_group_id``.
        """
        data = dict(data)

        # Shallow-copy each dict seed so alias resolution doesn't mutate caller-owned dicts;
        # non-dict seeds (e.g. Seed instances) pass through untouched. Nested sub_objectives are
        # flattened first so they can share a prompt_group_alias with top-level prompts.
        seeds_data: list[Any] = [
            dict(seed) if isinstance(seed, dict) else seed
            for seed in cls._flatten_sub_objectives(data.get("seeds", []))
        ]

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

    def get_decomposition_tree(self) -> DecompositionTree:
        """
        Build the decomposition trees of this dataset's objectives.

        Only objectives with a parent, paths, or children are included.

        Returns:
            DecompositionTree: The validated trees.

        Raises:
            ValueError: If a parent is missing from the dataset, the parents form a cycle, or a path
                step is not a direct child of the path's owner.
        """
        return build_objective_decomposition_tree(self.objectives)

    @classmethod
    def _flatten_sub_objectives(cls, raw_seeds: Sequence[Any]) -> list[Any]:
        """
        Expand nested ``sub_objectives`` into flat objectives linked to their parents.

        Each sub-objective has a ``key`` that is unique among its siblings, and the parent's
        ``paths`` name those keys. A path is either a mapping with ``steps`` (and optionally
        ``name`` and ``description``) or a bare list of steps. Seeds without sub-objectives pass
        through unchanged.

        Args:
            raw_seeds (Sequence[Any]): Seed dicts or Seed objects.

        Returns:
            list[Any]: The seeds, with each parent followed by its descendants.
        """
        flattened: list[Any] = []
        for seed in raw_seeds:
            if isinstance(seed, dict) and cls.SUB_OBJECTIVES_KEY in seed:
                flattened.extend(cls._flatten_objective(seed=seed, parent_id=None))
            else:
                if isinstance(seed, dict):
                    cls._require_children_for_symbolic_paths(seed)
                flattened.append(seed)
        return flattened

    @classmethod
    def _flatten_objective(cls, *, seed: dict[str, Any], parent_id: uuid.UUID | None) -> list[dict[str, Any]]:
        """
        Flatten one objective and its nested sub-objectives.

        Returns:
            list[dict[str, Any]]: The objective followed by its descendants, depth-first.

        Raises:
            ValueError: If a decomposed seed is not an objective, or a sub-objective key or path
                step is invalid.
        """
        cls._require_children_for_symbolic_paths(seed)
        seed = dict(seed)
        if seed.setdefault("seed_type", "objective") != "objective":
            raise ValueError(f"Only objectives can have {cls.SUB_OBJECTIVES_KEY}; got seed_type '{seed['seed_type']}'.")
        seed_id = uuid.UUID(str(seed["id"])) if seed.get("id") else uuid.uuid4()
        seed["id"] = seed_id
        if parent_id is not None:
            seed["parent_objective_id"] = parent_id
        children = seed.pop(cls.SUB_OBJECTIVES_KEY, None) or []
        keys: dict[str, uuid.UUID] = {}
        descendants: list[dict[str, Any]] = []
        for child in children:
            key, child = cls._take_sub_objective_key(child=child, taken=keys)
            child_id = uuid.UUID(str(child["id"])) if child.get("id") else uuid.uuid4()
            child["id"] = keys[key] = child_id
            descendants.extend(cls._flatten_objective(seed=child, parent_id=seed_id))
        if children:
            seed["paths"] = [cls._resolve_path_keys(path=path, keys=keys) for path in seed.get("paths") or []]
        return [seed, *descendants]

    @classmethod
    def _require_children_for_symbolic_paths(cls, seed: dict[str, Any]) -> None:
        """
        Distinguish unresolved child keys from flat UUID-based path steps.

        Raises:
            ValueError: If symbolic path steps have no sub-objectives to resolve.
        """
        paths = seed.get("paths")
        if seed.get(cls.SUB_OBJECTIVES_KEY) or not isinstance(paths, (list, tuple)):
            return
        for path in paths:
            steps = path.get("steps") if isinstance(path, dict) else path
            if not isinstance(steps, (list, tuple)):
                continue
            for step in steps:
                if isinstance(step, str):
                    try:
                        uuid.UUID(step)
                    except ValueError as exc:
                        raise ValueError(f"Symbolic paths require non-empty {cls.SUB_OBJECTIVES_KEY}.") from exc

    @classmethod
    def _take_sub_objective_key(cls, *, child: Any, taken: dict[str, uuid.UUID]) -> tuple[str, dict[str, Any]]:
        """
        Separate a sub-objective's key from its seed fields.

        Returns:
            tuple[str, dict[str, Any]]: The key and the remaining seed fields.

        Raises:
            ValueError: If the sub-objective is not a mapping, its key is missing or repeated, or it
                sets its own parent.
        """
        if not isinstance(child, dict):
            raise ValueError(f"Each entry of {cls.SUB_OBJECTIVES_KEY} must be a mapping.")
        child = dict(child)
        key = child.pop(cls.SUB_OBJECTIVE_KEY_FIELD, None)
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Each sub-objective needs a non-empty '{cls.SUB_OBJECTIVE_KEY_FIELD}'.")
        if key in taken:
            raise ValueError(f"Sub-objective key '{key}' is used more than once under the same parent.")
        if "parent_objective_id" in child:
            raise ValueError("A nested sub-objective takes its parent from its position, not parent_objective_id.")
        return key, child

    @staticmethod
    def _resolve_path_keys(*, path: Any, keys: dict[str, uuid.UUID]) -> dict[str, Any]:
        """
        Replace a path's sub-objective keys with their IDs.

        Returns:
            dict[str, Any]: The path fields, with step IDs.

        Raises:
            ValueError: If the path is malformed or names an unknown key.
        """
        path = {"steps": path} if isinstance(path, list) else dict(path) if isinstance(path, dict) else None
        if path is None or not isinstance(path.get("steps"), list):
            raise ValueError("Each path must be a list of sub-objective keys or a mapping with 'steps'.")
        unknown = [step for step in path["steps"] if step not in keys]
        if unknown:
            raise ValueError(f"Path steps name unknown sub-objective keys: {', '.join(map(str, unknown))}.")
        path["steps"] = [keys[step] for step in path["steps"]]
        return path

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
    def group_seed_prompts_by_prompt_group_id(seeds: Sequence[Seed[Any]]) -> Sequence[SeedGroup]:
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

        Raises:
            ValueError: If a seed has an unsupported concrete type.

        """
        # Group seeds by `prompt_group_id`
        grouped_seeds: dict[uuid.UUID, list[SeedUnion]] = defaultdict(list)
        for seed in seeds:
            if not isinstance(seed, (SeedPrompt, SeedObjective, SeedSimulatedConversation, SeedSimulatedToolHistory)):
                raise ValueError(f"Unsupported seed type: {type(seed).__name__}")
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
                attack_group = AttackSeedGroup(seeds=group_seeds)
                seed_groups.append(attack_group)
            except ValueError:
                seed_groups.append(SeedGroup(seeds=group_seeds))

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
