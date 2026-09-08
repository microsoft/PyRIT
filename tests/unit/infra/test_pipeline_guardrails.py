# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Guard the single-topology Azure DevOps deployment contract."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE = REPO_ROOT / "gui-deploy.yml"
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "pipelines" / "deploy_public_nat.sh"
WHAT_IF_VALIDATOR = REPO_ROOT / "infra" / "pipelines" / "validate_what_if.py"
EXAMPLE_PARAMETERS = REPO_ROOT / "infra" / "parameters.example.json"
DEMO_PARAMETERS = REPO_ROOT / "infra" / "parameters.demo.json"

SUBSCRIPTION_ID = "11111111-1111-1111-1111-111111111111"
RESOURCE_GROUP_ID = f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/copyrit-prod-v2"
PIP_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.Network/publicIPAddresses/copyrit-prod-v2-egress-pip"
NAT_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.Network/natGateways/copyrit-prod-v2-nat"
VNET_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.Network/virtualNetworks/copyrit-prod-v2-vnet"
SUBNET_ID = f"{VNET_ID}/subnets/copyrit-prod-v2-aca-subnet"
ENVIRONMENT_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.App/managedEnvironments/copyrit-prod-v2-env"


def _find_bash() -> str | None:
    """Find a native Bash executable without selecting the Windows WSL shim."""
    if os.name != "nt":
        return shutil.which("bash")

    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/bin/bash.exe",
    ]
    return next((str(candidate) for candidate in candidates if candidate.is_file()), None)


BASH = _find_bash()


