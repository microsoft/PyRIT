# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
from pathlib import Path
from typing import Self

from pyrit.common.utils import verify_and_resolve_path
from pyrit.common.yaml_helper import load_yaml_file


class YamlLoadable(abc.ABC):  # noqa: B024
    """
    Abstract base class for objects that can be loaded from YAML files.
    """

    @classmethod
    def from_yaml_file(cls, file: Path | str) -> Self:
        """
        Create a new object from a YAML file.

        Args:
            file: The input file path.

        Returns:
            A new object of the concrete class.

        Raises:
            FileNotFoundError: If the input YAML file path does not exist.
            ValueError: If the YAML file is invalid.
            TypeError: If the class's ``from_dict`` returns an object of another type.
        """
        file = verify_and_resolve_path(file)
        yaml_data = load_yaml_file(path=file)

        if yaml_data is None:
            raise ValueError(f"YAML file '{file}' is empty.")

        # If this class provides a from_dict factory, use it;
        # otherwise, just instantiate directly with **yaml_data
        from_dict = getattr(cls, "from_dict", None)
        if callable(from_dict):
            result = from_dict(yaml_data)
            if not isinstance(result, cls):
                raise TypeError(f"{cls.__name__}.from_dict returned {type(result).__name__}, not {cls.__name__}")
            return result
        return cls(**yaml_data)
