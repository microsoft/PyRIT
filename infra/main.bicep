// ============================================================================
// PyRIT GUI — Azure Container Apps Deployment (Security-Hardened)
//
// Deploys the CoPyRIT GUI as an Azure Container App with:
// - Azure Front Door Premium public entry point
// - VNet-integrated public workload-profiles environment with fixed NAT egress
// - MSAL PKCE authentication (frontend) + Microsoft Graph-backed auth (backend)
// - User-assigned managed identity for Azure SQL, ACR, Azure OpenAI, Key Vault
// - Azure SQL (existing) via managed identity — no passwords
// - Inline ACA secret or backend-managed Key Vault environment source
// - Centralized logging via Log Analytics (configurable retention)
// - No storage account keys or secrets embedded in source/container images
//
// Prerequisites:
// 1. An Entra ID app registration (no secrets/certs needed — PKCE public client)
// 2. A container image pushed to an Azure Container Registry (unique tag or digest)
// 3. Existing Azure SQL server with Entra admin configured
//
// Usage:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/main.bicep \
//     --parameters appName=pyrit-gui \
//                  containerImage=<acr>.azurecr.io/pyrit:<commit-sha> \
//                  entraClientId=<app-registration-client-id> \
//                  entraTenantId=<tenant-id> \
//                  allowedGroupObjectIds=<comma-separated-entra-group-ids> \
//                  allowedCidr='<your-corp-vpn-cidr>' \
//                  sqlServerFqdn=<your-server>.database.windows.net \
//                  sqlDatabaseName=<your-database> \
//                  keyVaultResourceId=<key-vault-resource-id>
// ============================================================================

// --- Parameters ---

@description('Name for the Container App and related resources')
@minLength(2)
@maxLength(32)
param appName string = 'pyrit-gui'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Container image — must use a unique tag (commit SHA) or digest, never :latest. Enforce in CI pipeline.')
@metadata({ example: 'myacr.azurecr.io/pyrit:a1b2c3d or myacr.azurecr.io/pyrit@sha256:...' })
param containerImage string

@description('Entra ID tenant ID')
param entraTenantId string

@description('Entra ID app registration client ID (no secrets needed)')
param entraClientId string

@description('Comma-separated object IDs of Entra security groups allowed to access the GUI. Find each ID in Azure Portal → Entra ID → Groups → your group → Object ID.')
@minLength(1)
param allowedGroupObjectIds string

var normalizedAllowedGroupObjectIds = filter(
  map(split(allowedGroupObjectIds, ','), groupId => trim(groupId)),
  groupId => !empty(groupId)
)
var validatedAllowedGroupObjectIds = !empty(normalizedAllowedGroupObjectIds)
  ? normalizedAllowedGroupObjectIds
  : fail('allowedGroupObjectIds must contain at least one non-empty group ID')

@description('Object ID of the Entra security group allowed to manage backend configuration')
@minLength(1)
param adminGroupObjectId string
var normalizedAdminGroupObjectId = trim(adminGroupObjectId)
var validatedAdminGroupObjectId = !empty(normalizedAdminGroupObjectId)
  ? normalizedAdminGroupObjectId
  : fail('adminGroupObjectId must contain a non-empty group ID')

@description('CIDR range allowed to reach ACA directly. Empty = unrestricted. Must be empty when Front Door is enabled because ACA sees Front Door backend IPs, not client IPs.')
param allowedCidr string = ''

@description('Human-readable description for the IP restriction rule')
param allowedCidrDescription string = 'Allowed IP range'

@description('Azure SQL server FQDN (e.g., myserver.database.windows.net)')
param sqlServerFqdn string

@description('Azure SQL database name')
param sqlDatabaseName string

// --- PyRIT Configuration (.pyrit_conf equivalent) ---
// Note: operator and operation are per-user settings configured in the GUI,
// not deployment-level config.

@description('PyRIT initializer to run. Default "target" registers target configs.')
param pyritInitializer string = 'target'

@secure()
@description('Optional Azure Blob HTTPS URI for the backend .pyrit_conf. The deployment helper accepts credential-free managed-identity URIs only. When empty, start.sh generates config from the SQL and initializer parameters.')
param pyritConfigFileUri string = ''

@description('Key Vault secret name containing the .env file contents. Used as env_akv_ref when envFileContents is empty.')
param envSecretName string = 'env-global'

