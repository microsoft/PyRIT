@description('Prefix used to name Front Door resources')
param namePrefix string

@description('ACA-generated origin hostname without a scheme')
param originHostName string

@description('Resource tags applied to the Front Door profile')
param tags object

var endpointSuffix = take(uniqueString(subscription().id, resourceGroup().id, namePrefix), 8)

resource profile 'Microsoft.Cdn/profiles@2024-09-01' = {
  name: '${namePrefix}-afd'
  location: 'global'
  tags: tags
  sku: {
    name: 'Premium_AzureFrontDoor'
  }
  properties: {
    originResponseTimeoutSeconds: 60
  }
}

resource endpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-09-01' = {
  parent: profile
  name: '${namePrefix}-${endpointSuffix}'
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
}

resource originGroup 'Microsoft.Cdn/profiles/originGroups@2024-09-01' = {
  parent: profile
  name: '${namePrefix}-origin-group'
  properties: {
    healthProbeSettings: {
      probeIntervalInSeconds: 30
      probePath: '/api/health'
      probeProtocol: 'Https'
      probeRequestType: 'GET'
    }
    loadBalancingSettings: {
      additionalLatencyInMilliseconds: 50
      sampleSize: 4
      successfulSamplesRequired: 3
    }
    sessionAffinityState: 'Disabled'
  }
}

resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2024-09-01' = {
  parent: originGroup
  name: '${namePrefix}-aca-origin'
  properties: {
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
    hostName: originHostName
    httpPort: 80
    httpsPort: 443
    originHostHeader: originHostName
    priority: 1
    weight: 1000
  }
}

resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-09-01' = {
  parent: endpoint
  name: '${namePrefix}-route'
  properties: {
    enabledState: 'Enabled'
    forwardingProtocol: 'HttpsOnly'
    httpsRedirect: 'Enabled'
    linkToDefaultDomain: 'Enabled'
    originGroup: {
      id: originGroup.id
    }
    patternsToMatch: [
      '/*'
    ]
    supportedProtocols: [
      'Http'
      'Https'
    ]
  }
  dependsOn: [
    origin
  ]
}

output endpointHostName string = endpoint.properties.hostName
output endpointId string = endpoint.id
output profileId string = profile.id