// ============================================================================
// Azure Bicep Infrastructure Template: Phase 1 Base Infrastructure
// Target: VNet, NSGs, ACR, Log Analytics, Azure Digital Twins, IoT Hub
// ============================================================================

@description('Azure location for all resources')
param location string = resourceGroup().location

@description('Prefix for resource names')
param prefix string = 'pump-station'

@description('Environment tag (dev, lab, test)')
param environment string = 'lab'

// ----------------------------------------------------------------------------
// 1. Networking (Isolated VNet Architecture mirroring Purdue Model)
// ----------------------------------------------------------------------------
resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: '${prefix}-vnet-${environment}'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.10.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'Subnet-Field-Level1'
        properties: {
          addressPrefix: '10.10.1.0/24'
          networkSecurityGroup: {
            id: nsgField.id
          }
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'Subnet-Control-Level2'
        properties: {
          addressPrefix: '10.10.2.0/24'
          networkSecurityGroup: {
            id: nsgControl.id
          }
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'Subnet-Supervisory-Level3'
        properties: {
          addressPrefix: '10.10.3.0/24'
          networkSecurityGroup: {
            id: nsgSupervisory.id
          }
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

resource nsgField 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${prefix}-nsg-field'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-AzureLoadBalancer'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Allow-Modbus-Field-5020'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '5020'
          sourceAddressPrefix: '10.10.2.0/24'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Deny-Other-VNet-Inbound'
        properties: {
          priority: 400
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource nsgControl 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${prefix}-nsg-control'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-AzureLoadBalancer'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Allow-Modbus-PLC-502'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '502'
          sourceAddressPrefix: '10.10.3.0/24'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Allow-OpenPLC-Web-8080'
        properties: {
          priority: 210
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '8080'
          sourceAddressPrefix: '10.10.3.0/24'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Deny-Other-VNet-Inbound'
        properties: {
          priority: 400
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource nsgSupervisory 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${prefix}-nsg-supervisory'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-AzureLoadBalancer'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Allow-HMI-HTTP-8000'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '8000'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Deny-Other-VNet-Inbound'
        properties: {
          priority: 400
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

// ----------------------------------------------------------------------------
// 2. Azure Container Registry (Artifact Repository)
// ----------------------------------------------------------------------------
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: replace('${prefix}acr${environment}', '-', '')
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    adminUserEnabled: true
  }
}

// ----------------------------------------------------------------------------
// 3. Log Analytics & Shared Monitoring
// ----------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${prefix}-logs-${environment}'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ----------------------------------------------------------------------------
// 4. Azure Digital Twins (ADT) & IoT Hub
// ----------------------------------------------------------------------------
resource digitalTwins 'Microsoft.DigitalTwins/digitalTwinsInstances@2023-01-31' = {
  name: '${prefix}-adt-${environment}'
  location: location
  properties: {}
}

resource ioTHub 'Microsoft.Devices/IotHubs@2023-06-30' = {
  name: '${prefix}-iothub-${environment}'
  location: location
  sku: {
    name: 'S1'
    capacity: 1
  }
  properties: {
    eventHubEndpoints: {
      events: {
        retentionTimeInDays: 1
        partitionCount: 2
      }
    }
  }
}

// Outputs
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output digitalTwinsHostName string = digitalTwins.properties.hostName
output ioTHubConnectionString string = ioTHub.name
