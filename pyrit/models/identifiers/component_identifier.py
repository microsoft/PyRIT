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
    5. Attributes are identity-bearing state: hashed like params, but excluded from
       the eval hash and never passed to a constructor.
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializationInfo,
    computed_field,
    model_serializer,
    model_validator,
)
from typing_extensions import Self, TypeAliasType

import pyrit

if TYPE_CHECKING:
    from pyrit.models.parameter import ComponentType

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
        "attributes",
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
    attributes: dict[str, JSONValue] | None = None,
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
        attributes (dict[str, JSONValue] | None): Identity-bearing state (non-None values
            only). Hashed like params but excluded from the eval hash.

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

    # Attributes sit under their own key (never inlined alongside params) and,
    # like params, only contribute non-None values so an optional attribute with
    # a None default stays hash-compatible.
    if attributes:
        attr_dict = {key: value for key, value in sorted(attributes.items()) if value is not None}
        if attr_dict:
            hash_dict[ComponentIdentifier.KEY_ATTRIBUTES] = attr_dict

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
    declared subclass; the content hash is recomputed identically on revalidation.

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

    Attributes: a third bucket alongside ``params`` and ``children`` for
    identity-bearing **state** that is neither behavioral nor a constructor input —
    e.g. a deployment / model version observed at runtime. Like params it feeds the
    content hash, but it is excluded from the eval hash and is never used to build
    the component. No identifier promotes attributes to a typed field today; they are
    populated explicitly through the ``attributes`` dict.

    Serialization: ``model_dump()`` returns a flat dict where reserved keys
    (``class_name``, ``class_module``, ``hash``, ``pyrit_version``, ``eval_hash``,
    Serialization: ``model_dump()`` returns a flat dict where reserved keys
    (``class_name``, ``class_module``, ``hash``, ``pyrit_version``, ``eval_hash``,
    ``children``, ``attributes``) sit at the top level alongside the inlined param
    values. This shape is also the storage / REST format. Param values are stored
    in full (no truncation). ``model_validate()`` accepts the same flat shape (plus
    a structured form with an explicit ``params`` dict); the content ``hash`` is
    always recomputed on validation, so any stored ``hash`` is ignored.

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
    KEY_ATTRIBUTES: ClassVar[str] = "attributes"
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
    #: Identity-bearing state that is hashed (like ``params``) but excluded from the
    #: eval hash and never passed to a constructor — e.g. a runtime-resolved model
    #: version. Same value rules as ``params`` (see ``JSONValue``).
    attributes: dict[str, JSONValue] = Field(default_factory=dict)
    #: Version tag for storage. Not included in the content hash.
    pyrit_version: str = Field(default=pyrit.__version__)
    #: Evaluation hash. The base identifier cannot compute it (the eval rules live
    #: in EvaluationIdentifier subclasses), so it is attached only through
    #: ``with_eval_hash``, which is the single supported way to set it. Stamped on
    #: so it lands in the stored JSON for DB-level filtering.
    eval_hash: str | None = None

    #: The registry family this identifier type builds. ``None`` on the base means
    #: a plain ``ComponentIdentifier`` is never a buildable/resolvable reference;
    #: each concrete leaf identifier (``TargetIdentifier`` / ``ConverterIdentifier``
    #: / ``ScorerIdentifier``) overrides it with its own ``ComponentType`` so a
    #: child-identifier-typed field self-reports which registry resolves it.
    component_type: ClassVar[ComponentType | None] = None

    #: Cache backing the read-only ``hash`` computed field. Populated once by the
    #: after-validator from the identifier's content; never set externally.
    _hash: str = PrivateAttr(default="")

    # ------------------------------------------------------------------
    # Promotion (typed projection — derived from the subclass's own fields)
    # ------------------------------------------------------------------

    @staticmethod
    def _child_identifier_type(annotation: Any) -> type[ComponentIdentifier] | None:
        """
        Return the ``ComponentIdentifier`` subclass a field annotation denotes, if any.

        Handles a direct subclass, an ``Optional`` wrapper, and a ``list[...]`` of
        subclasses (the two shapes promoted fields use for children).

        Args:
            annotation (Any): The resolved field annotation (from
                ``model_fields[name].annotation``).

        Returns:
            type[ComponentIdentifier] | None: The child identifier subclass, or
            ``None`` for a scalar (param) field.
        """
        if get_origin(annotation) is list:
            args = get_args(annotation)
            inner = args[0] if args else None
            return inner if isinstance(inner, type) and issubclass(inner, ComponentIdentifier) else None

        for candidate in get_args(annotation) or (annotation,):
            if isinstance(candidate, type) and issubclass(candidate, ComponentIdentifier):
                return candidate
        return None

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
        return ComponentIdentifier._child_identifier_type(annotation) is not None

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

    @classmethod
    def promoted_scalar_field_names(cls) -> tuple[str, ...]:
        """
        Get names of this identifier's promoted scalar (param) fields — the DB-column projection.

        Returns:
            tuple[str, ...]: Promoted scalar field names.
        """
        return cls._promoted_param_fields()

    @classmethod
    def promoted_child_field_names(cls) -> tuple[str, ...]:
        """
        Get names of this identifier's promoted child fields.

        Returns:
            tuple[str, ...]: Promoted child field names.
        """
        return cls._promoted_child_fields()

    def promoted_scalar_values(self) -> dict[str, Any]:
        """
        Get this identifier's promoted scalar fields as ``{name: value}`` (children/targets excluded).

        Returns:
            dict[str, Any]: Promoted scalar field names and values.
        """
        return {name: getattr(self, name) for name in self._promoted_param_fields()}

    @classmethod
    def get_reference_component_types(cls) -> dict[str, ComponentType]:
        """
        Map constructor-arg names to the component family each reference resolves to.

        A promoted field that is an included constructor parameter (explicit
        ``Param.Include`` or unmarked) and is typed as a child identifier
        contributes ``{arg_name: component_type}``, where ``arg_name`` is the marker
        alias or the field name and ``component_type`` is the child identifier
        type's own ``component_type``. ``Param.Exclude()`` fields, plain-value
        fields, and any field typed as a base ``ComponentIdentifier`` (whose
        ``component_type`` is ``None`` and is therefore not buildable) contribute
        nothing.

        Returns:
            dict[str, ComponentType]: Constructor-arg-name → referenced component type.
        """
        from pyrit.models.identifiers.param_markers import ClassAttrMarker, ExcludeMarker, IncludeMarker, ParamMarker

        references: dict[str, ComponentType] = {}
        for field_name in cls._promoted_fields():
            field = cls.model_fields[field_name]
            marker = next((m for m in field.metadata if isinstance(m, ParamMarker)), None)
            if isinstance(marker, (ExcludeMarker, ClassAttrMarker)):
                continue

            child_type = cls._child_identifier_type(field.annotation)
            if child_type is None or child_type.component_type is None:
                continue

            arg_name = marker.alias if isinstance(marker, IncludeMarker) and marker.alias else field_name
            references[arg_name] = child_type.component_type

        return references

    @classmethod
    def get_class_attribute_values(cls, target_cls: type) -> dict[str, Any]:
        """
        Read each ``Param.ClassAttr``-marked field's value off a target class.

        Lets a registry describe a *class* (with no configured instance) by
        sourcing the marked fields directly from class attributes. For every
        promoted field carrying a ``ClassAttrMarker``, reads the named class
        attribute (defaulting to the field name upper-cased) from ``target_cls``.

        Args:
            target_cls (type): The component class to read class attributes from.

        Returns:
            dict[str, Any]: Field-name → class-attribute value, for each
            ``Param.ClassAttr`` field. Missing attributes map to ``None``.
        """
        from pyrit.models.identifiers.param_markers import ClassAttrMarker

        values: dict[str, Any] = {}
        for field_name in cls._promoted_fields():
            field = cls.model_fields[field_name]
            marker = next((m for m in field.metadata if isinstance(m, ClassAttrMarker)), None)
            if marker is None:
                continue
            attr_name = marker.attr_name or field_name.upper()
            values[field_name] = getattr(target_cls, attr_name, None)

        return values

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

        # hash is a read-only computed field, never supplied. Drop any incoming
        # value (a constructor kwarg or one read back from the flat storage form)
        # so extra="forbid" does not reject it; the computed field derives it.
        data.pop(cls.KEY_HASH, None)

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
            cls.KEY_ATTRIBUTES,
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
            collisions: set[str] = {str(name) for name in params_dict} & RESERVED_PARAM_NAMES
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
            # The content hash is recomputed identically on revalidation.
            for name in cls._promoted_child_fields():
                if name in data:
                    data[name] = _dump_child_identifiers_to_dict(data[name])

        return data

    @model_validator(mode="after")
    def _promote_typed_fields(self) -> ComponentIdentifier:
        """
        Mirror promoted typed fields into ``params`` / ``children``, then hash.

        Promoted scalar fields are written into ``params`` and promoted
        identifier fields into ``children`` (``None`` / empty list dropped), so a
        typed subclass serializes and hashes identically to a plain
        ``ComponentIdentifier`` with the same values. The content-addressed hash
        is then computed once from the populated content and cached in ``_hash``,
        backing the read-only ``hash`` computed field.

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

        hash_dict = _build_hash_dict(
            class_name=self.class_name,
            class_module=self.class_module,
            params=self.params,
            children=self.children,
            attributes=self.attributes,
        )
        object.__setattr__(self, "_hash", config_hash(hash_dict))
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hash(self) -> str:
        """
        Content-addressed SHA256 hash, derived from this identifier's content.

        Computed once by the after-validator from ``class_name`` /
        ``class_module`` / ``params`` / ``children`` / ``attributes`` and cached
        in ``_hash``. It is a read-only computed field: nothing can set it, and
        any ``hash`` value supplied at construction (a kwarg, or one read back
        from the flat storage form) is dropped before validation.

        Returns:
            The SHA256 content hash.
        """
        return self._hash

    # ------------------------------------------------------------------
    # Serializer
    # ------------------------------------------------------------------

    @model_serializer(mode="plain")
    def _serialize_flat(self, info: SerializationInfo) -> dict[str, Any]:
        """
        Emit the flat storage shape.

        Propagates the serialization mode (``"python"`` vs ``"json"``) into
        recursive child dumps. Values are stored in full — identifiers are no
        longer truncated.

        Returns:
            The flat dict representation of this identifier.
        """
        mode = info.mode

        result: dict[str, Any] = {
            self.KEY_CLASS_NAME: self.class_name,
            self.KEY_CLASS_MODULE: self.class_module,
            self.KEY_HASH: self.hash,
            self.KEY_PYRIT_VERSION: self.pyrit_version,
        }
        if self.eval_hash is not None:
            result[self.KEY_EVAL_HASH] = self.eval_hash

        result.update(self.params)

        if self.children:
            serialized_children: dict[str, Any] = {}
            for name, child in self.children.items():
                if isinstance(child, ComponentIdentifier):
                    serialized_children[name] = child.model_dump(mode=mode)
                elif isinstance(child, list):
                    serialized_children[name] = [c.model_dump(mode=mode) for c in child]
            result[self.KEY_CHILDREN] = serialized_children

        if self.attributes:
            result[self.KEY_ATTRIBUTES] = dict(self.attributes)

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

        This is the single supported way to set ``eval_hash``: it is not
        computed by the base model, so callers attach it here rather than via
        the constructor. The content hash is recomputed from the (unchanged)
        params and children, so it is identical to this identifier's hash.

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
            attributes=self.attributes,
            pyrit_version=self.pyrit_version,
            eval_hash=eval_hash,
        )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @property
    def short_hash(self) -> str:
        """
        The first 8 characters of the hash, for display and logging.
        """
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
        attributes_str = ", ".join(f"{k}={v!r}" for k, v in sorted(self.attributes.items()))
        parts = [f"class={self.class_name}"]
        if params_str:
            parts.append(f"params=({params_str})")
        if children_str:
            parts.append(f"children=({children_str})")
        if attributes_str:
            parts.append(f"attributes=({attributes_str})")
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
        attributes: dict[str, Any] | None = None,
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
            attributes: Optional identity-bearing state (hashed, but excluded from
                the eval hash and not a constructor input). ``None`` values dropped.
            **promoted: Optional promoted typed fields (for subclasses). Passed
                by name; ``None`` values are dropped. These are mirrored back
                into ``params`` / ``children`` automatically.

        Returns:
            A new ComponentIdentifier describing ``obj``.
        """
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        clean_children = {k: v for k, v in (children or {}).items() if v is not None}
        clean_attributes = {k: v for k, v in (attributes or {}).items() if v is not None}
        clean_promoted = {k: v for k, v in promoted.items() if v is not None}

        return cls(
            class_name=obj.__class__.__name__,
            class_module=obj.__class__.__module__,
            params=clean_params,
            children=clean_children,
            attributes=clean_attributes,
            **clean_promoted,
        )

    @classmethod
    def from_component_identifier(cls, identifier: ComponentIdentifier) -> Self:
        """
        Return ``identifier`` as an instance of this typed subclass.

        Pass-through when ``identifier`` is already an instance of ``cls``;
        otherwise revalidate its flat dump into ``cls`` (e.g. a base identifier
        loaded from the DB), rehydrating promoted typed fields. The content hash
        is recomputed identically across the round-trip.

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
        Recursively collect all eval_hash values from descendant identifiers.

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
