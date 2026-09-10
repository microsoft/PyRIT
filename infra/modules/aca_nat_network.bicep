@description('Prefix used to name the Container Apps network resources')
param namePrefix string

@description('Azure region for the network resources')
param location string

@description('Resource tags applied to the network resources')
param tags object

@description('Virtual network address prefix')
param vnetAddressPrefix string

@description('Dedicated Container Apps infrastructure subnet address prefix')
param infrastructureSubnetAddressPrefix string

@description('Existing Azure Policy IP tags to preserve when adopting a reserved egress public IP')
param egressPublicIpTags array = []

@description('Protect the static egress public IP from accidental deletion')
param protectEgressPublicIp bool = false

var infrastructureSubnetName = '${namePrefix}-aca-subnet'

resource egressPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${namePrefix}-egress-pip'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    ddosSettings: {
      protectionMode: 'VirtualNetworkInherited'
    }
    ipTags: egressPublicIpTags
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    idleTimeoutInMinutes: 4
  }
}

resource egressPublicIpLock 'Microsoft.Authorization/locks@2020-05-01' = if (protectEgressPublicIp) {
  name: '${namePrefix}-egress-pip-lock'
  scope: egressPublicIp
  properties: {
    level: 'CanNotDelete'
    notes: 'Protects the allow-listed static egress IP. Remove only through an approved egress migration.'
  }
}

resource natGateway 'Microsoft.Network/natGateways@2024-05-01' = {
  name: '${namePrefix}-nat'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 4
    publicIpAddresses: [
      {
        id: egressPublicIp.id
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${namePrefix}-vnet'
  location: location
  tags: tags
  properties: {
    privateEndpointVNetPolicies: 'Disabled'
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: infrastructureSubnetName
        properties: {
          addressPrefix: infrastructureSubnetAddressPrefix
          defaultOutboundAccess: false
          delegations: [
            {
              name: 'aca-environment-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
          natGateway: {
            id: natGateway.id
          }
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output infrastructureSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, infrastructureSubnetName)
output natGatewayId string = natGateway.id
output egressPublicIpId string = egressPublicIp.id
output egressPublicIpAddress string = egressPublicIp.properties.ipAddress