@secure()
@description('Optional raw .env file contents. If provided, this is used directly instead of reading from Key Vault.')
param envFileContents string = ''

@description('Container CPU cores')
param cpuCores string = '1.0'

@description('Container memory in GB')
param memoryGb string = '2.0'

@description('Minimum number of replicas')
param minReplicas int = 1

@description('Maximum number of replicas')
param maxReplicas int = 1

@description('Azure Container Registry name (for managed identity pull). Used if acrResourceId is not provided.')
param acrName string = ''

@description('Virtual network address prefix')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Dedicated ACA infrastructure subnet prefix')
param infrastructureSubnetAddressPrefix string = '10.0.1.0/26'

@description('Existing Azure Policy IP tags to preserve when adopting a reserved egress public IP')
param egressPublicIpTags array = []

@description('Protect the static egress public IP from accidental deletion')
param protectEgressPublicIp bool = false

@description('Log Analytics retention in days (used only when creating a new workspace)')
param logRetentionDays int = 90

@description('Resource ID of an existing Log Analytics workspace. If provided, you must also provide logAnalyticsCustomerId. Recommended for orgs with a central governance workspace.')
param logAnalyticsWorkspaceId string = ''

@description('Customer ID of an existing Log Analytics workspace (required if logAnalyticsWorkspaceId is provided)')
param logAnalyticsCustomerId string = ''

@secure()
@description('Shared key of an existing Log Analytics workspace (required if logAnalyticsWorkspaceId is provided). This is used only for ACA log ingestion config.')
param logAnalyticsSharedKey string = ''

@description('Resource ID of an existing Key Vault (required). Use your org\'s governed vault to avoid soft-delete/purge-protection issues on redeployment.')
param keyVaultResourceId string

@description('Resource ID of the Azure Container Registry (for AcrPull role assignment). Recommended over acrName for IaC-managed access.')
param acrResourceId string = ''

@description('Optional existing user-assigned managed identity resource ID. Empty creates a new identity using the existing naming behavior.')
param existingManagedIdentityResourceId string = ''

@description('Resource tags applied to all resources (ownership + data classification)')
param tags object = {
  Service: 'pyrit-gui'
  Owner: '<your-team>'
  DataClass: '<your-data-classification>'
}

@description('Enable OpenTelemetry managed agent for audit logging. Creates Application Insights and wires the ACA managed OTel collector.')
param enableOtel bool = false

@description('Create Azure Front Door Premium as the public application endpoint')
param enableFrontDoor bool = false

@description('Connect Azure Front Door Premium to the ACA environment through Private Link')
param enableFrontDoorPrivateLink bool = false

@description('Deterministic message used to discover and approve the ACA Private Link request')
param frontDoorPrivateLinkRequestMessage string = 'Azure Front Door private access to ${appName}'

@description('Disable the ACA environment public endpoint after Front Door Private Link is configured')
param disableContainerAppsPublicAccess bool = false

// Determine whether to create or reference existing resources
var effectiveAllowedCidr = enableFrontDoor && !empty(allowedCidr)
  ? fail('allowedCidr must be empty when enableFrontDoor is true')
  : allowedCidr
var effectiveFrontDoorPrivateLink = enableFrontDoorPrivateLink && !enableFrontDoor
  ? fail('enableFrontDoor must be true when enableFrontDoorPrivateLink is true')
  : enableFrontDoorPrivateLink
var effectiveContainerAppsPublicAccess = disableContainerAppsPublicAccess
  ? (effectiveFrontDoorPrivateLink ? 'Disabled' : fail('Front Door Private Link is required before ACA public access can be disabled'))
  : 'Enabled'
var createLogAnalytics = logAnalyticsWorkspaceId == ''
var createAcr = acrResourceId == '' && acrName == ''
var useInlineEnvFile = !empty(envFileContents)
var createManagedIdentity = empty(existingManagedIdentityResourceId)
var generatedAcrName = '${padLeft(replace(appName, '-', ''), 2, 'p')}acr'
var existingManagedIdentitySegments = split(existingManagedIdentityResourceId, '/')
var existingManagedIdentitySubscriptionId = createManagedIdentity ? subscription().subscriptionId : existingManagedIdentitySegments[2]
var existingManagedIdentityResourceGroupName = createManagedIdentity ? resourceGroup().name : existingManagedIdentitySegments[4]
var existingManagedIdentityName = createManagedIdentity ? '' : last(existingManagedIdentitySegments)

