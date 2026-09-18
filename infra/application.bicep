// PyRIT GUI image and configuration on existing Azure infrastructure.

@description('Name for the Container App and related resources')
@minLength(2)
@maxLength(32)
param appName string = 'pyrit-gui'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Container image — must use a unique tag (commit SHA) or digest, never :latest. Enforce in CI pipeline.')
@metadata({ example: 'myacr.azurecr.io/pyrit:a1b2c3d or myacr.azurecr.io/pyrit@sha256:...' })
@minLength(1)
param containerImage string

@description('Entra ID tenant ID')
param entraTenantId string

@description('Entra ID app registration client ID (no secrets needed)')
param entraClientId string

@description('Comma-separated object IDs of Entra security groups allowed to access the GUI. Find each ID in Azure Portal → Entra ID → Groups → your group → Object ID.')
@minLength(1)
param allowedGroupObjectIds string

@description('Object ID of the Entra security group allowed to manage backend configuration')
@minLength(1)
param adminGroupObjectId string

@description('Allow administrators to register and manage custom initializers')
param allowCustomInitializers bool = false

@description('Azure SQL server FQDN (e.g., myserver.database.windows.net)')
param sqlServerFqdn string

@description('Azure SQL database name')
param sqlDatabaseName string

@description('Comma-separated PyRIT initializers to run. Defaults register target configs and attack techniques.')
param pyritInitializer string = 'target,technique'

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

@description('CIDR range allowed to reach ACA directly. Empty = unrestricted. Must be empty when Front Door is enabled because ACA sees Front Door backend IPs, not client IPs.')
param allowedCidr string = ''

@description('Human-readable description for the IP restriction rule')
param allowedCidrDescription string = 'Allowed IP range'

@description('Resource ID of an existing Key Vault (required). Use your org\'s governed vault to avoid soft-delete/purge-protection issues on redeployment.')
param keyVaultResourceId string

@description('Resource ID of the Azure Container Registry (for AcrPull role assignment). Recommended over acrName for IaC-managed access.')
param acrResourceId string = ''

@description('Azure Container Registry name (for managed identity pull). Used if acrResourceId is not provided.')
param acrName string = ''

@description('Resource ID of the existing user-assigned managed identity')
@minLength(1)
param existingManagedIdentityResourceId string

@description('Enable OpenTelemetry SDK export to the ACA managed OTel collector')
param enableOtel bool = false

@description('Use the existing Azure Front Door endpoint as the public application endpoint')
param enableFrontDoor bool = false

@description('Resource tags applied to all resources (ownership + data classification)')
param tags object = {
  Service: 'pyrit-gui'
  Owner: '<your-team>'
  DataClass: '<your-data-classification>'
}

var normalizedAllowedGroupObjectIds = filter(
  map(split(allowedGroupObjectIds, ','), groupId => trim(groupId)),
  groupId => !empty(groupId)
)
var validatedAllowedGroupObjectIds = !empty(normalizedAllowedGroupObjectIds)
  ? normalizedAllowedGroupObjectIds
  : fail('allowedGroupObjectIds must contain at least one non-empty group ID')
var normalizedAdminGroupObjectId = trim(adminGroupObjectId)
var validatedAdminGroupObjectId = !empty(normalizedAdminGroupObjectId)
  ? normalizedAdminGroupObjectId
  : fail('adminGroupObjectId must contain a non-empty group ID')
var effectiveAllowedCidr = enableFrontDoor && !empty(allowedCidr)
  ? fail('allowedCidr must be empty when enableFrontDoor is true')
  : allowedCidr
var effectiveAcrName = acrName != ''
  ? acrName
  : (acrResourceId != '' ? last(split(acrResourceId, '/')) : fail('App-only deployment requires an existing registry'))
var effectiveAcrServer = '${effectiveAcrName}.azurecr.io'
var useInlineEnvFile = !empty(envFileContents)
var keyVaultName = last(split(keyVaultResourceId, '/'))
var existingManagedIdentitySegments = split(existingManagedIdentityResourceId, '/')

resource referencedManagedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(existingManagedIdentitySegments)
  scope: resourceGroup(existingManagedIdentitySegments[2], existingManagedIdentitySegments[4])
}

var effectiveManagedIdentityId = referencedManagedIdentity.id
var effectiveManagedIdentityClientId = referencedManagedIdentity.properties.clientId

resource existingAcaEnvironment 'Microsoft.App/managedEnvironments@2024-10-02-preview' existing = {
  name: '${appName}-env'
}

resource existingFrontDoorEndpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-09-01' existing = if (enableFrontDoor) {
  name: '${appName}-afd/${appName}-${take(uniqueString(subscription().id, resourceGroup().id, appName), 8)}'
}

var acaOriginHostName = '${appName}.${existingAcaEnvironment.properties.defaultDomain}'
var frontDoorHostName = enableFrontDoor ? existingFrontDoorEndpoint!.properties.hostName : ''
var effectiveContainerAppsPublicAccess = existingAcaEnvironment.properties.publicNetworkAccess

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
  // Grant AcrPull and any required Key Vault roles before the first revision.
  properties: {
    managedEnvironmentId: existingAcaEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
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
            {
              name: 'PYRIT_INITIALIZER'
              value: pyritInitializer
            }
            {
              name: 'PYRIT_ALLOW_CUSTOM_INITIALIZERS'
              value: allowCustomInitializers ? 'true' : 'false'
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
            // MSAL PKCE authenticates without Easy Auth client secrets or certificates.
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
            {
              name: 'OTEL_EXPORTER_OTLP_ENDPOINT'
              value: enableOtel ? 'http://localhost:4318' : ''
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: appName
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: effectiveManagedIdentityClientId
            }
            // The ACA URL is usable only while environment public access remains enabled.
            {
              name: 'PYRIT_CORS_ORIGINS'
              value: enableFrontDoor
                ? (effectiveContainerAppsPublicAccess == 'Disabled'
                  ? 'https://${frontDoorHostName}'
                  : 'https://${acaOriginHostName},https://${frontDoorHostName}')
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

@description('The generated ACA FQDN; inaccessible when ACA public network access is disabled')
output appFqdn string = containerApp.properties.configuration.ingress.fqdn

@description('The Azure Front Door managed HTTPS hostname')
output frontDoorFqdn string = frontDoorHostName

@description('ACA environment public network access state')
output containerAppsPublicNetworkAccess string = effectiveContainerAppsPublicAccess
