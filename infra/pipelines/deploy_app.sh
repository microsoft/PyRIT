#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

source "${BASH_SOURCE[0]%/*}/deployment_common.sh"

validate_app_inputs() {
  validate_common_inputs
  require_values PYRIT_CONTAINER_IMAGE PYRIT_ENTRA_TENANT_ID PYRIT_ENTRA_CLIENT_ID \
    PYRIT_ALLOWED_GROUP_OBJECT_IDS PYRIT_ADMIN_GROUP_OBJECT_ID PYRIT_SQL_SERVER_FQDN \
    PYRIT_SQL_DATABASE_NAME PYRIT_KEY_VAULT_RESOURCE_ID PYRIT_ENV_SECRET_NAME
  validate_optional_values PYRIT_ALLOWED_CLIENT_CIDR PYRIT_CONFIG_FILE_URI
  if [[ -n "${PYRIT_ALLOWED_CLIENT_CIDR:-}" ]]; then
    deployment_error "Front Door cannot use an ACA client CIDR restriction because ACA sees Front Door backend IPs, not client IPs; leave PYRIT_ALLOWED_CLIENT_CIDR empty"
  fi
  local normalized_key_vault_resource_id
  normalized_key_vault_resource_id=$(lowercase "$PYRIT_KEY_VAULT_RESOURCE_ID")
  if [[ ! "$normalized_key_vault_resource_id" =~ ^/subscriptions/($guid_pattern)/resourcegroups/[^/]+/providers/microsoft\.keyvault/vaults/[a-z0-9-]{3,24}$ ]] \
    || [[ "${BASH_REMATCH[1]}" != "$expected_subscription" ]]; then
    deployment_error "Key Vault resource ID is not canonical or is in another subscription"
  fi
  if [[ ! "$PYRIT_SQL_SERVER_FQDN" =~ ^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]\.database\.windows\.net$ ||
    ! "$PYRIT_ENV_SECRET_NAME" =~ ^[a-zA-Z0-9-]{1,127}$ ]]; then
    deployment_error "Invalid SQL FQDN or Key Vault secret name"
  fi
  if ! python3 - "$PYRIT_ENTRA_TENANT_ID" "$PYRIT_ENTRA_CLIENT_ID" \
    "$PYRIT_ALLOWED_GROUP_OBJECT_IDS" "$PYRIT_ADMIN_GROUP_OBJECT_ID" "${PYRIT_CONFIG_FILE_URI:-}" << 'PY'; then
import sys
import uuid
from urllib.parse import urlparse

try:
    uuid.UUID(sys.argv[1])
    uuid.UUID(sys.argv[2])
    groups = [value.strip() for value in sys.argv[3].split(",") if value.strip()]
    if not groups:
        raise ValueError
    for group in groups:
        uuid.UUID(group)
    uuid.UUID(sys.argv[4])
    if sys.argv[5]:
        parsed = urlparse(sys.argv[5])
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
    deployment_error "Invalid Entra ID, group ID, or config URI"
  fi
  validate_immutable_image "$PYRIT_CONTAINER_IMAGE"
}

read_app_access_mode() {
  expected_public_access=$(jq -r '.publicNetworkAccess' <<< "$existing_environment")
  local front_door_count
  front_door_count=$(az resource list \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --resource-type Microsoft.Cdn/profiles --name "$PYRIT_APP_NAME-afd" --query 'length(@)' -o tsv)
  case "$front_door_count" in
    0) enable_front_door=false ;;
    1) enable_front_door=true ;;
    *) deployment_error "Could not identify the existing Front Door profile" ;;
  esac
  if [[ "$expected_public_access" == "Disabled" && "$enable_front_door" != "true" ]]; then
    deployment_error "Private ACA access requires the existing Front Door profile"
  fi
}

build_app_parameters() {
  template_file="$PYRIT_SOURCE_DIRECTORY/infra/application.bicep"
  deployment_name="pyrit-$PYRIT_SLOT-$PYRIT_BUILD_ID-app"
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
    "enableFrontDoor=$enable_front_door"
    "tags=$deployment_tags"
  )
}

main() {
  set -euo pipefail
  validate_app_inputs
  initialize_deployment_scope
  read_existing_topology
  read_app_access_mode
  build_app_parameters
  preview_deployment app "$template_file" "${parameters[@]}"
  az deployment group create \
    --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --template-file "$template_file" --mode Incremental --parameters "${parameters[@]}"
  verify_readiness 300 "$expected_public_access" "$immutable_image"
  echo "Deployment healthy: $revision; verified $health_url; ACA public access: $expected_public_access; egress IPv4: $egress_ip"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