class TestPipelineGuardrails(unittest.TestCase):
    """Verify one preview-first test/prod deployment workflow."""

    @classmethod
    def setUpClass(cls):
        cls.pipeline = PIPELINE.read_text(encoding="utf-8")
        cls.deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    def test_pipeline_has_one_test_and_prod_workflow(self):
        assert "deploymentTarget" not in self.pipeline
        assert "applyReplacement" not in self.pipeline
        assert "stage: Build" in self.pipeline
        assert "stage: DeployTest" in self.pipeline
        assert "stage: ApproveProd" in self.pipeline
        assert "stage: DeployProd" in self.pipeline
        assert "DeployReplacement" not in self.pipeline
        assert self.pipeline.count("timeoutInMinutes: 120") == 2
        assert self.pipeline.count("scriptPath: '$(Build.SourcesDirectory)/infra/pipelines/deploy_public_nat.sh'") == 2

    def test_production_remains_opt_in_and_independently_approved(self):
        assert "job: ValidateProdConfiguration" in self.pipeline
        assert "copyrit-gui-prod must define prodApprovers" in self.pipeline
        assert "PROD_APPROVERS: $(prodApprovers)" in self.pipeline
        assert '[[ -z "${PROD_APPROVERS:-}" || "$PROD_APPROVERS" == \'$(\'* ]]' in self.pipeline
        assert "task: ManualValidation@1" in self.pipeline
        assert "onTimeout: reject" in self.pipeline
        assert "approvers: '$(prodApprovers)'" in self.pipeline
        assert "allowApproversToApproveTheirOwnRuns: false" in self.pipeline
        assert "dependsOn: ValidateProdConfiguration" in self.pipeline
        approval_stage = self.pipeline[
            self.pipeline.index("stage: ApproveProd") : self.pipeline.index("stage: DeployProd")
        ]
        assert "- group: copyrit-gui-prod" in approval_stage
        assert '"$BUILD_SOURCEBRANCH" != refs/heads/main' in self.pipeline
        assert "eq(variables['Build.SourceBranch'], 'refs/heads/main')" in self.pipeline
        assert "refs/heads/releases/" not in self.pipeline
        assert "condition: succeeded('ApproveProd')" in self.pipeline

    def test_deploy_resolves_digest_and_previews_before_apply(self):
        assert "name: BuildImage" in self.pipeline
        assert "variable=immutableImage;isOutput=true" in self.pipeline
        assert "stageDependencies.Build.BuildAndPush.outputs['BuildImage.immutableImage']" in self.pipeline
        assert "PYRIT_CONTAINER_IMAGE: $(immutableImage)" in self.pipeline
        assert "@(sha256:[0-9a-fA-F]{64})" in self.deploy_script
        assert 'immutable_image="$registry_server/$repository@$digest"' in self.deploy_script
        assert '"containerImage=$immutable_image"' in self.deploy_script
        assert "az acr repository show" not in self.deploy_script
        assert self.deploy_script.index("az deployment group what-if") < self.deploy_script.index(
            "az deployment group create"
        )
        assert "validate_what_if.py" in self.deploy_script
        assert "cross-resource-group write" in self.deploy_script
        assert "networkMode=" not in self.deploy_script
        assert "enablePrivateEndpoint=" not in self.deploy_script
        assert '"enableFrontDoor=true"' in self.deploy_script
        assert '"enableFrontDoorPrivateLink=true"' in self.deploy_script
        assert '"frontDoorPrivateLinkRequestMessage=$private_link_request_message"' in self.deploy_script
        assert '"disableContainerAppsPublicAccess=true"' in self.deploy_script

    def test_pipeline_passes_values_via_environment(self):
        deploy_yaml = self.pipeline[self.pipeline.index("stage: DeployTest") :]
        assert "PYRIT_DEPLOYMENT_RESOURCE_GROUP: $(deploymentResourceGroup)" in deploy_yaml
        assert "PYRIT_CONTAINER_IMAGE: $(immutableImage)" in deploy_yaml
        assert "PYRIT_ALLOWED_CLIENT_CIDR: $(deploymentAllowedClientCidr)" in deploy_yaml
        assert "PYRIT_MANAGED_IDENTITY_RESOURCE_ID: $(managedIdentityResourceId)" in deploy_yaml
        assert "PYRIT_ADMIN_GROUP_OBJECT_ID: $(adminGroupObjectId)" in deploy_yaml
        assert "PYRIT_CONFIG_FILE_URI: $(pyritConfigFileUri)" in deploy_yaml
        assert '"adminGroupObjectId=$PYRIT_ADMIN_GROUP_OBJECT_ID"' in self.deploy_script
        assert '"pyritConfigFileUri=${PYRIT_CONFIG_FILE_URI:-}"' in self.deploy_script
        assert '="$(replacement' not in deploy_yaml
        assert "PYRIT_FALLBACK" not in deploy_yaml

    def test_deploy_validates_structured_inputs_before_arm(self):
        assert re.search(r"\$\{[a-zA-Z_][a-zA-Z0-9_]*(?:\[[^]]*\])?(?:,,?|\^\^?)", self.deploy_script) is None
        for unsupported_construct in ("declare -A", "mapfile", "readarray", "coproc", "&>>", ";;&"):
            assert unsupported_construct not in self.deploy_script
        assert "lowercase()" in self.deploy_script
        assert "ipaddress.ip_network" in self.deploy_script
        assert "subnet.subnet_of(vnet)" in self.deploy_script
        assert "subnet.prefixlen > 27" in self.deploy_script
        assert "uuid.UUID" in self.deploy_script
        assert "normalized_managed_identity_resource_id=" in self.deploy_script
        assert "normalized_key_vault_resource_id=" in self.deploy_script
        assert "microsoft\\.keyvault/vaults" in self.deploy_script
        assert "database\\.windows\\.net" in self.deploy_script
        assert self.deploy_script.index("ipaddress.ip_network") < self.deploy_script.index(
            "az deployment group what-if"
        )

    def test_deploy_preserves_existing_network_and_tags(self):
        assert "Front Door cannot use an ACA client CIDR restriction" in self.deploy_script
        assert "Internal deployments must adopt an existing app" in self.deploy_script
        assert '"tags=$deployment_tags"' in self.deploy_script
        assert '"egressPublicIpTags=$existing_pip_ip_tags"' in self.deploy_script
        assert '"protectEgressPublicIp=true"' in self.deploy_script
        assert "--result-format FullResourcePayloads" in self.deploy_script
        assert "--expected-pip-id" in self.deploy_script
        assert "--expected-subnet-id" in self.deploy_script
        assert "--expected-environment-id" in self.deploy_script
        assert "Reserved egress PIP identity or address changed" in self.deploy_script
        assert self.deploy_script.index("expected_egress_ip=") < self.deploy_script.index("az deployment group what-if")
        assert self.deploy_script.index("actual_pip_id=") > self.deploy_script.index("az deployment group create")

    def test_data_plane_health_probe_respects_ingress_restrictions(self):
        assert "properties.outputs.frontDoorFqdn.value" in self.deploy_script
        assert '"https://$front_door_fqdn/api/health"' in self.deploy_script
        assert "direct_aca_health=$(curl" in self.deploy_script
        assert '"https://$app_fqdn/api/health"' in self.deploy_script
        assert '[[ "$direct_aca_health" == "200" ]]' in self.deploy_script
        assert "Front Door did not route a healthy response" in self.deploy_script
        assert "aca_private_endpoint_approval.bicep" in self.deploy_script
        assert "connection_suffix=${connection_name:0:8}" in self.deploy_script
        assert '"$deployment_name-private-link-approval-$connection_suffix"' in self.deploy_script
        assert '"approvalDescription=$private_link_request_message"' in self.deploy_script
        assert "properties.sharedPrivateLinkResource.status" in self.deploy_script
        assert "properties.sharedPrivateLinkResource.privateLink.id" in self.deploy_script
        assert "providers/Microsoft.Cdn/profiles" in self.deploy_script
        assert "api-version=2024-09-01" in self.deploy_script
        assert "az afd" not in self.deploy_script
        assert "approved_connection_count=" in self.deploy_script
        assert "ACA approval and AFD health determine readiness" in self.deploy_script
        assert "cutover_in_progress=true" in self.deploy_script
        assert '"$deployment_name-rollback-origin"' in self.deploy_script
        assert "az rest --method delete" in self.deploy_script
        assert '"$deployment_name-rollback"' in self.deploy_script
        assert '"${rollback_parameters[@]}"' in self.deploy_script
        assert "trap 'exit 143' TERM" in self.deploy_script
        assert "trap 'exit 130' INT" in self.deploy_script
        assert "trap - EXIT TERM INT" in self.deploy_script
        deletion_guard = 'if [[ "$rollback_connection_count" != "0" ]]'
        assert deletion_guard in self.deploy_script
        assert "public access remains disabled and manual recovery is required" in self.deploy_script
        assert self.deploy_script.index(deletion_guard) < self.deploy_script.index(
            'if az deployment group create \\\n      --name "$deployment_name-rollback"'
        )
        assert "front_door_health_timeout_seconds=1800" in self.deploy_script
        assert "front_door_health_deadline=$((SECONDS + front_door_health_timeout_seconds))" in self.deploy_script
        assert "while ((SECONDS < front_door_health_deadline))" in self.deploy_script
        assert "budget before request" in self.deploy_script
        assert "Front Door health attempt $attempt/60" not in self.deploy_script
        assert "Direct ACA public access remains reachable" in self.deploy_script
        assert "ACA public access: disabled" in self.deploy_script

    def _run_cancellation_rollback(self, *, connection_count: int) -> tuple[subprocess.CompletedProcess[str], str]:
        assert BASH is not None
        lowercase_start = self.deploy_script.index("lowercase() {")
        lowercase_end = self.deploy_script.index("\n}\n", lowercase_start) + len("\n}\n")
        function_start = self.deploy_script.index("rollback_public_origin() {")
        trap_start = self.deploy_script.index("trap rollback_public_origin EXIT", function_start)
        trap_end = self.deploy_script.index("cutover_in_progress=true", trap_start)
        lowercase_function = self.deploy_script[lowercase_start:lowercase_end]
        rollback_function = self.deploy_script[function_start:trap_start]
        trap_setup = self.deploy_script[trap_start:trap_end]

        with tempfile.TemporaryDirectory() as directory:
            call_log = Path(directory) / "az-calls.log"
            harness = f"""
set -euo pipefail
{lowercase_function}
cutover_in_progress=false
private_link_request_message='Azure Front Door private access to copyrit-test'
PYRIT_DEPLOYMENT_RESOURCE_GROUP='copyrit-test-rg'
PYRIT_APP_NAME='copyrit-test'
PYRIT_SOURCE_DIRECTORY='/repo'
expected_environment_id='/subscriptions/test/resourceGroups/copyrit-test-rg/providers/Microsoft.App/managedEnvironments/copyrit-test-env'
normalized_expected_environment_id=$(lowercase "$expected_environment_id")
deployment_name='pyrit-test-1'
deployment_tags='{{}}'
rollback_parameters=('disableContainerAppsPublicAccess=false')

az() {{
    printf '%s\n' "$*" >> "$AZ_CALLS"
    case "$1 $2 $3" in
        'containerapp show --resource-group') printf '%s\n' 'copyrit-test.example' ;;
        'network private-endpoint-connection list') printf '%s\n' '[]' ;;
    esac
}}
jq() {{
    if [[ " $* " == *' -r '* ]]; then
        printf '%s\n' "$expected_environment_id/privateEndpointConnections/connection-1"
    else
        printf '%s\n' '{connection_count}'
    fi
}}
sleep() {{ :; }}

{rollback_function}
{trap_setup}
cutover_in_progress=true
kill -TERM $$
"""
            environment = os.environ.copy()
            environment["AZ_CALLS"] = str(call_log)
            result = subprocess.run(
                [BASH, "-s"],
                input=harness,
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

            calls = call_log.read_text(encoding="utf-8")

        return result, calls

    @unittest.skipIf(BASH is None, "Native Bash is not installed")
    def test_cancellation_rolls_back_without_reenabling_public_access_while_connection_exists(self):
        result, calls = self._run_cancellation_rollback(connection_count=1)

        assert result.returncode == 143
        assert "Private Link cutover failed" in result.stdout
        assert "private endpoint connection deletion was not confirmed" in result.stdout
        assert re.search(r"--name pyrit-test-1-rollback(?:\s|$)", calls) is None

    @unittest.skipIf(BASH is None, "Native Bash is not installed")
    def test_cancellation_reenables_public_access_after_connection_deletion(self):
        result, calls = self._run_cancellation_rollback(connection_count=0)

        assert result.returncode == 143
        assert "Public ACA origin rollback completed" in result.stdout
        assert "rest --method delete" in calls
        assert re.search(r"--name pyrit-test-1-rollback(?:\s|$)", calls)
        assert "disableContainerAppsPublicAccess=false" in calls

    def test_manual_parameter_files_use_the_single_topology(self):
        example = json.loads(EXAMPLE_PARAMETERS.read_text(encoding="utf-8"))
        demo = json.loads(DEMO_PARAMETERS.read_text(encoding="utf-8"))

        assert "_comment_resources" in example
        assert "_comment_resources" not in example["parameters"]
        unsupported = {
            "enablePrivateEndpoint",
            "networkMode",
            "infrastructureNsgName",
            "applicationGatewayNsgName",
        }
        assert unsupported.isdisjoint(example["parameters"])
        assert unsupported.isdisjoint(demo["parameters"])
        assert "vnetAddressPrefix" in example["parameters"]
        assert "infrastructureSubnetAddressPrefix" in example["parameters"]
        assert example["parameters"]["acrName"]["value"]
        assert example["parameters"]["existingManagedIdentityResourceId"]["value"]
        assert example["parameters"]["enableFrontDoorPrivateLink"]["value"] is False
        assert example["parameters"]["frontDoorPrivateLinkRequestMessage"]["value"].endswith(
            example["parameters"]["appName"]["value"]
        )
        assert example["parameters"]["disableContainerAppsPublicAccess"]["value"] is False
        assert demo["parameters"]["enableFrontDoorPrivateLink"]["value"] is False
        assert demo["parameters"]["frontDoorPrivateLinkRequestMessage"]["value"].endswith(
            demo["parameters"]["appName"]["value"]
        )
        assert demo["parameters"]["disableContainerAppsPublicAccess"]["value"] is False
        assert demo["parameters"]["existingManagedIdentityResourceId"]["value"]

    def _run_what_if_validator(
        self,
        changes: list[dict[str, object]],
        *,
        expected_subnet_id: str = SUBNET_ID,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            what_if_file = Path(directory) / "what-if.json"
            what_if_file.write_text(json.dumps({"changes": changes}), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(WHAT_IF_VALIDATOR),
                    "--what-if-file",
                    str(what_if_file),
                    "--deployment-resource-group-id",
                    RESOURCE_GROUP_ID,
                    "--expected-pip-id",
                    PIP_ID,
                    "--expected-nat-id",
                    NAT_ID,
                    "--expected-vnet-id",
                    VNET_ID,
                    "--expected-subnet-id",
                    expected_subnet_id,
                    "--expected-environment-id",
                    ENVIRONMENT_ID,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_what_if_validator_accepts_read_only_normalization_and_lock_create(self):
        lock_id = f"{PIP_ID}/providers/Microsoft.Authorization/locks/copyrit-prod-v2-egress-pip-lock"
        changes: list[dict[str, object]] = [
            {
                "changeType": "Modify",
                "resourceId": NAT_ID,
                "delta": [{"path": "properties.scope"}, {"path": "sku.tier"}],
            },
            {"changeType": "Modify", "resourceId": PIP_ID, "delta": [{"path": "sku.tier"}]},
            {
                "changeType": "Modify",
                "resourceId": ENVIRONMENT_ID,
                "delta": [
                    {"path": "properties.appLogsConfiguration.logAnalyticsConfiguration.customerId"},
                    {"path": "properties.publicNetworkAccess"},
                ],
            },
            {"changeType": "Create", "resourceId": lock_id},
        ]

        result = self._run_what_if_validator(changes)

        assert result.returncode == 0, result.stderr

    def test_what_if_validator_rejects_each_protected_topology_violation(self):
        other_resource_group_id = f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/other"
        workspace_provider_id = f"{RESOURCE_GROUP_ID}/providers/Microsoft.OperationalInsights"
        fixtures: dict[str, tuple[dict[str, object], str]] = {
            "delete": ({"changeType": "Delete", "resourceId": PIP_ID}, "delete"),
            "cross-resource-group": (
                {
                    "changeType": "Modify",
                    "resourceId": f"{other_resource_group_id}/providers/Microsoft.App/containerApps/app",
                    "delta": [{"path": "properties.configuration"}],
                },
                "cross-resource-group write",
            ),
            "protected-subnet": (
                {
                    "changeType": "Modify",
                    "resourceId": SUBNET_ID,
                    "delta": [{"path": "properties.addressPrefix"}],
                },
                "protected-resource delta",
            ),
            "opaque-protected-change": ({"changeType": "Modify", "resourceId": VNET_ID}, "opaque"),
            "protected-environment": (
                {
                    "changeType": "Modify",
                    "resourceId": ENVIRONMENT_ID,
                    "delta": [{"path": "properties.vnetConfiguration.internal"}],
                },
                "protected-resource delta",
            ),
            "container-app-create": (
                {
                    "changeType": "Create",
                    "resourceId": f"{RESOURCE_GROUP_ID}/providers/Microsoft.App/containerApps/replacement",
                },
                "core resource create",
            ),
            "workspace-create": (
                {
                    "changeType": "Create",
                    "resourceId": f"{workspace_provider_id}/workspaces/copyrit-prod-v2-logs",
                },
                "core resource create",
            ),
        }

        for name, (change, expected_error) in fixtures.items():
            with self.subTest(name=name):
                result = self._run_what_if_validator([change])
                assert result.returncode == 1
                assert expected_error in result.stderr

    def test_what_if_validator_normalizes_expected_protected_resource_ids(self):
        change: dict[str, object] = {
            "changeType": "Modify",
            "resourceId": SUBNET_ID,
            "delta": [{"path": "properties.addressPrefix"}],
        }

        result = self._run_what_if_validator([change], expected_subnet_id=f"{SUBNET_ID}/")

        assert result.returncode == 1
        assert "protected-resource delta" in result.stderr


if __name__ == "__main__":
    unittest.main()
