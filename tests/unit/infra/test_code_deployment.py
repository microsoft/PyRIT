# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Exercise code-only deployment with no Azure or network calls."""

import os
import subprocess
import unittest
from unittest.mock import patch

from infra.pipelines import deploy_code

SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
ENVIRONMENT = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/copyrit-test"
    "/providers/Microsoft.App/managedEnvironments/copyrit-test-env"
)
IMAGE = f"copyritacr.azurecr.io/pyrit@sha256:{'a' * 64}"
PREVIOUS_IMAGE = f"copyritacr.azurecr.io/pyrit@sha256:{'b' * 64}"
ACA_HOST = "copyrit-test.example.westus2.azurecontainerapps.io"
AFD_HOST = "copyrit-test.example.azurefd.net"
REVISION = "copyrit-test--0000002"


class TestCodeDeployment(unittest.TestCase):
    def setUp(self) -> None:
        self.arguments = {
            "slot": "test",
            "resource_group": "copyrit-test",
            "app_name": "copyrit-test",
            "acr_resource_id": (
                f"/subscriptions/{SUBSCRIPTION}/resourceGroups/shared"
                "/providers/Microsoft.ContainerRegistry/registries/copyritacr"
            ),
            "image": IMAGE,
        }
        self.values = {
            "id": SUBSCRIPTION,
            "properties.managedEnvironmentId": ENVIRONMENT,
            "properties.configuration.activeRevisionsMode": "Single",
            "length(properties.template.containers)": "1",
            "properties.template.containers[0].name": "pyrit-gui",
            "properties.configuration.ingress.fqdn": ACA_HOST,
            "properties.publicNetworkAccess": "Enabled",
            "properties.latestRevisionName": REVISION,
            "properties.latestReadyRevisionName": REVISION,
            "properties.healthState": "Healthy",
            "value[?properties.enabledState=='Enabled'].properties.hostName": AFD_HOST,
        }
        self.current_image = PREVIOUS_IMAGE
        self.calls: list[tuple[str, ...]] = []
        self.az_patch = patch.object(deploy_code, "_az", side_effect=self._az)
        self.az_patch.start()
        self.addCleanup(self.az_patch.stop)
        self.http = patch.object(deploy_code, "_wait_for_http_health").start()
        self.addCleanup(patch.stopall)

    def _az(self, *arguments: str) -> str:
        self.calls.append(arguments)
        if arguments[:2] == ("containerapp", "update"):
            self.current_image = arguments[arguments.index("--image") + 1]
            return ""
        query = arguments[arguments.index("--query") + 1]
        if query == "properties.template.containers[0].image":
            return self.current_image
        return self.values[query]

    def test_public_mode_updates_only_image_and_checks_aca(self) -> None:
        deploy_code.deploy_code(**self.arguments)

        updates = [call for call in self.calls if call[:2] == ("containerapp", "update")]
        assert updates == [
            (
                "containerapp",
                "update",
                "--resource-group",
                "copyrit-test",
                "--name",
                "copyrit-test",
                "--container-name",
                "pyrit-gui",
                "--image",
                IMAGE,
                "--output",
                "none",
            )
        ]
        self.http.assert_called_once_with(f"https://{ACA_HOST}/api/health")
        assert not any(call[0] in {"deployment", "network", "rest"} for call in self.calls)

    def test_private_mode_checks_front_door_without_changing_network(self) -> None:
        self.values["properties.publicNetworkAccess"] = "Disabled"
        self.arguments["slot"] = "prod"

        deploy_code.deploy_code(**self.arguments)

        self.http.assert_called_once_with(f"https://{AFD_HOST}/api/health")
        rest_calls = [call for call in self.calls if call[0] == "rest"]
        assert len(rest_calls) == 2
        assert all(call[1:3] == ("--method", "get") for call in rest_calls)
        assert not any(call[0] in {"deployment", "network"} for call in self.calls)

    def test_same_image_still_verified_without_update(self) -> None:
        self.current_image = IMAGE

        deploy_code.deploy_code(**self.arguments)

        assert not any(call[:2] == ("containerapp", "update") for call in self.calls)
        self.http.assert_called_once()

    def test_invalid_inputs_fail_before_azure_calls(self) -> None:
        invalid = {
            "slot": "other",
            "resource_group": "../wrong",
            "app_name": "wrong/name",
            "acr_resource_id": "/subscriptions/wrong",
            "image": "another.azurecr.io/pyrit:latest",
        }
        for key, value in invalid.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                deploy_code.deploy_code(**(self.arguments | {key: value}))
        assert not self.calls

    def test_invalid_existing_topology_never_updates(self) -> None:
        invalid = {
            "id": "22222222-2222-2222-2222-222222222222",
            "properties.managedEnvironmentId": ENVIRONMENT + "-other",
            "properties.configuration.activeRevisionsMode": "Multiple",
            "length(properties.template.containers)": "2",
            "properties.template.containers[0].name": "other",
            "properties.publicNetworkAccess": "unexpected",
            "properties.configuration.ingress.fqdn": "attacker.example",
        }
        for query, value in invalid.items():
            with self.subTest(query=query), patch.dict(self.values, {query: value}), self.assertRaises(ValueError):
                deploy_code.deploy_code(**self.arguments)
        assert not any(call[:2] == ("containerapp", "update") for call in self.calls)

    def test_private_mode_requires_one_valid_front_door_endpoint(self) -> None:
        self.values["properties.publicNetworkAccess"] = "Disabled"
        query = "value[?properties.enabledState=='Enabled'].properties.hostName"
        for value in ("", "None", f"{AFD_HOST}\nother.azurefd.net", "attacker.example"):
            with self.subTest(value=value), patch.dict(self.values, {query: value}), self.assertRaises(ValueError):
                deploy_code.deploy_code(**self.arguments)
        assert not any(call[:2] == ("containerapp", "update") for call in self.calls)

    def test_unhealthy_revision_fails_without_rollback(self) -> None:
        self.values["properties.healthState"] = "Unhealthy"
        with patch.object(deploy_code.time, "sleep"), self.assertRaisesRegex(RuntimeError, "revision"):
            deploy_code.deploy_code(**self.arguments)
        assert self.current_image == IMAGE
        assert len([call for call in self.calls if call[:2] == ("containerapp", "update")]) == 1
        self.http.assert_not_called()
        assert not any(call[0] in {"deployment", "network", "rest"} for call in self.calls)

    def test_http_failure_does_not_fallback_or_roll_back(self) -> None:
        self.values["properties.publicNetworkAccess"] = "Disabled"
        self.http.side_effect = RuntimeError("Health check failed")
        with self.assertRaisesRegex(RuntimeError, "Health check"):
            deploy_code.deploy_code(**self.arguments)
        self.http.assert_called_once_with(f"https://{AFD_HOST}/api/health")
        assert self.current_image == IMAGE
        assert len([call for call in self.calls if call[:2] == ("containerapp", "update")]) == 1

    def test_current_ready_revision_must_be_the_verified_revision(self) -> None:
        self.values["properties.latestReadyRevisionName"] = "copyrit-test--old"
        with self.assertRaisesRegex(RuntimeError, "current ready revision"):
            deploy_code.deploy_code(**self.arguments)

    def test_access_mode_change_is_not_reported_as_success(self) -> None:
        self.http.side_effect = lambda _: self.values.update({"properties.publicNetworkAccess": "Disabled"})
        with self.assertRaisesRegex(RuntimeError, "access mode changed"):
            deploy_code.deploy_code(**self.arguments)

    def test_main_reports_unresolved_input_without_azure_calls(self) -> None:
        with patch.dict(os.environ, {"PYRIT_SLOT": "$(slot)"}):
            assert deploy_code.main() == 1
        assert not self.calls


