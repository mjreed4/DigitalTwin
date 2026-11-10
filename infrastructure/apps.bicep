// ============================================================================
// Azure Bicep Infrastructure Template: Phase 2 Container Apps Provisioning
// Target: ACA Managed Environment & Container Apps (using custom ACR images)
// ============================================================================

@description('Azure location for all resources')
param location string = resourceGroup().location

@description('Prefix for resource names')
param prefix string = 'pump-station'

@description('Environment tag (dev, lab, test)')
param environment string = 'lab'

@description('Immutable container image tag to deploy')
param imageTag string

@description('Run one replica of the inert adversary placeholder container')
param adversaryEnabled bool = false

// ----------------------------------------------------------------------------
// Existing Infrastructure References (Provisioned in Phase 1)
// ----------------------------------------------------------------------------
resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: '${prefix}-vnet-${environment}'
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: replace('${prefix}acr${environment}', '-', '')
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: '${prefix}-logs-${environment}'
}

resource digitalTwins 'Microsoft.DigitalTwins/digitalTwinsInstances@2023-01-31' existing = {
  name: '${prefix}-adt-${environment}'
}

// ----------------------------------------------------------------------------
// Separate environments bind each Purdue level to its dedicated subnet.
// ----------------------------------------------------------------------------
resource fieldContainerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-aca-field-${environment}'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
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

resource controlContainerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-aca-control-${environment}'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    vnetConfiguration: {
      infrastructureSubnetId: vnet.properties.subnets[1].id
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

resource supervisoryContainerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-aca-supervisory-${environment}'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    vnetConfiguration: {
      infrastructureSubnetId: vnet.properties.subnets[2].id
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

// ----------------------------------------------------------------------------
// Container Apps (Deployed directly with actual custom ACR images)
// ----------------------------------------------------------------------------

// Process Simulator Container App (Field Level)
resource appSimulator 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-simulator'
  location: location
  properties: {
    managedEnvironmentId: fieldContainerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
      ingress: {
        // The field environment has an internal load balancer, so external
        // ingress exposes this TCP endpoint to the VNet, not the public internet.
        // This is required because OpenPLC runs in a separate Container Apps environment.
        external: true
        targetPort: 5020
        exposedPort: 5020
        transport: 'tcp'
      }
    }
    template: {
      containers: [
        {
          name: 'process-simulator'
          image: '${acr.properties.loginServer}/pump-simulator:${imageTag}'
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
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// OpenPLC Runtime Container App (Control Level)
resource appOpenPLC 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-openplc'
  location: location
  properties: {
    managedEnvironmentId: controlContainerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
      ingress: {
        external: true
        targetPort: 8080
        transport: 'tcp'
        additionalPortMappings: [
          {
            targetPort: 502
            exposedPort: 502
            external: true
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'openplc-runtime'
          image: '${acr.properties.loginServer}/openplc-runtime:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'SIMULATOR_HOST'
              value: fieldContainerAppEnv.properties.staticIp
            }
            {
              name: 'SIMULATOR_PORT'
              value: '5020'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// HMI Web App Container App (Supervisory Level)
resource appHMI 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-hmi'
  location: location
  properties: {
    managedEnvironmentId: supervisoryContainerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
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
          image: '${acr.properties.loginServer}/pump-hmi:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'PLC_HOST'
              value: controlContainerAppEnv.properties.staticIp
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
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: supervisoryContainerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'pump-telemetry-bridge'
          image: '${acr.properties.loginServer}/pump-telemetry-bridge:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'PLC_HOST'
              value: controlContainerAppEnv.properties.staticIp
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

// adversary Container App (Control Level)
resource appAdversary 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-adversary'
  location: location
  properties: {
    managedEnvironmentId: controlContainerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'adversary'
          image: '${acr.properties.loginServer}/pump-adversary:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'PLC_HOST'
              value: controlContainerAppEnv.properties.staticIp
            }
            {
              name: 'PLC_PORT'
              value: '502'
            }
          ]
        }
      ]
      scale: {
        minReplicas: adversaryEnabled ? 1 : 0
        maxReplicas: 1
      }
    }
  }
}

resource bridgeDigitalTwinsDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(
    digitalTwins.id,
    appBridge.name,
    subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'bcd981a7-7f74-457b-83e1-cceb9e632ffe'
    )
  )
  scope: digitalTwins
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'bcd981a7-7f74-457b-83e1-cceb9e632ffe'
    )
    principalId: appBridge.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Outputs
output hmiUrl string = appHMI.properties.configuration.ingress.fqdn
output bridgePrincipalId string = appBridge.identity.principalId
output digitalTwinsResourceId string = digitalTwins.id
