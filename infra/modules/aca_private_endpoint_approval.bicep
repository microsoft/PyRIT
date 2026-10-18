@description('Name of the existing Azure Container Apps managed environment')
param environmentName string

@description('Name of the existing private endpoint connection to approve')
param connectionName string

@description('Approval description preserved for deterministic discovery')
param approvalDescription string

resource environment 'Microsoft.App/managedEnvironments@2024-10-02-preview' existing = {
  name: environmentName
}

resource connection 'Microsoft.App/managedEnvironments/privateEndpointConnections@2024-10-02-preview' = {
  parent: environment
  name: connectionName
  properties: {
    privateLinkServiceConnectionState: {
      description: approvalDescription
      status: 'Approved'
    }
  }
}

output connectionId string = connection.id
