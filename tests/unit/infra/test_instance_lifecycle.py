# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Guard deployment ownership and destructive teardown preconditions."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module(*, name: str, path: Path) -> ModuleType:
    """Load an infrastructure script as a module without requiring a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPLOY = _load_module(name="copyrit_deploy_instance", path=REPO_ROOT / "infra" / "deploy_instance.py")
TEARDOWN = _load_module(name="copyrit_teardown_instance", path=REPO_ROOT / "infra" / "teardown_instance.py")

INSTANCE = "audit-demo"
SUBSCRIPTION_ID = "11111111-1111-1111-1111-111111111111"
RESOURCE_GROUP = f"copyrit-{INSTANCE}"
RESOURCE_GROUP_ID = f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
GROUP_ID = "22222222-2222-2222-2222-222222222222"
CONFIG_URI = "https://configstore.blob.core.windows.net/config/.pyrit_conf"
CONFIG_SCOPE = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/config-rg/providers/"
    "Microsoft.Storage/storageAccounts/configstore/blobServices/default/containers/config"
)


class TestDeployInstanceContract(unittest.TestCase):
    """Verify fail-fast inputs and ownership metadata for new instances."""

    def test_deployment_tags_identify_script_owned_instance(self):
        tags = DEPLOY._deployment_tags(instance=INSTANCE, owner="owner@example.com")

        self.assertEqual(tags["Service"], "pyrit-gui")
        self.assertEqual(tags["Instance"], INSTANCE)
        self.assertEqual(tags["ManagedBy"], "infra/deploy_instance.py")
        self.assertEqual(tags["DataClass"], "Confidential")
        self.assertEqual(tags["Owner"], "owner@example.com")

    def test_invalid_instance_name_fails_before_azure_calls(self):
        with patch.object(DEPLOY, "run_az") as run_az:
            result = DEPLOY.main(
                [
                    "--instance-name",
                    "Bad_Name",
                    "--env-file",
                    "missing.env",
                    "--subscription",
                    SUBSCRIPTION_ID,
                    "--acr-name",
                    "sharedacr",
                    "--container-image",
                    "sharedacr.azurecr.io/pyrit:abc123",
                    "--allowed-groups",
                    GROUP_ID,
                    "--admin-group",
                    GROUP_ID,
                ]
            )

        self.assertEqual(result, 1)
        run_az.assert_not_called()

    def test_dry_run_accepts_matching_immutable_image(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / "instance.env"
            env_file.write_text("OPENAI_KEY=placeholder\n", encoding="utf-8")
            result = DEPLOY.main(
                [
                    "--instance-name",
                    INSTANCE,
                    "--env-file",
                    str(env_file),
                    "--subscription",
                    SUBSCRIPTION_ID,
                    "--acr-name",
                    "sharedacr",
                    "--container-image",
                    "sharedacr.azurecr.io/pyrit:abc123",
                    "--allowed-groups",
                    GROUP_ID,
                    "--admin-group",
                    GROUP_ID,
                    "--dry-run",
                ]
            )

        self.assertEqual(result, 0)

    def test_invalid_ingress_cidr_fails_before_azure_calls(self):
        with patch.object(DEPLOY, "run_az") as run_az:
            result = DEPLOY.main(
                [
                    "--instance-name",
                    INSTANCE,
                    "--env-file",
                    "missing.env",
                    "--subscription",
                    SUBSCRIPTION_ID,
                    "--acr-name",
                    "sharedacr",
                    "--container-image",
                    "sharedacr.azurecr.io/pyrit:abc123",
                    "--allowed-groups",
                    GROUP_ID,
                    "--admin-group",
                    GROUP_ID,
                    "--allowed-cidr",
                    "999.1.1.1/24",
                ]
            )

        self.assertEqual(result, 1)
        run_az.assert_not_called()

    def test_external_config_requires_rbac_scope(self):
        with patch.object(DEPLOY, "run_az") as run_az:
            result = DEPLOY.main(
                [
                    "--instance-name",
                    INSTANCE,
                    "--env-file",
                    "missing.env",
                    "--subscription",
                    SUBSCRIPTION_ID,
                    "--acr-name",
                    "sharedacr",
                    "--container-image",
                    "sharedacr.azurecr.io/pyrit:abc123",
                    "--allowed-groups",
                    GROUP_ID,
                    "--admin-group",
                    GROUP_ID,
                    "--pyrit-config-file-uri",
                    CONFIG_URI,
                ]
            )

        self.assertEqual(result, 1)
        run_az.assert_not_called()

    def test_external_config_rejects_mismatched_rbac_scope(self):
        mismatched_scope = CONFIG_SCOPE.replace("storageAccounts/configstore", "storageAccounts/otherstore")
        with patch.object(DEPLOY, "run_az") as run_az:
            result = DEPLOY.main(
                [
                    "--instance-name",
                    INSTANCE,
                    "--env-file",
                    "missing.env",
                    "--subscription",
                    SUBSCRIPTION_ID,
                    "--acr-name",
                    "sharedacr",
                    "--container-image",
                    "sharedacr.azurecr.io/pyrit:abc123",
                    "--allowed-groups",
                    GROUP_ID,
                    "--admin-group",
                    GROUP_ID,
                    "--pyrit-config-file-uri",
                    CONFIG_URI,
                    "--pyrit-config-rbac-scope",
                    mismatched_scope,
                ]
            )

        self.assertEqual(result, 1)
        run_az.assert_not_called()

    def test_external_config_scope_receives_blob_contributor_role(self):
        acr_id = f"{RESOURCE_GROUP_ID}/providers/Microsoft.ContainerRegistry/registries/sharedacr"
        with (
            patch.object(DEPLOY, "run_az") as run_az,
            patch.object(DEPLOY, "run_az_json", side_effect=[GROUP_ID, acr_id]),
        ):
            DEPLOY.create_managed_identity_and_grant_roles(
                resource_group=RESOURCE_GROUP,
                location="eastus2",
                identity_name=f"{RESOURCE_GROUP}-identity",
                acr_name="sharedacr",
                storage_account_id=f"{RESOURCE_GROUP_ID}/providers/Microsoft.Storage/storageAccounts/instance",
                config_blob_scope=CONFIG_SCOPE,
            )

        role_commands = [
            call.kwargs["args"]
            for call in run_az.call_args_list
            if call.kwargs["args"][:3] == ["role", "assignment", "create"]
        ]
        self.assertEqual(len(role_commands), 3)
        self.assertEqual(role_commands[-1][role_commands[-1].index("--scope") + 1], CONFIG_SCOPE)

    def test_group_assignment_uses_resource_service_principal_relationship(self):
        with patch.object(DEPLOY, "run_az") as run_az:
            DEPLOY.assign_groups_to_app(sp_id=GROUP_ID, group_ids=[GROUP_ID])

        command = run_az.call_args.kwargs["args"]
        url = command[command.index("--url") + 1]
        self.assertTrue(url.endswith(f"servicePrincipals/{GROUP_ID}/appRoleAssignedTo"))
        self.assertNotIn("/appRoleAssignments", url)

    def test_storage_keeps_authenticated_sas_media_network_path(self):
        with (
            patch.object(DEPLOY, "run_az") as run_az,
            patch.object(
                DEPLOY,
                "run_az_json",
                return_value=(f"{RESOURCE_GROUP_ID}/providers/Microsoft.Storage/storageAccounts/copyritauditdemosa"),
            ),
        ):
            DEPLOY.create_storage_account(
                resource_group=RESOURCE_GROUP,
                location="eastus2",
                account_name="copyritauditdemosa",
            )

        commands = [call.kwargs["args"] for call in run_az.call_args_list]
        self.assertEqual(len(commands), 2)
        create_command = commands[0]
        self.assertEqual(create_command[:3], ["storage", "account", "create"])
        self.assertEqual(create_command[create_command.index("--allow-blob-public-access") + 1], "false")
        self.assertEqual(create_command[create_command.index("--public-network-access") + 1], "Enabled")
        self.assertEqual(create_command[create_command.index("--default-action") + 1], "Allow")
        container_command = commands[1]
        self.assertEqual(container_command[:3], ["storage", "container-rm", "create"])
        self.assertEqual(container_command[container_command.index("--public-access") + 1], "off")
        self.assertFalse(any(command[:3] == ["storage", "account", "network-rule"] for command in commands))
        self.assertFalse(
            any(
                "--default-action" in command and command[command.index("--default-action") + 1] == "Deny"
                for command in commands
            )
        )

    def test_sql_firewall_uses_only_static_egress_ip(self):
        with patch.object(DEPLOY, "run_az") as run_az:
            DEPLOY.configure_sql_network_access(
                resource_group=RESOURCE_GROUP,
                sql_server_name=f"{RESOURCE_GROUP}-sql",
                egress_ip="20.30.40.50",
            )

        commands = [call.kwargs["args"] for call in run_az.call_args_list]
        self.assertEqual(len(commands), 1)
        sql_command = commands[0]
        self.assertEqual(sql_command[:3], ["sql", "server", "firewall-rule"])
        self.assertEqual(sql_command[sql_command.index("--start-ip-address") + 1], "20.30.40.50")
        self.assertEqual(sql_command[sql_command.index("--end-ip-address") + 1], "20.30.40.50")
        self.assertNotIn("0.0.0.0", sql_command)


class TestTeardownInstanceContract(unittest.TestCase):
    """Verify teardown cannot bypass ownership and egress-release checks."""

    def _arguments(self) -> list[str]:
        return [
            "--instance-name",
            INSTANCE,
            "--subscription",
            SUBSCRIPTION_ID,
            "--resource-group-id",
            RESOURCE_GROUP_ID,
            "--acknowledge-egress-ip-release",
            "--yes",
        ]

    def test_missing_egress_acknowledgement_fails_before_azure_calls(self):
        arguments = self._arguments()
        arguments.remove("--acknowledge-egress-ip-release")
        with patch.object(TEARDOWN, "run_az") as run_az:
            result = TEARDOWN.main(arguments)

        self.assertEqual(result, 1)
        run_az.assert_not_called()

    def test_mismatched_ownership_tags_refuse_deletion(self):
        responses = [
            {"id": SUBSCRIPTION_ID, "name": "Audit Subscription"},
            {"id": RESOURCE_GROUP_ID, "name": RESOURCE_GROUP, "tags": {"Service": "unrelated"}},
        ]
        with (
            patch.object(TEARDOWN, "run_az") as run_az,
            patch.object(TEARDOWN, "run_az_json", side_effect=responses),
        ):
            result = TEARDOWN.main(self._arguments())

        self.assertEqual(result, 1)
        self.assertFalse(any(call.kwargs["args"][:2] == ["group", "delete"] for call in run_az.call_args_list))

    def test_verified_instance_removes_roles_and_waits_for_group_deletion(self):
        assignment_id = f"{RESOURCE_GROUP_ID}/providers/Microsoft.Authorization/roleAssignments/role-id"
        responses = [
            {"id": SUBSCRIPTION_ID, "name": "Audit Subscription"},
            {
                "id": RESOURCE_GROUP_ID,
                "name": RESOURCE_GROUP,
                "tags": {
                    "Service": "pyrit-gui",
                    "Instance": INSTANCE,
                    "ManagedBy": "infra/deploy_instance.py",
                },
            },
            "20.30.40.50",
            "33333333-3333-3333-3333-333333333333",
            [{"id": assignment_id, "scope": RESOURCE_GROUP_ID}],
            False,
        ]
        with (
            patch.object(TEARDOWN, "run_az") as run_az,
            patch.object(TEARDOWN, "run_az_json", side_effect=responses),
        ):
            result = TEARDOWN.main(self._arguments())

        self.assertEqual(result, 0)
        commands = [call.kwargs["args"] for call in run_az.call_args_list]
        self.assertIn(["role", "assignment", "delete", "--ids", assignment_id], commands)
        self.assertIn(["group", "delete", "--name", RESOURCE_GROUP, "--yes"], commands)
        self.assertFalse(any("--no-wait" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
