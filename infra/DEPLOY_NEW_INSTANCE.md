# Deploy a New CoPyRIT GUI Instance

Deploy an isolated CoPyRIT GUI instance for an external team (CELA, model ops, partners). Each instance gets its own database, secrets, and Entra app registration, Container Apps environment, virtual network, and fixed egress IP. Instances share only the selected subscription and ACR. Access is controlled via existing Entra security groups that you provide at deploy time.

## Security Model

All authenticated users on a GUI instance are **fully trusted**. Any user with Entra group membership can view and modify all targets, attack history, and query anything on the database connection. There is no per-user data isolation within an instance. The trust boundary is Entra group membership.

**Deploy separate instances for separate trust groups.**

## What You Need

| Prerequisite | Notes |
| --- | --- |
| [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) 2.84+ | Version 2.77 has a known `content-already-consumed` bug |
| Python 3.10+ | For running the deployment script |
| `az login` with Graph permissions | The script creates Entra app registrations, which requires Graph API access. Run `az login --scope https://graph.microsoft.com//.default` |
| Azure permissions | **Owner** (or Contributor + User Access Administrator) on the subscription, and **Application Administrator** in Entra ID for app registrations and Graph API operations |
| Container image pushed to ACR | Build and push before deploying (see [Building the Image](#building-the-image)) |
| A `.env` file with runtime config | Copy and fill in `infra/env.demo.template`. Contains target endpoints and content safety config. `AZURE_SQL_DB_CONNECTION_STRING` and `AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL` are auto-injected by the script — you can omit them. Required for the default `target` initializer. Targets can also be created manually in the GUI if deploying with the `target` initializer only |

### What the deployment creates (script + Bicep)

| Resource | Naming Convention |
| --- | --- |
| Resource Group | `copyrit-{instance-name}` |
| Container App | `copyrit-{instance-name}` |
| Container App Environment | `copyrit-{instance-name}-env` |
| Virtual Network + delegated ACA subnet | `copyrit-{instance-name}-vnet` / `copyrit-{instance-name}-aca-subnet` |
| NAT Gateway + static egress Public IP | `copyrit-{instance-name}-nat` / `copyrit-{instance-name}-egress-pip` |
| User-Assigned Managed Identity | `copyrit-{instance-name}-identity` |
| Azure SQL Server + Database | `copyrit-{instance-name}-sql` / `pyrit-{instance-name}` |
| Storage Account + Blob Container | `copyrit{instance-name-no-hyphens}sa` / `dbdata` |
| Key Vault (locked down; backup/audit only — NOT read at runtime) | `copyrit-{instance-name}-kv` |
| Entra App Registration | `CoPyRIT GUI ({instance-name})` |
| Log Analytics Workspace | `copyrit-{instance-name}-logs` |

All per-instance resources receive `Service`, `Instance`, `ManagedBy`, and `DataClass` ownership tags plus `Owner` when `--owner-tag` is supplied. Bicep creates the Container App, environment, network, static IP, and Log Analytics; the Python script provisions the other resources before invoking Bicep.

### What is shared across instances

| Resource                 | Notes                                         |
| ------------------------ | --------------------------------------------- |
| Azure Container Registry | Same image, different config per instance     |
| Subscription             | All instances deploy to the same subscription |

> **Time estimate:** A new instance takes approximately 15–20 minutes end-to-end (script runtime + manual SQL user creation). Plan for this cadence if deploying new instances monthly.

The script configures delegated Microsoft Graph `User.Read`; the backend uses the token with `/me` and `/me/checkMemberGroups` and requires at least one allowed group. See [README.md](README.md#security) for the full authentication model.

## Quick Deploy

### 1. Prepare the .env file

```bash
cp infra/env.demo.template my-demo.env
# Edit my-demo.env — fill in real endpoint URLs, API keys, and models.
# Required: chat target, unsafe chat targets (for converters), content safety.
# Optional: image, TTS, video, realtime, responses targets.
# Note: AZURE_SQL_DB_CONNECTION_STRING and AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL
#       are auto-injected by the deploy script — you can omit them from the .env file.
```

See `infra/env.demo.template` for the full list of variables with comments.

### 2. Run the deployment script

```bash
python infra/deploy_instance.py \
    --instance-name partners-demo \
    --env-file ./my-demo.env \
    --subscription "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
    --location eastus2 \
    --acr-name <shared-acr-name> \
    --container-image <acr>.azurecr.io/pyrit:<commit-sha> \
    --allowed-groups "group-oid-1,group-oid-2" \
    --admin-group "admin-group-oid" \
    --owner-tag "<your-alias>" \
    --service-management-reference "<service-tree-id>" \
    --aoai-resource-names "aoai-resource-1,aoai-resource-2"
```

| Flag | Required | Description |
| --- | --- | --- |
| `--instance-name` | Yes | Short name for this instance (max 13 chars) |
| `--env-file` | Yes | Path to the `.env` file with target endpoints |
| `--subscription` | Yes | Azure subscription ID |
| `--location` | No | Azure region (default: `eastus2`) |
| `--acr-name` | Yes | Shared ACR name |
| `--container-image` | Yes | Image in `--acr-name` using a non-`latest` tag or SHA-256 digest |
| `--allowed-groups` | Yes | Comma-separated Entra group object IDs (GUIDs) |
| `--admin-group` | Yes | Entra group object ID allowed to manage backend configuration |
| `--allowed-cidr` | No | Optional public ingress IPv4 network in canonical CIDR notation; empty permits all source IPs while Entra and backend group authorization remain enabled |
| `--pyrit-config-file-uri` | No | Credential-free Azure Blob HTTPS URI for a managed-identity-backed `.pyrit_conf` |
| `--pyrit-config-rbac-scope` | Conditional | Storage account or blob container resource ID containing `--pyrit-config-file-uri`; required when that URI is set |
| `--owner-tag` | Conditional | `Owner` tag value applied to all per-instance resources. **Required when the target subscription enforces a "Require a tag on resources" Azure Policy** (this is the case for the AI Red Team Tooling subscription — deployments without it fail with `RequestDisallowedByPolicy`). Optional only on subscriptions without such a policy |
| `--service-management-reference` | No | Service Tree ID (required by some tenants for Entra app creation) |
| `--aoai-resource-names` | No | Comma-separated Cognitive Services account names for automatic AOAI RBAC. Grants `Cognitive Services OpenAI User` to the MI on each resource. Does **not** cover Content Safety — see step 3 for that. If omitted, all AOAI roles must be granted manually |
| `--dry-run` | No | Preview what will be created without executing |

> **Instance name constraints:** 1–13 lowercase letters, numbers, or internal hyphens; the name must start and end with a letter or number. The Key Vault name `copyrit-{name}-kv` has a 24-character limit.

Use `--dry-run` to preview what will be created without making changes:

```bash
python infra/deploy_instance.py \
    --instance-name partners-demo \
    --env-file ./my-demo.env \
    ... \
    --dry-run
```

### 3. Complete the manual steps

The script prints these at the end. The following steps require manual action:

**Create the SQL contained user** (requires Entra admin on the SQL server):

```sql
-- Connect via Azure Portal Query Editor, Azure Data Studio, or sqlcmd
CREATE USER [copyrit-{instance-name}-identity] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [copyrit-{instance-name}-identity];
ALTER ROLE db_datawriter ADD MEMBER [copyrit-{instance-name}-identity];
ALTER ROLE db_ddladmin ADD MEMBER [copyrit-{instance-name}-identity];
```

**Grant Cognitive Services roles** (if using managed identity auth for Azure OpenAI):

If you passed `--aoai-resource-names` during deployment, the script granted `Cognitive Services OpenAI User` on each specified AOAI resource. Check the deploy output for the `AOAI RBAC: X/Y resources granted` line. Verify all requested resources were granted (X should equal Y).

An inaccessible or unknown AOAI name is logged and skipped rather than failing the deployment. Treat `X != Y` as incomplete setup and grant the missing roles manually.

**Content Safety requires a separate role** (`Cognitive Services User`, not `OpenAI User`). The `--aoai-resource-names` flag does not cover this. If your `.env` uses managed identity auth for Content Safety (blank API key), grant the role manually:

```bash
MI_ID=<managed-identity-principal-id-from-script-output>

az role assignment create --assignee-object-id $MI_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services User" \
    --scope <content-safety-resource-id>
```

If you did **not** pass `--aoai-resource-names`, grant all roles manually:

```bash
MI_ID=<managed-identity-principal-id-from-script-output>

# For each Azure OpenAI resource:
az role assignment create --assignee-object-id $MI_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services OpenAI User" \
    --scope <aoai-resource-id>

# For Content Safety:
az role assignment create --assignee-object-id $MI_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services User" \
    --scope <content-safety-resource-id>
```

### 4. Restart the container app

After creating the SQL user, restart the container so it picks up the database permissions. This command targets the current latest revision immediately after deployment; use an explicit revision name for older revisions:

```bash
az containerapp revision restart \
    -n copyrit-{instance-name} \
    -g copyrit-{instance-name} \
    --revision $(az containerapp show \
        -n copyrit-{instance-name} \
        -g copyrit-{instance-name} \
        --query properties.latestRevisionName -o tsv)
```

### 5. Validate

Do **not** rely solely on `/api/health` — it can pass on an old revision while the new one is crashing. Run through this checklist:

> **Known lifecycle limitation:** `deploy_instance.py` cannot currently declare the instance ready because SQL contained-user creation and the resulting revision restart are manual. Until revision-bound finalization is implemented, keep this manual gate. The required future behavior is to bind deployment success to the immutable image digest and exact ACA revision produced by the deployment, then fail unless that revision is healthy; a generic FQDN health response is insufficient.

- [ ] Latest ACA revision is `Healthy`:
  ```bash
  az containerapp revision list \
      -n copyrit-{instance-name} \
      -g copyrit-{instance-name} \
      --query "[0].{name:name, healthState:properties.healthState}" -o table
  ```
- [ ] App loads in browser at `https://<FQDN>`
- [ ] The static egress IP printed by the script is allowlisted by external providers
- [ ] SQL network rules contain that egress IP and no `0.0.0.0` SQL rule
- [ ] Storage has anonymous blob access disabled, and the `dbdata` container is private
- [ ] Entra login works
- [ ] Signed-in user name appears in the top bar
- [ ] Operator label auto-populates from signed-in username
- [ ] Targets are visible in Configuration view (one of each type)
- [ ] Can select a chat target and send a message → receive a response
- [ ] Attack history view loads
- [ ] Blob-backed image, audio, or video media renders in the browser through its signed URL
- [ ] Converter panel functions (requires unsafe target)
- [ ] Data persists after page refresh (Azure SQL is working)
- [ ] A different authorized user can also sign in and use it

## Customizing the .env

The `.env` file controls which targets appear in the GUI. You can point to any Azure OpenAI or OpenAI endpoints — they don't need to match the AIRT instance.

**Minimum viable** (just chat + converters):

- `AZURE_OPENAI_GPT4O_*` — one chat target
- `AZURE_OPENAI_GPT4O_UNSAFE_CHAT_*` — converter target
- `AZURE_OPENAI_GPT4O_UNSAFE_CHAT_*2` — scorer target
- `AZURE_CONTENT_SAFETY_*` — harm detection

> **Note:** `AZURE_SQL_DB_CONNECTION_STRING` and `AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL` are auto-injected by the deploy script from the SQL server and storage account it creates. You do not need to set them manually.

**Full modality demo** (uncomment optional sections in the template):

- Image (DALL-E 3)
- TTS
- Video (Sora-2)
- Responses (o4-mini)
- Realtime

## Updating Secrets

The Container App reads its `.env` contents from an **inline secret** named `env-file`. `deploy_instance.py` is a create-only workflow and must not be rerun for rotation.

Prepare a complete local file in the same format as `infra/env.demo.template`. The filename `updated.env` is only a convention. It must include the two values that the deployment script injected during initial deployment:

- `AZURE_SQL_DB_CONNECTION_STRING`
- `AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL`

Use one of these approved update paths:

1. Update the `env-file` Container App secret in the Azure portal without retrieving or printing its current value.
2. Redeploy `main.bicep` with a complete parameter file containing the current resource values and override only the secure parameter from disk:

   ```bash
   test -f ./updated.env || { echo "ERROR: ./updated.env not found"; exit 1; }
   test -f ./current.parameters.json || { echo "ERROR: complete parameter file not found"; exit 1; }
   az deployment group create \
     --resource-group copyrit-{instance-name} \
     --template-file infra/main.bicep \
     --parameters @./current.parameters.json \
     --parameters envFileContents=@./updated.env
   ```

   `current.parameters.json` must describe the existing deployment exactly; start from `infra/parameters.example.json` and fill it from the deployed resources. Review `what-if` first. Azure CLI file expansion is silent when a path is wrong, so both existence checks are mandatory.

Application-scoped secret updates do not update an existing revision. Restart the active revision after either path:

```bash
APP_NAME=copyrit-{instance-name}
RESOURCE_GROUP=copyrit-{instance-name}
REVISION=$(az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" \
  --query properties.latestRevisionName -o tsv)
az containerapp revision restart -n "$APP_NAME" -g "$RESOURCE_GROUP" \
  --revision "$REVISION"
```

These paths update the inline ACA secret, not the `env-global` Key Vault backup. To keep the backup synchronized, update it only from a host with an approved vault network path, or through a temporary network rule when policy permits:

```bash
az keyvault secret set --vault-name copyrit-{instance-name}-kv \
  --name env-global --file ./updated.env
```

> **Anti-patterns to avoid:**
>
> - `az containerapp secret set --secrets "env-file=$ENV_CONTENT"` — exposes the value via process arguments (visible in `ps` while the command runs).
> - `az containerapp secret set --secrets "env-file=@./updated.env"` — the Container Apps CLI does not document file expansion for secret values and can store the literal path marker instead of the file contents.
> - `az containerapp secret show --secret-name env-file` — returns the full plaintext to your terminal / shell history. Inspect the KV backup instead, or use `az containerapp secret list -o table` to confirm the secret exists without revealing its value.
> - `python infra/deploy_instance.py ... --env-file ./updated.env` — the deploy script is **not** rotation-safe. It runs unconditional `create` operations on the Entra app, SQL server, Key Vault, and managed identity, most of which fail with "already exists" errors when re-run against an existing instance. The Entra app create succeeds and produces a duplicate registration, which is worse than a hard failure.

> **Why inline instead of a Key Vault reference?** This workflow disables the vault's public network access and does not create a private endpoint, DNS, or peering path from the ACA VNet. Passing the value through a secure Bicep parameter stores it in the Container App secret store without creating that runtime network dependency.

## Adding or Removing Users

Users are managed via the Entra security group(s) passed at deploy time.

```bash
# Add a user
az ad group member add --group "<group-display-name>" --member-id <user-object-id>

# Remove a user
az ad group member remove --group "<group-display-name>" --member-id <user-object-id>

# List current members
az ad group member list --group "<group-display-name>" --query "[].displayName" -o tsv
```

## Teardown

```bash
python infra/teardown_instance.py \
    --instance-name partners-demo \
    --subscription "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
    --resource-group-id "/subscriptions/<subscription-id>/resourceGroups/copyrit-partners-demo" \
    --acknowledge-egress-ip-release \
    --delete-entra-app \
    --entra-app-id "<application-client-id-from-deploy-output>" \
    --yes
```

This deletes:

- All role assignments held by the instance managed identity, including assignments on resources outside the instance resource group
- The verified, deployment-tagged resource group and all resources in it
- The exact Entra app registration and service principal when both `--delete-entra-app` and `--entra-app-id` are supplied

Before teardown, remove the printed static egress IP from every external allowlist. The acknowledgement flag is mandatory even with `--yes`; the script then waits for resource-group deletion to finish. It refuses untagged legacy groups and groups not created by `deploy_instance.py`. For a legacy instance, inventory it manually rather than bypassing these checks.

> **Note:** Key Vault uses purge protection. The vault name will be reserved for ~90 days after deletion. Use a different instance name if redeploying immediately. The static egress IP is released and must not remain trusted by downstream systems.

## Building the Image

If you need to build and push a new container image:

```bash
cd <repo-root>
python docker/build_pyrit_docker.py --source local

COMMIT_SHA=$(git rev-parse --short HEAD)
ACR_NAME=<acr-name>

docker tag pyrit:latest $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA
az acr login --name $ACR_NAME
docker push $ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA
```

Use the resulting `$ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA` as the `--container-image` argument.

> **Microsoft maintainer note:** The team-owned ADO workflow builds its own image. Community and isolated-instance deployments must build and push the image before running `deploy_instance.py`.

## Troubleshooting

### Container fails to start (ActivationFailed)

Check the latest revision's health:

```bash
az containerapp revision list \
    -n copyrit-{instance-name} \
    -g copyrit-{instance-name} \
    -o table
```

Common causes:

- **AcrPull role not propagated yet** — RBAC can take a few minutes. The container will retry automatically.
- **Inline `env-file` secret missing or malformed** — The Container App reads the `.env` from its own inline secret, not from Key Vault. Verify it exists:
  ```bash
  az containerapp secret list \
      -n copyrit-{instance-name} \
      -g copyrit-{instance-name} -o table
  ```
- **Missing `.pyrit_conf`** — Older container images (before the `.pyrit_conf` guard was added) crash on startup because the legacy initializer unconditionally reads this file. Use an image built from current `main`.

### Entra login fails

- Verify the SPA redirect URI matches the app FQDN:
  ```bash
  FQDN=$(az containerapp show -n copyrit-{instance-name} \
      -g copyrit-{instance-name} \
      --query properties.configuration.ingress.fqdn -o tsv)
  echo "Expected redirect: https://$FQDN"
  ```
- Verify the user is in one of the `allowedGroupObjectIds` groups.
- Verify the security group is assigned to the enterprise app.

### Targets not appearing in the GUI

- Confirm the inline `env-file` secret exists on the Container App:
  ```bash
  az containerapp secret list \
      -n copyrit-{instance-name} \
      -g copyrit-{instance-name} -o table
  ```
- If you suspect the env content is wrong, inspect the Key Vault backup (`env-global`) instead of the inline secret. The Key Vault snapshot is written by the deploy script alongside the Container App secret. Read it only through an approved vault network path or a temporary network rule when policy permits. Avoid `az containerapp secret show --secret-name env-file` — it prints the full plaintext to terminal/logs.
- Check container logs for initializer errors:
  ```bash
  az containerapp logs show \
      -n copyrit-{instance-name} \
      -g copyrit-{instance-name} \
      --tail 100
  ```

### Database connection errors

- Verify the SQL contained user was created (step 3) with all three roles (`db_datareader`, `db_datawriter`, `db_ddladmin`).
- The deploy script auto-injects `AZURE_SQL_DB_CONNECTION_STRING` into the `.env` before passing it to the Container App as an inline secret. If you see a connection string mismatch, inspect the Key Vault backup (`env-global`) — it holds the value that was last deployed via the script. Read it only through an approved vault network path. Avoid `az containerapp secret show` — it prints the full plaintext to terminal/logs.
- Verify the Azure SQL firewall contains `AllowContainerAppEgress` with the static egress IP printed by the deployment. A `0.0.0.0` rule is not expected:
  ```bash
  az sql server firewall-rule list \
      -g copyrit-{instance-name} \
      -s copyrit-{instance-name}-sql -o table
  ```

### Blob storage errors

If the container logs show 403/AuthorizationPermissionMismatch when reading or writing to blob storage:

- Verify the storage account exists in the per-instance resource group:
  ```bash
  az storage account list -g copyrit-{instance-name} -o table
  ```
- Verify the managed identity has `Storage Blob Data Contributor` on the storage account scope (the script grants this automatically):

  ```bash
  az role assignment list \
      --assignee <mi-principal-id> \
      --scope $(az storage account show -n <storage-account-name> \
          -g copyrit-{instance-name} --query id -o tsv) \
      -o table
  ```

- Verify anonymous blob access is disabled while the public endpoint remains network-reachable for signed browser media URLs:

  ```bash
  az storage account show \
    -g copyrit-{instance-name} \
    -n <storage-account-name> \
    --query "{publicNetworkAccess:publicNetworkAccess,defaultAction:networkRuleSet.defaultAction,allowBlobPublicAccess:allowBlobPublicAccess}" -o json
  ```

  Expected values are `Enabled`, `Allow`, and `false`, respectively. The container remains private, so data access still requires managed identity RBAC or a valid SAS. A Storage firewall default of `Deny` requires a separate private media-delivery design; a same-region NAT public IP rule does not admit ACA traffic, and a subnet rule does not admit browsers following signed blob URLs.

- Verify the deployed env content has the correct `AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL`. The safest way to check is to inspect the Key Vault backup snapshot from an approved vault network path:
  ```bash
  az keyvault secret show --vault-name copyrit-{instance-name}-kv \
      --name env-global --query value -o tsv | \
      grep AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL
  ```
  Avoid `az containerapp secret show` — it prints the full plaintext to terminal/logs. If the value is wrong, rotate the secret using the manual procedure in [Updating Secrets](#updating-secrets).
- RBAC propagation takes ~60 seconds after a fresh deployment; if the role was granted very recently, restart the container revision.

### Graph API / Entra commands fail

If `az ad` commands fail with `AADSTS530084`, re-login with Graph scope:

```bash
az login --scope https://graph.microsoft.com//.default
```

This commonly happens in codespaces or non-corp-joined devices due to conditional access policies. Run Entra-related commands from a local machine with `az login`.

### AOAI returns 401 PermissionDenied

If chat returns `The principal <id> lacks the required data action`, the managed identity doesn't have `Cognitive Services OpenAI User` on the AOAI resource. Do not rerun the create-only deployment script. Grant the role manually:

```bash
az role assignment create --assignee-object-id <mi-principal-id> \
    --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services OpenAI User" \
    --scope <aoai-resource-id>
```

RBAC propagation takes ~60 seconds. No container restart needed.

### Windows: `FileNotFoundError` when running scripts

The Azure CLI is installed as `az.cmd` on Windows. Both `deploy_instance.py` and `teardown_instance.py` handle this automatically with `shell=True` when running on Windows. If you encounter this error, ensure you are using the latest version of the scripts.
