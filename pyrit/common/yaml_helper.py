# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Fast, safe YAML loading.

PyRIT parses thousands of YAML documents (seed datasets, scorer and converter
configurations) during normal use. ``yaml.safe_load`` uses PyYAML's pure-Python
parser; the libyaml-backed ``CSafeLoader`` parses the same documents several
times faster. ``CSafeLoader`` is only present when PyYAML was built against
libyaml, so fall back to the pure-Python ``SafeLoader`` when it is missing.

Both loaders use the YAML 1.1 safe schema. Explicit duplicate mapping keys are
rejected before merge expansion; merge sources may overlap, and explicit keys
may override merged values using PyYAML's existing precedence rules.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import IO, TYPE_CHECKING, Any

import yaml
from yaml.constructor import ConstructorError

if TYPE_CHECKING:
    from yaml import SafeLoader
    from yaml.nodes import MappingNode, Node
else:
    try:
        from yaml import CSafeLoader as SafeLoader
    except ImportError:  # pragma: no cover - depends on whether libyaml is available
        from yaml import SafeLoader


class _UniqueKeySafeLoader(SafeLoader):
    """Reject explicit duplicate keys while preserving safe YAML merges."""

    def __init__(self, stream: str | bytes | IO[str] | IO[bytes]) -> None:
        """Initialize the loader for YAML text, bytes, or a file-like stream."""
        super().__init__(stream)
        self._checked_mappings: set[MappingNode] = set()

    def flatten_mapping(self, node: MappingNode) -> None:
        """
        Validate original keys once, then apply standard merge precedence.

        Args:
            node (MappingNode): Mapping to validate and expand.

        Raises:
            ConstructorError: If an explicit key is duplicated or unhashable.
        """
        if node in self._checked_mappings:
            return
        seen: dict[Any, Node] = {}
        merge_key = object()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                key = merge_key
            else:
                if key_node.tag == "tag:yaml.org,2002:value":
                    key_node.tag = "tag:yaml.org,2002:str"
                key = self.construct_object(key_node)
            if not isinstance(key, Hashable):
                raise ConstructorError(
                    "while constructing a mapping", node.start_mark, "found unhashable key", key_node.start_mark
                )
            if key in seen:
                display_key = "<<" if key is merge_key else key
                raise ConstructorError(
                    f"while constructing a mapping (first occurrence of key {display_key!r})",
                    seen[key].start_mark,
                    f"found duplicate key {display_key!r}",
                    key_node.start_mark,
                )
            seen[key] = key_node
        self._checked_mappings.add(node)
        super().flatten_mapping(node)


def safe_load_yaml(source: str | bytes | IO[str] | IO[bytes]) -> Any:
    """
    Parse a YAML document using the fastest available safe loader.

    Args:
        source: YAML text, bytes, or an open file-like object, matching what
            ``yaml.safe_load`` accepts.

    Returns:
        Any: The parsed YAML content, or ``None`` if the document is empty.

    Raises:
        yaml.YAMLError: If the document is not valid YAML or contains explicit
            duplicate mapping keys. Duplicate errors include the key and source marks.
    """
    return yaml.load(source, Loader=_UniqueKeySafeLoader)
