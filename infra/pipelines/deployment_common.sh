#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

deployment_error() {
  echo "##vso[task.logissue type=error]$*"
  exit 1
}

lowercase() {
  printf '%s' "$1" | LC_ALL=C tr '[:upper:]' '[:lower:]'
}

require_values() {
  local variable_name
  for variable_name in "$@"; do
    if [[ -z "${!variable_name:-}" || "${!variable_name}" == '$('* ]]; then
      deployment_error "Required deployment value is missing: $variable_name"
    fi
  done
}

validate_optional_values() {
  local variable_name
  for variable_name in "$@"; do
    if [[ "${!variable_name:-}" == '$('* ]]; then
      deployment_error "Optional deployment value is unresolved: $variable_name"
    fi
  done
}

validate_common_inputs() {
  require_values PYRIT_SLOT PYRIT_BUILD_ID PYRIT_SOURCE_DIRECTORY PYRIT_AGENT_TEMP_DIRECTORY \
    PYRIT_DEPLOYMENT_RESOURCE_GROUP PYRIT_APP_NAME PYRIT_MANAGED_IDENTITY_RESOURCE_ID \
    PYRIT_ACR_RESOURCE_ID PYRIT_ENABLE_OTEL
  if [[ ! "$PYRIT_SLOT" =~ ^(test|prod)$ || ! "$PYRIT_BUILD_ID" =~ ^[0-9]+$ ]]; then
    deployment_error "Invalid slot or build ID"
  fi
  if [[ ! "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" =~ ^[[:alnum:]_.()-]{1,90}$ ||
    "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" == *. ||
    ! "$PYRIT_APP_NAME" =~ ^[a-z][a-z0-9-]{0,30}[a-z0-9]$ ]]; then
    deployment_error "Invalid deployment resource group or app name"
  fi
  if [[ ! "$PYRIT_ENABLE_OTEL" =~ ^(true|false)$ ]]; then
    deployment_error "Invalid enableOtel value"
  fi
  guid_pattern='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
  normalized_acr_resource_id=$(lowercase "$PYRIT_ACR_RESOURCE_ID")
  if [[ ! "$normalized_acr_resource_id" =~ ^/subscriptions/($guid_pattern)/resourcegroups/[^/]+/providers/microsoft\.containerregistry/registries/([a-z0-9]{5,50})$ ]]; then
    deployment_error "PYRIT_ACR_RESOURCE_ID is not canonical"
  fi
  expected_subscription=${BASH_REMATCH[1]}
  acr_name=${BASH_REMATCH[2]}
  normalized_managed_identity_resource_id=$(lowercase "$PYRIT_MANAGED_IDENTITY_RESOURCE_ID")
  if [[ ! "$normalized_managed_identity_resource_id" =~ ^/subscriptions/($guid_pattern)/resourcegroups/[^/]+/providers/microsoft\.managedidentity/userassignedidentities/[a-z0-9_-]{3,128}$ ]] \
    || [[ "${BASH_REMATCH[1]}" != "$expected_subscription" ]]; then
    deployment_error "Managed identity resource ID is not canonical or is in another subscription"
  fi
}

validate_immutable_image() {
  local requested_image=$1 registry_server repository digest
  if [[ ! "$requested_image" =~ ^([^/]+)/(.+)@(sha256:[0-9a-fA-F]{64})$ ]]; then
    deployment_error "Requested image must be an immutable registry digest"
  fi
  registry_server=${BASH_REMATCH[1]}
  repository=${BASH_REMATCH[2]}
  digest=${BASH_REMATCH[3]}
  if [[ "$registry_server" != "$acr_name.azurecr.io" ]]; then
    deployment_error "Requested image registry does not match ACR resource ID"
  fi
  local repository_pattern='^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$'
  if [[ ! "$repository" =~ $repository_pattern ]]; then
    deployment_error "Requested image repository is invalid"
  fi
  immutable_image="$registry_server/$repository@$digest"
}

