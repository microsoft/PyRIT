# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
from pathlib import Path
from typing import TypeVar, Union

import yaml

from pyrit.common.utils import verify_and_resolve_path

T = TypeVar("T", bound="YamlLoadable")


class YamlLoadable(abc.ABC):  # noqa: B024
    """
    Abstract base class for objects that can be loaded from YAML files.
    """

    @classmethod
    def from_yaml_file(cls: type[T], file: Union[Path | str]) -> T:
        """
        Create a new object from a YAML file.

        Args:
            file: The input file path.

        Returns:
            A new object of type T.

        Raises:
            FileNotFoundError: If the input YAML file path does not exist.
            ValueError: If the YAML file is invalid.
        """
        file = verify_and_resolve_path(file)
        try:
            yaml_data = yaml.safe_load(file.read_text("utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML file '{file}': {exc}") from exc

        if yaml_data is None:
            raise ValueError(f"YAML file '{file}' is empty.")

        # If this class provides a from_dict factory, use it;
        # otherwise, just instantiate directly with **yaml_data
        if hasattr(cls, "from_dict") and callable(getattr(cls, "from_dict")):  # noqa: B009
            return cls.from_dict(yaml_data)  # type: ignore[attr-defined, no-any-return]
        return cls(**yaml_data)
