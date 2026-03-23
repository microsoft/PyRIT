# PyRIT Azure Deployment

Deploy the CoPyRIT GUI as an Azure Container App with MSAL PKCE authentication,
managed identity, IP-based network restriction, and no embedded secrets.

## Architecture

```
Users (corp VPN) ──→ IP restriction (ingress) ──→ Container App
                                                       ↓
                                                 MSAL PKCE auth
                                                       ↓
                                              FastAPI JWT middleware
                                                       ↓
                                              User-Assigned MI
                                        ↙     ↙     ↓      ↘      ↘
                                 Azure SQL  ACR  Azure OpenAI  Key Vault  Storage
                                (MI auth) (AcrPull) (RBAC)   (secret refs) (Blob)
```

Logging & monitoring:
```
ACA Environment → Log Analytics (app logs)
Container App   → Application Insights (OTel traces + audit logs, when enabled)
```

## Security

- **Authentication**: MSAL PKCE on the frontend (`@azure/msal-browser`) + FastAPI JWT
  middleware on the backend. The backend validates Bearer tokens against Entra ID JWKS.
  No Easy Auth — the tenant blocks client secrets/certs on app registrations, so PKCE
  (public client) is used instead.
- **Authorization** (three layers, any combination):
  1. **IP restriction** (ingress-level) — `allowedCidr` param restricts to a CIDR range
     (e.g., corp VPN `131.107.0.0/16`). Blocked before auth runs. Empty = all traffic allowed.
  2. **Entra group check** — `allowedGroupObjectId` param. Requires `groupMembershipClaims:
     "ApplicationGroup"` + optional claims + the security group assigned to the enterprise app
     (see Prerequisites §3). Using `ApplicationGroup` instead of `SecurityGroup` avoids groups
     overage (>200 groups) by only emitting app-assigned groups in the token.
  3. **OID allowlist** — `allowedOids` param. Comma-separated user OIDs. Fallback when the
     groups claim is unavailable. The `oid` claim is always present in tokens.
  - If neither group nor OID restriction is set, all authenticated users pass.
  - The frontend (static SPA) loads without auth. Authorization is enforced at the `/api/*`
    layer — unauthorized users see the GUI shell but all API calls return 403.
- **Identity**: User-assigned managed identity — created before the container app so
  RBAC roles (AcrPull, KV Secrets User) are active before the first revision starts.
  Set `AZURE_CLIENT_ID` to the UAMI's client ID so `DefaultAzureCredential` uses
  the correct identity.
- **Network**: Public ingress with optional IP restriction via `allowedCidr`. Private
  Endpoint can be enabled via `enablePrivateEndpoint` (see Notes).
- **Data**: Azure SQL with managed identity authentication (no passwords)
- **Secrets**: Key Vault with RBAC (existing vault, secrets referenced via ACA secretRef)
- **Logging**: Log Analytics (app logs) + optional OTel via Application Insights
- **Images**: Unique tags or digests required — `:latest` triggers a warning output
- **Supply chain**: ACR pull via managed identity RBAC (AcrPull role assigned in IaC)
- **Tags**: All resources tagged with Service/Owner/DataClass for governance

## Prerequisites

The Bicep template creates most infrastructure automatically (ACR, Log Analytics,
managed identity, RBAC role assignments). Entra ID resources must be created
separately (Microsoft Graph, not ARM). Key Vault must be an existing vault
(avoids purge-protection issues on redeployment).

**Requirements:**
- Azure CLI **2.84+** (version 2.77 has a known `content-already-consumed` bug)
- Container image must be pushed to ACR **before** deployment

### 1. Resource group

```bash
az group create --name <rg> --location <region>
```

### 2. Entra ID app registration (manual — not an ARM resource)

No secrets or certificates needed — MSAL PKCE uses only the client ID (public client).

```bash
# Create app registration (--service-management-reference may be required by your org)
az ad app create --display-name pyrit-gui --sign-in-audience AzureADMyOrg \
  --service-management-reference "<your-asset-id-or-ticket>"

# Get the client ID (use this as entraClientId)
APP_ID=$(az ad app list --display-name pyrit-gui --query '[0].appId' -o tsv)
echo "entraClientId: $APP_ID"

# Get the tenant ID (use this as entraTenantId)
az account show --query tenantId -o tsv
```

