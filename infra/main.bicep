// Shopify AI Shopping Assistant on Azure Container Apps.
//
// Creates a user-assigned managed identity, a Key Vault for the secrets, a Log Analytics
// workspace, a Container Apps environment and the app itself. No passwords in config:
//   - the identity reads the three secrets from Key Vault (you add them yourself)
//   - the identity signs in to Azure SQL, where infra/grant_sql_access.py gives it its own
//     schema (shopassist) for conversations, tickets and analytics, and nothing else

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short name used to derive resource names.')
param appName string = 'shopassist'

@description('Container image, e.g. ghcr.io/<owner>/shopify-shopping-assistant:latest')
param image string

@description('The Shopify store, e.g. your-store.myshopify.com')
param shopifyDomain string

@description('Client ID of the Dev Dashboard app (not a secret).')
param shopifyClientId string

@description('Comma-separated origins where the widget is embedded, e.g. https://your-store.myshopify.com')
param allowedOrigins string

@description('Azure SQL server host name, e.g. myserver.database.windows.net')
param sqlServerHost string

@description('Azure SQL database name.')
param sqlDatabase string

@description('Claude model id.')
param claudeModel string = 'claude-opus-5-5'

@description('One line about the store, for the assistant.')
param storeAbout string = ''

@description('Daily spending limit for the whole store, in USD.')
param dailyBudgetUsd string = '5'

@description('Object id of the person deploying, given access to set the secrets.')
param deployerObjectId string

@description('false on the first run: creates the vault so the secrets can be added before the app starts.')
param deployApp bool = true

var suffix = uniqueString(resourceGroup().id)
var keyVaultName = 'kv-${appName}-${take(suffix, 6)}'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${appName}'
  location: location
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// Built-in role ids.
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var keyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource appCanReadSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, identity.id, keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerCanSetSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, deployerObjectId, keyVaultSecretsOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficer)
    principalId: deployerObjectId
    principalType: 'User'
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${appName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // hard cap on log ingestion cost
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${appName}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var secretNames = ['anthropic-api-key', 'shopify-client-secret', 'admin-token']

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: 'ca-${appName}'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [ appCanReadSecrets ]
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [for name in secretNames: {
        name: name
        keyVaultUrl: '${vault.properties.vaultUri}secrets/${name}'
        identity: identity.id
      }]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
            { name: 'CLAUDE_MODEL', value: claudeModel }
            { name: 'SHOPIFY_STORE_DOMAIN', value: shopifyDomain }
            { name: 'SHOPIFY_CLIENT_ID', value: shopifyClientId }
            { name: 'SHOPIFY_CLIENT_SECRET', secretRef: 'shopify-client-secret' }
            { name: 'ADMIN_TOKEN', secretRef: 'admin-token' }
            { name: 'ALLOWED_ORIGINS', value: allowedOrigins }
            { name: 'STORE_ABOUT', value: storeAbout }
            { name: 'DAILY_BUDGET_USD', value: dailyBudgetUsd }
            // Conversations, tickets and analytics, as the identity (DefaultAzureCredential uses AZURE_CLIENT_ID).
            { name: 'ASSISTANT_DB', value: 'mssql://${sqlServerHost}/${sqlDatabase}' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              periodSeconds: 30
            }
            {
              type: 'Startup'
              // The first connection can wait up to a minute for an auto-paused database.
              httpGet: { path: '/healthz', port: 8000 }
              periodSeconds: 5
              failureThreshold: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0 // scales to zero when nobody chats: no cost while idle
        maxReplicas: 2
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
    }
  }
}

output appUrl string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
output appName string = 'ca-${appName}'
output keyVaultName string = vault.name
output identityName string = identity.name
