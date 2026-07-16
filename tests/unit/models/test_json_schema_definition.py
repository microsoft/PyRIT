# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from pyrit.models import (
    COMMON_JSON_SCHEMAS,
    JSON_SCHEMA_METADATA_KEY,
    JsonSchemaDefinition,
    get_common_json_schema,
    register_common_json_schema,
    unregister_common_json_schema,
)
from pyrit.models.target import json_schema_definition as jsd


@pytest.fixture
def transient_schema_name() -> Iterator[str]:
    """
    Yield a schema name that is guaranteed to be unregistered before and after the
    test, so register/unregister tests cannot leak state into each other or into
    other test modules.
    """
    name = "test_transient_schema__do_not_use_in_prod"
    if name in COMMON_JSON_SCHEMAS:
        unregister_common_json_schema(name)
    try:
        yield name
    finally:
        if name in COMMON_JSON_SCHEMAS:
            unregister_common_json_schema(name)


@pytest.fixture
def isolated_yaml_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """
    Point ``JSON_SCHEMAS_PATH`` at an empty ``tmp_path`` and reset the registry
    cache before and after the test. Yields the ``tmp_path`` so the test can
    drop fixture YAML files into it. Guarantees the global registry is restored
    to its bundled-YAML state after the test runs.
    """
    monkeypatch.setattr(jsd, "JSON_SCHEMAS_PATH", tmp_path)
    jsd._reset_common_json_schemas_for_tests()
    try:
        yield tmp_path
    finally:
        jsd._reset_common_json_schemas_for_tests()


# --- Constants and aliases ---


def test_metadata_key_is_stable_string():
    """Producers and consumers of prompt_metadata key off this constant; pin its value."""
    assert JSON_SCHEMA_METADATA_KEY == "json_schema"


def test_seed_response_json_schema_metadata_key_is_reserved():
    """SeedEntry round-trips response_json_schema through this reserved key; pin its value."""
    assert jsd.SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY == "__response_json_schema__"


def test_json_schema_definition_alias_is_dict_str_any():
    """The alias is just dict[str, Any] — callers and Pydantic fields rely on this shape."""
    schema: JsonSchemaDefinition = {"type": "object", "properties": {}}
    assert isinstance(schema, dict)


# --- Bundled YAML registry contents ---


def test_bundled_true_false_with_rationale_loads_from_yaml():
    """
    The bundled ``true_false_with_rationale.yaml`` file is the source of truth for
    the self-ask True/False scorer's response shape. Pinning these assertions guards
    against accidental edits to the YAML file that would silently change the schema
    every scorer sees.
    """
    assert "true_false_with_rationale" in COMMON_JSON_SCHEMAS

    schema = COMMON_JSON_SCHEMAS["true_false_with_rationale"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"score_value", "rationale"}
    assert schema["properties"]["score_value"]["type"] == "boolean"
    assert schema["additionalProperties"] is False


def test_common_json_schemas_is_read_only_mapping():
    """
    ``COMMON_JSON_SCHEMAS`` is a ``Mapping`` subclass with no ``__setitem__``; any
    attempt to register via subscript assignment must raise ``TypeError`` so callers
    are forced through ``register_common_json_schema``.
    """
    with pytest.raises(TypeError):
        COMMON_JSON_SCHEMAS["new_key"] = {}  # type: ignore[index]


def test_get_common_json_schema_returns_deep_copy():
    """Mutating the returned schema must not affect the registry or future callers."""
    first = get_common_json_schema("true_false_with_rationale")
    first["properties"]["score_value"]["type"] = "string"
    first["new_top_level_key"] = "tampered"

    second = get_common_json_schema("true_false_with_rationale")
    assert second["properties"]["score_value"]["type"] == "boolean"
    assert "new_top_level_key" not in second

    registry_schema = COMMON_JSON_SCHEMAS["true_false_with_rationale"]
    assert registry_schema["properties"]["score_value"]["type"] == "boolean"
    assert "new_top_level_key" not in registry_schema


def test_get_common_json_schema_unknown_name_raises_keyerror():
    """Unknown names raise KeyError with a helpful message listing known names."""
    with pytest.raises(KeyError, match="Unknown JSON schema name 'not_a_real_schema'"):
        get_common_json_schema("not_a_real_schema")


def test_get_common_json_schema_error_lists_known_names():
    """The KeyError message includes known names so callers can discover them."""
    with pytest.raises(KeyError) as exc_info:
        get_common_json_schema("nope")
    assert "true_false_with_rationale" in str(exc_info.value)


# --- YAML loader behaviour ---


def test_loader_discovers_yaml_in_directory(isolated_yaml_dir: Path):
    """Every ``*.yaml`` whose top level is a mapping becomes a registry entry keyed by file stem."""
    (isolated_yaml_dir / "alpha.yaml").write_text(
        yaml.safe_dump({"type": "object", "properties": {"a": {"type": "string"}}}),
        encoding="utf-8",
    )
    (isolated_yaml_dir / "beta.yaml").write_text(
        yaml.safe_dump({"type": "object", "properties": {"b": {"type": "integer"}}}),
        encoding="utf-8",
    )

    assert set(COMMON_JSON_SCHEMAS) == {"alpha", "beta"}
    assert get_common_json_schema("alpha")["properties"] == {"a": {"type": "string"}}
    assert get_common_json_schema("beta")["properties"] == {"b": {"type": "integer"}}


