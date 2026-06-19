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
from typing import Any, ClassVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, SerializationInfo, model_serializer, model_validator
from typing_extensions import Self, TypeAliasType

import pyrit
from pyrit.common.deprecation import print_deprecation_message

#: The set of value types allowed inside ``ComponentIdentifier.params``. Params
#: must be JSON-serializable scalars (``str`` / ``int`` / ``float`` / ``bool`` /
#: ``None``) or arbitrarily nested ``list`` / ``dict`` containers of those. This
#: mirrors exactly what ``config_hash``'s ``json.dumps`` can serialize, so an
#: identifier that validates is guaranteed to hash. Composite identity belongs in
#: ``children`` (typed as ``ComponentIdentifier``), never in ``params``.
JSONValue = TypeAliasType(
    "JSONValue",
    "str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]",
)

#: Param names that collide with reserved top-level keys in the flat storage
#: shape. Forbidden inside ``ComponentIdentifier.params`` so storage / REST
#: round-trips stay lossless.
RESERVED_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "class_name",
        "class_module",
        "hash",
        "pyrit_version",
        "eval_hash",
        "children",
        "params",
        "__type__",
        "__module__",
    }
)

logger = logging.getLogger(__name__)


def config_hash(config_dict: dict[str, Any]) -> str:
    """
    Compute a deterministic SHA256 hash from a config dictionary.

    This is the single source of truth for identity hashing across the entire
    system. The dict is serialized with sorted keys and compact separators to
    ensure determinism.

    Args:
        config_dict (dict[str, Any]): A JSON-serializable dictionary.

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
    params: dict[str, JSONValue],
    children: dict[str, ComponentIdentifier | list[ComponentIdentifier]],
) -> dict[str, Any]:
    """
    Build the canonical dictionary used for hash computation.

    Children are represented by their hashes, not their full config.
    A parent's hash changes when a child's behavioral config changes,
    but the parent doesn't need to understand the child's internal structure.

    Args:
        class_name (str): The component's class name.
        class_module (str): The component's module path.
        params (dict[str, JSONValue]): Behavioral parameters (non-None values only).
        children (dict[str, ComponentIdentifier | list[ComponentIdentifier]]): Child name to
            ComponentIdentifier or list of ComponentIdentifier.

    Returns:
        dict[str, Any]: The canonical dictionary for hashing.
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


def _dump_child_identifiers_to_dict(value: Any) -> Any:
    """
    Replace ``ComponentIdentifier`` instances in a child value with their flat dict form.

    A promoted child field is typed as a specific ``ComponentIdentifier``
    subclass (e.g. ``TargetIdentifier``). Build sites and DB loads may supply a
    base ``ComponentIdentifier`` (or a different subclass) for that slot, which
    Pydantic's strict model validation would reject. Dumping such instances to
    their flat ``model_dump()`` dict lets validation re-parse them into the
    declared subclass; the stored ``hash`` rides along, so identity is preserved.

    Args:
        value (Any): The raw child value (an identifier instance, a dict, a list
            of either, or ``None``).

    Returns:
        Any: The value with any ``ComponentIdentifier`` instances replaced by
        their flat dict form.
    """
    if isinstance(value, ComponentIdentifier):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump_child_identifiers_to_dict(item) for item in value]
    return value


