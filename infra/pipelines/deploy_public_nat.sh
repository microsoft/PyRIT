#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

set -euo pipefail

required_variables=(
  PYRIT_SLOT
  PYRIT_BUILD_ID
  PYRIT_SOURCE_DIRECTORY
  PYRIT_AGENT_TEMP_DIRECTORY
  PYRIT_DEPLOYMENT_RESOURCE_GROUP
  PYRIT_APP_NAME
  PYRIT_CONTAINER_IMAGE
  PYRIT_VNET_ADDRESS_PREFIX
  PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX
  PYRIT_MANAGED_IDENTITY_RESOURCE_ID
  PYRIT_ENTRA_TENANT_ID
  PYRIT_ENTRA_CLIENT_ID
  PYRIT_ALLOWED_GROUP_OBJECT_IDS
  PYRIT_SQL_SERVER_FQDN
  PYRIT_SQL_DATABASE_NAME
  PYRIT_KEY_VAULT_RESOURCE_ID
  PYRIT_ACR_RESOURCE_ID
  PYRIT_ENABLE_OTEL
  PYRIT_ENV_SECRET_NAME
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" || "${!variable_name}" == '$('* ]]; then
    echo "##vso[task.logissue type=error]Required deployment value is missing: $variable_name"
    exit 1
  fi
done

if [[ "${PYRIT_ALLOWED_CLIENT_CIDR:-}" == '$('* ]]; then
  echo "##vso[task.logissue type=error]Optional deployment value is unresolved: PYRIT_ALLOWED_CLIENT_CIDR"
  exit 1
fi
if [[ -n "${PYRIT_ALLOWED_CLIENT_CIDR:-}" ]]; then
  echo "##vso[task.logissue type=error]Front Door cannot use an ACA client CIDR restriction because ACA sees Front Door backend IPs, not client IPs; leave PYRIT_ALLOWED_CLIENT_CIDR empty"
  exit 1
fi

if [[ ! "$PYRIT_SLOT" =~ ^(test|prod)$ || ! "$PYRIT_BUILD_ID" =~ ^[0-9]+$ ]]; then
  echo "##vso[task.logissue type=error]Invalid slot or build ID"
  exit 1
fi

validate_resource_group_name() {
  local value=$1
  [[ "$value" =~ ^[[:alnum:]_.()-]{1,90}$ && "$value" != *. ]]
}

validate_container_app_name() {
  local value=$1
  [[ "$value" =~ ^[a-z][a-z0-9-]{0,30}[a-z0-9]$ ]]
}

if ! validate_resource_group_name "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  || ! validate_container_app_name "$PYRIT_APP_NAME"; then
  echo "##vso[task.logissue type=error]Invalid deployment resource group or app name"
  exit 1
fi

if ! python3 - \
  "$PYRIT_VNET_ADDRESS_PREFIX" \
  "$PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX" \
  "${PYRIT_ALLOWED_CLIENT_CIDR:-}" \
  "$PYRIT_ENTRA_TENANT_ID" \
  "$PYRIT_ENTRA_CLIENT_ID" \
  "$PYRIT_ALLOWED_GROUP_OBJECT_IDS" <<'PY'
import ipaddress
import sys
import uuid

try:
    vnet = ipaddress.ip_network(sys.argv[1], strict=True)
    subnet = ipaddress.ip_network(sys.argv[2], strict=True)
    allowed = ipaddress.ip_network(sys.argv[3], strict=True) if sys.argv[3] else None
    if vnet.version != 4 or subnet.version != 4 or (allowed is not None and allowed.version != 4):
        raise ValueError
    if not subnet.subnet_of(vnet) or subnet.prefixlen > 27:
        raise ValueError
    uuid.UUID(sys.argv[4])
    uuid.UUID(sys.argv[5])
    groups = [value.strip() for value in sys.argv[6].split(",") if value.strip()]
    if not groups:
        raise ValueError
    for group in groups:
        uuid.UUID(group)
except (ValueError, IndexError):
    raise SystemExit(1)
PY
then
  echo "##vso[task.logissue type=error]Invalid network prefix, subnet sizing, Entra ID, or allowed group ID"
  exit 1
fi

guid_pattern='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
if [[ ! "$PYRIT_ACR_RESOURCE_ID" =~ ^/subscriptions/($guid_pattern)/resourceGroups/[^/]+/providers/Microsoft\.ContainerRegistry/registries/([a-z0-9]{5,50})$ ]]; then
  echo "##vso[task.logissue type=error]PYRIT_ACR_RESOURCE_ID is not canonical"
  exit 1
fi
expected_subscription=${BASH_REMATCH[1],,}
acr_name=${BASH_REMATCH[2]}
if [[ "$(az account show --query id -o tsv | tr '[:upper:]' '[:lower:]')" != "$expected_subscription" ]]; then
  echo "##vso[task.logissue type=error]Azure subscription does not match ACR"
  exit 1
fi

if [[ ! "$PYRIT_MANAGED_IDENTITY_RESOURCE_ID" =~ ^/subscriptions/($guid_pattern)/resourceGroups/[^/]+/providers/Microsoft\.ManagedIdentity/userAssignedIdentities/[a-zA-Z0-9_-]{3,128}$ ]] \
  || [[ "${BASH_REMATCH[1],,}" != "$expected_subscription" ]]; then
  echo "##vso[task.logissue type=error]Managed identity resource ID is not canonical or is in another subscription"
  exit 1
fi

if [[ ! "$PYRIT_KEY_VAULT_RESOURCE_ID" =~ ^/subscriptions/($guid_pattern)/resourceGroups/[^/]+/providers/Microsoft\.KeyVault/vaults/[a-zA-Z0-9-]{3,24}$ ]] \
  || [[ "${BASH_REMATCH[1],,}" != "$expected_subscription" ]]; then
  echo "##vso[task.logissue type=error]Key Vault resource ID is not canonical or is in another subscription"
  exit 1
fi
if [[ ! "$PYRIT_SQL_SERVER_FQDN" =~ ^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]\.database\.windows\.net$ \
  || ! "$PYRIT_ENV_SECRET_NAME" =~ ^[a-zA-Z0-9-]{1,127}$ \
  || ! "$PYRIT_ENABLE_OTEL" =~ ^(true|false)$ ]]; then
  echo "##vso[task.logissue type=error]Invalid SQL FQDN, Key Vault secret name, or enableOtel value"
  exit 1
