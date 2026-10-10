// Manual ACA job that upgrades the app database schema before each internal app deployment.
// It reuses the app image, identity, registry, and Key Vault environment source.

@minLength(2)
@maxLength(24)
param appName string
param location string = resourceGroup().location
@minLength(1)
param containerImage string
param existingManagedIdentityResourceId string
param acrResourceId string
param sqlServerFqdn string
param sqlDatabaseName string
param keyVaultResourceId string
param envSecretName string
@secure()
param pyritConfigFileUri string = ''
param tags object

var identitySegments = split(existingManagedIdentityResourceId, '/')

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(identitySegments)
  scope: resourceGroup(identitySegments[2], identitySegments[4])
}

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: '${appName}-env'
}

resource migrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${appName}-migrate'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: acaEnvironment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: '${last(split(acrResourceId, '/'))}.azurecr.io'
          identity: identity.id
        }
      ]
      secrets: !empty(pyritConfigFileUri) ? [
        {
          name: 'config-file-uri'
          value: pyritConfigFileUri
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'pyrit-migrate'
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2.0Gi'
          }
          env: [
            {
              name: 'PYRIT_MODE'
              value: 'migrate'
            }
            !empty(pyritConfigFileUri) ? {
              name: 'PYRIT_CONFIG_FILE'
              secretRef: 'config-file-uri'
            } : {
              name: 'PYRIT_CONFIG_FILE'
              value: ''
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
              name: 'PYRIT_ENV_AKV_REF'
              value: 'https://${last(split(keyVaultResourceId, '/'))}${environment().suffixes.keyvaultDns}/secrets/${envSecretName}'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identity.properties.clientId
            }
          ]
        }
      ]
    }
  }
}

output migrationJobName string = migrationJob.name
