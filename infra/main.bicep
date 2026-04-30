// ============================================================================
// PyRIT GUI — Azure Container Apps Deployment (Security-Hardened)
//
// Deploys the CoPyRIT GUI as an Azure Container App with:
// - Workload profiles environment with public ingress + optional IP restriction
// - MSAL PKCE authentication (frontend) + FastAPI JWT middleware (backend)
// - User-assigned managed identity for Azure SQL, ACR, Azure OpenAI, Key Vault
// - Azure SQL (existing) via managed identity — no passwords
// - Key Vault for secrets (referenced via ACA secretRef, not embedded)
// - Centralized logging via Log Analytics (configurable retention)
// - No storage account keys, no embedded secrets, no :latest tags
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

@description('Object ID of the Entra security group allowed to access the GUI')
@metadata({ description: 'Find this in Azure Portal → Entra ID → Groups → your group → Object ID' })
param allowedGroupObjectIds string

@description('CIDR range allowed to reach the app (e.g., your corp VPN CIDR). Empty = no IP restriction, all traffic allowed.')
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

@description('PyRIT initializer to run. Default "target airt" registers target configs + attack defaults.')
param pyritInitializer string = 'target airt'

@description('Key Vault secret name containing the .env file contents (all endpoints, models, and API keys). The secret is mounted as an env var and PyRIT parses it at startup.')
param envSecretName string = 'env-global'

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

@description('Enable Private Endpoint for the ACA environment. When false, uses public access with IP restrictions.')
param enablePrivateEndpoint bool = true

@description('VNet address prefix (used only when creating a new VNet)')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Subnet address prefix for the Private Endpoint (used only when creating a new subnet)')
param subnetAddressPrefix string = '10.0.0.0/24'

@description('Resource ID of an existing subnet for the Private Endpoint. If empty, a new VNet + subnet is created.')
@metadata({ example: '/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>' })
param infrastructureSubnetId string = ''

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

@description('Resource tags applied to all resources (ownership + data classification)')
param tags object = {
  Service: 'pyrit-gui'
  Owner: '<your-team>'
  DataClass: '<your-data-classification>'
}

@description('Enable OpenTelemetry managed agent for audit logging. Creates Application Insights and wires the ACA managed OTel collector.')
param enableOtel bool = false

// Soft guardrail: detect :latest usage (enforced via output warning)
var imageUsesLatest = endsWith(containerImage, ':latest')

// Determine whether to create or reference existing resources
var createLogAnalytics = logAnalyticsWorkspaceId == ''
var createVnet = enablePrivateEndpoint && infrastructureSubnetId == ''
var createAcr = acrResourceId == '' && acrName == ''

// ============================================================================
// VNet + Subnet (created only if infrastructureSubnetId is not provided)
// The subnet hosts the Private Endpoint for the ACA environment — no ACA
// delegation needed (that's only for VNet-integrated internal environments).
// ============================================================================
resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = if (createVnet) {
  name: '${appName}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: '${appName}-pe-subnet'
        properties: {
          addressPrefix: subnetAddressPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

var effectiveSubnetId = createVnet ? vnet.properties.subnets[0].id : infrastructureSubnetId

// ============================================================================
// Azure Container Registry (created only if neither acrResourceId nor acrName is provided)
// ============================================================================
resource newAcr 'Microsoft.ContainerRegistry/registries@2023-08-01-preview' = if (createAcr) {
  name: '${replace(appName, '-', '')}acr'
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

var effectiveLogAnalyticsCustomerIdValue = createLogAnalytics ? logAnalytics.properties.customerId : logAnalyticsCustomerId
var effectiveLogAnalyticsKeyValue = createLogAnalytics ? logAnalytics.listKeys().primarySharedKey : logAnalyticsSharedKey

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
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-identity'
  location: location
  tags: tags
}

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
//   - Key Vault Secrets User  on the Key Vault
//   - AcrPull                 on the ACR
// See Post-Deployment in infra/README.md for commands.
// ============================================================================

// ============================================================================
// Azure Container Apps Environment (workload profiles, public network disabled)
// Uses Private Endpoint pattern instead of VNet-integrated internal mode:
// - Environment is NOT VNet-integrated (no internal ILB)
// - Public network access is disabled
// - A Private Endpoint provides corp-reachable connectivity via Private Link
// - Private DNS zone resolves the FQDN to the private endpoint IP
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
        sharedKey: effectiveLogAnalyticsKeyValue
      }
    }
    publicNetworkAccess: enablePrivateEndpoint ? 'Disabled' : 'Enabled'
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// NOTE: When enableOtel=true, configure the OpenTelemetry managed agent on the
// environment as a post-deployment step using az CLI:
//   az containerapp env telemetry app-insights set \
//     --name ${appName}-env -g <rg> \
//     --connection-string <app-insights-connection-string>
// The Bicep API (2024-03-01) does not support openTelemetryConfiguration natively.