class ComponentIdentifier(BaseModel):
    """
    Immutable snapshot of a component's behavioral configuration.

    A single type for all component identity — scorers, targets, converters, and
    any future component types all produce a ComponentIdentifier with their relevant
    params and children.

    The hash is content-addressed: two ComponentIdentifiers with the same class,
    params, and children produce the same hash. This enables deterministic metrics
    lookup, DB deduplication, and registry keying.

    Typed projections: subclasses (``TargetIdentifier``, ``ConverterIdentifier``, …)
    may promote well-known params and children to ordinary typed fields. Promotion is
    automatic and keyed off the field's annotation: a scalar field maps to a ``params``
    entry; a field annotated as a ``ComponentIdentifier`` subclass (or a ``list``
    thereof) maps to a ``children`` slot of the same name. The promoted value is
    mirrored back into ``params`` / ``children`` before hashing, so a typed subclass
    serializes and hashes identically to a plain ``ComponentIdentifier`` built with the
    same params/children. Non-promoted members simply stay in ``params`` / ``children``.

    Serialization: ``model_dump()`` returns a flat dict where reserved keys
    (``class_name``, ``class_module``, ``hash``, ``pyrit_version``, ``eval_hash``,
    ``children``) sit at the top level alongside the inlined param values. This shape is
    also the storage / REST format. Pass ``context={"max_value_length": N}`` to truncate
    long string param values. ``model_validate()`` accepts the same flat shape (plus a
    structured form with an explicit ``params`` dict).

    Mutability: the model is frozen, but ``params`` and ``children`` are dicts whose
    contents are not deep-frozen — mutating them after construction creates an
    identifier whose stored ``hash`` no longer matches its content. Treat every
    identifier as a fully immutable value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

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
    #: Behavioral parameters that affect output. Values must be JSON-serializable
    #: scalars or nested ``list`` / ``dict`` containers of them (see ``JSONValue``);
    #: composite identity belongs in ``children`` instead.
    params: dict[str, JSONValue] = Field(default_factory=dict)
    #: Named child identifiers for compositional identity (e.g., a scorer's target).
    children: dict[str, ComponentIdentifier | list[ComponentIdentifier]] = Field(default_factory=dict)
    #: Content-addressed SHA256 hash. Computed automatically when ``None``;
    #: pass an explicit value to preserve a hash from DB storage where params
    #: may have been truncated.
    hash: str | None = None
    #: Version tag for storage. Not included in the content hash.
    pyrit_version: str = Field(default=pyrit.__version__)
    #: Evaluation hash. Computed by EvaluationIdentifier subclasses and attached
    #: to the identifier so it survives DB round-trips with truncated params.
    eval_hash: str | None = None

    # ------------------------------------------------------------------
    # Promotion (typed projection — derived from the subclass's own fields)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_child_field(annotation: Any) -> bool:
        """
        Return whether a field annotation denotes a child identifier.

        Args:
            annotation (Any): The resolved field annotation (from
                ``model_fields[name].annotation``).

        Returns:
            bool: ``True`` if the annotation is a ``ComponentIdentifier`` subclass
            or a ``list`` thereof (optionally wrapped in ``| None``); ``False`` for
            scalar (param) fields.
        """
        if get_origin(annotation) is list:
            args = get_args(annotation)
            inner = args[0] if args else None
            return isinstance(inner, type) and issubclass(inner, ComponentIdentifier)

        candidates: tuple[Any, ...] = get_args(annotation) or (annotation,)
        return any(isinstance(c, type) and issubclass(c, ComponentIdentifier) for c in candidates)

    @classmethod
    def _promoted_fields(cls) -> tuple[str, ...]:
        """
        Return the subclass's own fields (everything beyond the base structural fields).

        Returns:
            tuple[str, ...]: Field names declared by ``cls`` but not by the base
            ``ComponentIdentifier``, in field-definition order.
        """
        base_fields = set(ComponentIdentifier.model_fields)
        return tuple(name for name in cls.model_fields if name not in base_fields)

    @classmethod
    def _promoted_param_fields(cls) -> tuple[str, ...]:
        """
        Return the subclass's own scalar fields, which map to ``params`` entries.

        Returns:
            tuple[str, ...]: Promoted param field names, in field-definition order.
        """
        return tuple(n for n in cls._promoted_fields() if not cls._is_child_field(cls.model_fields[n].annotation))

    @classmethod
    def _promoted_child_fields(cls) -> tuple[str, ...]:
        """
        Return the subclass's own identifier-typed fields, which map to ``children`` slots.

        Returns:
            tuple[str, ...]: Promoted child field names, in field-definition order.
        """
        return tuple(n for n in cls._promoted_fields() if cls._is_child_field(cls.model_fields[n].annotation))

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> Any:
        """
        Normalize flat storage form into structured form before field validation.

        Accepts:

        1. The structured form (``params`` / ``children`` as nested dicts).
        2. The flat storage form (params inlined at the top level alongside
           reserved keys).
        3. Legacy keys ``__type__`` / ``__module__`` (mapped to canonical
           keys when the canonical key is absent).

        Rejects:

        * Mixed shape — both an explicit ``params`` key **and** stray
          top-level keys.
        * Param names that collide with reserved structural keys.

        Idempotent: feeding the validator already-normalized input is a no-op.

        Args:
            data: Input dict in either structured or flat form.

        Returns:
            The normalized dict ready for field validation.

        Raises:
            ValueError: If both ``params`` and stray top-level keys are
                present, or if any param name collides with a reserved key.
        """
        if not isinstance(data, dict):
            return data

        data = dict(data)

        # Map legacy keys onto canonical keys when canonical is absent.
        if cls.KEY_CLASS_NAME not in data and cls.LEGACY_KEY_TYPE in data:
            data[cls.KEY_CLASS_NAME] = data.pop(cls.LEGACY_KEY_TYPE)
        else:
            data.pop(cls.LEGACY_KEY_TYPE, None)
        if cls.KEY_CLASS_MODULE not in data and cls.LEGACY_KEY_MODULE in data:
            data[cls.KEY_CLASS_MODULE] = data.pop(cls.LEGACY_KEY_MODULE)
        else:
            data.pop(cls.LEGACY_KEY_MODULE, None)

        # Match the previous from_dict behavior: tolerate missing class info.
        data.setdefault(cls.KEY_CLASS_NAME, "Unknown")
        data.setdefault(cls.KEY_CLASS_MODULE, "unknown")

        promoted_fields = cls._promoted_fields()
        reserved_top = {
            cls.KEY_CLASS_NAME,
            cls.KEY_CLASS_MODULE,
            cls.KEY_HASH,
            cls.KEY_PYRIT_VERSION,
            cls.KEY_EVAL_HASH,
            cls.KEY_CHILDREN,
            *promoted_fields,
        }

        if "params" in data:
            stray = [k for k in data if k not in reserved_top and k != "params"]
            if stray:
                raise ValueError(
                    "ComponentIdentifier received both 'params' and stray "
                    f"top-level keys {sorted(stray)}; use either the flat "
                    "storage shape or the structured shape, not both."
                )
        else:
            extras = {k: v for k, v in data.items() if k not in reserved_top}
            for k in extras:
                del data[k]
            data["params"] = extras

        params_dict = data.get("params")
        if isinstance(params_dict, dict):
            collisions = set(params_dict) & RESERVED_PARAM_NAMES
            if collisions:
                raise ValueError(f"ComponentIdentifier params must not use reserved names: {sorted(collisions)}")

        # Promotion: lift any promoted value that arrived inside the flat
        # ``params`` / ``children`` buckets (e.g. the storage shape) up to its
        # matching top-level field so Pydantic validates it into the typed field.
        # Build-site construction already passes promoted values top-level, so
        # those are left untouched here.
        if promoted_fields:
            params_bucket = params_dict if isinstance(params_dict, dict) else {}
            children_value = data.get(cls.KEY_CHILDREN)
            children_bucket = children_value if isinstance(children_value, dict) else {}
            for name in promoted_fields:
                if name in data:
                    continue
                if name in params_bucket:
                    data[name] = params_bucket[name]
                elif name in children_bucket:
                    data[name] = children_bucket[name]

            # Promoted child values may arrive as ComponentIdentifier instances
            # (possibly a base ComponentIdentifier or a different subclass than
            # the typed field declares). Dump them to their flat dict form so
            # Pydantic re-parses them into the declared identifier subclass.
            # Round-tripping through model_dump preserves the stored hash.
            for name in cls._promoted_child_fields():
                if name in data:
                    data[name] = _dump_child_identifiers_to_dict(data[name])

        return data

    @model_validator(mode="after")
    def _promote_and_compute_hash(self) -> ComponentIdentifier:
        """
        Mirror promoted typed fields into ``params`` / ``children`` and hash.

        Promoted scalar fields are written into ``params`` and promoted
        identifier fields into ``children`` (``None`` / empty list dropped), so a
        typed subclass serializes and hashes identically to a plain
        ``ComponentIdentifier`` with the same values. The content-addressed hash
        is then computed if it was not provided — a pre-set hash (e.g. one
        reconstructed from a truncated DB row) is preserved.

        Returns:
            ``self`` (mutated in-place).
        """
        for name in self._promoted_param_fields():
            value = getattr(self, name)
            if value is not None:
                self.params[name] = value
        for name in self._promoted_child_fields():
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, list):
                # Store non-empty lists always; store an empty list only when it
                # was set explicitly (preserves hashes for builders that include
                # an empty child slot, while a defaulted empty list stays absent).
                if value or name in self.model_fields_set:
                    self.children[name] = value
            else:
                self.children[name] = value

        if self.hash is None:
            hash_dict = _build_hash_dict(
                class_name=self.class_name,
                class_module=self.class_module,
                params=self.params,
                children=self.children,
            )
            object.__setattr__(self, "hash", config_hash(hash_dict))
        return self

    # ------------------------------------------------------------------
    # Serializer
    # ------------------------------------------------------------------

    @model_serializer(mode="plain")
    def _serialize_flat(self, info: SerializationInfo) -> dict[str, Any]:
        """
        Emit the flat storage shape.

        Honors ``context={"max_value_length": N}`` to truncate long string
        param values, propagating both context and mode (``"python"`` vs
        ``"json"``) into recursive child dumps.

        Returns:
            The flat dict representation of this identifier.
        """
        context = info.context if isinstance(info.context, dict) else {}
        max_len = context.get("max_value_length")
        mode = info.mode

        result: dict[str, Any] = {
            self.KEY_CLASS_NAME: self.class_name,
            self.KEY_CLASS_MODULE: self.class_module,
            self.KEY_HASH: self.hash,
            self.KEY_PYRIT_VERSION: self.pyrit_version,
        }
        if self.eval_hash is not None:
            result[self.KEY_EVAL_HASH] = self.eval_hash

        for key, value in self.params.items():
            result[key] = self._truncate_value(value=value, max_length=max_len)

        if self.children:
            serialized_children: dict[str, Any] = {}
            for name, child in self.children.items():
                if isinstance(child, ComponentIdentifier):
                    serialized_children[name] = child.model_dump(mode=mode, context=context)
                elif isinstance(child, list):
                    serialized_children[name] = [c.model_dump(mode=mode, context=context) for c in child]
            result[self.KEY_CHILDREN] = serialized_children

        return result

    # ------------------------------------------------------------------
    # Equality / hashing — keyed off the content hash
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """
        Equality keyed off the content hash.

        Returns:
            ``True`` if ``other`` is a ``ComponentIdentifier`` with the same
            hash, otherwise ``NotImplemented`` (or ``False``).
        """
        if not isinstance(other, ComponentIdentifier):
            return NotImplemented
        return self.hash == other.hash

    def __hash__(self) -> int:
        """
        Hash keyed off the content hash (already content-addressed).

        Returns:
            The Python hash of the content-addressed hash string.
        """
        return hash(self.hash)

    # ------------------------------------------------------------------
    # Derived copies
    # ------------------------------------------------------------------

    def with_eval_hash(self, eval_hash: str) -> ComponentIdentifier:
        """
        Return a new identifier with ``eval_hash`` set.

        Builds a fresh instance, passing the existing ``hash`` through
        explicitly so it is preserved rather than recomputed. This matters
        for identifiers reconstructed from truncated DB data, where
        recomputing from the truncated params would produce a wrong hash.

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

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @property
    def short_hash(self) -> str:
        """
        Return the first 8 characters of the hash for display and logging.

        Raises:
            RuntimeError: If the hash has not been set by the validator.
        """
        if self.hash is None:
            raise RuntimeError("hash should be set by validator")
        return self.hash[:8]

    @property
    def unique_name(self) -> str:
        """Globally unique display name: ``class_name::short_hash``."""
        return f"{self.class_name}::{self.short_hash}"

    def __str__(self) -> str:
        """
        Human-readable identifier name.

        Returns:
            The display string ``class_name::short_hash``.
        """
        return f"{self.class_name}::{self.short_hash}"

    def __repr__(self) -> str:
        """
        Developer-oriented representation including params and children.

        Returns:
            A descriptive ``ComponentIdentifier(...)`` string.
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

    # ------------------------------------------------------------------
    # Factory + traversal
    # ------------------------------------------------------------------

    @classmethod
    def of(
        cls,
        obj: object,
        *,
        params: dict[str, Any] | None = None,
        children: dict[str, ComponentIdentifier | list[ComponentIdentifier]] | None = None,
        **promoted: Any,
    ) -> Self:
        """
        Build a ComponentIdentifier from a live object instance.

        Extracts ``class_name`` and ``class_module`` from the object's type
        automatically. None-valued params and children are filtered out to
        keep schemas backward-compatible.

        Args:
            obj: The live object whose class metadata will populate the
                identifier.
            params: Optional behavioral params.
            children: Optional child identifiers.
            **promoted: Optional promoted typed fields (for subclasses). Passed
                by name; ``None`` values are dropped. These are mirrored back
                into ``params`` / ``children`` automatically.

        Returns:
            A new ComponentIdentifier describing ``obj``.
        """
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        clean_children = {k: v for k, v in (children or {}).items() if v is not None}
        clean_promoted = {k: v for k, v in promoted.items() if v is not None}

        return cls(
            class_name=obj.__class__.__name__,
            class_module=obj.__class__.__module__,
            params=clean_params,
            children=clean_children,
            **clean_promoted,
        )

    @classmethod
    def from_component_identifier(cls, identifier: ComponentIdentifier) -> Self:
        """
        Return ``identifier`` as an instance of this typed subclass.

        Pass-through when ``identifier`` is already an instance of ``cls``;
        otherwise revalidate its flat dump into ``cls`` (e.g. a base identifier
        loaded from the DB), rehydrating promoted typed fields. The hash is
        preserved across the round-trip.

        Args:
            identifier: A ``ComponentIdentifier`` (possibly the base type).

        Returns:
            An instance of ``cls`` describing the same identity.
        """
        if isinstance(identifier, cls):
            return identifier
        return cls.model_validate(identifier.model_dump())

    def get_child(self, key: str) -> ComponentIdentifier | None:
        """
        Get a single child by key.

        Args:
            key: Child name.

        Returns:
            The child identifier, or ``None`` if not present.

        Raises:
            ValueError: If the child at ``key`` is a list. Use
                ``get_child_list`` for list-valued children.
        """
        child = self.children.get(key)
        if child is None:
            return None
        if isinstance(child, list):
            raise ValueError(f"Child '{key}' is a list of {len(child)} components. Use get_child_list() instead.")
        return child

    def get_child_list(self, key: str) -> list[ComponentIdentifier]:
        """
        Get a list of children by key. Wraps singletons; ``[]`` if missing.

        Args:
            key: Child name.

        Returns:
            A list of child identifiers.
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

        Returns:
            The set of non-empty eval_hash strings found in descendants.
        """
        hashes: set[str] = set()
        for child_val in self.children.values():
            children_list = child_val if isinstance(child_val, list) else [child_val]
            for child in children_list:
                if child.eval_hash:
                    hashes.add(child.eval_hash)
                hashes.update(child._collect_child_eval_hashes())
        return hashes

    @staticmethod
    def _truncate_value(*, value: Any, max_length: int | None) -> Any:
        """
        Truncate string values longer than ``max_length`` with a ``...`` suffix.

        Args:
            value: The value to potentially truncate.
            max_length: Maximum length, or ``None`` to disable.

        Returns:
            The (possibly truncated) value.
        """
        if max_length is not None and isinstance(value, str) and len(value) > max_length:
            return value[:max_length] + "..."
        return value

    # ------------------------------------------------------------------
    # Deprecated shims — kept for one release cycle
    # ------------------------------------------------------------------

    def to_dict(self, *, max_value_length: int | None = None) -> dict[str, Any]:
        """
        Return the flat storage dict (deprecated; use ``model_dump`` instead).

        Args:
            max_value_length: Optional truncation length for string params.

        Returns:
            The flat dict representation.
        """
        print_deprecation_message(
            old_item="ComponentIdentifier.to_dict",
            new_item="ComponentIdentifier.model_dump",
            removed_in="0.16.0",
        )
        context = {"max_value_length": max_value_length} if max_value_length is not None else None
        return self.model_dump(context=context)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentIdentifier:
        """
        Reconstruct from a flat dict (deprecated; use ``model_validate`` instead).

        Args:
            data: The flat storage dict.

        Returns:
            A new ComponentIdentifier.
        """
        print_deprecation_message(
            old_item="ComponentIdentifier.from_dict",
            new_item="ComponentIdentifier.model_validate",
            removed_in="0.16.0",
        )
        return cls.model_validate(data)


class Identifiable(ABC):
    """
    Abstract base class for components that provide a behavioral identity.

    Components implement ``_build_identifier()`` to return a frozen ComponentIdentifier
    snapshot. The identifier is built lazily on first access and cached for the
    component's lifetime.
    """

    _identifier: ComponentIdentifier | None = None

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
        identifier = self._identifier
        if identifier is None:
            identifier = self._build_identifier()
            self._identifier = identifier
        return identifier
