# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for CoPyRIT deployment configuration."""

import argparse
import json
from unittest.mock import patch

import pytest

from infra import deploy_instance


def test_managed_identity_blob_uri_accepts_credential_free_azure_uri() -> None:
    uri = "https://account.blob.core.windows.net/config/config.yaml"

    assert deploy_instance._managed_identity_blob_uri(uri) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://account.blob.core.windows.net/config/config.yaml?sig=secret",
        "https://account.blob.core.windows.net/config/config.yaml#fragment",
        "https://attacker.blob.example.com/config/config.yaml",
        "http://account.blob.core.windows.net/config/config.yaml",
    ],
)
def test_managed_identity_blob_uri_rejects_credentialed_or_untrusted_uri(uri: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="credential-free"):
        deploy_instance._managed_identity_blob_uri(uri)


def test_post_deploy_enables_spa_and_device_code_authentication() -> None:
    with patch.object(deploy_instance, "run_az") as run_az:
        deploy_instance.post_deploy(
            app_object_id="app-object-id",
            fqdn="copyrit.example.com",
        )

    args = run_az.call_args.kwargs["args"]
    body = json.loads(args[args.index("--body") + 1])
    assert body == {
        "spa": {"redirectUris": ["https://copyrit.example.com"]},
        "isFallbackPublicClient": True,
    }


def test_deploy_bicep_deploys_infrastructure_before_application() -> None:
    infrastructure_outputs = {
        "egressPublicIpAddress": {"value": "203.0.113.10"},
        "managedIdentityResourceId": {"value": "/subscriptions/sub/resourceGroups/rg/providers/identity"},
    }
    application_outputs = {"appFqdn": {"value": "copyrit.example.com"}}

    with patch.object(
        deploy_instance,
        "_deploy_bicep_template",
        side_effect=[infrastructure_outputs, application_outputs],
    ) as deploy_template:
        outputs = deploy_instance.deploy_bicep(
            resource_group="rg",
            app_name="copyrit-demo",
            container_image="acr.azurecr.io/pyrit:abc123",
            tenant_id="tenant-id",
            client_id="client-id",
            group_ids="group-id",
            admin_group_id="admin-group-id",
            allowed_cidr="",
            sql_server_fqdn="server.database.windows.net",
            sql_database_name="database",
            kv_resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/vault",
            acr_name="acr",
            managed_identity_resource_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.ManagedIdentity/userAssignedIdentities/copyrit-demo-identity"
            ),
            env_file_contents="KEY=value",
            pyrit_config_file_uri="",
            tags={"Service": "pyrit-gui"},
        )

    assert deploy_template.call_count == 2
    infrastructure_call, application_call = deploy_template.call_args_list
    assert infrastructure_call.kwargs["template_file"] == deploy_instance.INFRASTRUCTURE_BICEP_TEMPLATE
    assert infrastructure_call.kwargs["deployment_name"] == "copyrit-demo-infrastructure"
    assert set(infrastructure_call.kwargs["parameters"]) == {
        "appName",
        "acrName",
        "existingManagedIdentityResourceId",
        "tags",
    }
    assert application_call.kwargs["template_file"] == deploy_instance.APPLICATION_BICEP_TEMPLATE
    assert application_call.kwargs["deployment_name"] == "copyrit-demo-application"
    assert application_call.kwargs["parameters"]["containerImage"]["value"] == "acr.azurecr.io/pyrit:abc123"
    assert outputs == infrastructure_outputs | application_outputs