class TestHttpVerification(unittest.TestCase):
    def test_http_success_does_not_follow_redirects(self) -> None:
        response = subprocess.CompletedProcess(args=[], returncode=0, stdout="200", stderr="")
        with patch.object(deploy_code.subprocess, "run", return_value=response) as run:
            deploy_code._wait_for_http_health("https://example.azurefd.net/api/health")
        arguments = run.call_args.args[0]
        assert "--location" not in arguments
        assert "--insecure" not in arguments
        assert "--max-time" in arguments

    def test_http_errors_and_redirects_exhaust_the_bounded_budget(self) -> None:
        for status, exit_code in (("302", 0), ("504", 0), ("200", 28)):
            response = subprocess.CompletedProcess(args=[], returncode=exit_code, stdout=status, stderr="failure")
            with (
                self.subTest(status=status, exit_code=exit_code),
                patch.object(deploy_code.subprocess, "run", return_value=response),
                patch.object(deploy_code.time, "monotonic", side_effect=[0, 290, 299, 301]),
                patch.object(deploy_code.time, "sleep"),
                self.assertRaisesRegex(RuntimeError, "health check failed"),
            ):
                deploy_code._wait_for_http_health("https://example.azurefd.net/api/health")


if __name__ == "__main__":
    unittest.main()