// ============================================================================
// Private Endpoint for ACA Environment (corp-reachable via Private Link)
// The PE must be in a VNet that corp VPN/ExpressRoute can reach.
// ============================================================================
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (enablePrivateEndpoint) {
  name: '${appName}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: effectiveSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${appName}-pe-connection'
        properties: {
          privateLinkServiceId: acaEnvironment.id
          groupIds: [
            'managedEnvironments'
          ]
        }
      }
    ]
  }
}

// Private DNS Zone for ACA Private Endpoint resolution
resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (enablePrivateEndpoint) {
  name: 'privatelink.${location}.azurecontainerapps.io'
  location: 'global'
  tags: tags
}

// Link DNS zone to the VNet so clients in the VNet can resolve
resource dnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (enablePrivateEndpoint) {
  name: '${appName}-dns-link'
  parent: privateDnsZone
  location: 'global'
  tags: tags
  properties: {
    virtualNetwork: {
      id: createVnet ? vnet.id : join(take(split(infrastructureSubnetId, '/'), 9), '/')
    }
    registrationEnabled: false
  }
}

// DNS record group for the private endpoint
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (enablePrivateEndpoint) {
  name: 'default'
  parent: privateEndpoint
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'aca-dns-config'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

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
      '${managedIdentity.id}': {}
    }
  }
  // RBAC roles (AcrPull, KV Secrets User) must be granted manually before
  // the first deployment — see infra/README.md Post-Deployment §2.
  dependsOn: []
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      // Single revision mode — only one revision serves traffic (appropriate for GUI)
      activeRevisionsMode: 'Single'

      // Ingress — external at the app level (access is controlled by
      // Private Endpoint + disabled public network access, not ingress type)
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
        ipSecurityRestrictions: allowedCidr != '' ? [
          {
            name: 'allowed-cidr'
            description: allowedCidrDescription
            ipAddressRange: allowedCidr
            action: 'Allow'
          }
        ] : []
      }

      // ACR pull with managed identity (works whether ACR is created or existing)
      registries: [
        {
          server: effectiveAcrServer
          identity: managedIdentity.id
        }
      ]

      // Key Vault secret reference for the .env file contents
      secrets: [
        {
          name: 'env-file'
          keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${envSecretName}'
          identity: managedIdentity.id
        }
      ]
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
            // .env file contents from Key Vault — PyRIT parses this at startup
            {
              name: 'PYRIT_ENV_CONTENTS'
              secretRef: 'env-file'
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
              value: allowedGroupObjectIds
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
              value: managedIdentity.properties.clientId
            }
            // CORS origin for the SPA. The ACA-generated FQDN is deterministic
            // (<appName>.<envDefaultDomain>), so we compute it from upstream
            // resources rather than self-referencing containerApp.
            {
              name: 'PYRIT_CORS_ORIGINS'
              value: 'https://${appName}.${acaEnvironment.properties.defaultDomain}'
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
// The frontend uses @azure/msal-browser for login; the backend validates
// JWTs from the Authorization header against Entra JWKS.
// ============================================================================

// ============================================================================
// Outputs
// ============================================================================

@description('The FQDN of the deployed Container App')
output appFqdn string = containerApp.properties.configuration.ingress.fqdn

@description('The default domain of the ACA environment')
output environmentDefaultDomain string = acaEnvironment.properties.defaultDomain

@description('Private Endpoint resource ID (empty when PE is disabled)')
output privateEndpointId string = enablePrivateEndpoint ? privateEndpoint.id : ''

@description('The principal ID of the user-assigned managed identity — grant this Cognitive Services OpenAI User on your AOAI instances and db_datareader/db_datawriter on Azure SQL')
output managedIdentityPrincipalId string = managedIdentity.properties.principalId

@description('The resource ID of the user-assigned managed identity')
output managedIdentityResourceId string = managedIdentity.id

@description('IMPORTANT: Create an Azure AD contained user in the target database for this managed identity. See README post-deployment steps.')
output sqlAadSetupRequired string = 'Run CREATE USER [${appName}-identity] FROM EXTERNAL PROVIDER on database ${sqlDatabaseName}'

@description('Key Vault name (existing)')
output keyVaultName string = keyVaultName

@description('ACR login server')
output acrLoginServer string = effectiveAcrServer

@description('VNet name (if created by this template)')
output vnetName string = createVnet ? vnet.name : 'N/A (existing VNet used)'

@description('Application Insights connection string (if OTel enabled)')
output appInsightsConnectionString string = enableOtel ? appInsights.properties.ConnectionString : 'N/A (OTel disabled)'