> **Note**: The redirect URI requires the app FQDN, which is only known after
> the first deployment. After deploying, set the SPA redirect URI:
> ```bash
> FQDN=$(az deployment group show -g <rg> -n main \
>   --query properties.outputs.appFqdn.value -o tsv)
> az ad app update --id $APP_ID \
>   --spa-redirect-uris "https://$FQDN"
> ```

To enable the Entra group check, configure the app manifest:
- Set `groupMembershipClaims` to `"ApplicationGroup"` (not `"SecurityGroup"` — the latter
  causes groups overage for users in >200 groups, which breaks the token-based group check)
- Add `groups` as an optional claim for both ID tokens and access tokens

### 3. Entra security group (required for group-based authorization)

```bash
# Create security group for authorized users
# NOTE: This may require elevated permissions. If it fails, create the group
# in Azure Portal → Entra ID → Groups → New group (Security type).
az ad group create --display-name "PyRIT GUI Users" --mail-nickname pyrit-gui-users

# Get the group Object ID (use this as allowedGroupObjectId)
GROUP_ID=$(az ad group show --group "PyRIT GUI Users" --query id -o tsv)
echo "allowedGroupObjectId: $GROUP_ID"

# Add users to the group
az ad group member add --group "PyRIT GUI Users" --member-id <user-object-id>

# List current members
az ad group member list --group "PyRIT GUI Users" --query '[].displayName' -o tsv
```

**IMPORTANT: Assign the group to the enterprise application.** This is required for
`ApplicationGroup` to emit the group ID in tokens:

```bash
# Get the service principal (enterprise app) object ID
SP_ID=$(az ad sp show --id $APP_ID --query id -o tsv)

# Assign the security group (uses default access role)
az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_ID/appRoleAssignments" \
  --body "{\"principalId\": \"$GROUP_ID\", \"resourceId\": \"$SP_ID\", \"appRoleId\": \"00000000-0000-0000-0000-000000000000\"}"
```

### 4. Azure SQL server with Entra admin (existing)

The container app's managed identity authenticates via Entra — no SQL passwords.

```bash
# Check if Entra admin is already configured
az sql server ad-admin list \
  --resource-group <sql-rg> --server-name <sql-server>

# Set Entra admin (if not configured) — use your own user or a group
az sql server ad-admin create \
  --resource-group <sql-rg> \
  --server-name <sql-server> \
  --display-name "SQL Entra Admin" \
  --object-id <your-user-or-group-object-id>

# Get the SQL server FQDN (use this as sqlServerFqdn)
az sql server show \
  --resource-group <sql-rg> --name <sql-server> \
  --query fullyQualifiedDomainName -o tsv
```

### 5. Container image (**must be pushed to ACR before deployment**)

```bash
# Build image locally
cd <repo-root>
python docker/build_pyrit_docker.py --source local

# Tag with commit SHA (never use :latest)
COMMIT_SHA=$(git rev-parse --short HEAD)

# If using a template-created ACR, get its name after first deploy:
# ACR_NAME=$(az deployment group show -g <rg> -n main \
#   --query properties.outputs.acrLoginServer.value -o tsv | cut -d. -f1)
# Or if using an existing ACR:
ACR_NAME=<your-acr-name>

docker tag pyrit:latest $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA
az acr login --name $ACR_NAME
docker push $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA
echo "containerImage: $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA"
```

### 6. Key Vault (existing — required)

Use an existing Key Vault to avoid soft-delete/purge-protection naming conflicts
on redeployment. The template grants the container app's MI `Key Vault Secrets User`.

```bash
# Create a vault (if your org doesn't provide one)
az keyvault create \
  --resource-group <kv-rg> \
  --name <vault-name> \
  --enable-rbac-authorization true \
  --enable-purge-protection true

# Get the vault resource ID (use this as keyVaultResourceId)
az keyvault show --name <vault-name> --query id -o tsv
```

