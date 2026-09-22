# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Check deployment inputs, phase parameters, and bounded HTTP readiness without Azure."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINES = REPO_ROOT / "infra" / "pipelines"
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
RESOURCE_GROUP = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/copyrit-test"
IMAGE = f"copyritacr.azurecr.io/pyrit@sha256:{'a' * 64}"
COMMON_INPUTS = {
    "PYRIT_SLOT": "test",
    "PYRIT_BUILD_ID": "42",
    "PYRIT_SOURCE_DIRECTORY": REPO_ROOT.as_posix(),
    "PYRIT_AGENT_TEMP_DIRECTORY": REPO_ROOT.as_posix(),
    "PYRIT_DEPLOYMENT_RESOURCE_GROUP": "copyrit-test",
    "PYRIT_APP_NAME": "copyrit-test",
    "PYRIT_MANAGED_IDENTITY_RESOURCE_ID": (
        f"{RESOURCE_GROUP}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/copyrit-id"
    ),
    "PYRIT_ACR_RESOURCE_ID": f"{RESOURCE_GROUP}/providers/Microsoft.ContainerRegistry/registries/copyritacr",
    "PYRIT_ENABLE_OTEL": "false",
}
INFRA_INPUTS = {
    "PYRIT_VNET_ADDRESS_PREFIX": "10.20.0.0/16",
    "PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX": "10.20.0.0/23",
}
APP_INPUTS = {
    "PYRIT_CONTAINER_IMAGE": IMAGE,
    "PYRIT_ENTRA_TENANT_ID": SUBSCRIPTION,
    "PYRIT_ENTRA_CLIENT_ID": SUBSCRIPTION,
    "PYRIT_ALLOWED_GROUP_OBJECT_IDS": SUBSCRIPTION,
    "PYRIT_ADMIN_GROUP_OBJECT_ID": SUBSCRIPTION,
    "PYRIT_SQL_SERVER_FQDN": "copyrit.database.windows.net",
    "PYRIT_SQL_DATABASE_NAME": "copyrit",
    "PYRIT_KEY_VAULT_RESOURCE_ID": f"{RESOURCE_GROUP}/providers/Microsoft.KeyVault/vaults/copyrit-kv",
    "PYRIT_ENV_SECRET_NAME": "pyrit-env",
}


def _find_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
    ]
    return next((str(candidate) for candidate in candidates if candidate.is_file()), None)


BASH = _find_bash()
JQ = shutil.which("jq")


@unittest.skipIf(BASH is None, "Native Bash is not installed")
class TestPypiBuild(unittest.TestCase):
    def test_pypi_images_require_an_explicit_coordinated_version(self) -> None:
        pipeline = yaml.safe_load((REPO_ROOT / ".github/workflows/docker_build.yml").read_text(encoding="utf-8"))
        steps = pipeline["jobs"]["build-production-pypi"]["steps"]
        selection = next(step for step in steps if step.get("id") == "pypi-version")
        assert selection["env"]["PYRIT_PYPI_VERSION"] == "${{ inputs.pypiVersion || vars.PYRIT_PYPI_VERSION }}"
        assert "pip index" not in selection["run"]
        assert "0.10.0" not in selection["run"]
        assert BASH is not None

        for version, valid in (
            ("1.2.0", True),
            ("1.2.0.dev0", True),
            ("1.2.0rc1", True),
            ("", False),
            ("latest", False),
            ("1.2.0\nGIT_MODIFIED=false", False),
            ("1.2.0 --extra-index-url=https://example.com", False),
        ):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "github-output"
                result = subprocess.run(
                    [BASH, "--noprofile", "--norc", "-s"],
                    input=selection["run"],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=30,
                    env={**os.environ, "PYRIT_PYPI_VERSION": version, "GITHUB_OUTPUT": output.as_posix()},
                )
                assert (result.returncode == 0) == valid, result.stdout + result.stderr
                if valid:
                    assert output.read_text().strip() == f"version={version}"
                else:
                    assert not output.exists()


