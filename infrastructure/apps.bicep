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
// Azure Container Apps Environment
// ----------------------------------------------------------------------------
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

// ----------------------------------------------------------------------------
// Container Apps (Deployed directly with actual custom ACR images)
// ----------------------------------------------------------------------------

// Process Simulator Container App (Field Level)
resource appSimulator 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-simulator'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
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
        external: false
        targetPort: 5020
        transport: 'tcp'
      }
    }
    template: {
      containers: [
        {
          name: 'process-simulator'
          image: '${acr.properties.loginServer}/pump-simulator:latest'
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
          image: '${acr.properties.loginServer}/openplc-runtime:latest'
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
          image: '${acr.properties.loginServer}/pump-hmi:latest'
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
          image: '${acr.properties.loginServer}/pump-telemetry-bridge:latest'
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

// Outputs
output hmiUrl string = appHMI.properties.configuration.ingress.fqdn
