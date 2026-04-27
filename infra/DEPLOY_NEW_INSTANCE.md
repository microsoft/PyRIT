# Deploy a New CoPyRIT GUI Instance

Deploy an isolated CoPyRIT GUI instance for an external team (CELA, model ops,
partners). Each instance gets its own database, secrets, and Entra app
registration — fully isolated from the AIRT instance and from other instances.
Access is controlled via existing Entra security groups that you provide at
deploy time.

## Security Model

All authenticated users on a GUI instance are **fully trusted**. Any user with
Entra group membership can view and modify all targets, attack history, and
query anything on the database connection. There is no per-user data isolation
within an instance. The trust boundary is Entra group membership.

**Deploy separate instances for separate trust groups.**

## What You Need

| Prerequisite | Notes |
|---|---|
| [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) 2.84+ | Version 2.77 has a known `content-already-consumed` bug |
| Python 3.10+ | For running the deployment script |
| `az login` with Graph permissions | The script creates Entra app registrations, which requires Graph API access |
| Container image pushed to ACR | Build and push before deploying (see [Building the Image](#building-the-image)) |
| A `.env` file with target endpoints | Copy and fill in `infra/env.demo.template` |

### What the script creates (per-instance)

| Resource | Naming Convention |
|---|---|
| Resource Group | `copyrit-{instance-name}` |
| Container App | `copyrit-{instance-name}` |
| Container App Environment | `copyrit-{instance-name}-env` |
| User-Assigned Managed Identity | `copyrit-{instance-name}-identity` |
| Azure SQL Server + Database | `copyrit-{instance-name}-sql` / `pyrit-{instance-name}` |
| Key Vault | `copyrit-{instance-name}-kv` |
| Entra App Registration | `CoPyRIT GUI ({instance-name})` |
| Log Analytics Workspace | `copyrit-{instance-name}-logs` |

### What is shared across instances

| Resource | Notes |
|---|---|
| Azure Container Registry | Same image, different config per instance |
| Subscription | All instances deploy to the same subscription |

> **Time estimate:** A new instance takes approximately 15–20 minutes end-to-end
> (script runtime + manual SQL user creation). Plan for this cadence if deploying
> new instances monthly.

## Quick Deploy

### 1. Prepare the .env file

```bash
cp infra/env.demo.template my-demo.env
# Edit my-demo.env — fill in real endpoint URLs, API keys, and models.
# Required: chat target, unsafe chat targets (for converters), content safety,
#           SQL connection string, storage account URL.
# Optional: image, TTS, video, realtime, responses targets.
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
    --allowed-groups "group-oid-1,group-oid-2"
```

> **Instance name constraints:** Max 13 characters (lowercase letters, numbers,
> hyphens). The Key Vault name `copyrit-{name}-kv` has a 24-character limit.

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
```

**Grant Cognitive Services roles** (if using managed identity auth for Azure OpenAI):

```bash
MI_ID=<managed-identity-principal-id-from-script-output>

az role assignment create --assignee-object-id $MI_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services OpenAI User" \
    --scope <aoai-resource-id>

az role assignment create --assignee-object-id $MI_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services User" \
    --scope <content-safety-resource-id>
```

### 4. Restart the container app

After creating the SQL user, restart the container so it picks up the database
permissions:

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

Do **not** rely solely on `/api/health` — it can pass on an old revision while
the new one is crashing. Run through this checklist:

- [ ] Latest ACA revision is `Healthy`:
  ```bash
  az containerapp revision list \
      -n copyrit-{instance-name} \
      -g copyrit-{instance-name} \
      --query "[0].{name:name, healthState:properties.healthState}" -o table
  ```
- [ ] App loads in browser at `https://<FQDN>`
- [ ] Entra login works
- [ ] Signed-in user name appears in the top bar
- [ ] Operator label auto-populates from signed-in username
- [ ] Targets are visible in Configuration view (one of each type)
- [ ] Can select a chat target and send a message → receive a response
- [ ] Attack history view loads
- [ ] Converter panel functions (requires unsafe target)
- [ ] Data persists after page refresh (Azure SQL is working)
- [ ] A different authorized user can also sign in and use it

## Customizing the .env

The `.env` file controls which targets appear in the GUI. You can point to any
Azure OpenAI or OpenAI endpoints — they don't need to match the AIRT instance.

**Minimum viable** (just chat + converters):
- `AZURE_OPENAI_GPT4O_*` — one chat target
- `AZURE_OPENAI_GPT4O_UNSAFE_CHAT_*` — converter target
- `AZURE_OPENAI_GPT4O_UNSAFE_CHAT_*2` — scorer target
- `AZURE_CONTENT_SAFETY_*` — harm detection
- `AZURE_SQL_DB_CONNECTION_STRING` — database
- `AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL` — blob storage

**Full modality demo** (uncomment optional sections in the template):
- Image (DALL-E 3)
- TTS
- Video (Sora-2)
- Responses (o4-mini)
- Realtime

## Updating Secrets

To update the `.env` contents after deployment:

```bash
az keyvault secret set \
    --vault-name copyrit-{instance-name}-kv \
    --name env-global \
    --file ./updated.env
```

Then restart the container app (see step 4 above).

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
    --delete-entra-app \
    --yes
```

This deletes:
- The resource group (Container App, SQL server, Key Vault, MI, networking, logs)
- The Entra app registration and service principal (with `--delete-entra-app`)

> **Note:** Key Vault uses purge protection. The vault name will be reserved
> for ~90 days after deletion. Use a different instance name if redeploying
> immediately.

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

Use the resulting `$ACR_NAME.azurecr.io/pyrit:$COMMIT_SHA` as the
`--container-image` argument.

> **Note:** The CI/CD pipeline handles this automatically for the AIRT
> instance. Manual builds are only needed for the initial bootstrap or
> deployments outside the pipeline.

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
- **AcrPull role not propagated yet** — RBAC can take a few minutes. The
  container will retry automatically.
- **Key Vault secret not accessible** — Check that the managed identity has
  `Key Vault Secrets User` on the vault.
- **Missing `.pyrit_conf`** — Older container images (before the `.pyrit_conf`
  guard was added) crash on startup because the `airt` initializer
  unconditionally reads this file. Use an image built from current `main`.

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

- Check that the `.env` file in Key Vault has the correct endpoint/model/key
  variables for each target.
- Check container logs for initializer errors:
  ```bash
  az containerapp logs show \
      -n copyrit-{instance-name} \
      -g copyrit-{instance-name} \
      --tail 100
  ```

### Database connection errors

- Verify the SQL contained user was created (step 3).
- Verify `AZURE_SQL_DB_CONNECTION_STRING` in the `.env` matches the actual
  server FQDN and database name created by the script.
- Verify the Azure SQL firewall allows Azure services (the script configures
  this, but verify with `az sql server firewall-rule list`).

### Graph API / Entra commands fail

If `az ad` commands fail with `AADSTS530084`, re-login with Graph scope:

```bash
az login --scope https://graph.microsoft.com//.default
```