def test_loader_skips_invalid_yaml_files(
    isolated_yaml_dir: Path,
    caplog: pytest.LogCaptureFixture,
):
    """
    A YAML parse error or a non-mapping top level must be skipped with a warning;
    well-formed siblings must still load. A third-party dataset overlay with one
    bad file must not break PyRIT startup.
    """
    (isolated_yaml_dir / "good.yaml").write_text(
        yaml.safe_dump({"type": "object", "properties": {}}),
        encoding="utf-8",
    )
    # Malformed YAML.
    (isolated_yaml_dir / "broken.yaml").write_text("key: : :\n", encoding="utf-8")
    # Top-level is a list, not a mapping.
    (isolated_yaml_dir / "list_top_level.yaml").write_text(
        yaml.safe_dump(["a", "b", "c"]),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger=jsd.__name__):
        names = set(COMMON_JSON_SCHEMAS)

    assert names == {"good"}

    warning_messages = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "broken.yaml" in warning_messages
    assert "list_top_level.yaml" in warning_messages


def test_loader_missing_directory_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """A non-existent ``JSON_SCHEMAS_PATH`` must log a warning and produce an empty registry."""
    missing = tmp_path / "definitely_not_here"
    assert not missing.exists()

    monkeypatch.setattr(jsd, "JSON_SCHEMAS_PATH", missing)
    jsd._reset_common_json_schemas_for_tests()
    try:
        with caplog.at_level(logging.WARNING, logger=jsd.__name__):
            assert len(COMMON_JSON_SCHEMAS) == 0
        assert any("does not exist" in rec.getMessage() for rec in caplog.records)
    finally:
        jsd._reset_common_json_schemas_for_tests()


def test_register_after_discovery_collides_on_bundled_name():
    """Discovery runs before the register collision check, so bundled names also collide."""
    with pytest.raises(ValueError, match="already registered"):
        register_common_json_schema(name="true_false_with_rationale", schema={"type": "string"})


def test_runtime_register_survives_alongside_yaml_entries(transient_schema_name: str):
    """Runtime registrations coexist with YAML-loaded entries and do not perturb them."""
    register_common_json_schema(name=transient_schema_name, schema={"type": "object"})
    assert transient_schema_name in COMMON_JSON_SCHEMAS
    assert "true_false_with_rationale" in COMMON_JSON_SCHEMAS

    unregister_common_json_schema(transient_schema_name)
    assert transient_schema_name not in COMMON_JSON_SCHEMAS
    # YAML-loaded entries are unaffected by unregistering a runtime entry.
    assert "true_false_with_rationale" in COMMON_JSON_SCHEMAS


# --- register / unregister API ---


def test_register_common_json_schema_adds_entry(transient_schema_name: str):
    schema: JsonSchemaDefinition = {"type": "object", "properties": {"x": {"type": "string"}}}
    register_common_json_schema(name=transient_schema_name, schema=schema)

    assert transient_schema_name in COMMON_JSON_SCHEMAS
    fetched = get_common_json_schema(transient_schema_name)
    assert fetched == schema


def test_register_common_json_schema_duplicate_raises_by_default(transient_schema_name: str):
    register_common_json_schema(name=transient_schema_name, schema={"type": "object"})

    with pytest.raises(ValueError, match="already registered"):
        register_common_json_schema(name=transient_schema_name, schema={"type": "string"})


def test_register_common_json_schema_overwrite_replaces(transient_schema_name: str):
    register_common_json_schema(name=transient_schema_name, schema={"type": "object"})
    register_common_json_schema(name=transient_schema_name, schema={"type": "string"}, overwrite=True)

    assert get_common_json_schema(transient_schema_name) == {"type": "string"}


def test_register_common_json_schema_deep_copies_input(transient_schema_name: str):
    """Mutating the caller's dict after registration must not affect the registry."""
    source: JsonSchemaDefinition = {"type": "object", "properties": {"x": {"type": "string"}}}
    register_common_json_schema(name=transient_schema_name, schema=source)

    source["properties"]["x"]["type"] = "integer"
    source["new_key"] = "tampered"

    fetched = get_common_json_schema(transient_schema_name)
    assert fetched["properties"]["x"]["type"] == "string"
    assert "new_key" not in fetched


def test_register_common_json_schema_non_dict_raises_typeerror(transient_schema_name: str):
    with pytest.raises(TypeError, match="schema must be a dict"):
        register_common_json_schema(name=transient_schema_name, schema="not a dict")  # type: ignore[arg-type]


def test_unregister_common_json_schema_removes_entry(transient_schema_name: str):
    register_common_json_schema(name=transient_schema_name, schema={"type": "object"})
    assert transient_schema_name in COMMON_JSON_SCHEMAS

    unregister_common_json_schema(transient_schema_name)
    assert transient_schema_name not in COMMON_JSON_SCHEMAS


def test_unregister_common_json_schema_unknown_raises_keyerror():
    with pytest.raises(KeyError, match="is not registered"):
        unregister_common_json_schema("definitely_not_a_registered_schema")


def test_register_then_get_is_visible_via_proxy_view(transient_schema_name: str):
    """COMMON_JSON_SCHEMAS is a live read-only view of the mutable backing dict."""
    assert transient_schema_name not in COMMON_JSON_SCHEMAS
    register_common_json_schema(name=transient_schema_name, schema={"type": "boolean"})
    assert COMMON_JSON_SCHEMAS[transient_schema_name] == {"type": "boolean"}