initialize_deployment_scope() {
  if [[ "$(lowercase "$(az account show --query id -o tsv)")" != "$expected_subscription" ]]; then
    deployment_error "Azure subscription does not match ACR"
  fi
  if ! az resource show --ids "$PYRIT_MANAGED_IDENTITY_RESOURCE_ID" --api-version 2023-01-31 -o none 2> /dev/null; then
    deployment_error "Managed identity does not exist or is not readable"
  fi
  deployment_resource_group_id=$(az group show \
    --name "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --query id -o tsv 2> /dev/null || true)
  if [[ -z "$deployment_resource_group_id" ]]; then
    deployment_error "Deployment resource group must already exist"
  fi
  if [[ "$(lowercase "$deployment_resource_group_id")" != "/subscriptions/$expected_subscription/resourcegroups/"* ]]; then
    deployment_error "Deployment resource group is in another subscription"
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
}

read_existing_topology() {
  existing_app=$(az containerapp show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" \
    --query '{id:id,environmentId:properties.managedEnvironmentId,tags:tags,mode:properties.configuration.activeRevisionsMode,revision:properties.latestRevisionName,containers:properties.template.containers[].{name:name,image:image}}' -o json 2> /dev/null || true)
  existing_environment=$(az containerapp env show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-env" \
    --query '{id:id,publicNetworkAccess:properties.publicNetworkAccess}' -o json 2> /dev/null || true)
  existing_vnet=$(az network vnet show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-vnet" \
    --query '{id:id,prefix:addressSpace.addressPrefixes[0],tags:tags}' -o json 2> /dev/null || true)
  existing_subnet=$(az network vnet subnet show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --vnet-name "$PYRIT_APP_NAME-vnet" \
    --name "$PYRIT_APP_NAME-aca-subnet" \
    --query '{id:id,prefix:addressPrefix,natId:natGateway.id}' -o json 2> /dev/null || true)
  existing_nat=$(az network nat gateway show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-nat" \
    --query '{id:id,pipId:publicIpAddresses[0].id,tags:tags}' -o json 2> /dev/null || true)
  existing_pip=$(az network public-ip show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-egress-pip" \
    --query '{id:id,ip:ipAddress,allocation:publicIPAllocationMethod,sku:sku.name,tags:tags}' -o json 2> /dev/null || true)
  if [[ -z "$existing_app" || -z "$existing_environment" || -z "$existing_vnet" ||
    -z "$existing_subnet" || -z "$existing_nat" || -z "$existing_pip" ]]; then
    deployment_error "Internal deployments must adopt an existing app, environment, VNet, subnet, NAT, and egress PIP"
  fi
  deployment_tags=$(jq -cS '.tags' <<< "$existing_app")
  local pip_tags nat_tags vnet_tags
  pip_tags=$(jq -cS '.tags' <<< "$existing_pip")
  nat_tags=$(jq -cS '.tags' <<< "$existing_nat")
  vnet_tags=$(jq -cS '.tags' <<< "$existing_vnet")
  expected_egress_ip=$(jq -r '.ip // empty' <<< "$existing_pip")
  if [[ "$(jq -r '.id | ascii_downcase' <<< "$existing_app")" != "$normalized_expected_app_id" ||
  "$(jq -r '.environmentId | ascii_downcase' <<< "$existing_app")" != "$normalized_expected_environment_id" ||
  "$(jq -r '.id | ascii_downcase' <<< "$existing_environment")" != "$normalized_expected_environment_id" ||
  ! "$(jq -r '.publicNetworkAccess' <<< "$existing_environment")" =~ ^(Enabled|Disabled)$ ||
  "$(jq -r '.id | ascii_downcase' <<< "$existing_vnet")" != "$normalized_expected_vnet_id" ||
  "$(jq -r '.id | ascii_downcase' <<< "$existing_subnet")" != "$normalized_expected_subnet_id" ||
  "$(jq -r '.id | ascii_downcase' <<< "$existing_nat")" != "$normalized_expected_nat_id" ||
  "$(jq -r '.id | ascii_downcase' <<< "$existing_pip")" != "$normalized_expected_pip_id" ||
  "$(jq -r '.natId | ascii_downcase' <<< "$existing_subnet")" != "$normalized_expected_nat_id" ||
  "$(jq -r '.pipId | ascii_downcase' <<< "$existing_nat")" != "$normalized_expected_pip_id" ||
  "$(jq -r '.allocation' <<< "$existing_pip")" != "Static" ||
  "$(jq -r '.sku' <<< "$existing_pip")" != "Standard" || -z "$expected_egress_ip" ]]; then
    deployment_error "Deployment variables do not match the existing protected topology"
  fi
  if [[ "$deployment_tags" == *'<'* || "$deployment_tags" == "null" ||
    "$deployment_tags" != "$pip_tags" || "$deployment_tags" != "$nat_tags" || "$deployment_tags" != "$vnet_tags" ]]; then
    deployment_error "Protected resource tags are missing, placeholders, or inconsistent"
  fi
  if [[ "$(jq '.containers | length' <<< "$existing_app")" != "1" ||
  "$(jq -r '.containers[0].name' <<< "$existing_app")" != "pyrit-gui" ||
  "$(jq -r '.mode' <<< "$existing_app")" != "Single" ]]; then
    deployment_error "Deployment requires the existing single-revision pyrit-gui container"
  fi
  current_image=$(jq -r '.containers[0].image // empty' <<< "$existing_app")
  current_revision=$(jq -r '.revision // empty' <<< "$existing_app")
  echo "Current image (not automatically restored after an app failure): $current_image"
}

preview_deployment() {
  local deployment_mode=$1 template_file=$2
  shift 2
  local what_if_file="$PYRIT_AGENT_TEMP_DIRECTORY/$deployment_name-what-if.json"
  az deployment group what-if \
    --name "$deployment_name-preview" \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --template-file "$template_file" --mode Incremental \
    --parameters "$@" --result-format FullResourcePayloads --no-pretty-print -o json > "$what_if_file"
  if ! python3 "$PYRIT_SOURCE_DIRECTORY/infra/pipelines/validate_what_if.py" \
    --what-if-file "$what_if_file" \
    --deployment-mode "$deployment_mode" \
    --deployment-resource-group-id "$deployment_resource_group_id" \
    --expected-app-id "$expected_app_id" \
    --expected-pip-id "$expected_pip_id" \
    --expected-nat-id "$expected_nat_id" \
    --expected-vnet-id "$expected_vnet_id" \
    --expected-subnet-id "$expected_subnet_id" \
    --expected-environment-id "$expected_environment_id"; then
    deployment_error "What-if contains a forbidden write, delete, protected-network change, or core resource create"
  fi
}

wait_for_http_health() {
  local health_url=$1 timeout_seconds=$2
  local health_deadline=$((SECONDS + timeout_seconds))
  local application_health="" attempt=0 remaining_seconds request_timeout sleep_seconds
  while ((SECONDS < health_deadline)); do
    ((attempt += 1))
    remaining_seconds=$((health_deadline - SECONDS))
    request_timeout=$((remaining_seconds < 30 ? remaining_seconds : 30))
    if ! application_health=$(curl \
      --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --max-time "$request_timeout" "$health_url"); then
      application_health=""
    fi
    echo "Application health at $health_url attempt $attempt (${remaining_seconds}s budget before request): ${application_health:-<connection-failed>}"
    [[ "$application_health" == "200" ]] && break
    remaining_seconds=$((health_deadline - SECONDS))
    ((remaining_seconds > 0)) || break
    sleep_seconds=$((remaining_seconds < 30 ? remaining_seconds : 30))
    sleep "$sleep_seconds"
  done
  if [[ "$application_health" != "200" ]]; then
    deployment_error "Application endpoint did not return a healthy response"
  fi
}

verify_readiness() {
  local timeout_seconds=$1 expected_public_access=$2 expected_image=$3 unchanged_revision=${4:-}
  local public_network_access
  public_network_access=$(az containerapp env show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-env" \
    --query properties.publicNetworkAccess -o tsv)
  if [[ "$public_network_access" != "$expected_public_access" ]]; then
    deployment_error "ACA environment public network access differs from the expected mode"
  fi
  revision=$(az containerapp show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" \
    --query properties.latestRevisionName -o tsv)
  if [[ -z "$revision" || "$revision" == "null" || "$revision" == "None" ]]; then
    deployment_error "Container App did not report a revision"
  fi
  if [[ -n "$unchanged_revision" && "$revision" != "$unchanged_revision" ]]; then
    deployment_error "Infrastructure-only deployment changed the running app revision"
  fi
  local health="" revision_state attempt
  for attempt in {1..5}; do
    revision_state=$(az containerapp revision show \
      --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" \
      --revision "$revision" \
      --query '{image:properties.template.containers[0].image,health:properties.healthState}' -o json)
    if [[ "$(jq -r '.image' <<< "$revision_state")" != "$expected_image" ]]; then
      deployment_error "Deployed revision does not contain the requested image"
    fi
    health=$(jq -r '.health // empty' <<< "$revision_state")
    echo "Revision $revision health attempt $attempt/5: ${health:-<not-found>}"
    [[ "$health" == "Healthy" ]] && break
    [[ "$attempt" -lt 5 ]] && sleep 120
  done
  if [[ "$health" != "Healthy" ]]; then
    deployment_error "Deployed revision did not become healthy"
  fi
  local app_fqdn front_door_fqdn actual_pip
  app_fqdn=$(az containerapp show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" \
    --query properties.configuration.ingress.fqdn -o tsv)
  front_door_fqdn=$(az deployment group show \
    --name "$deployment_name" --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" \
    --query properties.outputs.frontDoorFqdn.value -o tsv)
  actual_pip=$(az network public-ip show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-egress-pip" \
    --query '{id:id,ip:ipAddress}' -o json)
  egress_ip=$(jq -r '.ip // empty' <<< "$actual_pip")
  if [[ "$egress_ip" != "$expected_egress_ip" ||
    "$(jq -r '.id | ascii_downcase' <<< "$actual_pip")" != "$normalized_expected_pip_id" ]]; then
    deployment_error "Reserved egress PIP identity or address changed"
  fi
  if [[ ! "$app_fqdn" =~ ^[a-z0-9][a-z0-9.-]*\.azurecontainerapps\.io$ ]]; then
    deployment_error "Deployment returned an invalid ACA hostname"
  fi
  health_url="https://$app_fqdn/api/health"
  if [[ "$expected_public_access" == "Disabled" ]]; then
    if [[ ! "$front_door_fqdn" =~ ^[a-z0-9][a-z0-9.-]*\.azurefd\.net$ ]]; then
      deployment_error "Private ACA access requires a valid Front Door hostname"
    fi
    health_url="https://$front_door_fqdn/api/health"
  fi
  wait_for_http_health "$health_url" "$timeout_seconds"
  if [[ "$expected_public_access" == "Disabled" ]]; then
    local direct_aca_health
    direct_aca_health=$(curl \
      --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --max-time 15 "https://$app_fqdn/api/health" || true)
    if [[ "$direct_aca_health" == "200" ]]; then
      deployment_error "Direct ACA public access remains reachable"
    fi
  fi
  local final_app final_access
  final_app=$(az containerapp show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME" \
    --query '{latest:properties.latestRevisionName,ready:properties.latestReadyRevisionName,image:properties.template.containers[0].image}' -o json)
  final_access=$(az containerapp env show \
    --resource-group "$PYRIT_DEPLOYMENT_RESOURCE_GROUP" --name "$PYRIT_APP_NAME-env" \
    --query properties.publicNetworkAccess -o tsv)
  if [[ "$(jq -r '.latest' <<< "$final_app")" != "$revision" ||
  "$(jq -r '.ready' <<< "$final_app")" != "$revision" ||
  "$(jq -r '.image' <<< "$final_app")" != "$expected_image" || "$final_access" != "$expected_public_access" ]]; then
    deployment_error "Verified image is not the current ready revision or the access mode changed"
  fi
}