@unittest.skipIf(BASH is None or JQ is None, "Native Bash and jq are required")
class TestCodeDeployment(unittest.TestCase):
    def _run(
        self, *, script: str, command: str = "", inputs: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert BASH is not None and JQ is not None
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PYRIT_")}
        environment.update(COMMON_INPUTS | (inputs or {}))
        environment["MSYS2_ARG_CONV_EXCL"] = "*"
        environment.pop("BASH_ENV", None)
        jq_flags = "-b" if os.name == "nt" else ""
        prelude = (
            "set -euo pipefail\n"
            "az() { echo 'Unexpected Azure call' >&2; exit 97; }\n"
            "curl() { echo 'Unexpected network call' >&2; exit 97; }\n"
            "sleep() { echo 'Unexpected wait' >&2; exit 97; }\n"
            f'python3() {{ {shlex.quote(Path(sys.executable).as_posix())} "$@"; }}\n'
            f'jq() {{ {shlex.quote(Path(JQ).as_posix())} {jq_flags} "$@"; }}\n'
            "before_options=$(set +o); before_traps=$(trap -p); before_directory=$PWD\n"
            f"source {shlex.quote((PIPELINES / script).as_posix())}\n"
            '[[ "$(set +o)" == "$before_options" && "$(trap -p)" == "$before_traps" '
            '&& "$PWD" == "$before_directory" ]]\n'
        )
        result = subprocess.run(
            [BASH, "--noprofile", "--norc", "-s"],
            input=prelude + command + "\n",
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            timeout=30,
        )
        assert "Unexpected " not in result.stderr, result.stderr
        return result

    def test_source_defines_helpers_without_initializing_deployment(self) -> None:
        for script in ("deployment_common.sh", "deploy_infra.sh", "deploy_app.sh"):
            with self.subTest(script=script):
                result = self._run(
                    script=script,
                    inputs={"PYRIT_SLOT": ""},
                    command="[[ -z ${deployment_name+x} && -z ${parameters+x} && -z ${cutover_in_progress+x} ]]",
                )
                assert result.returncode == 0, result.stdout + result.stderr
                assert result.stdout == ""

    def test_infra_parameters_exclude_app_settings_and_preserve_rollback_scope(self) -> None:
        result = self._run(
            script="deploy_infra.sh",
            inputs=INFRA_INPUTS,
            command="""
validate_infra_inputs
deployment_tags='{"owner":"copyrit"}'
existing_pip_ip_tags='[]'
build_infra_parameters
printf '%s\\n' "$template_file" "$deployment_name" "${parameters[@]}" --rollback "${rollback_parameters[@]}"
""",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        template, name, *values = result.stdout.splitlines()
        assert template.endswith("/infra/infrastructure.bicep")
        assert name == "pyrit-test-42-infra"
        separator = values.index("--rollback")
        parameters = dict(value.split("=", 1) for value in values[:separator])
        rollback = dict(value.split("=", 1) for value in values[separator + 1 :])
        assert set(parameters) == {
            "appName",
            "acrResourceId",
            "existingManagedIdentityResourceId",
            "enableOtel",
            "enableFrontDoor",
            "enableFrontDoorPrivateLink",
            "frontDoorPrivateLinkRequestMessage",
            "disableContainerAppsPublicAccess",
            "vnetAddressPrefix",
            "infrastructureSubnetAddressPrefix",
            "egressPublicIpTags",
            "protectEgressPublicIp",
            "tags",
        }
        assert parameters["vnetAddressPrefix"] == INFRA_INPUTS["PYRIT_VNET_ADDRESS_PREFIX"]
        assert parameters["protectEgressPublicIp"] == "true"
        assert parameters["enableFrontDoorPrivateLink"] == "true"
        assert parameters["disableContainerAppsPublicAccess"] == "true"
        assert rollback == parameters | {
            "enableFrontDoorPrivateLink": "false",
            "disableContainerAppsPublicAccess": "false",
        }

    def test_app_parameters_exclude_network_inputs_and_cutover_flags(self) -> None:
        for front_door in ("true", "false"):
            with self.subTest(front_door=front_door):
                result = self._run(
                    script="deploy_app.sh",
                    inputs=APP_INPUTS,
                    command=f"""
validate_app_inputs
deployment_tags='{{"owner":"copyrit"}}'
enable_front_door={front_door}
build_app_parameters
printf '%s\\n' "$template_file" "$deployment_name" "${{parameters[@]}}"
""",
                )
                assert result.returncode == 0, result.stdout + result.stderr
                template, name, *values = result.stdout.splitlines()
                assert template.endswith("/infra/application.bicep")
                assert name == "pyrit-test-42-app"
                parameters = dict(value.split("=", 1) for value in values)
                assert set(parameters) == {
                    "appName",
                    "containerImage",
                    "entraTenantId",
                    "entraClientId",
                    "allowedGroupObjectIds",
                    "adminGroupObjectId",
                    "allowedCidr",
                    "sqlServerFqdn",
                    "sqlDatabaseName",
                    "keyVaultResourceId",
                    "acrResourceId",
                    "existingManagedIdentityResourceId",
                    "enableOtel",
                    "envSecretName",
                    "pyritConfigFileUri",
                    "enableFrontDoor",
                    "tags",
                }
                assert parameters["containerImage"] == IMAGE
                assert parameters["sqlDatabaseName"] == "copyrit"
                assert parameters["enableFrontDoor"] == front_door
                assert parameters["allowedCidr"] == parameters["pyritConfigFileUri"] == ""

    def test_common_inputs_reject_missing_unresolved_and_noncanonical_values(self) -> None:
        cases = [
            ("PYRIT_SLOT", "$(slot)", "Required deployment value"),
            ("PYRIT_BUILD_ID", "", "Required deployment value"),
            ("PYRIT_SLOT", "staging", "Invalid slot"),
            ("PYRIT_BUILD_ID", "42;echo unsafe", "Invalid slot or build ID"),
            ("PYRIT_APP_NAME", "Copyrit", "Invalid deployment resource group or app name"),
            ("PYRIT_DEPLOYMENT_RESOURCE_GROUP", "copyrit.", "Invalid deployment resource group or app name"),
            ("PYRIT_ENABLE_OTEL", "yes", "Invalid enableOtel"),
            ("PYRIT_ACR_RESOURCE_ID", COMMON_INPUTS["PYRIT_ACR_RESOURCE_ID"] + "/", "not canonical"),
            (
                "PYRIT_ACR_RESOURCE_ID",
                COMMON_INPUTS["PYRIT_ACR_RESOURCE_ID"].replace(SUBSCRIPTION, "bad"),
                "not canonical",
            ),
            ("PYRIT_MANAGED_IDENTITY_RESOURCE_ID", "copyrit-id", "not canonical"),
            (
                "PYRIT_MANAGED_IDENTITY_RESOURCE_ID",
                COMMON_INPUTS["PYRIT_MANAGED_IDENTITY_RESOURCE_ID"].replace(
                    SUBSCRIPTION, "22222222-2222-2222-2222-222222222222"
                ),
                "another subscription",
            ),
        ]
        for key, value, message in cases:
            with self.subTest(key=key, value=value):
                result = self._run(script="deployment_common.sh", inputs={key: value}, command="validate_common_inputs")
                assert result.returncode != 0
                assert message in result.stdout, result.stdout + result.stderr

    def test_active_subscription_must_match_configured_acr(self) -> None:
        result = self._run(
            script="deployment_common.sh",
            command="""
validate_common_inputs
az() { printf '%s\\n' 22222222-2222-2222-2222-222222222222; }
initialize_deployment_scope
""",
        )
        assert result.returncode != 0
        assert "Azure subscription does not match ACR" in result.stdout

    def test_infra_rejects_invalid_network_prefixes(self) -> None:
        for subnet in ("$(subnet)", "10.30.0.0/23", "10.20.0.1/23", "10.20.0.0/28", "2001:db8::/64"):
            with self.subTest(subnet=subnet):
                result = self._run(
                    script="deploy_infra.sh",
                    inputs=INFRA_INPUTS | {"PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX": subnet},
                    command="validate_infra_inputs",
                )
                assert result.returncode != 0
                assert "deployment value" in result.stdout or "Invalid network prefix" in result.stdout

    def test_app_rejects_invalid_auth_configuration_and_optional_values(self) -> None:
        cases = [
            ("PYRIT_ENTRA_TENANT_ID", "not-a-guid", "Invalid Entra"),
            ("PYRIT_ALLOWED_GROUP_OBJECT_IDS", " , ", "Invalid Entra"),
            ("PYRIT_SQL_SERVER_FQDN", "copyrit.example.com", "Invalid SQL"),
            ("PYRIT_ENV_SECRET_NAME", "invalid/secret", "Invalid SQL"),
            ("PYRIT_ALLOWED_CLIENT_CIDR", "$(allowed)", "Optional deployment value"),
            ("PYRIT_CONFIG_FILE_URI", "$(config)", "Optional deployment value"),
            ("PYRIT_ALLOWED_CLIENT_CIDR", "192.0.2.0/24", "leave PYRIT_ALLOWED_CLIENT_CIDR empty"),
            ("PYRIT_CONFIG_FILE_URI", "https://example.com/container/config", "Invalid Entra"),
            ("PYRIT_CONFIG_FILE_URI", "https://account.blob.core.windows.net/container/config?sas=1", "Invalid Entra"),
            (
                "PYRIT_KEY_VAULT_RESOURCE_ID",
                APP_INPUTS["PYRIT_KEY_VAULT_RESOURCE_ID"].replace(SUBSCRIPTION, "22222222-2222-2222-2222-222222222222"),
                "another subscription",
            ),
        ]
        for key, value, message in cases:
            with self.subTest(key=key, value=value):
                result = self._run(
                    script="deploy_app.sh", inputs=APP_INPUTS | {key: value}, command="validate_app_inputs"
                )
                assert result.returncode != 0
                assert message in result.stdout, result.stdout + result.stderr

    def test_shared_image_validation_requires_registry_digest_and_valid_repository(self) -> None:
        for image, message in (
            ("copyritacr.azurecr.io/pyrit:latest", "immutable registry digest"),
            (IMAGE[:-1], "immutable registry digest"),
            (IMAGE.replace("copyritacr", "otheracr"), "registry does not match"),
            (IMAGE.replace("/pyrit@", "/../pyrit@"), "repository is invalid"),
            (IMAGE.replace("/pyrit@", "/Pyrit@"), "repository is invalid"),
        ):
            with self.subTest(image=image):
                result = self._run(
                    script="deployment_common.sh",
                    inputs={"PYRIT_CONTAINER_IMAGE": image},
                    command='validate_common_inputs; validate_immutable_image "$PYRIT_CONTAINER_IMAGE"',
                )
                assert result.returncode != 0
                assert message in result.stdout, result.stdout + result.stderr

    def test_http_readiness_accepts_only_successful_200(self) -> None:
        for response, code in (("200", 0), ("302", 0), ("504", 0), ("200", 28)):
            with self.subTest(response=response, code=code):
                result = self._run(
                    script="deployment_common.sh",
                    command=f"""
curl() {{ printf '%s' {response}; return {code}; }}
sleep() {{ SECONDS=$((SECONDS + $1)); }}
wait_for_http_health https://copyrit.example.azurefd.net/api/health 60
""",
                )
                assert (result.returncode == 0) == (response == "200" and code == 0), result.stdout + result.stderr
                assert result.stdout.count("Application health at ") <= 2
                if result.returncode:
                    assert "Application endpoint did not return a healthy response" in result.stdout

    def test_http_readiness_caps_requests_and_sleep_to_remaining_budget(self) -> None:
        result = self._run(
            script="deployment_common.sh",
            command="""
curl() { printf 'request:%s\\n' "$*" >&2; printf '504'; }
sleep() { printf 'sleep:%s\\n' "$1" >&2; SECONDS=$((SECONDS + $1)); }
wait_for_http_health https://copyrit.example.azurefd.net/api/health 31
""",
        )
        assert result.returncode != 0
        requests = [
            shlex.split(line.removeprefix("request:"))
            for line in result.stderr.splitlines()
            if line.startswith("request:")
        ]
        sleeps = [int(line.removeprefix("sleep:")) for line in result.stderr.splitlines() if line.startswith("sleep:")]
        assert 1 <= len(requests) <= 2
        assert 30 <= sum(sleeps) <= 31
        for request in requests:
            assert "--location" not in request and "--insecure" not in request
            assert 0 < int(request[request.index("--max-time") + 1]) <= 30
        if len(requests) == 2:
            assert requests[1][requests[1].index("--max-time") + 1] == "1"

    def _run_cancellation_rollback(
        self, *, removed: bool, signal: str
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        environment_id = f"{RESOURCE_GROUP}/providers/Microsoft.App/managedEnvironments/copyrit-test-env"
        connection = json.dumps(
            [
                {
                    "id": f"{environment_id}/privateEndpointConnections/connection-1",
                    "properties": {
                        "privateLinkServiceConnectionState": {
                            "description": "Azure Front Door private access to copyrit-test",
                            "status": "Pending",
                        }
                    },
                }
            ]
        )
        result = self._run(
            script="deploy_infra.sh",
            inputs=INFRA_INPUTS
            | {
                "TEST_ENVIRONMENT_ID": environment_id,
                "TEST_INITIAL_CONNECTION": connection,
                "TEST_REMOVAL_RESULT": "[]" if removed else connection,
                "TEST_SIGNAL": signal,
            },
            command="""
validate_infra_inputs
deployment_tags='{}'
existing_pip_ip_tags='[]'
build_infra_parameters
normalized_expected_environment_id=$(lowercase "$TEST_ENVIRONMENT_ID")
exec 3<<< "$(
  printf '%s\\n' "$TEST_INITIAL_CONNECTION"
  for attempt in {1..20}; do printf '%s\\n' "$TEST_REMOVAL_RESULT"; done
)"
az() {
  printf 'az:' >&2; printf '%s\\t' "$@" >&2; printf '\\n' >&2
  case "$1 $2" in
    'containerapp show') printf '%s\\n' copyrit-test.example.azurecontainerapps.io ;;
    'network private-endpoint-connection') local response; IFS= read -r response <&3; printf '%s\\n' "$response" ;;
    'deployment group'|'rest --method') : ;;
    *) echo 'Unexpected Azure call' >&2; exit 97 ;;
  esac
}
sleep() { :; }
trap rollback_public_origin EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
cutover_in_progress=true
kill -"$TEST_SIGNAL" $$
""",
        )
        calls = [
            line.removeprefix("az:").rstrip("\t").split("\t")
            for line in result.stderr.splitlines()
            if line.startswith("az:")
        ]
        return result, calls

    def test_cancellation_keeps_public_access_disabled_until_connection_removal(self) -> None:
        for signal, exit_code in (("TERM", 143), ("INT", 130)):
            with self.subTest(signal=signal):
                result, calls = self._run_cancellation_rollback(removed=False, signal=signal)
                assert result.returncode == exit_code, result.stdout + result.stderr
                assert "private endpoint connection deletion was not confirmed" in result.stdout
                assert any(call[:3] == ["rest", "--method", "delete"] for call in calls)
                deployments = [call for call in calls if call[:3] == ["deployment", "group", "create"]]
                assert len(deployments) == 1
                assert deployments[0][deployments[0].index("--name") + 1].endswith("-rollback-origin")
                assert not any("disableContainerAppsPublicAccess=false" in call for call in calls)

    def test_cancellation_rolls_back_only_infrastructure_after_connection_removal(self) -> None:
        for signal, exit_code in (("TERM", 143), ("INT", 130)):
            with self.subTest(signal=signal):
                result, calls = self._run_cancellation_rollback(removed=True, signal=signal)
                assert result.returncode == exit_code, result.stdout + result.stderr
                assert "Public ACA origin rollback completed" in result.stdout
                assert any(call[:3] == ["rest", "--method", "delete"] for call in calls)
                deployments = [call for call in calls if call[:3] == ["deployment", "group", "create"]]
                assert len(deployments) == 2
                rollback = deployments[-1]
                assert rollback[rollback.index("--template-file") + 1].endswith("/infra/infrastructure.bicep")
                assert "disableContainerAppsPublicAccess=false" in rollback
                assert "enableFrontDoorPrivateLink=false" in rollback
                for deployment in deployments:
                    assert deployment[deployment.index("--mode") + 1] == "Incremental"
                    assert not any(
                        argument.startswith(("containerImage=", "deployApp=", "deployInfra="))
                        for argument in deployment
                    )


if __name__ == "__main__":
    unittest.main()
