# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
YAML loaders for seed types.

These functions live separately from the seed classes themselves because the
*trust claim* that a value came from a vetted local YAML file (vs. an untrusted
remote dataset) is a property of the loader, not of the data class. A ``Seed``
instance can't know its own provenance; the loader can, so the
``is_jinja_template=True`` marker is set exactly once at this boundary.

The ``from_yaml_file`` and ``from_yaml_with_required_parameters`` classmethods
on the seed classes are thin shims that delegate here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

import yaml

from pyrit.common.utils import verify_and_resolve_path
from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_dataset import SeedDataset
from pyrit.models.seeds.seed_prompt import SeedPrompt

if TYPE_CHECKING:
    from pathlib import Path

T = TypeVar("T", bound=Seed)

# Seed model fields that callers may write as a bare string in YAML
# (e.g. ``authors: Jane Doe``) but the model declares as ``list[str]``.
# The loader wraps such scalars before constructing the model so the model
# itself can stay strict and YAML's "scalar-or-sequence" idiom doesn't leak
# into the data class.
_SCALAR_OR_LIST_FIELDS: tuple[str, ...] = ("harm_categories", "authors", "groups", "parameters")


def _canonicalize_scalar_lists(data: dict[str, Any]) -> dict[str, Any]:
    """
    Wrap bare-string values into single-element lists for known list-typed seed fields.

    Mutates ``data`` in place and recurses into nested ``seeds`` entries so
    dataset/group YAML files (which carry both top-level defaults and a list of seed
    dicts) are normalized in one pass.

    Args:
        data: A YAML-decoded mapping representing a seed, group, or dataset.

    Returns:
        The same mapping, with scalar values on known list fields wrapped into lists.
    """
    for key in _SCALAR_OR_LIST_FIELDS:
        if isinstance(data.get(key), str):
            data[key] = [data[key]]
    seeds = data.get("seeds")
    if isinstance(seeds, list):
        for seed in seeds:
            if isinstance(seed, dict):
                _canonicalize_scalar_lists(seed)
    return data


def _read_yaml(file: str | Path) -> dict[str, Any]:
    """
    Resolve, read, and parse a YAML file as a mapping.

    Args:
        file: Path to a YAML file.

    Returns:
        The parsed top-level mapping.

    Raises:
        FileNotFoundError: If the path does not resolve to an existing file.
        ValueError: If the YAML is malformed or empty.
    """
    file = verify_and_resolve_path(file)
    try:
        data = yaml.safe_load(file.read_text("utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML file '{file}': {exc}") from exc

    if data is None:
        raise ValueError(f"YAML file '{file}' is empty.")
    if not isinstance(data, dict):
        raise ValueError(f"YAML file '{file}' must contain a mapping at the top level.")
    return data


def load_seed_from_yaml(file: str | Path, *, cls: type[T]) -> T:
    """
    Load a single seed of type ``cls`` from a YAML file.

    The seed is marked ``is_jinja_template=True`` because the file is treated
    as a trusted, vetted local template at this boundary. Bare-string values
    for known list-typed fields (``authors``, ``harm_categories``, ``groups``,
    ``parameters``) are wrapped into single-element lists so the model itself
    can stay strict about its shape.

    Args:
        file: Path to the YAML file containing the seed definition.
        cls: Seed subclass to instantiate (e.g. ``SeedPrompt``, ``SeedObjective``).

    Returns:
        An instance of ``cls`` populated from the YAML file.

    Raises:
        FileNotFoundError: If the path does not resolve to an existing file.
        ValueError: If the YAML is malformed, empty, or fails validation for ``cls``.
    """
    data = _canonicalize_scalar_lists(_read_yaml(file))
    data["is_jinja_template"] = True
    return cls(**data)


def load_seed_dataset_from_yaml(file: str | Path) -> SeedDataset:
    """
    Load a ``SeedDataset`` from a YAML file.

    Nested seeds inherit the ``is_jinja_template=True`` trust marker set at this
    boundary; per-seed overrides in the YAML are intentionally ignored.

    Args:
        file: Path to the YAML file containing the dataset definition.

    Returns:
        A ``SeedDataset`` populated from the YAML file.

    Raises:
        FileNotFoundError: If the path does not resolve to an existing file.
        ValueError: If the YAML is malformed, empty, or fails dataset validation.
    """
    data = _canonicalize_scalar_lists(_read_yaml(file))
    data["is_jinja_template"] = True
    return SeedDataset.from_dict(data)


def load_seed_prompt_from_yaml_with_required_parameters(
    template_path: str | Path,
    required_parameters: list[str],
    *,
    error_message: str | None = None,
) -> SeedPrompt:
    """
    Load a ``SeedPrompt`` and assert that its ``parameters`` field declares each required name.

    Args:
        template_path: Path to the YAML file containing the prompt template.
        required_parameters: Parameter names that must appear in ``SeedPrompt.parameters``.
        error_message: Optional custom message used in the raised ``ValueError``.

    Returns:
        The loaded ``SeedPrompt``.

    Raises:
        ValueError: If the loaded prompt is missing any required parameter.
    """
    sp = load_seed_from_yaml(template_path, cls=SeedPrompt)
    if sp.parameters is None or not all(p in sp.parameters for p in required_parameters):
        if error_message is None:
            error_message = f"Template must have these parameters: {', '.join(required_parameters)}"
        raise ValueError(f"{error_message}: '{sp}'")
    return sp
