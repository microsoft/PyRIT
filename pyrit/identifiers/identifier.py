# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Literal, Type, TypeVar

from pyrit.common.deprecation import print_deprecation_message
from pyrit.identifiers.class_name_utils import class_name_to_snake_case

IdentifierType = Literal["class", "instance"]

# Metadata keys for field configuration
EXCLUDE_FROM_STORAGE = "exclude_from_storage"
MAX_STORAGE_LENGTH = "max_storage_length"

T = TypeVar("T", bound="Identifier")


@dataclass(frozen=True)
class Identifier:
    """
    Base dataclass for identifying PyRIT components.

    This frozen dataclass provides a stable identifier for registry items,
    targets, scorers, attacks, converters, and other components. The hash is computed at creation
    time from the core fields and remains constant.

    This class serves as:
    1. Base for registry metadata (replacing RegistryItemMetadata)
    2. Future replacement for get_identifier() dict patterns

    All component-specific identifier types should extend this with additional fields.
    """

    class_name: str  # The actual class name, equivalent to __type__ (e.g., "SelfAskRefusalScorer")
    class_module: str  # The module path, equivalent to __module__ (e.g., "pyrit.score.self_ask_refusal_scorer")

    # Fields excluded from storage
    class_description: str = field(metadata={EXCLUDE_FROM_STORAGE: True})
    identifier_type: IdentifierType = field(metadata={EXCLUDE_FROM_STORAGE: True})

    # Auto-computed fields
    snake_class_name: str = field(init=False, metadata={EXCLUDE_FROM_STORAGE: True})
    hash: str | None = field(default=None, compare=False, kw_only=True)
    unique_name: str = field(init=False)  # Unique identifier: {full_snake_case}::{hash[:8]}

    def __post_init__(self) -> None:
        """Compute derived fields: snake_class_name, hash, and unique_name."""
        # Use object.__setattr__ since this is a frozen dataclass
        # 1. Compute snake_class_name
        object.__setattr__(self, "snake_class_name", class_name_to_snake_case(self.class_name))
        # 2. Compute hash only if not already provided (e.g., from from_dict)
        if self.hash is None:
            object.__setattr__(self, "hash", self._compute_hash())
        # 3. Compute unique_name: full snake_case :: hash prefix
        full_snake = class_name_to_snake_case(self.class_name)
        object.__setattr__(self, "unique_name", f"{full_snake}::{self.hash[:8]}")

    def _compute_hash(self) -> str:
        """
        Compute a stable SHA256 hash from storable identifier fields.

        Fields marked with metadata={"exclude_from_storage": True}, 'hash', and 'name'
        are excluded from the hash computation.

        Returns:
            A hex string of the SHA256 hash.
        """
        hashable_dict: dict[str, Any] = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in ("hash", "unique_name") and not f.metadata.get(EXCLUDE_FROM_STORAGE, False)
        }
        config_json = json.dumps(hashable_dict, sort_keys=True, separators=(",", ":"), default=_dataclass_encoder)
        return hashlib.sha256(config_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """
        Return only fields suitable for DB storage.

        Fields with max_storage_length metadata are truncated to show the first
        N characters followed by the field's hash, formatted as:
        "<first N chars>... [sha256:<hash[:16]>]"

        Nested Identifier objects are recursively serialized to dicts.

        Returns:
            dict[str, Any]: A dictionary containing the storable fields.
        """
        result: dict[str, Any] = {}
        for f in fields(self):
            if f.metadata.get(EXCLUDE_FROM_STORAGE, False):
                continue
            value = getattr(self, f.name)
            max_len = f.metadata.get(MAX_STORAGE_LENGTH)
            if max_len is not None and isinstance(value, str) and len(value) > max_len:
                truncated = value[:max_len]
                field_hash = hashlib.sha256(value.encode()).hexdigest()[:16]
                value = f"{truncated}... [sha256:{field_hash}]"
            # Recursively serialize nested Identifier objects
            elif isinstance(value, Identifier):
                value = value.to_dict()
            elif isinstance(value, list) and value and isinstance(value[0], Identifier):
                value = [item.to_dict() for item in value]
            # Exclude None and empty values
            if value is None or value == "" or value == [] or value == {}:
                continue
            result[f.name] = value
        return result

    @classmethod
    def from_dict(cls: Type[T], data: dict[str, Any]) -> T:
        """
        Create an Identifier from a dictionary (e.g., retrieved from database).

        This handles:
        - Legacy '__type__' key mapping to 'class_name'
        - Legacy 'type' key mapping to 'class_name' (with deprecation warning)
        - Legacy '__module__' key mapping to 'class_module'
        - Ignoring unknown fields not present in the dataclass

        Note:
            For fields with max_storage_length, stored values may be truncated
            strings like "<first N chars>... [sha256:<hash>]". If a 'hash' key is
            present in the input dict, it will be preserved rather than recomputed,
            ensuring identity matching works correctly.

        Args:
            data: The dictionary representation.

        Returns:
            A new Identifier instance.
        """
        # Create a mutable copy
        data = dict(data)

        # Handle legacy key mappings for class_name
        if "class_name" not in data:
            if "__type__" in data:
                print_deprecation_message(
                    old_item="'__type__' key in Identifier dict",
                    new_item="'class_name' key",
                    removed_in="0.13.0",
                )
                data["class_name"] = data.pop("__type__")
            elif "type" in data:
                print_deprecation_message(
                    old_item="'type' key in Identifier dict",
                    new_item="'class_name' key",
                    removed_in="0.13.0",
                )
                data["class_name"] = data.pop("type")
            else:
                # Default for truly legacy data without any class identifier
                data["class_name"] = "Unknown"

        # Handle legacy key mapping for class_module
        if "class_module" not in data:
            if "__module__" in data:
                print_deprecation_message(
                    old_item="'__module__' key in Identifier dict",
                    new_item="'class_module' key",
                    removed_in="0.13.0",
                )
                data["class_module"] = data.pop("__module__")
            else:
                # Default for truly legacy data without module info
                data["class_module"] = "unknown"

        # Provide defaults for fields excluded from storage (not in stored dicts)
        if "class_description" not in data:
            data["class_description"] = ""
        if "identifier_type" not in data:
            data["identifier_type"] = "instance"

        # Get the set of valid field names for this class
        valid_fields = {f.name for f in fields(cls) if f.init}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        return cls(**filtered_data)

    @classmethod
    def normalize(cls: Type[T], value: T | dict[str, Any]) -> T:
        """
        Normalize a value to an Identifier instance.

        This method handles conversion from legacy dict format to Identifier,
        emitting a deprecation warning when a dict is passed. Existing Identifier
        instances are returned as-is.

        Args:
            value: An Identifier instance or a dict (legacy format).

        Returns:
            The normalized Identifier instance.

        Raises:
            TypeError: If value is not an Identifier or dict.
        """
        if isinstance(value, cls):
            return value

        if isinstance(value, dict):
            print_deprecation_message(
                old_item=f"dict for {cls.__name__}",
                new_item=cls.__name__,
                removed_in="0.14.0",
            )
            return cls.from_dict(value)

        raise TypeError(f"Expected {cls.__name__} or dict, got {type(value).__name__}")


def _dataclass_encoder(obj: Any) -> Any:
    """
    JSON encoder that handles dataclasses by converting them to dicts.

    Args:
        obj: The object to encode.

    Returns:
        Any: The dictionary representation of the dataclass.

    Raises:
        TypeError: If the object is not a dataclass instance.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
