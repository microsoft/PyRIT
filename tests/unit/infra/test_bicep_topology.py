# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Compile the deployment Bicep and verify its public-NAT contract."""

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
MAIN_BICEP = REPO_ROOT / "infra" / "main.bicep"
NETWORK_BICEP = REPO_ROOT / "infra" / "modules" / "aca_nat_network.bicep"
FRONT_DOOR_BICEP = REPO_ROOT / "infra" / "modules" / "aca_front_door.bicep"
AZ_CLI = shutil.which("az")


def _compile_bicep(source: Path, output: Path) -> dict[str, Any]:
    """Compile one Bicep file and return its generated ARM template."""
    assert AZ_CLI is not None
    command = [AZ_CLI, "bicep", "build", "--file", str(source), "--outfile", str(output)]
    command_input: str | list[str] = subprocess.list2cmdline(command) if os.name == "nt" else command
    result = subprocess.run(command_input, capture_output=True, text=True, check=False, shell=os.name == "nt")
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def _resources(template: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    """Return resources of one ARM type from a compiled template."""
    return [resource for resource in template["resources"] if resource["type"] == resource_type]


@unittest.skipIf(AZ_CLI is None, "Azure CLI is not installed")
class BicepTopologyTests(unittest.TestCase):
    """Verify the only supported public ACA topology with fixed NAT egress."""

    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.output_directory = Path(self._temporary_directory.name)

    def test_main_has_one_public_nat_topology(self):
        template = _compile_bicep(MAIN_BICEP, self.output_directory / "main.json")

        unsupported_parameters = {
            "networkMode",
            "enablePrivateEndpoint",
            "infrastructureSubnetId",
            "infrastructureNsgName",
            "applicationGatewayNsgName",
        }
        assert unsupported_parameters.isdisjoint(template["parameters"])
        assert template["parameters"]["enableFrontDoorPrivateLink"]["defaultValue"] is False
        assert template["parameters"]["disableContainerAppsPublicAccess"]["defaultValue"] is False

        existing_identity = template["parameters"]["existingManagedIdentityResourceId"]
        assert existing_identity["defaultValue"] == ""
        created_identity = _resources(template, "Microsoft.ManagedIdentity/userAssignedIdentities")[0]
        assert created_identity["condition"] == "[variables('createManagedIdentity')]"

        modules = _resources(template, "Microsoft.Resources/deployments")
        assert len(modules) == 2
        network_module = next(module for module in modules if "aca-nat-network" in module["name"])
        front_door_module = next(module for module in modules if "aca-front-door" in module["name"])
        assert "condition" not in network_module
        assert "parameters('enableFrontDoor')" in front_door_module["condition"]
        assert (
            "effectiveFrontDoorPrivateLink"
            in front_door_module["properties"]["parameters"]["enablePrivateLink"]["value"]
        )
        assert (
            "Microsoft.App/managedEnvironments"
            in front_door_module["properties"]["parameters"]["originResourceId"]["value"]
        )

        nested_types = {
            resource_type
            for module in modules
            for resource_type in (resource["type"] for resource in module["properties"]["template"]["resources"])
        }
        assert "Microsoft.Network/natGateways" in nested_types
        assert "Microsoft.Network/virtualNetworks" in nested_types
        assert "Microsoft.Network/publicIPAddresses" in nested_types
        assert "Microsoft.Authorization/locks" in nested_types
        assert not any("privateDnsZones" in resource_type for resource_type in nested_types)
        assert "Microsoft.Network/applicationGateways" not in nested_types
        assert "Microsoft.Network/networkSecurityGroups" not in nested_types
        assert "Microsoft.Cdn/profiles" in nested_types
        assert "Microsoft.Cdn/profiles/afdEndpoints" in nested_types
        assert "Microsoft.Cdn/profiles/originGroups" in nested_types
        assert "Microsoft.Cdn/profiles/originGroups/origins" in nested_types
        assert "Microsoft.Cdn/profiles/afdEndpoints/routes" in nested_types

        environment = _resources(template, "Microsoft.App/managedEnvironments")[0]
        environment_properties = environment["properties"]
        assert environment_properties["publicNetworkAccess"] == "[variables('effectiveContainerAppsPublicAccess')]"
        effective_public_access = template["variables"]["effectiveContainerAppsPublicAccess"]
        assert "disableContainerAppsPublicAccess" in effective_public_access
        assert "effectiveFrontDoorPrivateLink" in effective_public_access
        assert "fail(" in effective_public_access
        assert environment_properties["vnetConfiguration"]["internal"] is False
        assert (
            environment_properties["appLogsConfiguration"]["logAnalyticsConfiguration"]["dynamicJsonColumns"] is False
        )
        assert environment_properties["peerAuthentication"]["mtls"]["enabled"] is False
        assert environment_properties["peerTrafficConfiguration"]["encryption"]["enabled"] is False
        assert (
            "outputs.infrastructureSubnetId.value"
            in environment_properties["vnetConfiguration"]["infrastructureSubnetId"]
        )

        container_app = _resources(template, "Microsoft.App/containerApps")[0]
        assert container_app["properties"]["configuration"]["registries"][0]["identity"] == (
            "[variables('effectiveManagedIdentityId')]"
        )
        ingress_restrictions = container_app["properties"]["configuration"]["ingress"]["ipSecurityRestrictions"]
        assert "variables('effectiveAllowedCidr')" in ingress_restrictions
        effective_allowed_cidr = template["variables"]["effectiveAllowedCidr"]
        assert "parameters('allowedCidr')" in effective_allowed_cidr
        assert "parameters('enableFrontDoor')" in effective_allowed_cidr
        assert "fail(" in effective_allowed_cidr
        assert not _resources(template, "Microsoft.Network/privateEndpoints")
        assert not _resources(template, "Microsoft.Network/applicationGateways")
        cors_value = next(
            value["value"]
            for value in container_app["properties"]["template"]["containers"][0]["env"]
            if value["name"] == "PYRIT_CORS_ORIGINS"
        )
        assert "aca-front-door" in cors_value
        assert "outputs.endpointHostName.value" in cors_value

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
        assert public_ip["properties"]["ipTags"] == "[parameters('egressPublicIpIpTags')]"

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


if __name__ == "__main__":
    unittest.main()