> **Note**: The vault should have `enableRbacAuthorization: true` so the template
> can grant the MI access. Diagnostic settings (AuditEvent logs) should be
> configured on the vault separately by the vault owner.

## Deploy

```bash
# Copy and fill in parameters
cp infra/parameters.example.json infra/parameters.json
# Edit parameters.json with your values

# Deploy
az deployment group create \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters @infra/parameters.json
```

## Post-Deployment

1. **Set SPA redirect URI** on the app registration (requires the FQDN from deploy output):
   ```bash
   FQDN=$(az deployment group show -g <rg> -n main \
     --query properties.outputs.appFqdn.value -o tsv)
   az ad app update --id <entraClientId> \
     --spa-redirect-uris "https://$FQDN"
   ```

2. **Grant managed identity RBAC on Azure resources**:
   ```bash
   # Get the MI's principal ID from deployment output
   MI_ID=$(az deployment group show -g <rg> -n main \
     --query properties.outputs.managedIdentityPrincipalId.value -o tsv)

   # Azure OpenAI — Cognitive Services OpenAI User on each AOAI instance
   az role assignment create \
     --assignee-object-id $MI_ID \
     --role "Cognitive Services OpenAI User" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<aoai-name>

   # Content Safety — Cognitive Services User on Content Safety resources
   az role assignment create \
     --assignee-object-id $MI_ID \
     --role "Cognitive Services User" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<content-safety-name>

   # Azure Storage — Storage Blob Data Contributor on storage accounts used by PyRIT
   az role assignment create \
     --assignee-object-id $MI_ID \
     --role "Storage Blob Data Contributor" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<storage-name>

   # Azure ML — Azure ML Data Scientist on ML workspaces (for serverless endpoints like DeepSeek, Phi-4)
   az role assignment create \
     --assignee-object-id $MI_ID \
     --role "Azure ML Data Scientist" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.MachineLearningServices/workspaces/<workspace-name>
   ```

3. **Create Azure SQL contained user** for the managed identity:
   ```sql
   -- Run on the target database as Entra admin
   -- Use the UAMI name (appName + "-identity")
   -- If recreating the MI, drop the old user first:
   -- DROP USER IF EXISTS [<appName>-identity];
   CREATE USER [<appName>-identity] FROM EXTERNAL PROVIDER;
   ALTER ROLE db_datareader ADD MEMBER [<appName>-identity];
   ALTER ROLE db_datawriter ADD MEMBER [<appName>-identity];
   ```

4. **Manage access** — Add or remove users via Entra security group (if using
   `allowedGroupObjectId`) or update `allowedOids` in parameters.

## Access the GUI

The app is reachable via its public FQDN. If `allowedCidr` is set, only traffic
from that CIDR range (e.g., corp VPN) can reach the app. Get the FQDN:
```bash
az deployment group show -g <rg> -n main \
  --query properties.outputs.appFqdn.value -o tsv
```

Open `https://<FQDN>` in a browser. You'll be redirected to Entra ID login via MSAL PKCE.

## Configuration: .pyrit_conf and .env

The template replaces `.pyrit_conf` and `.env` with Bicep parameters — no files
needed in the container.

### .pyrit_conf fields → Bicep params

| .pyrit_conf field | Bicep param | Env var | Notes |
|-------------------|-------------|---------|-------|
| `initializers` | `pyritInitializer` | `PYRIT_INITIALIZER` | Default `targets airt`: `targets` populates the TargetRegistry (read by the GUI), `airt` sets up converter/scorer/adversarial defaults |
| `operator` | — | Set per-user in the GUI | |
| `operation` | — | Set per-user in the GUI | |

### .env file → Key Vault secret

The entire `.env` file is stored as a single Key Vault secret (`env-global` by
default). The template references it via ACA secret and injects it as the
`PYRIT_ENV_CONTENTS` env var. PyRIT parses this at startup to set all endpoint,
model, and API key environment variables.

To update the `.env` contents:
```bash
az keyvault secret set --vault-name <vault> --name env-global --file ~/.pyrit/.env
```

