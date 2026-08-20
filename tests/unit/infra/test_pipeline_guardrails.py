# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Guard the single-topology Azure DevOps deployment contract."""

import json
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


class PipelineGuardrailTests(unittest.TestCase):
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
        assert self.pipeline.count("scriptPath: '$(Build.SourcesDirectory)/infra/pipelines/deploy_public_nat.sh'") == 2

    def test_production_remains_opt_in_and_independently_approved(self):
        assert "task: ManualValidation@1" in self.pipeline
        assert "onTimeout: reject" in self.pipeline
        assert "approvers: '$(prodApprovers)'" in self.pipeline
        assert "allowApproversToApproveTheirOwnRuns: false" in self.pipeline
        approval_stage = self.pipeline[self.pipeline.index("stage: ApproveProd") : self.pipeline.index("stage: DeployProd")]
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
        assert '@(sha256:[0-9a-fA-F]{64})' in self.deploy_script
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
        assert "enableFrontDoorPrivateLink=" not in self.deploy_script

    def test_pipeline_passes_values_via_environment(self):
        deploy_yaml = self.pipeline[self.pipeline.index("stage: DeployTest") :]
        assert "PYRIT_DEPLOYMENT_RESOURCE_GROUP: $(deploymentResourceGroup)" in deploy_yaml
        assert "PYRIT_CONTAINER_IMAGE: $(immutableImage)" in deploy_yaml
        assert "PYRIT_ALLOWED_CLIENT_CIDR: $(deploymentAllowedClientCidr)" in deploy_yaml
        assert "PYRIT_MANAGED_IDENTITY_RESOURCE_ID: $(managedIdentityResourceId)" in deploy_yaml
        assert '="$(replacement' not in deploy_yaml
        assert "PYRIT_FALLBACK" not in deploy_yaml

    def test_deploy_validates_structured_inputs_before_arm(self):
        assert "ipaddress.ip_network" in self.deploy_script
        assert "subnet.subnet_of(vnet)" in self.deploy_script
        assert "subnet.prefixlen > 27" in self.deploy_script
        assert "uuid.UUID" in self.deploy_script
        assert "Microsoft\\.KeyVault/vaults" in self.deploy_script
        assert "database\\.windows\\.net" in self.deploy_script
        assert self.deploy_script.index("ipaddress.ip_network") < self.deploy_script.index("az deployment group what-if")

    def test_deploy_preserves_existing_network_and_tags(self):
        assert "Front Door cannot use an ACA client CIDR restriction" in self.deploy_script
        assert "Internal deployments must adopt an existing app" in self.deploy_script
        assert '"tags=$deployment_tags"' in self.deploy_script
        assert '"egressPublicIpIpTags=$existing_pip_ip_tags"' in self.deploy_script
        assert '"protectEgressPublicIp=true"' in self.deploy_script
        assert "--result-format FullResourcePayloads" in self.deploy_script
        assert "--expected-pip-id" in self.deploy_script
        assert "--expected-subnet-id" in self.deploy_script
        assert "Reserved egress PIP identity or address changed" in self.deploy_script
        assert self.deploy_script.index("expected_egress_ip=") < self.deploy_script.index("az deployment group what-if")
        assert self.deploy_script.index("actual_pip_id=") > self.deploy_script.index("az deployment group create")

    def test_data_plane_health_probe_respects_ingress_restrictions(self):
        assert "properties.outputs.frontDoorFqdn.value" in self.deploy_script
        assert '"https://$front_door_fqdn/api/health"' in self.deploy_script
        assert '"https://$app_fqdn/api/health"' not in self.deploy_script
        assert "Front Door did not route a healthy response" in self.deploy_script
        assert "ACA origin: https://$app_fqdn" in self.deploy_script

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
            "enableFrontDoorPrivateLink",
        }
        assert unsupported.isdisjoint(example["parameters"])
        assert unsupported.isdisjoint(demo["parameters"])
        assert "vnetAddressPrefix" in example["parameters"]
        assert "infrastructureSubnetAddressPrefix" in example["parameters"]
        assert example["parameters"]["acrName"]["value"]
        assert example["parameters"]["existingManagedIdentityResourceId"]["value"]
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
            {"changeType": "Create", "resourceId": lock_id},
        ]

        result = self._run_what_if_validator(changes)

        assert result.returncode == 0, result.stderr

    def test_what_if_validator_rejects_each_protected_topology_violation(self):
        fixtures: dict[str, tuple[dict[str, object], str]] = {
            "delete": ({"changeType": "Delete", "resourceId": PIP_ID}, "delete"),
            "cross-resource-group": (
                {
                    "changeType": "Modify",
                    "resourceId": f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/other/providers/Microsoft.App/containerApps/app",
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
                    "resourceId": f"{RESOURCE_GROUP_ID}/providers/Microsoft.OperationalInsights/workspaces/copyrit-prod-v2-logs",
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