fi
if ! az resource show --ids "$PYRIT_MANAGED_IDENTITY_RESOURCE_ID" --api-version 2023-01-31 -o none 2>/dev/null; then
  echo "##vso[task.logissue type=error]Managed identity does not exist or is not readable"
  exit 1
fi

deployment_resource_group_id=$(az group show \
  --name "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --query id -o tsv 2>/dev/null || true)
if [[ -z "$deployment_resource_group_id" ]]; then
  echo "##vso[task.logissue type=error]Deployment resource group must already exist"
  exit 1
fi
if [[ "${deployment_resource_group_id,,}" != "/subscriptions/$expected_subscription/resourcegroups/"* ]]; then
  echo "##vso[task.logissue type=error]Deployment resource group is in another subscription"
  exit 1
fi

expected_app_id="$deployment_resource_group_id/providers/Microsoft.App/containerApps/$PYRIT_APP_NAME"
expected_environment_id="$deployment_resource_group_id/providers/Microsoft.App/managedEnvironments/$PYRIT_APP_NAME-env"
expected_vnet_id="$deployment_resource_group_id/providers/Microsoft.Network/virtualNetworks/$PYRIT_APP_NAME-vnet"
expected_subnet_id="$expected_vnet_id/subnets/$PYRIT_APP_NAME-aca-subnet"
expected_nat_id="$deployment_resource_group_id/providers/Microsoft.Network/natGateways/$PYRIT_APP_NAME-nat"
expected_pip_id="$deployment_resource_group_id/providers/Microsoft.Network/publicIPAddresses/$PYRIT_APP_NAME-egress-pip"

existing_app=$(az containerapp show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME" \
  --query '{id:id,environmentId:properties.managedEnvironmentId,tags:tags}' -o json 2>/dev/null || true)
existing_vnet=$(az network vnet show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME-vnet" \
  --query '{id:id,prefix:addressSpace.addressPrefixes[0],tags:tags}' -o json 2>/dev/null || true)
existing_subnet=$(az network vnet subnet show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --vnet-name "$PYRIT_APP_NAME-vnet" \
  --name "$PYRIT_APP_NAME-aca-subnet" \
  --query '{id:id,prefix:addressPrefix,natId:natGateway.id}' -o json 2>/dev/null || true)
existing_nat=$(az network nat gateway show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME-nat" \
  --query '{id:id,pipId:publicIpAddresses[0].id,tags:tags}' -o json 2>/dev/null || true)
existing_pip=$(az network public-ip show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME-egress-pip" \
  --query '{id:id,ip:ipAddress,allocation:publicIPAllocationMethod,sku:sku.name,tags:tags}' -o json 2>/dev/null || true)

if [[ -z "$existing_app" || -z "$existing_vnet" || -z "$existing_subnet" \
  || -z "$existing_nat" || -z "$existing_pip" ]]; then
  echo "##vso[task.logissue type=error]Internal deployments must adopt an existing app, environment, VNet, subnet, NAT, and egress PIP"
  exit 1
fi

deployment_tags=$(jq -cS '.tags' <<< "$existing_app")
pip_tags=$(jq -cS '.tags' <<< "$existing_pip")
nat_tags=$(jq -cS '.tags' <<< "$existing_nat")
vnet_tags=$(jq -cS '.tags' <<< "$existing_vnet")
existing_pip_ip_tags=$(az network public-ip show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME-egress-pip" --query 'ipTags || `[]`' -o json | jq -c .)
expected_egress_ip=$(jq -r '.ip // empty' <<< "$existing_pip")

if [[ "$(jq -r '.id | ascii_downcase' <<< "$existing_app")" != "${expected_app_id,,}" \
  || "$(jq -r '.environmentId | ascii_downcase' <<< "$existing_app")" != "${expected_environment_id,,}" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_vnet")" != "${expected_vnet_id,,}" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_subnet")" != "${expected_subnet_id,,}" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_nat")" != "${expected_nat_id,,}" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_pip")" != "${expected_pip_id,,}" \
  || "$(jq -r '.natId | ascii_downcase' <<< "$existing_subnet")" != "${expected_nat_id,,}" \
  || "$(jq -r '.pipId | ascii_downcase' <<< "$existing_nat")" != "${expected_pip_id,,}" \
  || "$(jq -r '.prefix' <<< "$existing_vnet")" != "$PYRIT_VNET_ADDRESS_PREFIX" \
  || "$(jq -r '.prefix' <<< "$existing_subnet")" != "$PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX" \
  || "$(jq -r '.allocation' <<< "$existing_pip")" != "Static" \
  || "$(jq -r '.sku' <<< "$existing_pip")" != "Standard" \
  || -z "$expected_egress_ip" ]]; then
  echo "##vso[task.logissue type=error]Deployment variables do not match the existing protected topology"
  exit 1
fi

if [[ "$deployment_tags" == *'<'* || "$deployment_tags" == "null" \
  || "$deployment_tags" != "$pip_tags" || "$deployment_tags" != "$nat_tags" \
  || "$deployment_tags" != "$vnet_tags" ]]; then
  echo "##vso[task.logissue type=error]Protected resource tags are missing, placeholders, or inconsistent"
  exit 1
fi

if [[ ! "$PYRIT_CONTAINER_IMAGE" =~ ^([^/]+)/(.+)@(sha256:[0-9a-fA-F]{64})$ ]]; then
  echo "##vso[task.logissue type=error]Built image must be an immutable registry digest"
  exit 1
fi
registry_server=${BASH_REMATCH[1]}
repository=${BASH_REMATCH[2]}
digest=${BASH_REMATCH[3]}
if [[ "$registry_server" != "$acr_name.azurecr.io" ]]; then
  echo "##vso[task.logissue type=error]Built image registry does not match ACR resource ID"
  exit 1
fi
repository_pattern='^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$'
if [[ ! "$repository" =~ $repository_pattern ]]; then
  echo "##vso[task.logissue type=error]Built image repository is invalid"
  exit 1
fi
immutable_image="$registry_server/$repository@$digest"

parameters=(
  "appName=$PYRIT_APP_NAME"
  "containerImage=$immutable_image"
  "entraTenantId=$PYRIT_ENTRA_TENANT_ID"
  "entraClientId=$PYRIT_ENTRA_CLIENT_ID"
  "allowedGroupObjectIds=$PYRIT_ALLOWED_GROUP_OBJECT_IDS"
  "allowedCidr=${PYRIT_ALLOWED_CLIENT_CIDR:-}"
  "sqlServerFqdn=$PYRIT_SQL_SERVER_FQDN"
  "sqlDatabaseName=$PYRIT_SQL_DATABASE_NAME"
  "keyVaultResourceId=$PYRIT_KEY_VAULT_RESOURCE_ID"
  "acrResourceId=$PYRIT_ACR_RESOURCE_ID"
  "existingManagedIdentityResourceId=$PYRIT_MANAGED_IDENTITY_RESOURCE_ID"
  "enableOtel=$PYRIT_ENABLE_OTEL"
  "envSecretName=$PYRIT_ENV_SECRET_NAME"
  "enableFrontDoor=true"
  "vnetAddressPrefix=$PYRIT_VNET_ADDRESS_PREFIX"
  "infrastructureSubnetAddressPrefix=$PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX"
  "egressPublicIpIpTags=$existing_pip_ip_tags"
  "protectEgressPublicIp=true"
  "tags=$deployment_tags"
)

deployment_name="pyrit-$PYRIT_SLOT-$PYRIT_BUILD_ID"
what_if_file="$PYRIT_AGENT_TEMP_DIRECTORY/$deployment_name-what-if.json"
az deployment group what-if \
  --name "$deployment_name-preview" \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --template-file "$PYRIT_SOURCE_DIRECTORY/infra/main.bicep" \
  --parameters "${parameters[@]}" \
  --result-format FullResourcePayloads --no-pretty-print -o json > "$what_if_file"

# ARM what-if reports these read-only server defaults as deletes when adopting
# existing Standard NAT/PIP resources. Every other protected-resource delta fails.
if ! python3 "$PYRIT_SOURCE_DIRECTORY/infra/pipelines/validate_what_if.py" \
  --what-if-file "$what_if_file" \
  --deployment-resource-group-id "$deployment_resource_group_id" \
  --expected-pip-id "$expected_pip_id" \
  --expected-nat-id "$expected_nat_id" \
  --expected-vnet-id "$expected_vnet_id" \
  --expected-subnet-id "$expected_subnet_id"; then
  echo "##vso[task.logissue type=error]What-if contains a delete, cross-resource-group write, protected-network change, or core resource create"
  exit 1
fi

az deployment group create \
  --name "$deployment_name" \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --template-file "$PYRIT_SOURCE_DIRECTORY/infra/main.bicep" \
  --parameters "${parameters[@]}"

health=""
for attempt in {1..5}; do
  health=$(az containerapp revision list \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --name "$PYRIT_APP_NAME" \
    --query "[?properties.template.containers[0].image=='$immutable_image'] | sort_by(@,&properties.createdTime)[-1].properties.healthState" \
    -o tsv || true)
  echo "Revision health attempt $attempt/5: ${health:-<not-found>}"
  [[ "$health" == "Healthy" ]] && break
  [[ "$attempt" -lt 5 ]] && sleep 120
done
if [[ "$health" != "Healthy" ]]; then
  echo "##vso[task.logissue type=error]Deployed revision did not become healthy"
  exit 1
fi

app_fqdn=$(az deployment group show \
  --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --query properties.outputs.appFqdn.value -o tsv)
front_door_fqdn=$(az deployment group show \
  --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --query properties.outputs.frontDoorFqdn.value -o tsv)
egress_ip=$(az deployment group show \
  --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --query properties.outputs.egressPublicIpAddress.value -o tsv)
actual_pip_id=$(az network public-ip show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME-egress-pip" --query id -o tsv)
if [[ "$egress_ip" != "$expected_egress_ip" \
  || "${actual_pip_id,,}" != "${expected_pip_id,,}" ]]; then
  echo "##vso[task.logissue type=error]Reserved egress PIP identity or address changed"
  exit 1
fi
front_door_health=""
for attempt in {1..20}; do
  front_door_health=$(curl \
    --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --max-time 30 "https://$front_door_fqdn/api/health" || true)
  echo "Front Door health attempt $attempt/20: ${front_door_health:-<connection-failed>}"
  [[ "$front_door_health" == "200" ]] && break
  [[ "$attempt" -lt 20 ]] && sleep 30
done
if [[ "$front_door_health" != "200" ]]; then
  echo "##vso[task.logissue type=error]Front Door did not route a healthy response"
  exit 1
fi
echo "Deployment healthy; public URL: https://$front_door_fqdn; ACA origin: https://$app_fqdn; egress IPv4: $egress_ip"