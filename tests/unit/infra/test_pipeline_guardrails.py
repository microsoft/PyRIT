# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Guard pipeline wiring and deployment policies without modeling Azure."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.infra.test_code_deployment import BASH

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE = REPO_ROOT / "gui-deploy.yml"
STAGE_TEMPLATE = REPO_ROOT / "infra" / "pipelines" / "deploy-stage.yml"
WHAT_IF_VALIDATOR = REPO_ROOT / "infra" / "pipelines" / "validate_what_if.py"
RESOURCE_GROUP_ID = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/copyrit-prod-v2"
APP_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.App/containerApps/copyrit-prod-v2"
JOB_ID = f"{RESOURCE_GROUP_ID}/providers/Microsoft.App/jobs/copyrit-prod-v2-migrate"
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

    def test_deployment_runs_in_the_morning_and_manually_instead_of_on_merges(self) -> None:
        assert self.pipeline["trigger"] == "none"
        assert self.pipeline["pr"] == "none"
        (schedule,) = self.pipeline["schedules"]
        assert schedule["cron"] == "0 15 * * *"
        assert schedule["branches"] == {"include": ["main"]}
        assert schedule["always"] is True
        assert "resources" not in self.pipeline

    @unittest.skipIf(BASH is None, "Native Bash is required")
    def test_source_guards_before_build_or_private_access(self) -> None:
        stage = next(stage for stage in self.pipeline["stages"] if stage.get("stage") == "ValidateInputs")
        step = next(step for step in stage["jobs"][0]["steps"] if step.get("bash"))
        environment = {key: value for key, value in os.environ.items() if key != "BASH_ENV"}
        main, feature = "refs/heads/main", "refs/heads/feature"
        for production, profile, version, run, public_ref, repo, rejected in (
            ("True", "False", "main", "latest", main, "microsoft/PyRIT", False),
            ("true", "False", "1.2.3", "latest", feature, "microsoft/PyRIT", True),
            ("False", "False", "main", "latest", feature, "microsoft/PyRIT", False),
            ("False", "True", "main", "latest", feature, "microsoft/PyRIT", True),
            ("False", "True", "main", "123", main, "fork/PyRIT", True),
            ("False", "True", "main", "latest", main, "microsoft/PyRIT", False),
            ("False", "True", "1.2.3", "latest", main, "microsoft/PyRIT", True),
            ("True", "true", "1.2.3", "123", main, "microsoft/PyRIT", False),
            ("False", "False", "1.2.3rc1", "latest", main, "microsoft/PyRIT", True),
            ("False", "True", "main", "0", main, "microsoft/PyRIT", True),
            ("False", "True", "main", "1.2", main, "microsoft/PyRIT", True),
        ):
            with self.subTest(production=production, profile=profile, version=version, run=run, ref=public_ref):
                result = subprocess.run(
                    [str(BASH), "--noprofile", "--norc", "-s"],
                    input=step["bash"],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=TEST_WATCHDOG_SECONDS,
                    env={
                        **environment,
                        "DEPLOY_TO_PROD": production,
                        "DEPLOY_TO_TEST": "True",
                        "USE_PROFILE": profile,
                        "PYRIT_VERSION": version,
                        "PROFILE_RUN": run,
                        "BUILD_SOURCEBRANCH": public_ref,
                        "BUILD_REPOSITORY_NAME": repo,
                    },
                )
                assert (result.returncode != 0) is rejected, result.stdout + result.stderr

    def test_build_reclaims_docker_space_before_build_and_post_job_scanning(self) -> None:
        stage = next(stage for stage in self.pipeline["stages"] if stage.get("stage") == "Build")
        assert stage["pool"] == {"vmImage": "ubuntu-latest"}
        steps = stage["jobs"][0]["steps"]
        build_index = next(index for index, step in enumerate(steps) if step.get("name") == "BuildImage")
        before, after = steps[build_index - 1], steps[-1]
        assert before["displayName"] == "Reclaim Docker disk space before build"
        assert after["displayName"] == "Reclaim Docker disk space after build"
        assert after["condition"] == "always()"
        assert steps[-1] == after
        for cleanup in (before, after):
            assert not cleanup.get("continueOnError", False)
            commands = cleanup["bash"].splitlines()
            assert commands == [
                "set -euo pipefail",
                "df -h /",
                "docker system df",
                "docker system prune --all --force",
                "df -h /",
            ]

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
        required = {name for name, spec in parameters.items() if "default" not in spec}
        assert all(required <= set(call["parameters"]) <= set(parameters) for call in calls)
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
        assert stage["lockBehavior"] == "sequential"
        assert stage["variables"][:2] == [
            {"group": "copyrit-gui-common"},
            {"group": "copyrit-gui-${{ parameters.slot }}"},
        ]
        job = stage["jobs"][0]
        assert job["deployment"] == "${{ parameters.deploymentName }}"
        assert job["environment"] == "copyrit-${{ parameters.slot }}"
        assert job["timeoutInMinutes"] == 180
        steps = job["strategy"]["runOnce"]["deploy"]["steps"]
        assert steps[0] == {"checkout": "self", "fetchDepth": 1}
        assert steps[-1]["task"] == "AzureCLI@2"
        assert steps[-1]["inputs"] == {
            "azureSubscription": "$(azureServiceConnection)",
            "scriptType": "bash",
            "scriptLocation": "scriptPath",
            "scriptPath": "$(Build.SourcesDirectory)/infra/pipelines/deploy_${{ parameters.phase }}.sh",
        }

    def test_each_phase_receives_only_its_configuration(self) -> None:
        stage = self.template["stages"][0]
        task = stage["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"][-1]
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
            "${{ if parameters.useInternalDeploymentProfile }}",
            "${{ else }}",
            "PYRIT_SQL_SERVER_FQDN",
            "PYRIT_SQL_DATABASE_NAME",
            "PYRIT_KEY_VAULT_RESOURCE_ID",
            "PYRIT_ENV_SECRET_NAME",
        }
        assert app["PYRIT_CONTAINER_IMAGE"] == "$(immutableImage)"
        assert app["${{ if parameters.useInternalDeploymentProfile }}"] == {"PYRIT_CONFIG_FILE_URI": ""}
        assert app["${{ else }}"] == {"PYRIT_CONFIG_FILE_URI": "$(pyritConfigFileUri)"}
        image = stage["variables"][2]["${{ if eq(parameters.phase, 'app') }}"][0]
        assert image == {
            "name": "immutableImage",
            "value": "$[ stageDependencies.Build.BuildAndPush.outputs['BuildImage.immutableImage'] ]",
        }

    def test_private_artifact_read_is_opt_in_and_pinned_once(self) -> None:
        parameters = {parameter["name"]: parameter for parameter in self.pipeline["parameters"]}
        assert parameters["useInternalDeploymentProfile"]["default"] is False
        assert parameters["profileRun"]["default"] == "latest"
        assert parameters["pyritVersion"]["default"] == "main"
        build = next(stage for stage in self.pipeline["stages"] if stage.get("stage") == "Build")
        assert build["dependsOn"] == "ValidateInputs"
        steps = build["jobs"][0]["steps"]
        private = "${{ if parameters.useInternalDeploymentProfile }}"
        resolver, artifact = steps[1][private]
        assert resolver["env"]["SYSTEM_ACCESSTOKEN"] == "$(System.AccessToken)"
        assert self.pipeline_text.count("$(System.AccessToken)") == 1
        assert artifact["task"] == "DownloadPipelineArtifact@2"
        assert artifact["inputs"] == {
            "buildType": "specific",
            "project": "$(System.TeamProjectId)",
            "definition": resolver["env"]["PROFILE_DEFINITION"],
            "buildVersionToDownload": "specific",
            "pipelineId": "$(ResolveProfile.runId)",
            "artifactName": "deployment-profile",
            "targetPath": "$(Agent.TempDirectory)/deployment-profile",
        }
        builder = next(step for step in steps if step.get("name") == "BuildImage")
        assert builder["env"][private]["PROFILE_DIRECTORY"] == artifact["inputs"]["targetPath"]
        assert builder["env"][private]["PROFILE_COMMIT"] == "$(ResolveProfile.commit)"
        assert "checkout: git://" not in self.pipeline_text
        assert "PublishPipelineArtifact" not in self.pipeline_text
        deploy_steps = self.template["stages"][0]["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]
        assert deploy_steps[1:-1] == [{"download": "none"}]
        for name in ("DeployTest", "DeployProd"):
            assert "Build" in self.deployments[name]["dependsOn"]
            assert (
                self.deployments[name]["useInternalDeploymentProfile"]
                == "${{ parameters.useInternalDeploymentProfile }}"
            )

    def test_runtime_conditions_preserve_intentional_skip_and_failure_gates(self) -> None:
        parameters = {parameter["name"]: parameter for parameter in self.pipeline["parameters"]}
        assert parameters["deployInfra"]["default"] is False
        assert parameters["deployToTest"]["default"] is True
        assert parameters["deployToProd"]["default"] is False
        test_only = "eq('${{ parameters.deployToTest }}', 'true'),"
        for name, dependencies, gate in (
            ("DeployTestInfra", ["Build"], test_only),
            ("DeployProdInfra", ["ApproveProd"], ""),
        ):
            stage = self.deployments[name]
            assert stage["dependsOn"] == dependencies
            expected = f"and(succeeded(), {gate} eq('${{{{ parameters.deployInfra }}}}', 'true'))"
            assert "".join(stage["condition"].split()) == "".join(expected.split())
        for name, dependencies, gate in (
            ("DeployTest", ["Build", "DeployTestInfra"], test_only),
            ("DeployProd", ["ApproveProd", "Build", "DeployProdInfra"], ""),
        ):
            stage = self.deployments[name]
            assert stage["dependsOn"] == dependencies
            approval = (
                "in(dependencies.ApproveProd.result, 'Succeeded', 'SucceededWithIssues'),"
                if name == "DeployProd"
                else ""
            )
            expected = (
                f"and(not(canceled()), {gate} in(dependencies.Build.result, 'Succeeded', 'SucceededWithIssues'),"
                f"{approval}or(in(dependencies.{name}Infra.result, 'Succeeded', 'SucceededWithIssues'),"
                "and(eq('${{ parameters.deployInfra }}', 'false'),"
                f"eq(dependencies.{name}Infra.result, 'Skipped'))))"
            )
            assert "".join(stage["condition"].split()) == "".join(expected.split())

    def test_production_is_opt_in_from_allowed_branches_and_independently_approved(self) -> None:
        stage = next(stage for stage in self.pipeline["stages"] if stage.get("stage") == "ApproveProd")
        assert stage["dependsOn"] == ["Build", "DeployTest"]
        expected = (
            "and(not(canceled()), eq('${{ parameters.deployToProd }}', 'true'),"
            "or(eq(variables['Build.SourceBranch'], 'refs/heads/main'),"
            "startsWith(variables['Build.SourceBranch'], 'refs/heads/releases/v')),"
            "in(dependencies.Build.result, 'Succeeded', 'SucceededWithIssues'),"
            "or(in(dependencies.DeployTest.result, 'Succeeded', 'SucceededWithIssues'),"
            "and(eq('${{ parameters.deployToTest }}', 'false'), eq(dependencies.DeployTest.result, 'Skipped'))))"
        )
        assert "".join(stage["condition"].split()) == "".join(expected.split())
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
        assert "Test deployment requested: ${{ parameters.deployToTest }}" in task["inputs"]["instructions"]

    @unittest.skipIf(BASH is None, "Native Bash is not installed")
    def test_input_validation_allows_production_only_from_main_or_release_branches(self) -> None:
        assert BASH is not None
        stage = next(stage for stage in self.pipeline["stages"] if stage.get("stage") == "ValidateInputs")
        script = stage["jobs"][0]["steps"][1]["bash"]
        for branch, deploy_to_test, deploy_to_prod, profile, version, allowed in (
            ("refs/heads/feature/x", "True", "False", "False", "main", True),
            ("refs/heads/main", "True", "True", "False", "main", True),
            ("refs/heads/releases/v1.2.0", "False", "True", "False", "main", True),
            ("refs/heads/releases/v0.10.0rc0", "True", "True", "False", "main", True),
            ("refs/heads/feature/x", "True", "True", "False", "main", False),
            ("refs/heads/releases/1.2.0", "True", "True", "False", "main", False),
            ("refs/heads/main", "False", "False", "False", "main", False),
            ("refs/heads/main", "False", "True", "True", "main", False),
            ("refs/heads/main", "False", "True", "False", "1.2.1", False),
            ("refs/heads/releases/v1.2.0", "True", "True", "True", "main", False),
        ):
            with self.subTest(branch=branch, deploy_to_test=deploy_to_test, deploy_to_prod=deploy_to_prod):
                result = subprocess.run(
                    [BASH, "--noprofile", "--norc", "-s"],
                    input=script,
                    capture_output=True,
                    text=True,
                    check=False,
                    env={
                        **os.environ,
                        "BUILD_SOURCEBRANCH": branch,
                        "BUILD_REPOSITORY_NAME": "microsoft/PyRIT",
                        "DEPLOY_TO_TEST": deploy_to_test,
                        "DEPLOY_TO_PROD": deploy_to_prod,
                        "USE_PROFILE": profile,
                        "PROFILE_RUN": "42",
                        "PYRIT_VERSION": version,
                    },
                    timeout=TEST_WATCHDOG_SECONDS,
                )
                assert (result.returncode == 0) is allowed, result.stdout + result.stderr

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
            arguments += ["--expected-job-id", JOB_ID]
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
            ("job", JOB_ID, "Create", True),
            ("job", JOB_ID, "Modify", True),
            ("job", APP_ID, "Modify", False),
            ("job", f"{RESOURCE_GROUP_ID}/providers/Microsoft.App/jobs/other-job", "Create", False),
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
