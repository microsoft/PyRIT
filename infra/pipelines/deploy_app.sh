#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

source "${BASH_SOURCE[0]%/*}/deployment_common.sh"

validate_app_inputs() {
  validate_common_inputs
  if ((${#PYRIT_APP_NAME} > 24)); then
    deployment_error "PYRIT_APP_NAME must have 24 characters or fewer for the migration job name"
  fi
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
    "requireCurrentSchema=true"
    "tags=$deployment_tags"
  )
}

stop_app() {
  local revisions revision replica_count active_count attempt
  revisions=$(az containerapp revision list \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" --all -o json)
  local revision_names
  if ! revision_names=$(jq -er 'if type == "array" and length > 0 then .[].name else error("No revisions found") end' <<< "$revisions"); then
    deployment_error "Could not identify app revisions to stop"
  fi
  while IFS= read -r revision; do
    if jq -e --arg name "$revision" '.[] | select(.name == $name and .properties.active == true)' <<< "$revisions" > /dev/null; then
      echo "Stopping revision $revision for the database migration"
      az containerapp revision deactivate \
        --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" --revision "$revision" -o none
    fi
  done <<< "$revision_names"
  for ((attempt = 1; attempt <= ${PYRIT_STOP_POLL_ATTEMPTS:-60}; attempt++)); do
    active_count=$(az containerapp revision list \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" --all \
      --query 'length([?properties.active])' -o tsv)
    replica_count=0
    while IFS= read -r revision; do
      local count
      count=$(az containerapp replica list \
        --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" \
        --revision "$revision" --query 'length(@)' -o tsv)
      [[ "$count" =~ ^[0-9]+$ ]] || deployment_error "Could not confirm replica shutdown for $revision"
      replica_count=$((replica_count + count))
    done <<< "$revision_names"
    [[ "$active_count" == "0" && "$replica_count" == "0" ]] && return 0
    echo "Waiting for app shutdown: $active_count active revisions, $replica_count replicas"
    sleep "${PYRIT_STOP_POLL_SECONDS:-5}"
  done
  deployment_error "App shutdown was not confirmed; the database migration was not started"
}

start_unchanged_revision() {
  local latest_revision
  latest_revision=$(az containerapp show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" \
    --query properties.latestRevisionName -o tsv)
  if [[ "$latest_revision" == "$current_revision" ]]; then
    echo "App template is unchanged; starting revision $current_revision"
    az containerapp revision activate \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" --revision "$current_revision" -o none
  fi
}

prepare_database_migration() {
  local deployment_name="$deployment_name-migrate"
  local migration_template="$PYRIT_SOURCE_DIRECTORY/infra/migration.bicep"
  local migration_parameters=(
    "appName=$PYRIT_APP_NAME"
    "containerImage=$immutable_image"
    "existingManagedIdentityResourceId=$PYRIT_MANAGED_IDENTITY_RESOURCE_ID"
    "acrResourceId=$PYRIT_ACR_RESOURCE_ID"
    "sqlServerFqdn=$PYRIT_SQL_SERVER_FQDN"
    "sqlDatabaseName=$PYRIT_SQL_DATABASE_NAME"
    "keyVaultResourceId=$PYRIT_KEY_VAULT_RESOURCE_ID"
    "envSecretName=$PYRIT_ENV_SECRET_NAME"
    "pyritConfigFileUri=${PYRIT_CONFIG_FILE_URI:-}"
    "tags=$deployment_tags"
  )
  preview_deployment job "$migration_template" "${migration_parameters[@]}"
  az deployment group create \
    --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --template-file "$migration_template" --mode Incremental --parameters "${migration_parameters[@]}" -o none
}

run_database_migration() {
  local job_name="$PYRIT_APP_NAME-migrate"
  local execution status="" attempt
  execution=$(az containerapp job start \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$job_name" --query name -o tsv)
  [[ -n "$execution" ]] || deployment_error "Database migration job did not start"
  for ((attempt = 1; attempt <= ${PYRIT_MIGRATION_POLL_ATTEMPTS:-90}; attempt++)); do
    status=$(az containerapp job execution show \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$job_name" \
      --job-execution-name "$execution" --query properties.status -o tsv)
    echo "Database migration $execution: $status"
    case "$status" in
      Succeeded) return 0 ;;
      Failed | Stopped | Degraded)
        deployment_error "Database migration $execution ended with status $status; the app is stopped and was not deployed"
        ;;
    esac
    sleep "${PYRIT_MIGRATION_POLL_SECONDS:-20}"
  done
  deployment_error "Database migration $execution did not finish in time; the app is stopped and was not deployed"
}

warn_if_sql_public() {
  local sql_public_access
  sql_public_access=$(az sql server list \
    --query "[?fullyQualifiedDomainName=='$PYRIT_SQL_SERVER_FQDN'].publicNetworkAccess | [0]" -o tsv 2> /dev/null || true)
  if [[ "$sql_public_access" != "Disabled" ]]; then
    echo "##vso[task.logissue type=warning]SQL server $PYRIT_SQL_SERVER_FQDN public network access: ${sql_public_access:-unknown}. See infra/README.md SQL isolation."
  fi
}

main() {
  set -euo pipefail
  validate_app_inputs
  initialize_deployment_scope
  read_existing_topology
  read_app_access_mode
  build_app_parameters
  preview_deployment app "$template_file" "${parameters[@]}"
  prepare_database_migration
  stop_app
  run_database_migration
  az deployment group create \
    --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --template-file "$template_file" --mode Incremental --parameters "${parameters[@]}"
  start_unchanged_revision
  verify_readiness 300 "$expected_public_access" "$immutable_image"
  warn_if_sql_public
  echo "Deployment healthy: $revision; verified $health_url; ACA public access: $expected_public_access; egress IPv4: $egress_ip"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
