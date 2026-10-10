// Administrator-run SQL private path. Does not change shared SQL server access.
// Keep the subnet properties equal to aca_nat_network.bicep so infrastructure what-if shows NoChange.
param appName string
param location string = resourceGroup().location
param sqlServerResourceId string
@description('Unused prefix inside the app VNet address space, for example a /28')
param sqlSubnetAddressPrefix string

resource sqlSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  name: '${appName}-vnet/${appName}-sql-subnet'
  properties: {
    addressPrefix: sqlSubnetAddressPrefix
    defaultOutboundAccess: false
    privateEndpointNetworkPolicies: 'Disabled'
  }
}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink${environment().suffixes.sqlServerHostname}'
  location: 'global'
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: '${appName}-sql'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: resourceId('Microsoft.Network/virtualNetworks', '${appName}-vnet')
    }
  }
}

resource endpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${appName}-sql'
  location: location
  properties: {
    subnet: {
      id: sqlSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: '${appName}-sql'
        properties: {
          privateLinkServiceId: sqlServerResourceId
          groupIds: [
            'sqlServer'
          ]
        }
      }
    ]
  }
}

resource dns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: endpoint
  name: 'sql'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'sql'
        properties: {
          privateDnsZoneId: zone.id
        }
      }
    ]
  }
}