module acaNatNetwork './modules/aca_nat_network.bicep' = {
  name: '${appName}-aca-nat-network'
  params: {
    namePrefix: appName
    location: location
    tags: tags
    vnetAddressPrefix: vnetAddressPrefix
    infrastructureSubnetAddressPrefix: infrastructureSubnetAddressPrefix
    egressPublicIpTags: egressPublicIpTags
    protectEgressPublicIp: protectEgressPublicIp
  }
}

// ============================================================================
// Azure Container Registry (created only if neither acrResourceId nor acrName is provided)
// ============================================================================
resource newAcr 'Microsoft.ContainerRegistry/registries@2023-08-01-preview' = if (createAcr) {
  name: generatedAcrName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    adminUserEnabled: false
  }
}

var effectiveAcrName = createAcr ? newAcr.name : (acrName != '' ? acrName : last(split(acrResourceId, '/')))
var effectiveAcrServer = '${effectiveAcrName}.azurecr.io'

// ============================================================================
// Log Analytics Workspace
// Created only if logAnalyticsWorkspaceId is not provided. For orgs with a
// central governance workspace, pass the existing workspace ID instead.
// Note: The ACA environment requires a shared key to connect to Log Analytics.
// This is the only supported integration method as of the 2024-03-01 API.
// The key is used during deployment for log ingestion config only — it is NOT
// injected into the container or accessible to application code.
// ============================================================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (createLogAnalytics) {
  name: '${appName}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
  }
}

var effectiveLogAnalyticsCustomerIdValue = createLogAnalytics ? logAnalytics!.properties.customerId : logAnalyticsCustomerId
var effectiveLogAnalyticsKeyValue = createLogAnalytics ? logAnalytics!.listKeys().primarySharedKey : logAnalyticsSharedKey

// ============================================================================
// Application Insights (created when OTel is enabled — destination for traces/logs)
// ============================================================================
resource appInsights 'Microsoft.Insights/components@2020-02-02' = if (enableOtel) {
  name: '${appName}-ai'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: createLogAnalytics ? logAnalytics.id : logAnalyticsWorkspaceId
  }
}

// ============================================================================
// User-Assigned Managed Identity
// Created BEFORE the container app so roles can be granted before the first
// revision starts. This avoids the chicken-and-egg problem with system-assigned
// MI where the revision tries to pull images / access KV before RBAC propagates.
// ============================================================================
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (createManagedIdentity) {
  name: '${appName}-identity'
  location: location
  tags: tags
}

resource referencedManagedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = if (!createManagedIdentity) {
  name: existingManagedIdentityName
  scope: resourceGroup(existingManagedIdentitySubscriptionId, existingManagedIdentityResourceGroupName)
}

var effectiveManagedIdentityId = createManagedIdentity ? managedIdentity!.id : referencedManagedIdentity!.id
var effectiveManagedIdentityClientId = createManagedIdentity
  ? managedIdentity!.properties.clientId
  : referencedManagedIdentity!.properties.clientId
var effectiveManagedIdentityPrincipalId = createManagedIdentity
  ? managedIdentity!.properties.principalId
  : referencedManagedIdentity!.properties.principalId

// ============================================================================
// Key Vault (existing — avoids soft-delete/purge-protection redeployment issues)
// All auth uses managed identity (Azure SQL, ACR, AOAI). The vault is for
// downstream API keys or sensitive config added as ACA Key Vault secret
// references. Ensure the vault has RBAC authorization enabled.
// ============================================================================
// Extract KV name and resource group from the resource ID.
// keyVaultResourceId format: /subscriptions/.../resourceGroups/<rg>/providers/.../vaults/<name>
var keyVaultName = last(split(keyVaultResourceId, '/'))

// ============================================================================
// RBAC role assignments are NOT managed by this template.
// Grant the following roles to the UAMI manually before first deployment:
//   - Key Vault Secrets Officer on the Key Vault when envFileContents is empty
//   - AcrPull                 on the ACR
// See Post-Deployment in infra/README.md for commands.
// ============================================================================

