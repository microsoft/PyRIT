#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

set -euo pipefail

lowercase() {
  printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]'
}

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
  PYRIT_ADMIN_GROUP_OBJECT_ID
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
if [[ "${PYRIT_CONFIG_FILE_URI:-}" == '$('* ]]; then
  echo "##vso[task.logissue type=error]Optional deployment value is unresolved: PYRIT_CONFIG_FILE_URI"
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
  "$PYRIT_ALLOWED_GROUP_OBJECT_IDS" \
  "$PYRIT_ADMIN_GROUP_OBJECT_ID" \
  "${PYRIT_CONFIG_FILE_URI:-}" <<'PY'
import ipaddress
import sys
import uuid
from urllib.parse import urlparse

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
    uuid.UUID(sys.argv[7])
    if sys.argv[8]:
      parsed = urlparse(sys.argv[8])
      hostname = parsed.hostname or ""
      suffixes = (
        ".blob.core.windows.net",
        ".blob.core.chinacloudapi.cn",
        ".blob.core.usgovcloudapi.net",
        ".blob.core.cloudapi.de",
      )
      if (
        parsed.scheme != "https"
        or not any(hostname.endswith(suffix) and hostname != suffix[1:] for suffix in suffixes)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or len(parsed.path.strip("/").split("/")) < 2
      ):
        raise ValueError
except (ValueError, IndexError):
    raise SystemExit(1)
PY
then
    echo "##vso[task.logissue type=error]Invalid network prefix, subnet sizing, Entra ID, group ID, or config URI"
  exit 1
fi

guid_pattern='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
normalized_acr_resource_id=$(lowercase "$PYRIT_ACR_RESOURCE_ID")
if [[ ! "$normalized_acr_resource_id" =~ ^/subscriptions/($guid_pattern)/resourcegroups/[^/]+/providers/microsoft\.containerregistry/registries/([a-z0-9]{5,50})$ ]]; then
  echo "##vso[task.logissue type=error]PYRIT_ACR_RESOURCE_ID is not canonical"
  exit 1
fi
expected_subscription=$(lowercase "${BASH_REMATCH[1]}")
acr_name=${BASH_REMATCH[2]}
if [[ "$(lowercase "$(az account show --query id -o tsv)")" != "$expected_subscription" ]]; then
  echo "##vso[task.logissue type=error]Azure subscription does not match ACR"
  exit 1
fi

normalized_managed_identity_resource_id=$(lowercase "$PYRIT_MANAGED_IDENTITY_RESOURCE_ID")
if [[ ! "$normalized_managed_identity_resource_id" =~ ^/subscriptions/($guid_pattern)/resourcegroups/[^/]+/providers/microsoft\.managedidentity/userassignedidentities/[a-z0-9_-]{3,128}$ ]] \
  || [[ "$(lowercase "${BASH_REMATCH[1]}")" != "$expected_subscription" ]]; then
  echo "##vso[task.logissue type=error]Managed identity resource ID is not canonical or is in another subscription"
  exit 1
fi

normalized_key_vault_resource_id=$(lowercase "$PYRIT_KEY_VAULT_RESOURCE_ID")
if [[ ! "$normalized_key_vault_resource_id" =~ ^/subscriptions/($guid_pattern)/resourcegroups/[^/]+/providers/microsoft\.keyvault/vaults/[a-z0-9-]{3,24}$ ]] \
  || [[ "$(lowercase "${BASH_REMATCH[1]}")" != "$expected_subscription" ]]; then
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
normalized_deployment_resource_group_id=$(lowercase "$deployment_resource_group_id")
if [[ "$normalized_deployment_resource_group_id" != "/subscriptions/$expected_subscription/resourcegroups/"* ]]; then
  echo "##vso[task.logissue type=error]Deployment resource group is in another subscription"
  exit 1
fi

