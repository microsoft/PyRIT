# CoPyRIT GUI — Azure Deployment

Deploy the CoPyRIT GUI as an Azure Container App with
[MSAL](https://learn.microsoft.com/en-us/entra/msal/) PKCE authentication,
managed identity, security response headers, and no embedded secrets.

## Architecture

```
Users ──→ MSAL PKCE auth ──→ Container App
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
Container App   → Application Insights (OTel traces, when enabled)
```

## Development Workflow

### Local development

```bash
cd frontend
npm install   # one-time: install frontend dependencies
npm start     # starts both backend (port 8000) and frontend (port 3000)
```

`npm start` runs `dev.py`, which launches the FastAPI backend and Vite dev server
together, waits for the health check, and prints URLs when ready. Press Ctrl+C to
stop both.

When `ENTRA_TENANT_ID` and `ENTRA_CLIENT_ID` are not set, auth is disabled —
all requests are allowed. Swagger UI is available at `http://localhost:8000/docs`.

> ⚠️ Auth-disabled mode is for **local development only**. Never deploy to a
> network-accessible environment without both env vars set.

### Deployment Workflow

```
Local dev → Push to branch → Trigger pipeline in ADO → Deploys to test → Opt-in prod deploy
```

The CI/CD pipeline (`gui-deploy.yml`) automates build → push → deploy.
Production is opt-in via `deployToProd: true`.

## Security

- **Authentication**: [MSAL](https://learn.microsoft.com/en-us/entra/msal/)
  [PKCE](https://oauth.net/2/pkce/) on the frontend (`@azure/msal-browser`) +
  FastAPI JWT middleware on the backend. Validates Bearer tokens against Entra ID
  JWKS. PKCE (public client) — no client secrets or certificates needed.
- **Authorization**: Entra group check via `allowedGroupObjectIds` param. Requires
  `groupMembershipClaims: "ApplicationGroup"` + optional claims + each security group
  assigned to the enterprise app (see Prerequisites §3). If no group restriction is
  set, all authenticated users pass.
- **Identity**: User-assigned managed identity (UAMI) — created before the container
  app so [RBAC](https://learn.microsoft.com/en-us/azure/role-based-access-control/)
  roles are active before the first revision starts. `AZURE_CLIENT_ID` is set to the
  UAMI's client ID so `DefaultAzureCredential` uses the correct identity.
- **Network** (opt-in hardening):
  - **Private Endpoint** (`enablePrivateEndpoint=true`): disables public access
    entirely — the app is only reachable via the PE's private network. Requires VNet
    peering or VPN to reach. When PE is enabled, `allowedCidr` is ignored.
  - **IP restriction** (`allowedCidr`): restricts ingress to a
    [CIDR](https://en.wikipedia.org/wiki/Classless_Inter-Domain_Routing) range.
    Only applies when PE is disabled. Empty = no IP-level restriction (auth still
    required).
  - Neither is required — MSAL auth + group checks are the primary access controls.
- **Response headers**: `SecurityHeadersMiddleware` adds
  [CSP](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP),
  HTTP Strict Transport Security (HSTS, production only), X-Frame-Options, X-Content-Type-Options, Referrer-Policy,
  Permissions-Policy, and Cache-Control (`no-store` on API routes). Swagger/OpenAPI
  disabled in production.
- **Data**: Azure SQL with managed identity authentication (no passwords)
- **Secrets**: Key Vault with RBAC (existing vault, secrets referenced via
  [ACA](https://learn.microsoft.com/en-us/azure/container-apps/) secretRef)
- **Images**: Unique tags or digests required — `:latest` is detected by a soft guardrail
- **Supply chain**: [ACR](https://learn.microsoft.com/en-us/azure/container-registry/)
  pull via managed identity RBAC (must be granted manually; see Post-Deployment §2).
  `frontend/.npmrc` pins the npm registry. `docker/Dockerfile` declares
  `ARG BASE_IMAGE` with no default — all callers pass it explicitly to avoid container
  supply chain security scanner warnings.
- **Tags**: All resources tagged with Service/Owner/DataClass for governance
- **Logging**: Log Analytics (app logs) + optional
  [OTel](https://opentelemetry.io/) via Application Insights

## Prerequisites

> **Before you begin**: Run `az login` and confirm your subscription with
> `az account show`. You need permissions to create Entra app registrations,
> security groups, and Azure resource deployments.

The Bicep template creates most infrastructure automatically (ACR, Log Analytics,
managed identity). Entra ID resources must be created
separately (Microsoft Graph, not ARM). Key Vault must be an existing vault
(avoids purge-protection issues on redeployment). RBAC role assignments must
be created manually — see [Post-Deployment §2](#post-deployment).

**Requirements:**
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) **2.84+**
(version 2.77 has a known `content-already-consumed` bug)
- Container image must be pushed to ACR **before** deployment (see [§5
below](#5-container-image-must-be-pushed-to-acr-before-deployment))

**Quick reference** — what you need before running `az deployment group create`:

| # | What | How | Key Output |
|---|------|-----|------------|
| 1 | Resource group | `az group create` | `<rg>` name |
| 2 | Entra app registration | Portal or CLI (Graph API) | `entraClientId`, `entraTenantId` |
| 3 | Security group + SP assignment | Portal or CLI | `allowedGroupObjectIds` |
| 4 | SQL server with Entra admin | Existing server | `sqlServerFqdn`, `sqlDatabaseName` |
| 5 | Container image in ACR | Docker build + push | `containerImage` |
| 6 | Key Vault | Existing vault | `keyVaultResourceId` |

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

**Expose an API scope** (required — the frontend requests `api://{clientId}/access` tokens):

1. In Azure Portal → App registrations → your app → **Expose an API**
2. Set the Application ID URI (accept the default `api://<client-id>`)
3. **Add a scope**: value = `access`, admin consent display name = "Access PyRIT GUI",
   who can consent = "Admins and users", state = Enabled

Or via CLI:
```bash
# Set application ID URI
APP_OBJ_ID=$(az ad app show --id $APP_ID --query id -o tsv)
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$APP_OBJ_ID" \
  --body "{\"identifierUris\": [\"api://$APP_ID\"]}"

# Add the 'access' scope (generate a unique GUID for the scope ID)
SCOPE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$APP_OBJ_ID" \
  --body "{\"api\":{\"oauth2PermissionScopes\":[{\"id\":\"$SCOPE_ID\",\"isEnabled\":true,\"type\":\"User\",\"value\":\"access\",\"adminConsentDisplayName\":\"Access PyRIT GUI\",\"adminConsentDescription\":\"Allow access to the PyRIT GUI API\",\"userConsentDisplayName\":\"Access PyRIT GUI\",\"userConsentDescription\":\"Allow access to the PyRIT GUI API\"}]}}"
```

**Configure group claims** for group-based authorization:

```bash
# Set groupMembershipClaims to ApplicationGroup (not SecurityGroup — the latter
# causes groups overage for users in >200 groups, breaking token-based group checks)
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$APP_OBJ_ID" \
  --body '{"groupMembershipClaims": "ApplicationGroup"}'
```

Then add `groups` as an optional claim for both ID tokens and access tokens:
Azure Portal → App registrations → your app → Token configuration → Add optional
claim → Token type: Access → check `groups` → Save. Repeat for ID token.

### 3. Entra security groups (required for group-based authorization)

Create one or more security groups for authorized users. Multiple groups can be
specified as comma-separated IDs in `allowedGroupObjectIds`.

```bash
# Create security group for authorized users
# NOTE: This may require elevated permissions. If it fails, create the group
# in Azure Portal → Entra ID → Groups → New group (Security type).
az ad group create --display-name "MyApp-Users" --mail-nickname myapp-users

# Get the group Object ID (use this as allowedGroupObjectIds)
GROUP_ID=$(az ad group show --group "MyApp-Users" --query id -o tsv)
echo "allowedGroupObjectIds: $GROUP_ID"

# Add users to the group
az ad group member add --group "MyApp-Users" --member-id <user-object-id>

# List current members
az ad group member list --group "MyApp-Users" --query '[].displayName' -o tsv
```

**IMPORTANT: Assign each group to the enterprise application.** This is required for
`ApplicationGroup` to emit group IDs in tokens:

```bash
# Get the service principal (enterprise app) object ID
SP_ID=$(az ad sp show --id $APP_ID --query id -o tsv)

# Assign the security group (uses default access role)
az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_ID/appRoleAssignments" \
  --body "{\"principalId\": \"$GROUP_ID\", \"resourceId\": \"$SP_ID\", \"appRoleId\": \"00000000-0000-0000-0000-000000000000\"}"

# Restrict token issuance to assigned users/groups only (recommended).
# Without this, any tenant user can obtain a token — they'll get a 403 from
# the backend group check, but defense-in-depth says reject at the IdP level.
az ad sp update --id $SP_ID --set appRoleAssignmentRequired=true
```

**Nested groups**: Entra enterprise app assignment does **not** cascade to nested
groups. If group A contains group B as a member, only direct members of A are
considered assigned. To grant access to members of B, assign B to the enterprise
app separately and include both group IDs in `allowedGroupObjectIds`.

**App roles** (optional): You can define custom app roles on the app registration
(e.g., `MyApp.User.All`) and assign groups to specific roles instead of the
default access role. The backend currently authorizes via the `groups` token claim,
not `roles`, so app roles serve as organizational metadata and for
`appRoleAssignmentRequired` gating at the IdP level.

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

A shared ACR is used by both test and prod environments.

```bash
# Build image locally
cd <repo-root>
python docker/build_pyrit_docker.py --source local

# Tag with commit SHA (never use :latest)
COMMIT_SHA=$(git rev-parse --short HEAD)
ACR_NAME=<acr-name>

docker tag pyrit:latest $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA
az acr login --name $ACR_NAME
docker push $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA
echo "containerImage: $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA"
```

> **Note**: The CI/CD pipeline handles build + push automatically. Manual push is
> only needed for the initial bootstrap or if deploying outside the pipeline.

### 6. Key Vault (existing — required)

Use an existing Key Vault to avoid soft-delete/purge-protection naming conflicts
on redeployment. The managed identity must be granted `Key Vault Secrets User` on
the vault manually (the Bicep template does **not** create RBAC role assignments).

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

> **Note**: The vault should have `enableRbacAuthorization: true` so the managed
> identity can be granted access. Diagnostic settings (AuditEvent logs) should be
> configured on the vault separately by the vault owner.

## Preview changes before deploying (recommended)

Use `what-if` to see what Azure will create, modify, or delete
— without making any changes. Review the output before deploying.

```bash
az deployment group what-if \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters @infra/parameters.json
```

The output shows a color-coded diff: green (+) for new resources,
orange (~) for modifications, red (-) for deletions, and purple (*)
for no change.

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

2. **Grant managed identity RBAC** (required — the Bicep template does **not** create
   role assignments; the app will fail to start without AcrPull and KV roles):
   ```bash
   MI_ID=$(az deployment group show -g <rg> -n main \
     --query properties.outputs.managedIdentityPrincipalId.value -o tsv)

   # Required — app won't start without these
   # To find acrResourceId: az acr show --name <acr-name> --query id -o tsv
   az role assignment create --assignee-object-id $MI_ID \
     --assignee-principal-type ServicePrincipal --role "AcrPull" --scope <acrResourceId>
   az role assignment create --assignee-object-id $MI_ID \
     --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope <keyVaultResourceId>

   # Grant based on which services you use (scope as narrowly as possible)
   az role assignment create --assignee-object-id $MI_ID \
     --assignee-principal-type ServicePrincipal --role "Cognitive Services OpenAI User" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<aoai-name>
   az role assignment create --assignee-object-id $MI_ID \
     --assignee-principal-type ServicePrincipal --role "Cognitive Services User" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<content-safety-name>
   az role assignment create --assignee-object-id $MI_ID \
     --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<storage-name>
   az role assignment create --assignee-object-id $MI_ID \
     --assignee-principal-type ServicePrincipal --role "Azure ML Data Scientist" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.MachineLearningServices/workspaces/<workspace-name>
   ```

3. **Create Azure SQL contained user** for the managed identity:
   ```sql
   -- Connect as Entra admin (Azure Portal Query Editor, Azure Data Studio, or sqlcmd)
   CREATE USER [<appName>-identity] FROM EXTERNAL PROVIDER;
   ALTER ROLE db_datareader ADD MEMBER [<appName>-identity];
   ALTER ROLE db_datawriter ADD MEMBER [<appName>-identity];
   ```

4. **Manage access** — Add or remove users via Entra security groups
   (`allowedGroupObjectIds`). Each group must also be assigned to the enterprise app.

5. **Set CORS origins** for production (the Bicep template does not set this):
   ```bash
   az containerapp update -n <appName> -g <rg> \
     --set-env-vars "PYRIT_CORS_ORIGINS=https://$FQDN"
   ```

## Access the GUI

```bash
az deployment group show -g <rg> -n main --query properties.outputs.appFqdn.value -o tsv
```

Open `https://<FQDN>` in a browser. If `allowedCidr` is set, only traffic from
that CIDR range can reach the app.

## Configuration: .pyrit_conf and .env

The template replaces `.pyrit_conf` and `.env` with Bicep parameters — no files
needed in the container.

### .pyrit_conf fields → Bicep params

| .pyrit_conf field | Bicep param | Env var | Notes |
|-------------------|-------------|---------|-------|
| `initializers` | `pyritInitializer` | `PYRIT_INITIALIZER` | Default `target airt`: `target` populates the TargetRegistry (read by the GUI); `airt` loads converter, scorer, and adversarial defaults |
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

> ⚠️ `PYRIT_ENV_CONTENTS` may contain API keys. Ensure application logging does
> **not** dump environment variables or process state.

Azure services (OpenAI, Content Safety, Speech) support managed identity — when
API key env vars are not set, PyRIT auto-falls back to `DefaultAzureCredential`,
which picks up the container app's user-assigned MI. Non-Azure providers (OpenAI
Platform, Groq, Google Gemini) require API keys in the `.env`.

## Notes

- **Network hardening** (opt-in): Both Private Endpoint and IP restriction are
  optional. See the Security section for details. The CI/CD pipeline controls
  `enablePrivateEndpoint` via the ADO variable group — check your pipeline variables
  to confirm the current posture.
- **Log Analytics shared key**: `listKeys()` is the standard ACA pattern. The key is
  used during deployment only, not exposed to the application.
- **Workload profiles**: Consumption tier. Defaults to 1 replica (no auto-scale).
- **Key Vault**: Must be an existing vault. RBAC must be granted manually (see
  Post-Deployment §2).
- **OpenTelemetry**: When `enableOtel=true`, configure the agent post-deploy:
  ```bash
  AI_CONN=$(az deployment group show -g <rg> -n main \
    --query properties.outputs.appInsightsConnectionString.value -o tsv)
  az containerapp env telemetry app-insights set \
    --name <appName>-env -g <rg> --connection-string "$AI_CONN"
  ```
- **Existing resources**: Log Analytics, VNet, and ACR can be provided as existing
  resources to skip creation.
- **Azure CLI**: Version 2.84+ required (2.77 has a known bug).

## Teardown and Redeployment

```bash
az group delete --name <rg> --yes
```

Key Vault is external to the RG — no purge-protection naming conflicts.

> **Note**: Entra ID resources (app registration, security groups) are **not** deleted
> by `az group delete`. Remove them manually if no longer needed.
