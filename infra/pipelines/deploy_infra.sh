#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

source "${BASH_SOURCE[0]%/*}/deployment_common.sh"

validate_infra_inputs() {
  validate_common_inputs
  require_values PYRIT_VNET_ADDRESS_PREFIX PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX
  if ! python3 - "$PYRIT_VNET_ADDRESS_PREFIX" "$PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX" << 'PY'; then
import ipaddress
import sys

try:
    vnet = ipaddress.ip_network(sys.argv[1], strict=True)
    subnet = ipaddress.ip_network(sys.argv[2], strict=True)
    if vnet.version != 4 or subnet.version != 4 or not subnet.subnet_of(vnet) or subnet.prefixlen > 27:
        raise ValueError
except (ValueError, IndexError):
    raise SystemExit(1)
PY
    deployment_error "Invalid network prefix or subnet sizing"
  fi
}

validate_infra_topology() {
  if [[ "$(jq -r '.prefix' <<< "$existing_vnet")" != "$PYRIT_VNET_ADDRESS_PREFIX" ||
  "$(jq -r '.prefix' <<< "$existing_subnet")" != "$PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX" ]]; then
    deployment_error "Deployment variables do not match the existing protected topology"
  fi
  if [[ -z "$current_revision" || "$current_revision" == "null" || "$current_revision" == "None" ]]; then
    deployment_error "Infrastructure deployment requires an existing app revision"
  fi
  validate_immutable_image "$current_image"
  existing_pip_ip_tags=$(az network public-ip show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-egress-pip" \
    --query 'ipTags || `[]`' -o json | jq -c .)
}

build_infra_parameters() {
  template_file="$PYRIT_SOURCE_DIRECTORY/infra/infrastructure.bicep"
  deployment_name="pyrit-$PYRIT_SLOT-$PYRIT_BUILD_ID-infra"
  private_link_request_message="Azure Front Door private access to $PYRIT_APP_NAME"
  parameters=(
    "appName=$PYRIT_APP_NAME"
    "acrResourceId=$PYRIT_ACR_RESOURCE_ID"
    "existingManagedIdentityResourceId=$PYRIT_MANAGED_IDENTITY_RESOURCE_ID"
    "enableOtel=$PYRIT_ENABLE_OTEL"
    "enableFrontDoor=true"
    "frontDoorPrivateLinkRequestMessage=$private_link_request_message"
    "vnetAddressPrefix=$PYRIT_VNET_ADDRESS_PREFIX"
    "infrastructureSubnetAddressPrefix=$PYRIT_INFRASTRUCTURE_SUBNET_ADDRESS_PREFIX"
    "egressPublicIpTags=$existing_pip_ip_tags"
    "protectEgressPublicIp=true"
    "tags=$deployment_tags"
  )
  rollback_parameters=("${parameters[@]}" "enableFrontDoorPrivateLink=false" "disableContainerAppsPublicAccess=false")
  parameters+=("enableFrontDoorPrivateLink=true" "disableContainerAppsPublicAccess=true")
}

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
        --mode Incremental --parameters \
        "namePrefix=$PYRIT_APP_NAME" "originHostName=$rollback_origin_host" \
        "tags=$deployment_tags" "enablePrivateLink=false" || true
    fi
    local rollback_connections connection_id normalized_connection_id attempt
    local rollback_connection_count=-1
    rollback_connections=$(az network private-endpoint-connection list \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-env" \
      --type Microsoft.App/managedEnvironments -o json 2> /dev/null || true)
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
        --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-env" \
        --type Microsoft.App/managedEnvironments -o json 2> /dev/null || true)
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
      --template-file "$PYRIT_SOURCE_DIRECTORY/infra/infrastructure.bicep" \
      --mode Incremental --parameters "${rollback_parameters[@]}"; then
      echo "##vso[task.logissue type=warning]Public ACA origin rollback completed"
    else
      echo "##vso[task.logissue type=error]Public ACA origin rollback failed; manual recovery is required"
    fi
  fi
  exit "$exit_code"
}

approve_private_link() {
  local deployed_message origin_resource_url origin_private_link private_link_status private_link_resource_id
  deployed_message=$(az deployment group show \
    --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --query properties.outputs.frontDoorPrivateLinkRequestMessage.value -o tsv)
  if [[ "$deployed_message" != "$private_link_request_message" ]]; then
    deployment_error "Deployment Private Link request message does not match the approved pipeline value"
  fi
  origin_resource_url="https://management.azure.com${deployment_resource_group_id}/providers/Microsoft.Cdn/profiles/$PYRIT_APP_NAME-afd/originGroups/$PYRIT_APP_NAME-origin-group/origins/$PYRIT_APP_NAME-aca-origin?api-version=2024-09-01"
  origin_private_link=$(az rest --method get --url "$origin_resource_url" \
    --query '{status:properties.sharedPrivateLinkResource.status,resourceId:properties.sharedPrivateLinkResource.privateLink.id}' -o json)
  private_link_status=$(jq -r '.status // empty' <<< "$origin_private_link")
  private_link_resource_id=$(jq -r '.resourceId // empty | ascii_downcase' <<< "$origin_private_link")
  if [[ "$private_link_resource_id" != "$normalized_expected_environment_id" ||
    ! "$private_link_status" =~ ^(Pending|Approved)$ ]]; then
    deployment_error "Front Door Private Link does not target the expected ACA environment"
  fi
  local matching_connections='' connections connection_count attempt
  for attempt in {1..20}; do
    connections=$(az network private-endpoint-connection list \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-env" \
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
    deployment_error "Front Door did not create the expected ACA Private Link request"
  fi
  local connection_id connection_status normalized_connection_id connection_name connection_suffix
  while IFS=$'\t' read -r connection_id connection_status; do
    normalized_connection_id=$(lowercase "$connection_id")
    if [[ "$normalized_connection_id" != "$normalized_expected_environment_id/privateendpointconnections/"* ]]; then
      deployment_error "Private Link request is outside the expected ACA environment"
    fi
    if [[ "$connection_status" == "Pending" ]]; then
      connection_name=${connection_id##*/}
      connection_suffix=${connection_name:0:8}
      az deployment group create \
        --name "$deployment_name-private-link-approval-$connection_suffix" \
        --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
        --template-file "$PYRIT_SOURCE_DIRECTORY/infra/modules/aca_private_endpoint_approval.bicep" \
        --mode Incremental --parameters \
        "environmentName=$PYRIT_APP_NAME-env" "connectionName=$connection_name" \
        "approvalDescription=$private_link_request_message" -o none
    fi
  done < <(jq -r '.[] | [.id, .properties.privateLinkServiceConnectionState.status] | @tsv' \
    <<< "$matching_connections")
  local approved_connection_count=0
  for attempt in {1..20}; do
    connections=$(az network private-endpoint-connection list \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-env" \
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
    deployment_error "ACA Private Link connection did not become approved"
  fi
  echo "AFD origin status is ${private_link_status}; ACA approval and AFD health determine readiness"
}

main() {
  set -euo pipefail
  validate_infra_inputs
  initialize_deployment_scope
  read_existing_topology
  validate_infra_topology
  build_infra_parameters
  echo "Reconciling infrastructure only; leaving the running application unchanged"
  preview_deployment infra "$template_file" "${parameters[@]}"
  cutover_in_progress=false
  trap rollback_public_origin EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  cutover_in_progress=true
  az deployment group create \
    --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --template-file "$template_file" --mode Incremental --parameters "${parameters[@]}"
  approve_private_link
  verify_readiness 1800 Disabled "$immutable_image" "$current_revision"
  cutover_in_progress=false
  trap - EXIT TERM INT
  echo "Infrastructure healthy; app revision unchanged: $revision; verified $health_url; egress IPv4: $egress_ip"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