expected_app_id="$deployment_resource_group_id/providers/Microsoft.App/containerApps/$PYRIT_APP_NAME"
expected_environment_id="$deployment_resource_group_id/providers/Microsoft.App/managedEnvironments/$PYRIT_APP_NAME-env"
expected_vnet_id="$deployment_resource_group_id/providers/Microsoft.Network/virtualNetworks/$PYRIT_APP_NAME-vnet"
expected_subnet_id="$expected_vnet_id/subnets/$PYRIT_APP_NAME-aca-subnet"
expected_nat_id="$deployment_resource_group_id/providers/Microsoft.Network/natGateways/$PYRIT_APP_NAME-nat"
expected_pip_id="$deployment_resource_group_id/providers/Microsoft.Network/publicIPAddresses/$PYRIT_APP_NAME-egress-pip"
normalized_expected_app_id=$(lowercase "$expected_app_id")
normalized_expected_environment_id=$(lowercase "$expected_environment_id")
normalized_expected_vnet_id=$(lowercase "$expected_vnet_id")
normalized_expected_subnet_id=$(lowercase "$expected_subnet_id")
normalized_expected_nat_id=$(lowercase "$expected_nat_id")
normalized_expected_pip_id=$(lowercase "$expected_pip_id")

existing_app=$(az containerapp show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME" \
  --query '{id:id,environmentId:properties.managedEnvironmentId,tags:tags}' -o json 2>/dev/null || true)
existing_environment=$(az containerapp env show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME-env" \
  --query '{id:id,publicNetworkAccess:properties.publicNetworkAccess}' -o json 2>/dev/null || true)
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

if [[ -z "$existing_app" || -z "$existing_environment" || -z "$existing_vnet" || -z "$existing_subnet" \
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

if [[ "$(jq -r '.id | ascii_downcase' <<< "$existing_app")" != "$normalized_expected_app_id" \
  || "$(jq -r '.environmentId | ascii_downcase' <<< "$existing_app")" != "$normalized_expected_environment_id" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_environment")" != "$normalized_expected_environment_id" \
  || ! "$(jq -r '.publicNetworkAccess' <<< "$existing_environment")" =~ ^(Enabled|Disabled)$ \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_vnet")" != "$normalized_expected_vnet_id" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_subnet")" != "$normalized_expected_subnet_id" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_nat")" != "$normalized_expected_nat_id" \
  || "$(jq -r '.id | ascii_downcase' <<< "$existing_pip")" != "$normalized_expected_pip_id" \
  || "$(jq -r '.natId | ascii_downcase' <<< "$existing_subnet")" != "$normalized_expected_nat_id" \
  || "$(jq -r '.pipId | ascii_downcase' <<< "$existing_nat")" != "$normalized_expected_pip_id" \
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
private_link_request_message="Azure Front Door private access to $PYRIT_APP_NAME"

parameters=(
  "appName=$PYRIT_APP_NAME"
  "containerImage=$immutable_image"
  "entraTenantId=$PYRIT_ENTRA_TENANT_ID"
  "entraClientId=$PYRIT_ENTRA_CLIENT_ID"
  "allowedGroupObjectIds=$PYRIT_ALLOWED_GROUP_OBJECT_IDS"
  "adminGroupObjectId=$PYRIT_ADMIN_GROUP_OBJECT_ID"
  "allowedCidr=${PYRIT_ALLOWED_CLIENT_CIDR:-}"
  "sqlServerFqdn=$PYRIT_SQL_SERVER_FQDN"
  "sqlDatabaseName=$PYRIT_SQL_DATABASE_NAME"
  "keyVaultResourceId=$PYRIT_KEY_VAULT_RESOURCE_ID"
  "acrResourceId=$PYRIT_ACR_RESOURCE_ID"
  "existingManagedIdentityResourceId=$PYRIT_MANAGED_IDENTITY_RESOURCE_ID"
  "enableOtel=$PYRIT_ENABLE_OTEL"
  "envSecretName=$PYRIT_ENV_SECRET_NAME"
  "pyritConfigFileUri=${PYRIT_CONFIG_FILE_URI:-}"
  "enableFrontDoor=true"
  "enableFrontDoorPrivateLink=true"
  "frontDoorPrivateLinkRequestMessage=$private_link_request_message"
  "disableContainerAppsPublicAccess=true"
  "vnetAddressPrefix=$PYRIT_VNET_ADDRESS_PREFIX"
  "infrastructureSubnetAddressPrefix=$PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX"
  "egressPublicIpTags=$existing_pip_ip_tags"
  "protectEgressPublicIp=true"
  "tags=$deployment_tags"
)

rollback_parameters=()
for parameter in "${parameters[@]}"; do
  case "$parameter" in
    enableFrontDoorPrivateLink=*) rollback_parameters+=("enableFrontDoorPrivateLink=false") ;;
    disableContainerAppsPublicAccess=*) rollback_parameters+=("disableContainerAppsPublicAccess=false") ;;
    *) rollback_parameters+=("$parameter") ;;
  esac
done

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
  --expected-subnet-id "$expected_subnet_id" \
  --expected-environment-id "$expected_environment_id"; then
  echo "##vso[task.logissue type=error]What-if contains a delete, cross-resource-group write, protected-network change, or core resource create"
  exit 1
fi

cutover_in_progress=false
rollback_public_origin() {
  local exit_code=$?
  trap - EXIT TERM INT
  if [[ "$cutover_in_progress" == "true" && "$exit_code" != "0" ]]; then
    echo "##vso[task.logissue type=warning]Private Link cutover failed; restoring the public ACA origin"
    local rollback_origin_host
    rollback_origin_host=$(az containerapp show \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
      --name "$PYRIT_APP_NAME" --query properties.configuration.ingress.fqdn -o tsv || true)

    if [[ -n "$rollback_origin_host" ]]; then
      az deployment group create \
        --name "$deployment_name-rollback-origin" \
        --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
        --template-file "$PYRIT_SOURCE_DIRECTORY/infra/modules/aca_front_door.bicep" \
        --parameters \
          "namePrefix=$PYRIT_APP_NAME" \
          "originHostName=$rollback_origin_host" \
          "tags=$deployment_tags" \
          "enablePrivateLink=false" || true
    fi

    local rollback_connections
    local rollback_connection_count=-1
    local normalized_connection_id
    rollback_connections=$(az network private-endpoint-connection list \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
      --name "$PYRIT_APP_NAME-env" \
      --type Microsoft.App/managedEnvironments -o json 2>/dev/null || true)
    if [[ -n "$rollback_connections" ]]; then
      while IFS= read -r connection_id; do
        [[ -z "$connection_id" ]] && continue
        normalized_connection_id=$(lowercase "$connection_id")
        if [[ "$normalized_connection_id" == "$normalized_expected_environment_id/privateendpointconnections/"* ]]; then
          az rest --method delete \
            --url "https://management.azure.com${connection_id}?api-version=2024-10-02-preview" || true
        fi
      done < <(jq -r --arg message "$private_link_request_message" \
        '.[] | select(.properties.privateLinkServiceConnectionState.description == $message) | .id' \
        <<< "$rollback_connections")
    fi

    for attempt in {1..20}; do
      rollback_connections=$(az network private-endpoint-connection list \
        --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
        --name "$PYRIT_APP_NAME-env" \
        --type Microsoft.App/managedEnvironments -o json 2>/dev/null || true)
      if [[ -z "$rollback_connections" ]]; then
        rollback_connection_count=-1
        [[ "$attempt" -lt 20 ]] && sleep 15
        continue
      fi
      rollback_connection_count=$(jq --arg message "$private_link_request_message" \
        '[.[] | select(.properties.privateLinkServiceConnectionState.description == $message)] | length' \
        <<< "$rollback_connections")
      [[ "$rollback_connection_count" == "0" ]] && break
      [[ "$attempt" -lt 20 ]] && sleep 15
    done

    if [[ "$rollback_connection_count" != "0" ]]; then
      echo "##vso[task.logissue type=error]ACA private endpoint connection deletion was not confirmed; public access remains disabled and manual recovery is required"
      exit "$exit_code"
    fi

    if az deployment group create \
      --name "$deployment_name-rollback" \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
      --template-file "$PYRIT_SOURCE_DIRECTORY/infra/main.bicep" \
      --parameters "${rollback_parameters[@]}"; then
      echo "##vso[task.logissue type=warning]Public ACA origin rollback completed"
    else
      echo "##vso[task.logissue type=error]Public ACA origin rollback failed; manual recovery is required"
    fi
  fi
  exit "$exit_code"
}
trap rollback_public_origin EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
cutover_in_progress=true

az deployment group create \
  --name "$deployment_name" \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --template-file "$PYRIT_SOURCE_DIRECTORY/infra/main.bicep" \
  --parameters "${parameters[@]}"

deployed_private_link_request_message=$(az deployment group show \
  --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --query properties.outputs.frontDoorPrivateLinkRequestMessage.value -o tsv)
if [[ "$deployed_private_link_request_message" != "$private_link_request_message" ]]; then
  echo "##vso[task.logissue type=error]Deployment Private Link request message does not match the approved pipeline value"
  exit 1
fi
origin_resource_url="https://management.azure.com${deployment_resource_group_id}/providers/Microsoft.Cdn/profiles/$PYRIT_APP_NAME-afd/originGroups/$PYRIT_APP_NAME-origin-group/origins/$PYRIT_APP_NAME-aca-origin?api-version=2024-09-01"
origin_private_link=$(az rest --method get --url "$origin_resource_url" \
  --query '{status:properties.sharedPrivateLinkResource.status,resourceId:properties.sharedPrivateLinkResource.privateLink.id}' -o json)
private_link_status=$(jq -r '.status // empty' <<< "$origin_private_link")
private_link_resource_id=$(jq -r '.resourceId // empty | ascii_downcase' <<< "$origin_private_link")
if [[ "$private_link_resource_id" != "$normalized_expected_environment_id" \
  || ! "$private_link_status" =~ ^(Pending|Approved)$ ]]; then
  echo "##vso[task.logissue type=error]Front Door Private Link does not target the expected ACA environment"
  exit 1
fi

matching_connections=''
for attempt in {1..20}; do
  connections=$(az network private-endpoint-connection list \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --name "$PYRIT_APP_NAME-env" \
    --type Microsoft.App/managedEnvironments -o json || echo '[]')
  matching_connections=$(jq -c --arg message "$private_link_request_message" \
    '[.[] | select(
      .properties.privateLinkServiceConnectionState.description == $message
      and (.properties.privateLinkServiceConnectionState.status == "Pending"
        or .properties.privateLinkServiceConnectionState.status == "Approved"))]' <<< "$connections")
  connection_count=$(jq 'length' <<< "$matching_connections")
  echo "Private Link request discovery attempt $attempt/20: $connection_count active connection(s)"
  [[ "$connection_count" -gt 0 ]] && break
  [[ "$attempt" -lt 20 ]] && sleep 15
done
if [[ "$(jq 'length' <<< "$matching_connections")" == "0" ]]; then
  echo "##vso[task.logissue type=error]Front Door did not create the expected ACA Private Link request"
  exit 1
fi

while IFS=$'\t' read -r connection_id connection_status; do
  normalized_connection_id=$(lowercase "$connection_id")
  if [[ "$normalized_connection_id" != "$normalized_expected_environment_id/privateendpointconnections/"* ]]; then
    echo "##vso[task.logissue type=error]Private Link request is outside the expected ACA environment"
    exit 1
  fi
  if [[ "$connection_status" == "Pending" ]]; then
    connection_name=${connection_id##*/}
    connection_suffix=${connection_name:0:8}
    az deployment group create \
      --name "$deployment_name-private-link-approval-$connection_suffix" \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
      --template-file "$PYRIT_SOURCE_DIRECTORY/infra/modules/aca_private_endpoint_approval.bicep" \
      --parameters \
        "environmentName=$PYRIT_APP_NAME-env" \
        "connectionName=$connection_name" \
        "approvalDescription=$private_link_request_message" -o none
  fi
done < <(jq -r '.[] | [.id, .properties.privateLinkServiceConnectionState.status] | @tsv' \
  <<< "$matching_connections")

approved_connection_count=0
for attempt in {1..20}; do
  connections=$(az network private-endpoint-connection list \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --name "$PYRIT_APP_NAME-env" \
    --type Microsoft.App/managedEnvironments -o json || echo '[]')
  approved_connection_count=$(jq --arg message "$private_link_request_message" \
    '[.[] | select(
      .properties.privateLinkServiceConnectionState.description == $message
      and .properties.privateLinkServiceConnectionState.status == "Approved")] | length' <<< "$connections")
  echo "ACA Private Link approval attempt $attempt/20: $approved_connection_count approved connection(s)"
  [[ "$approved_connection_count" -gt 0 ]] && break
  [[ "$attempt" -lt 20 ]] && sleep 15
done
if [[ "$approved_connection_count" == "0" ]]; then
  echo "##vso[task.logissue type=error]ACA Private Link connection did not become approved"
  exit 1
fi
echo "AFD origin status is ${private_link_status}; ACA approval and AFD health determine readiness"

public_network_access=$(az containerapp env show \
  --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
  --name "$PYRIT_APP_NAME-env" \
  --query properties.publicNetworkAccess -o tsv)
if [[ "$public_network_access" != "Disabled" ]]; then
  echo "##vso[task.logissue type=error]ACA environment public network access remains enabled"
  exit 1
fi

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
normalized_actual_pip_id=$(lowercase "$actual_pip_id")
if [[ "$egress_ip" != "$expected_egress_ip" \
  || "$normalized_actual_pip_id" != "$normalized_expected_pip_id" ]]; then
  echo "##vso[task.logissue type=error]Reserved egress PIP identity or address changed"
  exit 1
fi
front_door_health=""
front_door_health_timeout_seconds=1800
front_door_health_deadline=$((SECONDS + front_door_health_timeout_seconds))
attempt=0
while ((SECONDS < front_door_health_deadline)); do
  ((attempt += 1))
  remaining_seconds=$((front_door_health_deadline - SECONDS))
  request_timeout=$((remaining_seconds < 30 ? remaining_seconds : 30))
  front_door_health=$(curl \
    --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --max-time "$request_timeout" "https://$front_door_fqdn/api/health" || true)
  echo "Front Door health attempt $attempt (${remaining_seconds}s budget before request): ${front_door_health:-<connection-failed>}"
  [[ "$front_door_health" == "200" ]] && break
  remaining_seconds=$((front_door_health_deadline - SECONDS))
  ((remaining_seconds > 0)) || break
  sleep_seconds=$((remaining_seconds < 30 ? remaining_seconds : 30))
  sleep "$sleep_seconds"
done
if [[ "$front_door_health" != "200" ]]; then
  echo "##vso[task.logissue type=error]Front Door did not route a healthy response"
  exit 1
fi
direct_aca_health=$(curl \
  --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --max-time 15 "https://$app_fqdn/api/health" || true)
if [[ "$direct_aca_health" == "200" ]]; then
  echo "##vso[task.logissue type=error]Direct ACA public access remains reachable"
  exit 1
fi
cutover_in_progress=false
trap - EXIT TERM INT
echo "Deployment healthy; public URL: https://$front_door_fqdn; ACA public access: disabled; egress IPv4: $egress_ip"
