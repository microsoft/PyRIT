# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Component identity system for PyRIT.

A ComponentIdentifier is an immutable snapshot of a component's behavioral configuration,
serving as both its identity and its storable representation.

Design principles:
    1. The identifier dict is the identity.
    2. Hash is content-addressed from behavioral params only.
    3. Children carry their own hashes.
    4. Adding optional params with None default is backward-compatible (None values excluded).
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional, Union

import pyrit

logger = logging.getLogger(__name__)


def config_hash(config_dict: dict[str, Any]) -> str:
    """
    Compute a deterministic SHA256 hash from a config dictionary.

    This is the single source of truth for identity hashing across the entire
    system. The dict is serialized with sorted keys and compact separators to
    ensure determinism.

    Args:
        config_dict (Dict[str, Any]): A JSON-serializable dictionary.

    Returns:
        str: Hex-encoded SHA256 hash string.

    Raises:
        TypeError: If config_dict contains values that are not JSON-serializable.
    """
    canonical = json.dumps(config_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_hash_dict(
    *,
    class_name: str,
    class_module: str,
    params: dict[str, Any],
    children: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the canonical dictionary used for hash computation.

    Children are represented by their hashes, not their full config.
    A parent's hash changes when a child's behavioral config changes,
    but the parent doesn't need to understand the child's internal structure.

    Args:
        class_name (str): The component's class name.
        class_module (str): The component's module path.
        params (Dict[str, Any]): Behavioral parameters (non-None values only).
        children (Dict[str, Any]): Child name to ComponentIdentifier or list of ComponentIdentifier.

    Returns:
        Dict[str, Any]: The canonical dictionary for hashing.
    """
    hash_dict: dict[str, Any] = {
        ComponentIdentifier.KEY_CLASS_NAME: class_name,
        ComponentIdentifier.KEY_CLASS_MODULE: class_module,
    }

    # Only include non-None params — adding an optional param with None default
    # won't change existing hashes, making the schema backward-compatible.
    hash_dict.update({key: value for key, value in sorted(params.items()) if value is not None})

    # Children contribute their hashes, not their full structure.
    if children:
        children_hashes: dict[str, Any] = {}
        for name, child in sorted(children.items()):
            if isinstance(child, ComponentIdentifier):
                children_hashes[name] = child.hash
            elif isinstance(child, list):
                children_hashes[name] = [c.hash for c in child if isinstance(c, ComponentIdentifier)]
        if children_hashes:
            hash_dict[ComponentIdentifier.KEY_CHILDREN] = children_hashes

    return hash_dict


@dataclass(frozen=True)
class ComponentIdentifier:
    """
    Immutable snapshot of a component's behavioral configuration.

    A single type for all component identity — scorers, targets, converters, and
    any future component types all produce a ComponentIdentifier with their relevant
    params and children.

    The hash is content-addressed: two ComponentIdentifiers with the same class, params,
    and children produce the same hash. This enables deterministic metrics lookup,
    DB deduplication, and registry keying.
    """

    KEY_CLASS_NAME: ClassVar[str] = "class_name"
    KEY_CLASS_MODULE: ClassVar[str] = "class_module"
    KEY_HASH: ClassVar[str] = "hash"
    KEY_EVAL_HASH: ClassVar[str] = "eval_hash"
    KEY_PYRIT_VERSION: ClassVar[str] = "pyrit_version"
    KEY_CHILDREN: ClassVar[str] = "children"
    LEGACY_KEY_TYPE: ClassVar[str] = "__type__"
    LEGACY_KEY_MODULE: ClassVar[str] = "__module__"

    #: Python class name (e.g., "SelfAskScaleScorer").
    class_name: str
    #: Full module path (e.g., "pyrit.score.self_ask_scale_scorer").
    class_module: str
    #: Behavioral parameters that affect output.
    params: dict[str, Any] = field(default_factory=dict)
    #: Named child identifiers for compositional identity (e.g., a scorer's target).
    children: dict[str, Union[ComponentIdentifier, list[ComponentIdentifier]]] = field(default_factory=dict)
    #: Content-addressed SHA256 hash computed from class, params, and children.
    #: When ``None`` (the default), it is computed automatically in ``__post_init__``.
    #: Pass an explicit value to preserve a pre-computed hash (e.g. from DB storage
    #: where params may have been truncated).
    hash: Optional[str] = field(default=None, compare=False)
    #: Version tag for storage. Not included in hash.
    pyrit_version: str = field(default_factory=lambda: pyrit.__version__, compare=False)
    #: Evaluation hash. Computed by EvaluationIdentifier subclasses (e.g. ScorerEvaluationIdentifier)
    #: and attached to the identifier so it is always available via ``to_dict()``.
    #: Survives DB round-trips even when param values are truncated.
    eval_hash: Optional[str] = field(default=None, compare=False)

    def __post_init__(self) -> None:
        """Compute the content-addressed hash at creation time if not already provided."""
        if self.hash is None:
            hash_dict = _build_hash_dict(
                class_name=self.class_name,
                class_module=self.class_module,
                params=self.params,
                children=self.children,
            )
            object.__setattr__(self, "hash", config_hash(hash_dict))

    def with_eval_hash(self, eval_hash: str) -> ComponentIdentifier:
        """
        Return a new frozen ComponentIdentifier with ``eval_hash`` set.

        The original ``hash`` is preserved (important for identifiers
        reconstructed from truncated DB data where recomputation would
        produce a wrong hash).

        Args:
            eval_hash: The evaluation hash to attach.

        Returns:
            A new ComponentIdentifier identical to this one but with
            ``eval_hash`` set to the given value.
        """
        return ComponentIdentifier(
            class_name=self.class_name,
            class_module=self.class_module,
            params=self.params,
            children=self.children,
            hash=self.hash,
            pyrit_version=self.pyrit_version,
            eval_hash=eval_hash,
        )

    @property
    def short_hash(self) -> str:
        """
        Return the first 8 characters of the hash for display and logging.

        Returns:
            str: First 8 hex characters of the SHA256 hash.

        Raises:
            RuntimeError: If the hash was not set by __post_init__.
        """
        if self.hash is None:
            raise RuntimeError("hash should be set by __post_init__")
        return self.hash[:8]

    @property
    def unique_name(self) -> str:
        """
        Globally unique display name: ``class_name::short_hash``.

        Used as the default registration key in instance registries (e.g., "SelfAskScaleScorer::a1b2c3d4").

        Returns:
            str: Unique name combining class name and short hash.
        """
        return f"{self.class_name}::{self.short_hash}"

    @classmethod
    def of(
        cls,
        obj: object,
        *,
        params: Optional[dict[str, Any]] = None,
        children: Optional[dict[str, Union[ComponentIdentifier, list[ComponentIdentifier]]]] = None,
    ) -> ComponentIdentifier:
        """
        Build a ComponentIdentifier from a live object instance.

        This factory method extracts class_name and class_module from the object's
        type automatically, making it the preferred way to create identifiers in
        component implementations. None-valued params and children are filtered out
        to ensure backward-compatible hashing.

        Args:
            obj (object): The live component instance whose type info will be captured.
            params (Optional[Dict[str, Any]]): Behavioral parameters that affect the
                component's output. Only include params that change behavior — exclude
                operational settings like rate limits, retry counts, or logging config.
            children (Optional[Dict[str, Union[ComponentIdentifier, List[ComponentIdentifier]]]]):
                Named child component identifiers. Use for compositional components like
                scorers that wrap other scorers or targets that chain converters.

        Returns:
            ComponentIdentifier: The frozen identity snapshot with computed hash.
        """
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        clean_children = {k: v for k, v in (children or {}).items() if v is not None}

        return cls(
            class_name=obj.__class__.__name__,
            class_module=obj.__class__.__module__,
            params=clean_params,
            children=clean_children,
        )

    def to_dict(self, *, max_value_length: Optional[int] = None) -> dict[str, Any]:
        """
        Serialize to a JSON-compatible dictionary for DB/JSONL storage.

        Produces a flat structure where params are inlined at the top level alongside
        class_name, class_module, hash, and pyrit_version.

        Children are recursively serialized into a nested "children" key.

        Args:
            max_value_length (Optional[int]): If provided, string param values longer
                than this limit are truncated and suffixed with "...". Useful for
                DB storage where column sizes may be limited. The truncation applies
                only to param values, not to structural keys like class_name or hash.
                The limit is propagated to children. Defaults to None (no truncation).

        Returns:
            Dict[str, Any]: JSON-serializable dictionary suitable for database storage
                or JSONL export.
        """
        result: dict[str, Any] = {
            self.KEY_CLASS_NAME: self.class_name,
            self.KEY_CLASS_MODULE: self.class_module,
            self.KEY_HASH: self.hash,
            self.KEY_PYRIT_VERSION: self.pyrit_version,
        }

        if self.eval_hash is not None:
            result[self.KEY_EVAL_HASH] = self.eval_hash

        for key, value in self.params.items():
            result[key] = self._truncate_value(value=value, max_length=max_value_length)

        if self.children:
            serialized_children: dict[str, Any] = {}
            for name, child in self.children.items():
                if isinstance(child, ComponentIdentifier):
                    serialized_children[name] = child.to_dict(max_value_length=max_value_length)
                elif isinstance(child, list):
                    serialized_children[name] = [c.to_dict(max_value_length=max_value_length) for c in child]
            result[self.KEY_CHILDREN] = serialized_children

        return result

    @staticmethod
    def _truncate_value(*, value: Any, max_length: Optional[int]) -> Any:
        """
        Truncate a string value if it exceeds the maximum length.

        Non-string values are returned unchanged.

        Args:
            value (Any): The value to potentially truncate.
            max_length (Optional[int]): Maximum allowed length. None means no truncation.

        Returns:
            Any: The original value, or a truncated string ending with "...".
        """
        if max_length is not None and isinstance(value, str) and len(value) > max_length:
            return value[:max_length] + "..."
        return value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentIdentifier:
        """
        Deserialize from a stored dictionary.

        Reconstructs a ComponentIdentifier from data previously saved via to_dict().
        Handles both the current format (``class_name``/``class_module``) and legacy
        format (``__type__``/``__module__``) for backward compatibility with
        older database records.

        Note:
            This reconstruction is lossy. If ``to_dict()`` was called with a
            ``max_value_length`` limit, param values may have been truncated
            before storage. The original untruncated values cannot be recovered.
            To preserve correct identity, the stored hash (computed from the
            original untruncated data) is kept as-is rather than recomputed
            from the potentially truncated params.

        Args:
            data (Dict[str, Any]): Dictionary from DB/JSONL storage. The original
                dict is not mutated; a copy is made internally.

        Returns:
            ComponentIdentifier: Reconstructed identifier with the stored hash
                preserved (if available) to maintain correct identity despite
                potential param truncation.
        """
        data = dict(data)  # Don't mutate the input

        # Handle legacy key mappings
        class_name = data.pop(cls.KEY_CLASS_NAME, None) or data.pop(cls.LEGACY_KEY_TYPE, None) or "Unknown"
        class_module = data.pop(cls.KEY_CLASS_MODULE, None) or data.pop(cls.LEGACY_KEY_MODULE, None) or "unknown"

        stored_hash = data.pop(cls.KEY_HASH, None)
        stored_eval_hash = data.pop(cls.KEY_EVAL_HASH, None)
        pyrit_version = data.pop(cls.KEY_PYRIT_VERSION, pyrit.__version__)

        # Reconstruct children
        children = cls._reconstruct_children(data.pop(cls.KEY_CHILDREN, None))

        # Everything remaining is a param
        params = data

        return cls(
            class_name=class_name,
            class_module=class_module,
            params=params,
            children=children,
            hash=stored_hash,
            pyrit_version=pyrit_version,
            eval_hash=stored_eval_hash,
        )

    def get_child(self, key: str) -> Optional[ComponentIdentifier]:
        """
        Get a single child by key.

        Args:
            key (str): The child key.

        Returns:
            Optional[ComponentIdentifier]: The child, or None if not found.

        Raises:
            ValueError: If the child is a list (use get_child_list instead).
        """
        child = self.children.get(key)
        if child is None:
            return None
        if isinstance(child, list):
            raise ValueError(f"Child '{key}' is a list of {len(child)} components. Use get_child_list() instead.")
        return child

    def get_child_list(self, key: str) -> list[ComponentIdentifier]:
        """
        Get a list of children by key.

        Args:
            key (str): The child key.

        Returns:
            List[ComponentIdentifier]: The children. Returns empty list if
                not found, wraps single child in a list.
        """
        child = self.children.get(key)
        if child is None:
            return []
        if isinstance(child, ComponentIdentifier):
            return [child]
        return child

    def _collect_child_eval_hashes(self) -> set[str]:
        """
        Recursively collect all eval_hash values from child identifiers.

        Walks the entire children tree and returns a set of all non-None
        eval_hash values found on any descendant ComponentIdentifier.

        Returns:
            set[str]: All eval_hash values found in the children tree.
        """
        hashes: set[str] = set()
        for child_val in self.children.values():
            children_list = child_val if isinstance(child_val, list) else [child_val]
            for child in children_list:
                if child.eval_hash:  # type: ignore[ty:unresolved-attribute]
                    hashes.add(child.eval_hash)  # type: ignore[ty:unresolved-attribute]
                hashes.update(child._collect_child_eval_hashes())  # type: ignore[ty:unresolved-attribute]
        return hashes

    @classmethod
    def _reconstruct_children(
        cls, children_dict: Optional[dict[str, Any]]
    ) -> dict[str, Union[ComponentIdentifier, list[ComponentIdentifier]]]:
        """
        Reconstruct child identifiers from raw dictionary data.

        Args:
            children_dict (Optional[Dict[str, Any]]): Raw children dict from storage,
                or None if no children were stored.

        Returns:
            Dict mapping child names to reconstructed ComponentIdentifier instances or lists thereof.
        """
        children: dict[str, Union[ComponentIdentifier, list[ComponentIdentifier]]] = {}
        if not children_dict or not isinstance(children_dict, dict):
            return children

        for name, child_data in children_dict.items():
            if isinstance(child_data, dict):
                children[name] = cls.from_dict(child_data)
            elif isinstance(child_data, list):
                children[name] = [cls.from_dict(c) for c in child_data if isinstance(c, dict)]

        return children

    def __str__(self) -> str:
        """
        Return a human-readable string representation.

        Format: ``ClassName::abcd1234`` (class name followed by short hash).

        Returns:
            str: Human-readable identifier string.
        """
        return f"{self.class_name}::{self.short_hash}"

    def __repr__(self) -> str:
        """
        Return a detailed representation for debugging.

        Includes class name, all params, children references, and the short hash.

        Returns:
            str: Detailed debug string showing all identifier components.
        """
        params_str = ", ".join(f"{k}={v!r}" for k, v in sorted(self.params.items()))
        children_str = ", ".join(f"{k}={v}" for k, v in sorted(self.children.items()))
        parts = [f"class={self.class_name}"]
        if params_str:
            parts.append(f"params=({params_str})")
        if children_str:
            parts.append(f"children=({children_str})")
        parts.append(f"hash={self.short_hash}")
        return f"ComponentIdentifier({', '.join(parts)})"


class Identifiable(ABC):
    """
    Abstract base class for components that provide a behavioral identity.

    Components implement ``_build_identifier()`` to return a frozen ComponentIdentifier
    snapshot. The identifier is built lazily on first access and cached for the
    component's lifetime.
    """

    _identifier: Optional[ComponentIdentifier] = None

    @abstractmethod
    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the behavioral identity for this component.

        Only include params that affect the component's behavior/output.
        Exclude operational params (rate limits, retry config, logging settings).

        Returns:
            ComponentIdentifier: The frozen identity snapshot.
        """
        ...

    def get_identifier(self) -> ComponentIdentifier:
        """
        Get the component's identifier, building it lazily on first access.

        The identifier is computed once via _build_identifier() and then cached for
        subsequent calls. This ensures consistent identity throughout the
        component's lifetime while deferring computation until actually needed.

        Note:
            Not thread-safe. If thread safety is required, subclasses should
            implement appropriate synchronization.

        Returns:
            ComponentIdentifier: The frozen identity snapshot representing
                this component's behavioral configuration.
        """
        if self._identifier is None:
            self._identifier = self._build_identifier()
        return self._identifier
