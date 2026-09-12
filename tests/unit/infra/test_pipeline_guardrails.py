# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Guard pipeline wiring and deployment policies without modeling Azure."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE = REPO_ROOT / "gui-deploy.yml"
STAGE_TEMPLATE = REPO_ROOT / "infra" / "pipelines" / "deploy-stage.yml"
WHAT_IF_VALIDATOR = REPO_ROOT / "infra" / "pipelines" / "validate_what_if.py"
RESOURCE_GROUP_ID = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/copyrit-prod-v2"
APP_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.App/containerApps/copyrit-prod-v2"
ENVIRONMENT_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.App/managedEnvironments/copyrit-prod-v2-env"
PIP_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.Network/publicIPAddresses/copyrit-prod-v2-egress-pip"
NAT_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.Network/natGateways/copyrit-prod-v2-nat"
VNET_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.Network/virtualNetworks/copyrit-prod-v2-vnet"
SUBNET_ID = f"{VNET_ID}/subnets/copyrit-prod-v2-aca-subnet"


class TestPipelineGuardrails(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipeline_text = PIPELINE.read_text(encoding="utf-8")
        cls.pipeline = yaml.safe_load(cls.pipeline_text)
        cls.template = yaml.safe_load(STAGE_TEMPLATE.read_text(encoding="utf-8"))
        cls.deployments = {
            stage["parameters"]["stageName"]: stage["parameters"]
            for stage in cls.pipeline["stages"]
            if "template" in stage
        }

    def test_published_images_require_verified_clean_source_provenance(self) -> None:
        assert "source_commit=$(git rev-parse HEAD)" in self.pipeline_text
        assert '"$source_commit" != "$PYRIT_SOURCE_VERSION"' in self.pipeline_text
        assert '-n "$(git status --porcelain)"' in self.pipeline_text
        assert '--build-arg GIT_COMMIT="$source_commit"' in self.pipeline_text
        assert "--build-arg GIT_MODIFIED=false" in self.pipeline_text
        assert self.pipeline_text.index("source_commit=$(git rev-parse HEAD)") < self.pipeline_text.index("docker build")

    def test_all_deployment_stages_share_one_template_and_remain_visible(self) -> None:
        stages = self.pipeline["stages"]
        assert [stage.get("stage") or stage["parameters"]["stageName"] for stage in stages] == [
            "ValidateInputs",
            "Build",
            "DeployTestInfra",
            "DeployTest",
            "ApproveProd",
            "DeployProdInfra",
            "DeployProd",
        ]
        calls = [stage for stage in stages if "template" in stage]
        assert len(calls) == 4
        assert all(stage["template"] == "infra/pipelines/deploy-stage.yml" for stage in calls)
        parameters = {parameter["name"]: parameter for parameter in self.template["parameters"]}
        assert parameters["phase"]["values"] == ["infra", "app"]
        assert parameters["slot"]["values"] == ["test", "prod"]
        assert all(set(call["parameters"]) == set(parameters) for call in calls)
        for name, slot, phase, job in (
            ("DeployTestInfra", "test", "infra", "DeployInfrastructure"),
            ("DeployTest", "test", "app", "DeployToTest"),
            ("DeployProdInfra", "prod", "infra", "DeployInfrastructure"),
            ("DeployProd", "prod", "app", "DeployToProd"),
        ):
            assert self.deployments[name]["slot"] == slot
            assert self.deployments[name]["phase"] == phase
            assert self.deployments[name]["deploymentName"] == job

    def test_template_preserves_ado_deployment_job_and_script_path(self) -> None:
        stage = self.template["stages"][0]
        assert stage["stage"] == "${{ parameters.stageName }}"
        assert stage["dependsOn"] == "${{ parameters.dependsOn }}"
        assert stage["condition"] == "${{ parameters.condition }}"
        assert stage["variables"][:2] == [
            {"group": "copyrit-gui-common"},
            {"group": "copyrit-gui-${{ parameters.slot }}"},
        ]
        job = stage["jobs"][0]
        assert job["deployment"] == "${{ parameters.deploymentName }}"
        assert job["environment"] == "copyrit-${{ parameters.slot }}"
        assert job["timeoutInMinutes"] == 120
        steps = job["strategy"]["runOnce"]["deploy"]["steps"]
        assert steps[0] == {"checkout": "self", "fetchDepth": 1}
        assert steps[1]["task"] == "AzureCLI@2"
        assert steps[1]["inputs"] == {
            "azureSubscription": "$(azureServiceConnection)",
            "scriptType": "bash",
            "scriptLocation": "scriptPath",
            "scriptPath": "$(Build.SourcesDirectory)/infra/pipelines/deploy_${{ parameters.phase }}.sh",
        }

    def test_each_phase_receives_only_its_configuration(self) -> None:
        stage = self.template["stages"][0]
        task = stage["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"][1]
        env = task["env"]
        assert env["PYRIT_SLOT"] == "${{ parameters.slot }}"
        assert env["PYRIT_DEPLOYMENT_RESOURCE_GROUP"] == "$(deploymentResourceGroup)"
        assert env["PYRIT_APP_NAME"] == "$(deploymentAppName)"
        assert env["PYRIT_MANAGED_IDENTITY_RESOURCE_ID"] == "$(managedIdentityResourceId)"
        assert env["PYRIT_ACR_RESOURCE_ID"] == "$(acrResourceId)"
        assert "PYRIT_DEPLOY_INFRA" not in env
        infra = env["${{ if eq(parameters.phase, 'infra') }}"]
        app = env["${{ else }}"]
        assert set(infra) == {"PYRIT_VNET_ADDRESS_PREFIX", "PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX"}
        assert set(app) == {
            "PYRIT_CONTAINER_IMAGE",
            "PYRIT_ALLOWED_CLIENT_CIDR",
            "PYRIT_ENTRA_TENANT_ID",
            "PYRIT_ENTRA_CLIENT_ID",
            "PYRIT_ALLOWED_GROUP_OBJECT_IDS",
            "PYRIT_ADMIN_GROUP_OBJECT_ID",
            "PYRIT_CONFIG_FILE_URI",
            "PYRIT_SQL_SERVER_FQDN",
            "PYRIT_SQL_DATABASE_NAME",
            "PYRIT_KEY_VAULT_RESOURCE_ID",
            "PYRIT_ENV_SECRET_NAME",
        }
        assert app["PYRIT_CONTAINER_IMAGE"] == "$(immutableImage)"
        image = stage["variables"][2]["${{ if eq(parameters.phase, 'app') }}"][0]
        assert image == {
            "name": "immutableImage",
            "value": "$[ stageDependencies.Build.BuildAndPush.outputs['BuildImage.immutableImage'] ]",
        }
        assert "variable=immutableImage;isOutput=true" in self.pipeline_text
        assert 'immutable_image="$PYRIT_ACR_LOGIN_SERVER/$PYRIT_IMAGE_NAME@$digest"' in self.pipeline_text

    def test_runtime_conditions_preserve_intentional_skip_and_failure_gates(self) -> None:
        parameters = {parameter["name"]: parameter for parameter in self.pipeline["parameters"]}
        assert parameters["deployInfra"]["default"] is False
        assert parameters["deployToProd"]["default"] is False
        for name, dependencies in (
            ("DeployTestInfra", ["Build"]),
            ("DeployProdInfra", ["ApproveProd"]),
        ):
            stage = self.deployments[name]
            assert stage["dependsOn"] == dependencies
            assert stage["condition"] == "and(succeeded(), eq('${{ parameters.deployInfra }}', 'true'))"
        for name, dependencies in (
            ("DeployTest", ["Build", "DeployTestInfra"]),
            ("DeployProd", ["ApproveProd", "Build", "DeployProdInfra"]),
        ):
            stage = self.deployments[name]
            assert stage["dependsOn"] == dependencies
            approval = (
                "in(dependencies.ApproveProd.result, 'Succeeded', 'SucceededWithIssues'),"
                if name == "DeployProd"
                else ""
            )
            expected = (
                "and(not(canceled()), in(dependencies.Build.result, 'Succeeded', 'SucceededWithIssues'),"
                f"{approval}or(in(dependencies.{name}Infra.result, 'Succeeded', 'SucceededWithIssues'),"
                "and(eq('${{ parameters.deployInfra }}', 'false'),"
                f"eq(dependencies.{name}Infra.result, 'Skipped'))))"
            )
            assert "".join(stage["condition"].split()) == "".join(expected.split())

    def test_production_remains_main_only_opt_in_and_independently_approved(self) -> None:
        stage = next(stage for stage in self.pipeline["stages"] if stage.get("stage") == "ApproveProd")
        assert stage["dependsOn"] == "DeployTest"
        assert stage["condition"] == (
            "and(succeeded('DeployTest'), eq('${{ parameters.deployToProd }}', 'true'), "
            "eq(variables['Build.SourceBranch'], 'refs/heads/main'))"
        )
        assert stage["variables"] == [{"group": "copyrit-gui-prod"}]
        validation, approval = stage["jobs"]
        assert validation["job"] == "ValidateProdConfiguration"
        assert validation["steps"][1]["env"] == {"PROD_APPROVERS": "$(prodApprovers)"}
        assert "copyrit-gui-prod must define prodApprovers" in validation["steps"][1]["bash"]
        assert approval["dependsOn"] == "ValidateProdConfiguration"
        assert approval["pool"] == "server"
        task = approval["steps"][0]
        assert task["task"] == "ManualValidation@1"
        assert task["inputs"]["onTimeout"] == "reject"
        assert task["inputs"]["approvers"] == "$(prodApprovers)"
        assert task["inputs"]["allowApproversToApproveTheirOwnRuns"] is False
        assert '"$BUILD_SOURCEBRANCH" != refs/heads/main' in self.pipeline_text

    def test_community_parameter_files_are_phase_specific(self) -> None:
        infrastructure = json.loads(
            (REPO_ROOT / "infra" / "parameters.infrastructure.example.json").read_text(encoding="utf-8")
        )["parameters"]
        application = json.loads(
            (REPO_ROOT / "infra" / "parameters.application.example.json").read_text(encoding="utf-8")
        )["parameters"]

        assert "containerImage" not in infrastructure
        assert infrastructure["existingManagedIdentityResourceId"]["value"]
        assert infrastructure["enableFrontDoorPrivateLink"]["value"] is False
        assert infrastructure["disableContainerAppsPublicAccess"]["value"] is False
        assert application["containerImage"]["value"]
        assert application["existingManagedIdentityResourceId"]["value"]
        assert "enableFrontDoorPrivateLink" not in application
        assert "disableContainerAppsPublicAccess" not in application


class TestWhatIfPolicies(unittest.TestCase):
    def _run_validator(
        self,
        *,
        payload: object,
        mode: str | None = None,
        app_id: str | None = APP_ID,
        subnet_id: str = SUBNET_ID,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "what-if.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            arguments = [
                sys.executable,
                str(WHAT_IF_VALIDATOR),
                "--what-if-file",
                str(path),
                "--deployment-resource-group-id",
                RESOURCE_GROUP_ID,
                "--expected-pip-id",
                PIP_ID,
                "--expected-nat-id",
                NAT_ID,
                "--expected-vnet-id",
                VNET_ID,
                "--expected-subnet-id",
                subnet_id,
                "--expected-environment-id",
                ENVIRONMENT_ID,
            ]
            if mode is not None:
                arguments += ["--deployment-mode", mode]
            if app_id is not None:
                arguments += ["--expected-app-id", app_id]
            return subprocess.run(arguments, capture_output=True, text=True, check=False)

    def test_infrastructure_accepts_read_only_normalization_and_lock_create(self) -> None:
        changes = [
            {
                "changeType": "Modify",
                "resourceId": NAT_ID,
                "delta": [{"path": "properties.scope"}, {"path": "sku.tier"}],
            },
            {"changeType": "Modify", "resourceId": PIP_ID, "delta": [{"path": "sku.tier"}]},
            {
                "changeType": "Modify",
                "resourceId": ENVIRONMENT_ID,
                "delta": [{"path": "properties.publicNetworkAccess"}],
            },
            {"changeType": "Create", "resourceId": f"{PIP_ID}/providers/Microsoft.Authorization/locks/egress-lock"},
        ]
        result = self._run_validator(payload={"changes": changes}, mode="infra")
        assert result.returncode == 0, result.stderr

    def test_phase_write_boundaries(self) -> None:
        for mode, resource_id, change_type, accepted in (
            ("app", APP_ID, "Modify", True),
            ("app", APP_ID.upper() + "/", "Modify", True),
            ("app", ENVIRONMENT_ID, "NoChange", True),
            ("app", APP_ID + "/authConfigs/current", "Modify", False),
            ("app", f"{RESOURCE_GROUP_ID}/providers/Microsoft.Cdn/profiles/front-door", "Create", False),
            ("infra", APP_ID, "Ignore", True),
            ("infra", APP_ID, "NoChange", True),
            ("infra", APP_ID.upper() + "/", "Modify", False),
            ("infra", APP_ID + "/authConfigs/current", "Modify", False),
        ):
            with self.subTest(mode=mode, resource_id=resource_id, change_type=change_type):
                result = self._run_validator(
                    payload={"changes": [{"resourceId": resource_id, "changeType": change_type}]}, mode=mode
                )
                assert (result.returncode == 0) is accepted, result.stderr

    def test_protected_topology_violations_are_rejected(self) -> None:
        cases = [
            ({"changeType": "Delete", "resourceId": PIP_ID}, "delete"),
            (
                {
                    "changeType": "Modify",
                    "resourceId": APP_ID.replace("resourceGroups/copyrit-prod-v2", "resourceGroups/other"),
                },
                "cross-resource-group",
            ),
            (
                {"changeType": "Modify", "resourceId": SUBNET_ID, "delta": [{"path": "properties.addressPrefix"}]},
                "protected-resource delta",
            ),
            ({"changeType": "Modify", "resourceId": VNET_ID}, "opaque"),
            (
                {
                    "changeType": "Modify",
                    "resourceId": ENVIRONMENT_ID,
                    "delta": [{"path": "properties.vnetConfiguration.internal"}],
                },
                "protected-resource delta",
            ),
            ({"changeType": "Create", "resourceId": APP_ID}, "core resource create"),
            (
                {
                    "changeType": "Create",
                    "resourceId": f"{RESOURCE_GROUP_ID}/providers/Microsoft.OperationalInsights/workspaces/new",
                },
                "core resource create",
            ),
        ]
        for change, message in cases:
            with self.subTest(change=change):
                result = self._run_validator(payload={"changes": [change]}, subnet_id=SUBNET_ID + "/")
                assert result.returncode == 1
                assert message in result.stderr

    def test_phase_requires_an_app_id_in_the_deployment_scope(self) -> None:
        for app_id in (None, APP_ID.replace("resourceGroups/copyrit-prod-v2", "resourceGroups/other"), ENVIRONMENT_ID):
            with self.subTest(app_id=app_id):
                result = self._run_validator(payload={"changes": []}, mode="app", app_id=app_id)
                assert result.returncode == 2
                assert "requires an expected app ID" in result.stderr

    def test_malformed_what_if_payloads_fail_closed(self) -> None:
        for payload in ({}, {"changes": None}, {"changes": [None]}, {"changes": [{"changeType": "Modify"}]}):
            with self.subTest(payload=payload):
                result = self._run_validator(payload=payload)
                assert result.returncode == 2
                assert "validation failed closed" in result.stderr


if __name__ == "__main__":
    unittest.main()
