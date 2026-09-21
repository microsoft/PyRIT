// Shared Azure infrastructure for the PyRIT GUI. Does not deploy a Container App.

@description('Name for the Container App and related resources')
@minLength(2)
@maxLength(32)
param appName string = 'pyrit-gui'

@description('Azure region for all resources')
param location string = resourceGroup().location

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

@description('Resource ID of the Azure Container Registry (for AcrPull role assignment). Recommended over acrName for IaC-managed access.')
param acrResourceId string = ''

@description('Azure Container Registry name (for managed identity pull). Used if acrResourceId is not provided.')
param acrName string = ''

@description('Optional existing user-assigned managed identity resource ID. Empty creates a new identity using the existing naming behavior.')
param existingManagedIdentityResourceId string = ''

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

@description('Resource tags applied to all resources (ownership + data classification)')
param tags object = {
  Service: 'pyrit-gui'
  Owner: '<your-team>'
  DataClass: '<your-data-classification>'
}

var effectiveFrontDoorPrivateLink = enableFrontDoorPrivateLink && !enableFrontDoor
  ? fail('enableFrontDoor must be true when enableFrontDoorPrivateLink is true')
  : enableFrontDoorPrivateLink
var effectiveContainerAppsPublicAccess = disableContainerAppsPublicAccess
  ? (effectiveFrontDoorPrivateLink ? 'Disabled' : fail('Front Door Private Link is required before ACA public access can be disabled'))
  : 'Enabled'
var createLogAnalytics = logAnalyticsWorkspaceId == ''
var createAcr = acrResourceId == '' && acrName == ''
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

// ACA log ingestion requires a workspace shared key; it is not exposed to the app.
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
var effectiveManagedIdentityPrincipalId = createManagedIdentity
  ? managedIdentity!.properties.principalId
  : referencedManagedIdentity!.properties.principalId

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
      infrastructureSubnetId: acaNatNetwork.outputs.infrastructureSubnetId
      internal: false
    }
  }
}

var environmentDefaultDomain = acaEnvironment.properties.defaultDomain
var acaOriginHostName = '${appName}.${environmentDefaultDomain}'

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

@description('The deterministic ACA Private Link approval request message; empty when Private Link is disabled')
output frontDoorPrivateLinkRequestMessage string = effectiveFrontDoorPrivateLink
  ? acaFrontDoor!.outputs.privateLinkRequestMessage
  : ''

@description('The Azure Front Door managed HTTPS hostname')
output frontDoorFqdn string = enableFrontDoor ? acaFrontDoor!.outputs.endpointHostName : ''

@description('The default domain of the ACA environment')
output environmentDefaultDomain string = environmentDefaultDomain

@description('ACA environment public network access state')
output containerAppsPublicNetworkAccess string = effectiveContainerAppsPublicAccess

@description('Static outbound IPv4 address')
output egressPublicIpAddress string = acaNatNetwork.outputs.egressPublicIpAddress

@description('NAT Gateway resource ID')
output natGatewayId string = acaNatNetwork.outputs.natGatewayId

@description('ACA infrastructure subnet resource ID')
output acaInfrastructureSubnetId string = acaNatNetwork.outputs.infrastructureSubnetId

@description('Virtual network name')
output vnetName string = acaNatNetwork.outputs.vnetName

@description('The resource ID of the user-assigned managed identity')
output managedIdentityResourceId string = effectiveManagedIdentityId

@description('The principal ID of the user-assigned managed identity')
output managedIdentityPrincipalId string = effectiveManagedIdentityPrincipalId

@description('ACR login server')
output acrLoginServer string = effectiveAcrServer

@description('Application Insights connection string (if OTel enabled)')
output appInsightsConnectionString string = enableOtel
  ? appInsights!.properties.ConnectionString
  : 'N/A (OTel disabled)'
