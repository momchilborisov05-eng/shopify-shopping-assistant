<#
.SYNOPSIS
  Deploys the Shopify AI Shopping Assistant to Azure Container Apps, end to end.

.DESCRIPTION
  1. Creates the identity, Key Vault, logs and Container Apps environment (infra/main.bicep).
  2. Stops if a secret isn't in the vault yet, and tells you how to add it.
  3. Lets Azure services reach the SQL server and gives the identity its own schema.
  4. Deploys the app and prints its URL and the widget snippet for the theme.
  Safe to run again: every step checks what already exists.

.EXAMPLE
  ./infra/deploy.ps1 -ResourceGroup rg-shopassist -SqlServer sql-claude-moni-polandcentral -SqlDatabase appdb `
      -ShopifyDomain nordvik-outdoor-demo.myshopify.com -ShopifyClientId <client id> `
      -Image ghcr.io/momchilborisov05-eng/shopify-shopping-assistant:latest
#>
param(
    [Parameter(Mandatory)] [string] $ResourceGroup,
    [Parameter(Mandatory)] [string] $SqlServer,      # server name, without .database.windows.net
    [Parameter(Mandatory)] [string] $SqlDatabase,
    [Parameter(Mandatory)] [string] $ShopifyDomain,  # your-store.myshopify.com
    [Parameter(Mandatory)] [string] $ShopifyClientId,
    [Parameter(Mandatory)] [string] $Image,
    [string] $AllowedOrigins = "",                   # default: https://<ShopifyDomain>
    [string] $Location = "polandcentral",
    [string] $AppName = "shopassist",
    [string] $StoreAbout = "",
    [string] $DailyBudgetUsd = "5"
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$sqlHost = "$SqlServer.database.windows.net"
if (-not $AllowedOrigins) { $AllowedOrigins = "https://$ShopifyDomain" }

function Invoke-Az {
    $out = az @args
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed" }
    return $out
}

function Deploy-Infra([bool] $deployApp) {
    $result = Invoke-Az deployment group create -g $ResourceGroup -f "$PSScriptRoot/main.bicep" `
        --parameters image=$Image shopifyDomain=$ShopifyDomain shopifyClientId=$ShopifyClientId `
                     allowedOrigins=$AllowedOrigins sqlServerHost=$sqlHost sqlDatabase=$SqlDatabase `
                     appName=$AppName storeAbout=$StoreAbout dailyBudgetUsd=$DailyBudgetUsd `
                     deployerObjectId=$me deployApp=$($deployApp.ToString().ToLower()) `
        --query properties.outputs -o json
    return $result | ConvertFrom-Json
}

Write-Host "==> Resource group $ResourceGroup ($Location)"
Invoke-Az provider register --namespace Microsoft.App --wait -o none
Invoke-Az provider register --namespace Microsoft.OperationalInsights --wait -o none
Invoke-Az group create -n $ResourceGroup -l $Location -o none
$me = Invoke-Az ad signed-in-user show --query id -o tsv

Write-Host "==> Identity, Key Vault, logs, Container Apps environment"
$out = Deploy-Infra $false
$vault = $out.keyVaultName.value

$missing = @()
foreach ($name in "anthropic-api-key", "shopify-client-secret", "admin-token") {
    $found = az keyvault secret list --vault-name $vault --query "[?name=='$name'].id" -o tsv
    if (-not $found) { $missing += $name }
}
if ($missing) {
    Write-Host ""
    Write-Host "Add these secrets to Key Vault '$vault' yourself, then run this script again:" -ForegroundColor Yellow
    foreach ($name in $missing) {
        Write-Host "  az keyvault secret set --vault-name $vault --name $name --value (Read-Host '$name') -o none"
    }
    Write-Host "  (admin-token is a password you choose for the /admin dashboard)"
    exit 1
}

Write-Host "==> Azure SQL: allow Azure services, own schema for the app"
$sqlGroup = Invoke-Az sql server list --query "[?name=='$SqlServer'].resourceGroup | [0]" -o tsv
Invoke-Az sql server firewall-rule create -g $sqlGroup -s $SqlServer -n AllowAzureServices `
    --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none
& "$root/.venv/Scripts/python.exe" "$PSScriptRoot/grant_sql_access.py" $sqlHost $SqlDatabase --identity $out.identityName.value
if ($LASTEXITCODE -ne 0) { throw "Granting database access failed" }

Write-Host "==> Container app"
$out = Deploy-Infra $true
$url = $out.appUrl.value

Write-Host ""
Write-Host "Deployed: $url" -ForegroundColor Green
Write-Host "Readiness (database and store reachable): $url/readyz"
Write-Host "Dashboard: $url/admin (sign in with the admin-token secret)"
Write-Host ""
Write-Host "Add the widget to the theme (Online Store > Themes > Edit code > theme.liquid, before </body>):"
Write-Host "  <script src=""$url/widget.js"" defer data-color=""#1f3d2b"" data-lang=""auto""></script>"
