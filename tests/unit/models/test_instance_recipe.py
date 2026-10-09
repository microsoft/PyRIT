# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for saved instance recipe models."""

import pytest
from pydantic import ValidationError

from pyrit.models import ComponentType
from pyrit.models.catalog.instance_recipe import CredentialReference, InstanceRecipe


def test_recipe_accepts_each_persisted_kind() -> None:
    for kind in (ComponentType.TARGET, ComponentType.CONVERTER, ComponentType.SCORER):
        assert InstanceRecipe(kind=kind, name="saved", type="SomeType").kind is kind


def test_recipe_rejects_a_kind_that_is_not_persisted() -> None:
    with pytest.raises(ValidationError, match="not persisted"):
        InstanceRecipe(kind=ComponentType.SCENARIO, name="saved", type="SomeScenario")


def test_only_target_recipes_carry_an_authentication_mode() -> None:
    assert InstanceRecipe(kind=ComponentType.TARGET, name="t", type="T", auth_mode="identity").auth_mode == "identity"
    with pytest.raises(ValidationError, match="Only target recipes"):
        InstanceRecipe(kind=ComponentType.CONVERTER, name="c", type="C", auth_mode="api_key")


@pytest.mark.parametrize("schema_version", [0, 2])
def test_recipe_rejects_formats_other_than_the_current_one(schema_version: int) -> None:
    with pytest.raises(ValidationError, match="Unsupported recipe format"):
        InstanceRecipe(schema_version=schema_version, kind=ComponentType.TARGET, name="t", type="T")


def test_recipe_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        InstanceRecipe.model_validate({"kind": "target", "name": "t", "type": "T", "api_key": "secret"})


@pytest.mark.parametrize("name", ["Team-Chat.1", "compat_0123456789abcdef0123456789abcdef", "x" * 64])
def test_recipe_accepts_names_the_api_accepts(name: str) -> None:
    assert InstanceRecipe(kind=ComponentType.TARGET, name=name, type="T").name == name


@pytest.mark.parametrize("name", ["", "team/chat", "x" * 65, "-leading-dash", "with space"])
def test_recipe_rejects_names_the_api_cannot_address(name: str) -> None:
    with pytest.raises(ValidationError):
        InstanceRecipe(kind=ComponentType.TARGET, name=name, type="T")


@pytest.mark.parametrize("env_var", ["OPENAI_CHAT_KEY", "_private", "lower_case1"])
def test_credential_reference_accepts_environment_variable_names(env_var: str) -> None:
    assert CredentialReference(env_var=env_var).env_var == env_var


@pytest.mark.parametrize("env_var", ["", "1STARTS_WITH_DIGIT", "HAS-DASH", "sk-live-value with spaces"])
def test_credential_reference_rejects_values_that_are_not_variable_names(env_var: str) -> None:
    with pytest.raises(ValidationError):
        CredentialReference(env_var=env_var)


def test_credential_reference_holds_only_the_variable_name() -> None:
    with pytest.raises(ValidationError):
        CredentialReference.model_validate({"env_var": "KEY", "value": "secret"})
