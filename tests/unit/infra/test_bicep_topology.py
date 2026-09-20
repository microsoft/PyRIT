# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Compile the deployment phases and verify their public-NAT contract."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRASTRUCTURE_BICEP = REPO_ROOT / "infra" / "infrastructure.bicep"
APPLICATION_BICEP = REPO_ROOT / "infra" / "application.bicep"
NETWORK_BICEP = REPO_ROOT / "infra" / "modules" / "aca_nat_network.bicep"
FRONT_DOOR_BICEP = REPO_ROOT / "infra" / "modules" / "aca_front_door.bicep"
PRIVATE_ENDPOINT_APPROVAL_BICEP = REPO_ROOT / "infra" / "modules" / "aca_private_endpoint_approval.bicep"
BICEP_TOPOLOGY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bicep_topology.yml"


def _find_bicep_cli() -> str | None:
    """Return an already-installed Bicep binary without triggering a download."""
    path_binary = shutil.which("bicep")
    if path_binary:
        return path_binary

    azure_config_directory = Path(os.environ.get("AZURE_CONFIG_DIR", Path.home() / ".azure"))
    managed_binary = azure_config_directory / "bin" / ("bicep.exe" if os.name == "nt" else "bicep")
    return str(managed_binary) if managed_binary.is_file() else None


BICEP_CLI = _find_bicep_cli()
BICEP_REQUIRED = os.environ.get("PYRIT_REQUIRE_BICEP", "").strip().casefold() == "true"


