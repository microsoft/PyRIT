# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Every credential-like constructor parameter is classified, so a new credential cannot be saved by accident."""

import re

import pytest

from pyrit.common.credential_names import CredentialNames
from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.target_identifier import TargetIdentifier
from pyrit.registry import ConverterRegistry, ScorerRegistry, TargetRegistry

_CREDENTIAL_LIKE = re.compile(r"key|token|secret|password|cookie|credential|auth", re.IGNORECASE)

# Parameters whose names look like credentials but whose values are not.
_NOT_CREDENTIALS = frozenset(
    {
        "api_key_header",
        "auth_mode",
        "authenticator",
        "category_output_key",
        "chunk_overlap_tokens",
        "description_output_key",
        "end_token",
        "key",
        "mask_token",
        "max_completion_tokens",
        "max_input_tokens",
        "max_new_tokens",
        "max_objective_tokens",
        "max_output_tokens",
        "max_tokens",
        "metadata_output_key",
        "preserve_tokens",
        "rationale_output_key",
        "score_value_output_key",
        "skip_special_tokens",
        "start_token",
        "token_insert_mode",
        "token_to_repeat",
        "tokenizer",
    }
)


@pytest.mark.parametrize("registry_class", [TargetRegistry, ConverterRegistry, ScorerRegistry])
def test_every_credential_like_parameter_is_classified(
    registry_class: type[TargetRegistry] | type[ConverterRegistry] | type[ScorerRegistry],
) -> None:
    classified = ComponentIdentifier.get_sensitive_parameter_names() | _NOT_CREDENTIALS
    registry = registry_class.get_registry_singleton()

    unclassified = sorted(
        f"{metadata.class_name}.{parameter.name}"
        for metadata in registry.get_all_registered_class_metadata()
        for parameter in metadata.parameters
        if (_CREDENTIAL_LIKE.search(parameter.name) or CredentialNames.is_credential(parameter.name))
        and parameter.name not in classified
    )

    assert unclassified == []


def test_sensitive_and_ordinary_names_do_not_overlap() -> None:
    assert ComponentIdentifier.get_sensitive_parameter_names().isdisjoint(_NOT_CREDENTIALS)


def test_request_templates_and_headers_stay_reference_only() -> None:
    # Their names do not look like credentials, but their values commonly hold an Authorization or Cookie value.
    assert {"cookie", "headers", "http_request"} <= ComponentIdentifier.get_sensitive_parameter_names()


@pytest.mark.parametrize("registry_class", [TargetRegistry, ConverterRegistry, ScorerRegistry])
def test_catalog_marks_exactly_the_credential_parameters_sensitive(
    registry_class: type[TargetRegistry] | type[ConverterRegistry] | type[ScorerRegistry],
) -> None:
    sensitive_names = ComponentIdentifier.get_sensitive_parameter_names()
    parameters = [
        (metadata.class_name, parameter)
        for metadata in registry_class.get_registry_singleton().get_all_registered_class_metadata()
        for parameter in metadata.parameters
        if parameter.reference is None
    ]

    mismatched = sorted(
        f"{class_name}.{parameter.name}"
        for class_name, parameter in parameters
        if parameter.sensitive != (parameter.name in sensitive_names)
    )

    assert mismatched == []
    assert any(parameter.sensitive for _, parameter in parameters)


def test_identity_conflicting_parameters_are_credentials_of_identity_targets() -> None:
    conflicting = TargetIdentifier.get_identity_conflicting_parameter_names()
    declared = {
        parameter.name
        for metadata in TargetRegistry.get_registry_singleton().get_all_registered_class_metadata()
        if "identity" in metadata.supported_auth_modes
        for parameter in metadata.parameters
    }

    assert conflicting
    assert conflicting <= ComponentIdentifier.get_sensitive_parameter_names()
    assert conflicting <= declared
