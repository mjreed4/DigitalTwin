// ============================================================================
// Azure Bicep Infrastructure Template: Pipeline Pump Station Digital Twin
// Target: OT Security Cyber-Physical Attack Simulation Environment (v2)
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
        }
      }
      {
        name: 'Subnet-Supervisory-Level3'
        properties: {
          addressPrefix: '10.10.3.0/24'
          networkSecurityGroup: {
            id: nsgSupervisory.id
          }
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
        name: 'Allow-Modbus-Field-5020'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '5020'
          sourceAddressPrefix: '10.10.2.0/24'
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
        name: 'Allow-Modbus-PLC-502'
        properties: {
          priority: 100
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
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '8080'
          sourceAddressPrefix: '10.10.3.0/24'
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
        name: 'Allow-HMI-HTTP-8000'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '8000'
          sourceAddressPrefix: '*'
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
// 3. Azure Container Apps Environment & Services
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

resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-aca-env-${environment}'
  location: location
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: vnet.properties.subnets[0].id
      internal: true
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// Process Simulator Container App (Field Level)
resource appSimulator 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-simulator'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: false
        targetPort: 5020
        transport: 'tcp'
      }
    }
    template: {
      containers: [
        {
          name: 'process-simulator'
          image: 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'SIM_STEP_HZ'
              value: '10'
            }
            {
              name: 'MODBUS_PORT'
              value: '5020'
            }
          ]
        }
      ]
    }
  }
}

// OpenPLC Runtime Container App (Control Level)
resource appOpenPLC 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-openplc'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: false
        targetPort: 502
        transport: 'tcp'
        additionalPortMappings: [
          {
            targetPort: 8080
            external: false
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'openplc-runtime'
          image: 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'SIMULATOR_HOST'
              value: appSimulator.properties.configuration.ingress.fqdn
            }
            {
              name: 'SIMULATOR_PORT'
              value: '5020'
            }
          ]
        }
      ]
    }
  }
}

// HMI Web App Container App (Supervisory Level)
resource appHMI 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-hmi'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
    }
    template: {
      containers: [
        {
          name: 'pump-hmi'
          image: 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'PLC_HOST'
              value: appOpenPLC.properties.configuration.ingress.fqdn
            }
            {
              name: 'PLC_PORT'
              value: '502'
            }
          ]
        }
      ]
    }
  }
}

// Telemetry Bridge Container App (OT -> Cloud ADT Sync)
resource appBridge 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-bridge'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    template: {
      containers: [
        {
          name: 'pump-telemetry-bridge'
          image: 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'PLC_HOST'
              value: appOpenPLC.properties.configuration.ingress.fqdn
            }
            {
              name: 'PLC_PORT'
              value: '502'
            }
            {
              name: 'ADT_HOST_NAME'
              value: digitalTwins.properties.hostName
            }
          ]
        }
      ]
    }
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
output hmiUrl string = appHMI.properties.configuration.ingress.fqdn
output digitalTwinsHostName string = digitalTwins.properties.hostName
output ioTHubConnectionString string = ioTHub.name