// ============================================================================
// Azure Container Apps Environment (workload profiles)
// Public ACA-managed HTTPS ingress with optional app-level IP restrictions and
// VNet-integrated fixed NAT egress.
//
// OTel: When enableOtel=true, configure the managed OTel agent
// as a post-deploy CLI step (2024-03-01 schema does not support it natively).
// ============================================================================
resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: '${appName}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: effectiveLogAnalyticsCustomerIdValue
        dynamicJsonColumns: false
        sharedKey: effectiveLogAnalyticsKeyValue
      }
    }
    peerAuthentication: {
      mtls: {
        enabled: false
      }
    }
    peerTrafficConfiguration: {
      encryption: {
        enabled: false
      }
    }
    publicNetworkAccess: effectiveContainerAppsPublicAccess
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    vnetConfiguration: {
      infrastructureSubnetId: acaNatNetwork!.outputs.infrastructureSubnetId
      internal: false
    }
  }
}

var acaOriginHostName = '${appName}.${acaEnvironment.properties.defaultDomain}'

module acaFrontDoor './modules/aca_front_door.bicep' = if (enableFrontDoor) {
  name: '${appName}-aca-front-door'
  params: {
    namePrefix: appName
    originHostName: acaOriginHostName
    tags: tags
    enablePrivateLink: effectiveFrontDoorPrivateLink
    originResourceId: acaEnvironment.id
    originLocation: location
    privateLinkRequestMessage: frontDoorPrivateLinkRequestMessage
  }
}

// NOTE: When enableOtel=true, configure the OpenTelemetry managed agent on the
// environment as a post-deployment step using az CLI:
//   az containerapp env telemetry app-insights set \
//     --name ${appName}-env -g <rg> \
//     --connection-string <app-insights-connection-string>
// The Bicep API (2024-03-01) does not support openTelemetryConfiguration natively.

