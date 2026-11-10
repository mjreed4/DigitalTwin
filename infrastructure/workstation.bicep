@description('Azure location for the engineering workstation')
param location string = resourceGroup().location

@description('Resource name prefix')
param prefix string = 'pump-station'

@description('Name of the existing VNet hosting the Container Apps environments')
param vnetName string = '${prefix}-vnet-lab'

@description('Your public IPv4 address in CIDR notation (/32). RDP is allowed only from this address.')
param rdpSourceAddressPrefix string

@description('Default DNS domain reported by the supervisory Container Apps environment')
param supervisoryEnvironmentDomain string

@description('VM size')
param vmSize string = 'Standard_B2s'

@description('Local administrator username')
param workstationAdminUsername string = 'enguser'

@secure()
@description('Local administrator password meeting Azure Windows VM complexity requirements.')
param workstationAdminPassword string

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: vnetName
}

resource workstationSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-09-01' existing = {
  parent: vnet
  name: 'Subnet-Engineering-Workstation'
}

resource workstationNsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${prefix}-nsg-engineering-workstation'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-RDP-From-Operator'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '3389'
          sourceAddressPrefix: rdpSourceAddressPrefix
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Deny-Other-Inbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

resource workstationPublicIp 'Microsoft.Network/publicIPAddresses@2023-09-01' = {
  name: '${prefix}-engineering-workstation-pip'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}

resource supervisoryContainerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: '${prefix}-aca-supervisory-lab'
}

resource supervisoryDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: supervisoryEnvironmentDomain
  location: 'global'
}

resource supervisoryDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: supervisoryDnsZone
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource supervisoryDnsARecord 'Microsoft.Network/privateDnsZones/A@2020-06-01' = {
  parent: supervisoryDnsZone
  name: '*'
  properties: {
    ttl: 300
    aRecords: [
      {
        ipv4Address: supervisoryContainerAppEnv.properties.staticIp
      }
    ]
  }
}

resource workstationNic 'Microsoft.Network/networkInterfaces@2023-09-01' = {
  name: '${prefix}-engineering-workstation-nic'
  location: location
  properties: {
    networkSecurityGroup: {
      id: workstationNsg.id
    }
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: workstationSubnet.id
          }
          publicIPAddress: {
            id: workstationPublicIp.id
          }
        }
      }
    ]
  }
}

resource workstation 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: '${prefix}-engineering-workstation'
  location: location
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftWindowsServer'
        offer: 'WindowsServer'
        sku: '2022-datacenter-azure-edition'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
    osProfile: {
      computerName: 'eng-workstation'
      adminUsername: workstationAdminUsername
      adminPassword: workstationAdminPassword
      windowsConfiguration: {
        provisionVMAgent: true
        enableAutomaticUpdates: true
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: workstationNic.id
          properties: {
            primary: true
          }
        }
      ]
    }
  }
}

output workstationVmName string = workstation.name
output workstationSubnetName string = workstationSubnet.name
output workstationPrivateIp string = workstationNic.properties.ipConfigurations[0].properties.privateIPAddress
output workstationPublicIpAddress string = workstationPublicIp.properties.ipAddress
output hmiEnvironmentDomain string = supervisoryContainerAppEnv.properties.defaultDomain