def _compile_bicep(source: Path, output: Path) -> dict[str, Any]:
    """Compile one Bicep file and return its generated ARM template."""
    if BICEP_CLI is None:
        raise RuntimeError("Bicep CLI is required to compile infrastructure topology tests")
    command = [BICEP_CLI, "build", str(source), "--outfile", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def _resources(template: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    """Return resources of one ARM type from a compiled template."""
    return [resource for resource in template["resources"] if resource["type"] == resource_type]


class TestBicepCiContract(unittest.TestCase):
    """Keep one fail-closed CI path for compiling the infrastructure templates."""

    def test_workflow_installs_bicep_and_requires_topology_tests(self):
        workflow = BICEP_TOPOLOGY_WORKFLOW.read_text(encoding="utf-8")

        assert "bicep-topology:" in workflow
        assert "PYRIT_REQUIRE_BICEP: 'true'" in workflow
        assert workflow.count("AZURE_CONFIG_DIR: ${{ runner.temp }}/.azure") == 2
        assert "az bicep install --version v0.46.1" in workflow
        assert "python tests/unit/infra/test_bicep_topology.py -v" in workflow
        assert workflow.count("'infra/**/*.bicep'") == 2
        assert workflow.count("'tests/unit/infra/**'") == 2
        assert workflow.count("'.github/workflows/bicep_topology.yml'") == 2
        assert "merge_group:" in workflow
        assert "workflow_dispatch:" in workflow


@unittest.skipIf(BICEP_CLI is None and not BICEP_REQUIRED, "Bicep CLI is not already installed")
class TestBicepTopology(unittest.TestCase):
    """Verify the only supported public ACA topology with fixed NAT egress."""

    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.output_directory = Path(self._temporary_directory.name)

    def test_standalone_phases_have_disjoint_write_sets(self) -> None:
        infrastructure = _compile_bicep(INFRASTRUCTURE_BICEP, self.output_directory / "infrastructure.json")
        application = _compile_bicep(APPLICATION_BICEP, self.output_directory / "application.json")

        assert {
            "containerImage",
            "entraTenantId",
            "entraClientId",
            "sqlServerFqdn",
            "sqlDatabaseName",
            "keyVaultResourceId",
            "pyritConfigFileUri",
            "envFileContents",
        }.isdisjoint(infrastructure["parameters"])
        assert {
            "vnetAddressPrefix",
            "infrastructureSubnetAddressPrefix",
            "enableFrontDoorPrivateLink",
            "disableContainerAppsPublicAccess",
            "protectEgressPublicIp",
            "logAnalyticsSharedKey",
        }.isdisjoint(application["parameters"])
        infrastructure_resources = infrastructure["resources"] + [
            resource
            for module in _resources(infrastructure, "Microsoft.Resources/deployments")
            for resource in module["properties"]["template"]["resources"]
        ]
        infrastructure_types = {resource["type"] for resource in infrastructure_resources}
        assert infrastructure_types == {
            "Microsoft.Resources/deployments",
            "Microsoft.ContainerRegistry/registries",
            "Microsoft.OperationalInsights/workspaces",
            "Microsoft.Insights/components",
            "Microsoft.ManagedIdentity/userAssignedIdentities",
            "Microsoft.App/managedEnvironments",
            "Microsoft.Network/publicIPAddresses",
            "Microsoft.Authorization/locks",
            "Microsoft.Network/natGateways",
            "Microsoft.Network/virtualNetworks",
            "Microsoft.Cdn/profiles",
            "Microsoft.Cdn/profiles/afdEndpoints",
            "Microsoft.Cdn/profiles/originGroups",
            "Microsoft.Cdn/profiles/originGroups/origins",
            "Microsoft.Cdn/profiles/afdEndpoints/routes",
        }
        assert len(application["resources"]) == 1
        assert application["resources"][0]["type"] == "Microsoft.App/containerApps"
        assert application["resources"][0]["type"] not in infrastructure_types
        assert "condition" not in application["resources"][0]
        assert {
            "frontDoorPrivateLinkRequestMessage",
            "frontDoorFqdn",
            "environmentDefaultDomain",
            "containerAppsPublicNetworkAccess",
            "egressPublicIpAddress",
            "natGatewayId",
            "acaInfrastructureSubnetId",
            "vnetName",
            "managedIdentityResourceId",
            "managedIdentityPrincipalId",
            "acrLoginServer",
            "appInsightsConnectionString",
        } <= set(infrastructure["outputs"])
        assert set(application["outputs"]) == {
            "appFqdn",
            "frontDoorFqdn",
            "containerAppsPublicNetworkAccess",
        }

    def test_application_reads_existing_state_and_preserves_authentication(self) -> None:
        template = _compile_bicep(APPLICATION_BICEP, self.output_directory / "application-config.json")

        for name in ("containerImage", "existingManagedIdentityResourceId"):
            assert template["parameters"][name]["minLength"] == 1
            assert "defaultValue" not in template["parameters"][name]
        assert template["parameters"]["maxReplicas"]["allowedValues"] == [1]
        assert "fail(" in template["variables"]["validatedAllowedGroupObjectIds"]
        assert "fail(" in template["variables"]["validatedAdminGroupObjectId"]
        assert "trim(" in template["variables"]["normalizedAllowedGroupObjectIds"]
        assert "trim(" in template["variables"]["normalizedAdminGroupObjectId"]
        assert "fail('App-only deployment requires an existing registry')" in template["variables"]["effectiveAcrName"]
        effective_allowed_cidr = template["variables"]["effectiveAllowedCidr"]
        assert "parameters('allowedCidr')" in effective_allowed_cidr
        assert "parameters('enableFrontDoor')" in effective_allowed_cidr
        assert "fail(" in effective_allowed_cidr
        container_app = _resources(template, "Microsoft.App/containerApps")[0]
        assert container_app["identity"]["type"] == "UserAssigned"
        assert container_app["properties"]["template"]["containers"][0]["image"] == "[parameters('containerImage')]"
        identity = template["variables"]["effectiveManagedIdentityId"]
        assert "resourceId(" in identity or "extensionResourceId(" in identity
        assert "variables('existingManagedIdentitySegments')[2]" in identity
        assert "variables('existingManagedIdentitySegments')[4]" in identity
        assert "Microsoft.ManagedIdentity/userAssignedIdentities" in identity
        container_env = container_app["properties"]["template"]["containers"][0]["env"]
        environment = {value["name"]: value["value"] for value in container_env if isinstance(value, dict)}
        assert "validatedAllowedGroupObjectIds" in environment["ENTRA_ALLOWED_GROUP_IDS"]
        assert "validatedAdminGroupObjectId" in environment["ENTRA_ADMIN_GROUP_ID"]
        assert "Microsoft.ManagedIdentity/userAssignedIdentities" in environment["AZURE_CLIENT_ID"]
        assert ".clientId" in environment["AZURE_CLIENT_ID"]
        assert environment["ENTRA_CLIENT_ID"] == "[parameters('entraClientId')]"
        assert environment["ENTRA_TENANT_ID"] == "[parameters('entraTenantId')]"
        assert "parameters('enableOtel')" in environment["OTEL_EXPORTER_OTLP_ENDPOINT"]
        assert "http://localhost:4318" in environment["OTEL_EXPORTER_OTLP_ENDPOINT"]
        secrets = container_app["properties"]["configuration"]["secrets"]
        assert "parameters('envFileContents')" in secrets
        assert "parameters('pyritConfigFileUri')" in secrets
        serialized_container_env = json.dumps(container_env)
        assert "'secretRef', 'env-file'" in serialized_container_env
        assert "'secretRef', 'config-file-uri'" in serialized_container_env
        assert "PYRIT_CONFIG_FILE" in serialized_container_env
        assert "PYRIT_ENV_AKV_REF" in serialized_container_env
        assert "keyvaultDns" in serialized_container_env
        cors_value = environment["PYRIT_CORS_ORIGINS"]
        assert "parameters('enableFrontDoor')" in cors_value
        assert "Microsoft.Cdn/profiles/afdEndpoints" in cors_value
        assert ".hostName" in cors_value
        assert "uniqueString(subscription().id, resourceGroup().id, parameters('appName'))" in cors_value
        assert "Microsoft.App/managedEnvironments" in cors_value
        assert ".publicNetworkAccess" in cors_value
        assert "'Disabled'" in cors_value
        assert ".defaultDomain" in cors_value
        assert "Microsoft.Resources/deployments" not in json.dumps(template)
        assert "deployInfra" not in json.dumps(template)
        assert "deployApp" not in json.dumps(template)

    def test_aca_nat_network_is_static_and_delegated(self):
        template = _compile_bicep(NETWORK_BICEP, self.output_directory / "network.json")

        public_ips = _resources(template, "Microsoft.Network/publicIPAddresses")
        assert len(public_ips) == 1
        public_ip = public_ips[0]
        assert public_ip["sku"]["name"] == "Standard"
        assert public_ip["sku"]["tier"] == "Regional"
        assert public_ip["properties"]["publicIPAllocationMethod"] == "Static"
        assert public_ip["properties"]["publicIPAddressVersion"] == "IPv4"
        assert public_ip["properties"]["ddosSettings"]["protectionMode"] == "VirtualNetworkInherited"
        assert public_ip["properties"]["ipTags"] == "[parameters('egressPublicIpTags')]"

        locks = _resources(template, "Microsoft.Authorization/locks")
        assert len(locks) == 1
        assert "parameters('protectEgressPublicIp')" in locks[0]["condition"]
        assert locks[0]["properties"]["level"] == "CanNotDelete"
        assert "publicIPAddresses" in locks[0]["scope"]

        nat_gateway = _resources(template, "Microsoft.Network/natGateways")[0]
        assert nat_gateway["sku"]["name"] == "Standard"
        assert len(nat_gateway["properties"]["publicIpAddresses"]) == 1
        assert not _resources(template, "Microsoft.Network/routeTables")

        assert not _resources(template, "Microsoft.Network/virtualNetworks/subnets")
        vnet = _resources(template, "Microsoft.Network/virtualNetworks")[0]
        assert vnet["properties"]["privateEndpointVNetPolicies"] == "Disabled"
        assert len(vnet["properties"]["subnets"]) == 1
        subnet = vnet["properties"]["subnets"][0]
        assert subnet["properties"]["addressPrefix"] == "[parameters('infrastructureSubnetAddressPrefix')]"
        assert subnet["properties"]["defaultOutboundAccess"] is False
        assert subnet["properties"]["delegations"][0]["properties"]["serviceName"] == "Microsoft.App/environments"
        assert "natGateway" in subnet["properties"]
        assert "networkSecurityGroup" not in subnet["properties"]

        assert not _resources(template, "Microsoft.Network/networkSecurityGroups")
        assert not _resources(template, "Microsoft.Network/networkSecurityGroups/securityRules")

    def test_front_door_uses_https_health_probe_without_caching(self):
        template = _compile_bicep(FRONT_DOOR_BICEP, self.output_directory / "front-door.json")

        profile = _resources(template, "Microsoft.Cdn/profiles")[0]
        assert profile["sku"]["name"] == "Premium_AzureFrontDoor"
        assert profile["properties"]["originResponseTimeoutSeconds"] == 240
        assert "namePrefix" in template["parameters"]["privateLinkRequestMessage"]["defaultValue"]

        origin_group = _resources(template, "Microsoft.Cdn/profiles/originGroups")[0]
        probe = origin_group["properties"]["healthProbeSettings"]
        assert probe["probePath"] == "/api/health"
        assert probe["probeProtocol"] == "Https"
        assert probe["probeRequestType"] == "GET"

        origin = _resources(template, "Microsoft.Cdn/profiles/originGroups/origins")[0]
        origin_properties = origin["properties"]
        assert "originHostHeader" in origin_properties
        assert "enforceCertificateNameCheck" in origin_properties
        assert "parameters('enablePrivateLink')" in origin_properties
        assert "sharedPrivateLinkResource" in origin_properties
        assert "managedEnvironments" in origin_properties
        assert "effectiveOriginResourceId" in origin_properties
        assert "effectiveOriginLocation" in origin_properties
        assert "Pending" in origin_properties

        route = _resources(template, "Microsoft.Cdn/profiles/afdEndpoints/routes")[0]
        assert route["properties"]["forwardingProtocol"] == "HttpsOnly"
        assert route["properties"]["httpsRedirect"] == "Enabled"
        assert "cacheConfiguration" not in route["properties"]

    def test_private_endpoint_approval_preserves_discovery_description(self):
        template = _compile_bicep(PRIVATE_ENDPOINT_APPROVAL_BICEP, self.output_directory / "approval.json")

        connections = _resources(template, "Microsoft.App/managedEnvironments/privateEndpointConnections")
        assert len(connections) == 1
        state = connections[0]["properties"]["privateLinkServiceConnectionState"]
        assert state["status"] == "Approved"
        assert state["description"] == "[parameters('approvalDescription')]"


if __name__ == "__main__":
    unittest.main()