// ============================================================================
// Container App — PyRIT GUI
// ============================================================================
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${effectiveManagedIdentityId}': {}
    }
  }
  // RBAC roles (AcrPull, Key Vault Secrets Officer) must be granted manually before
  // the first deployment — see infra/README.md Post-Deployment §2.
  dependsOn: []
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      // Single revision mode — only one revision serves traffic (appropriate for GUI)
      activeRevisionsMode: 'Single'

      // ACA-managed public HTTPS ingress, optionally restricted by source CIDR.
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
        ipSecurityRestrictions: effectiveAllowedCidr != '' ? [
          {
            name: 'allowed-cidr'
            description: allowedCidrDescription
            ipAddressRange: effectiveAllowedCidr
            action: 'Allow'
          }
        ] : []
      }

      // ACR pull with managed identity (works whether ACR is created or existing)
      registries: [
        {
          server: effectiveAcrServer
          identity: effectiveManagedIdentityId
        }
      ]

      secrets: concat(
        useInlineEnvFile ? [
          {
            name: 'env-file'
            value: envFileContents
          }
        ] : [],
        !empty(pyritConfigFileUri) ? [
          {
            name: 'config-file-uri'
            value: pyritConfigFileUri
          }
        ] : []
      )
    }

    template: {
      containers: [
        {
          name: 'pyrit-gui'
          image: containerImage
          resources: {
            cpu: json(cpuCores)
            memory: '${memoryGb}Gi'
          }
          env: [
            {
              name: 'PYRIT_MODE'
              value: 'gui'
            }
            {
              name: 'AZURE_SQL_SERVER'
              value: sqlServerFqdn
            }
            {
              name: 'AZURE_SQL_DATABASE'
              value: sqlDatabaseName
            }
            // .pyrit_conf equivalent (operator/operation set per-user in GUI)
            {
              name: 'PYRIT_INITIALIZER'
              value: pyritInitializer
            }
            // Keep the managed-identity config URI out of plain Container App configuration.
            !empty(pyritConfigFileUri)
              ? {
                  name: 'PYRIT_CONFIG_FILE'
                  secretRef: 'config-file-uri'
                }
              : {
                  name: 'PYRIT_CONFIG_FILE'
                  value: ''
                }
            useInlineEnvFile
              ? {
                  name: 'PYRIT_ENV_CONTENTS'
                  secretRef: 'env-file'
                }
              : {
                  name: 'PYRIT_ENV_AKV_REF'
                  value: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${envSecretName}'
                }
            // MSAL PKCE auth config — frontend uses these to authenticate users
            // Easy Auth is NOT used because the tenant blocks client secrets/certs
            // on app registrations. PKCE (public client) needs no secrets.
            {
              name: 'ENTRA_CLIENT_ID'
              value: entraClientId
            }
            {
              name: 'ENTRA_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'ENTRA_ALLOWED_GROUP_IDS'
              value: join(validatedAllowedGroupObjectIds, ',')
            }
            {
              name: 'ENTRA_ADMIN_GROUP_ID'
              value: validatedAdminGroupObjectId
            }
            // OTel: point the SDK at the ACA managed agent (localhost sidecar)
            {
              name: 'OTEL_EXPORTER_OTLP_ENDPOINT'
              value: enableOtel ? 'http://localhost:4318' : ''
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: appName
            }
            // DefaultAzureCredential needs the UAMI client ID to pick the correct identity
            {
              name: 'AZURE_CLIENT_ID'
              value: effectiveManagedIdentityClientId
            }
            // The ACA URL is usable only while environment public access remains enabled.
            {
              name: 'PYRIT_CORS_ORIGINS'
              value: enableFrontDoor
                ? (effectiveContainerAppsPublicAccess == 'Disabled'
                  ? 'https://${acaFrontDoor!.outputs.endpointHostName}'
                  : 'https://${acaOriginHostName},https://${acaFrontDoor!.outputs.endpointHostName}')
                : 'https://${acaOriginHostName}'
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

// ============================================================================
// NOTE: Easy Auth (authConfigs) is intentionally NOT used.
// The tenant's credential policy blocks client secrets and trusted-CA-only
// certificates on app registrations, making Easy Auth's OAuth authorization
// code flow impossible. Instead, authentication is handled in-app using
// MSAL with PKCE (public client flow) — no secrets needed.
// The frontend uses @azure/msal-browser to acquire a delegated Microsoft Graph
// token; the backend validates it through trusted Graph endpoints and applies
// local group-based authorization.
// ============================================================================

// ============================================================================
// Outputs
// ============================================================================

@description('The generated ACA FQDN; inaccessible when ACA public network access is disabled')
output appFqdn string = containerApp.properties.configuration.ingress.fqdn

@description('The Azure Front Door managed HTTPS hostname')
output frontDoorFqdn string = enableFrontDoor ? acaFrontDoor!.outputs.endpointHostName : ''

@description('The Azure Front Door public URL')
output frontDoorUrl string = enableFrontDoor ? 'https://${acaFrontDoor!.outputs.endpointHostName}' : ''

@description('The deterministic ACA Private Link approval request message; empty when Private Link is disabled')
output frontDoorPrivateLinkRequestMessage string = effectiveFrontDoorPrivateLink
  ? acaFrontDoor!.outputs.privateLinkRequestMessage
  : ''

@description('ACA environment public network access state')
output containerAppsPublicNetworkAccess string = effectiveContainerAppsPublicAccess

@description('The public application FQDN selected for this deployment')
output publicFqdn string = enableFrontDoor ? acaFrontDoor!.outputs.endpointHostName : containerApp.properties.configuration.ingress.fqdn

@description('The default domain of the ACA environment')
output environmentDefaultDomain string = acaEnvironment.properties.defaultDomain

@description('Static outbound IPv4 address')
output egressPublicIpAddress string = acaNatNetwork!.outputs.egressPublicIpAddress

@description('NAT Gateway resource ID')
output natGatewayId string = acaNatNetwork!.outputs.natGatewayId

@description('ACA infrastructure subnet resource ID')
output acaInfrastructureSubnetId string = acaNatNetwork!.outputs.infrastructureSubnetId

@description('The principal ID of the user-assigned managed identity — grant this Cognitive Services OpenAI User on your AOAI instances and db_datareader/db_datawriter on Azure SQL')
output managedIdentityPrincipalId string = effectiveManagedIdentityPrincipalId

@description('The resource ID of the user-assigned managed identity')
output managedIdentityResourceId string = effectiveManagedIdentityId

@description('IMPORTANT: Create an Azure AD contained user in the target database for this managed identity. See README post-deployment steps.')
output sqlAadSetupRequired string = createManagedIdentity
  ? 'Run CREATE USER [${appName}-identity] FROM EXTERNAL PROVIDER on database ${sqlDatabaseName}'
  : 'Verify the existing managed identity has the required contained user and database roles on ${sqlDatabaseName}'

@description('Key Vault name (existing)')
output keyVaultName string = keyVaultName

@description('ACR login server')
output acrLoginServer string = effectiveAcrServer

@description('Virtual network name')
output vnetName string = acaNatNetwork!.outputs.vnetName

@description('Application Insights connection string (if OTel enabled)')
output appInsightsConnectionString string = enableOtel ? appInsights!.properties.ConnectionString : 'N/A (OTel disabled)'