Azure services (OpenAI, Content Safety, Speech) support managed identity — when
API key env vars are not set, PyRIT auto-falls back to `DefaultAzureCredential`,
which picks up the container app's user-assigned MI. Set the `AZURE_CLIENT_ID`
env var to the UAMI's client ID so `DefaultAzureCredential` selects the correct
identity. Non-Azure providers (OpenAI Platform, Groq, Google Gemini) require API
keys in the `.env`.

## Notes

- **IP restriction**: When `allowedCidr` is set, only traffic from that CIDR range
  can reach the app at the ingress level (blocked before auth runs). When empty, all
  traffic is allowed and authorization relies solely on MSAL + group/OID checks.
  **Caveat**: Azure Cloud PCs route traffic to Azure services through CGNAT IPs
  (`100.64.0.0/10`), not the public NAT IPs shown by `ifconfig.me`. IP restrictions
  based on `ifconfig.me` will not work for Cloud PC → ACA traffic.
- **Private Endpoint**: Set `enablePrivateEndpoint=true` to create a Private Endpoint,
  Private DNS zone, and VNet link for the ACA environment. This disables public network
  access — the app is only reachable via the PE's private network path. Requires that
  clients (e.g., Cloud PCs, dev machines) are on the PE's VNet or a peered VNet.
  When disabled (default for test deployments), the app uses public ingress.
- **Log Analytics shared key**: The ACA environment uses `listKeys()` to connect to
  Log Analytics. This is the standard pattern required by the ACA API. The key is used
  only during deployment and is not exposed to the application.
- **Workload profiles**: The environment uses workload profiles mode (Consumption tier).
- **Scaling**: Defaults to 1 replica (no auto-scale). Adjust `minReplicas`/`maxReplicas`
  in parameters if needed.
- **Key Vault**: Must be an existing vault (passed via `keyVaultResourceId`).
  The template grants `Key Vault Secrets User` to the user-assigned MI.
- **OpenTelemetry (SFI-SM 2.3.1)**: When `enableOtel=true`, the template creates
  Application Insights, but the OTel agent must be configured as a post-deploy step:
  ```bash
  AI_CONN=$(az deployment group show -g <rg> -n main \
    --query properties.outputs.appInsightsConnectionString.value -o tsv)
  az containerapp env telemetry app-insights set \
    --name <appName>-env -g <rg> --connection-string "$AI_CONN"
  ```
- **Existing resources**: Log Analytics (`logAnalyticsWorkspaceId` + credentials),
  VNet (`infrastructureSubnetId`), and ACR (`acrResourceId`) can optionally be
  provided as existing resources to skip creation.
- **Azure CLI**: Version 2.84+ required (2.77 has a known bug).

## Production Hardening

The default configuration (Entra MSAL PKCE + security group authorization) is
sufficient for test deployments. For production, add defense-in-depth:

1. **Private Endpoint with VNet peering**: Set `enablePrivateEndpoint=true` and
   provide an `infrastructureSubnetId` on a VNet peered with your clients' network
   (e.g., Cloud PC VNet, corp VPN gateway VNet). This disables public access entirely.
2. **Entra Conditional Access Policies**: Require compliant devices, MFA, or
   specific named locations. Configured in Entra ID → Security → Conditional Access
   (not in the Bicep template).
3. **IP restrictions as fallback**: If using public access, set `allowedCidr` to
   restrict ingress to known corporate IP ranges. Note the CGNAT caveat for Cloud PCs
   (see Notes above).
4. **WAF / Front Door**: Place Azure Front Door with WAF policies in front of the
   ACA ingress for DDoS protection, bot filtering, and geo-restrictions.
5. **Audit logging**: Enable `enableOtel=true` for Application Insights telemetry
   and ensure Log Analytics retention meets compliance requirements.

## Teardown and Redeployment

You can safely delete the resource group and redeploy — Key Vault is external
to the RG so there are no purge-protection naming conflicts:

```bash
az group delete --name <rg> --yes
```

All resources created by the template (ACR, ACA, Log Analytics, App Insights,
VNet) are deleted cleanly with no naming conflicts.
