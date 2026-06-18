# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CoPyRIT GUI — Deploy a new isolated instance.

Automates the full deployment of an isolated CoPyRIT GUI instance:
  1. Resource group
  2. Entra app registration + API scope + group claims
  3. Entra security group (optional — can use existing)
  4. Azure SQL server + database
  5. Storage account + blob container (auto-injects container URL into .env)
  6. Key Vault + populate .env secret (auto-injects SQL connection string;
     applies SFI network lockdown — backup/audit only, NOT read at runtime)
  7. Managed identity + RBAC role assignments (AcrPull, Storage Blob Data Contributor)
  7b. AOAI RBAC (optional — Cognitive Services OpenAI User on specified resources)
  8. Bicep deployment (Container App, networking, logging)
  9. Post-deploy: SPA redirect URI

Usage:
    python infra/deploy_instance.py \\
        --instance-name partners-demo \\
        --env-file ./my-demo.env \\
        --subscription "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \\
        --location eastus2 \\
        --acr-name myacr \\
        --container-image myacr.azurecr.io/pyrit:abc1234 \\
        --allowed-groups "group-oid-1,group-oid-2"

"""

import argparse
import json
import logging
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

INFRA_DIR = Path(__file__).resolve().parent
BICEP_TEMPLATE = INFRA_DIR / "main.bicep"

# On Windows, az CLI is a .cmd script that requires shell=True for subprocess to find it.
_SHELL = platform.system() == "Windows"


def run_az(
    *,
    args: list[str],
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run an Azure CLI command.

    Args:
        args (list[str]): The az CLI arguments (without the leading 'az').
        capture (bool): Whether to capture stdout/stderr. Defaults to True.
        check (bool): Whether to raise on non-zero exit. Defaults to True.

    Returns:
        subprocess.CompletedProcess[str]: The completed process.

    Raises:
        subprocess.CalledProcessError: If the command fails and check is True.
    """
    cmd = ["az"] + args
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        shell=_SHELL,
    )


def run_az_json(*, args: list[str]) -> dict | list | str:
    """
    Run an Azure CLI command and parse JSON output.

    Args:
        args (list[str]): The az CLI arguments (without the leading 'az').

    Returns:
        dict | list | str: The parsed JSON output.
    """
    result = run_az(args=args + ["-o", "json"])
    return json.loads(result.stdout)


def set_subscription(subscription: str) -> None:
    """
    Set the active Azure subscription.

    Args:
        subscription (str): The subscription name or ID.
    """
    logger.info("Setting subscription to: %s", subscription)
    run_az(args=["account", "set", "--subscription", subscription])


def create_resource_group(*, name: str, location: str, tags: list[str] | None = None) -> None:
    """
    Create an Azure resource group.

    Args:
        name (str): The resource group name.
        location (str): The Azure region.
        tags (list[str] | None): Tags in 'Key=Value' format.
    """
    logger.info("Creating resource group: %s in %s", name, location)
    cmd = ["group", "create", "--name", name, "--location", location]
    if tags:
        cmd += ["--tags"] + tags
    run_az(args=cmd)


def create_entra_app(*, display_name: str, service_management_reference: str = "") -> dict:
    """
    Create an Entra app registration with API scope and group claims.

    Args:
        display_name (str): The display name for the app registration.
        service_management_reference (str): Service Tree ID (required by some tenants).

    Returns:
        dict: A dict with keys 'app_id', 'app_object_id', 'tenant_id', 'sp_id'.
    """
    logger.info("Creating Entra app registration: %s", display_name)

    # Create app registration and capture output directly (avoids fragile name-based lookup)
    create_args = [
        "ad",
        "app",
        "create",
        "--display-name",
        display_name,
        "--sign-in-audience",
        "AzureADMyOrg",
    ]
    if service_management_reference:
        create_args += ["--service-management-reference", service_management_reference]
    create_args += [
        "--query",
        "{appId:appId, id:id}",
    ]
    app_create_result = run_az_json(args=create_args)
    app_id = app_create_result["appId"]
    app_object_id = app_create_result["id"]

    tenant_id = run_az_json(args=["account", "show", "--query", "tenantId"])

    # Create service principal (enterprise app)
    logger.info("Creating service principal for app: %s", app_id)
    run_az(args=["ad", "sp", "create", "--id", app_id])
    sp_info = run_az_json(
        args=[
            "ad",
            "sp",
            "show",
            "--id",
            app_id,
            "--query",
            "{id:id}",
        ]
    )
    sp_id = sp_info["id"]

    # Set Application ID URI
    logger.info("Setting Application ID URI: api://%s", app_id)
    run_az(
        args=[
            "rest",
            "--method",
            "PATCH",
            "--url",
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
            "--body",
            json.dumps({"identifierUris": [f"api://{app_id}"]}),
        ]
    )

    # Add 'access' scope
    scope_id = str(uuid.uuid4())
    logger.info("Adding 'access' scope (id: %s)", scope_id)
    scope_body = {
        "api": {
            "oauth2PermissionScopes": [
                {
                    "id": scope_id,
                    "isEnabled": True,
                    "type": "User",
                    "value": "access",
                    "adminConsentDisplayName": "Access PyRIT GUI",
                    "adminConsentDescription": "Allow access to the PyRIT GUI API",
                    "userConsentDisplayName": "Access PyRIT GUI",
                    "userConsentDescription": "Allow access to the PyRIT GUI API",
                }
            ]
        }
    }
    run_az(
        args=[
            "rest",
            "--method",
            "PATCH",
            "--url",
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
            "--body",
            json.dumps(scope_body),
        ]
    )

    # Configure group claims (ApplicationGroup)
    logger.info("Configuring group claims: ApplicationGroup")
    run_az(
        args=[
            "rest",
            "--method",
            "PATCH",
            "--url",
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
            "--body",
            json.dumps({"groupMembershipClaims": "ApplicationGroup"}),
        ]
    )

    # Configure optional claims (groups in ID and access tokens)
    logger.info("Adding 'groups' optional claim to ID and access tokens")
    optional_claims_body = {
        "optionalClaims": {
            "idToken": [{"name": "groups", "essential": False}],
            "accessToken": [{"name": "groups", "essential": False}],
        }
    }
    run_az(
        args=[
            "rest",
            "--method",
            "PATCH",
            "--url",
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
            "--body",
            json.dumps(optional_claims_body),
        ]
    )

    # Restrict token issuance to assigned users/groups
    logger.info("Restricting token issuance to assigned users/groups")
    run_az(args=["ad", "sp", "update", "--id", sp_id, "--set", "appRoleAssignmentRequired=true"])

    return {
        "app_id": app_id,
        "app_object_id": app_object_id,
        "tenant_id": tenant_id,
        "sp_id": sp_id,
    }


def assign_groups_to_app(*, sp_id: str, group_ids: list[str]) -> None:
    """
    Assign Entra security groups to the enterprise app.

    Args:
        sp_id (str): The service principal object ID.
        group_ids (list[str]): List of group object IDs to assign.
    """
    for group_id in group_ids:
        logger.info("Assigning group %s to enterprise app %s", group_id, sp_id)
        body = {
            "principalId": group_id,
            "resourceId": sp_id,
            "appRoleId": "00000000-0000-0000-0000-000000000000",
        }
        run_az(
            args=[
                "rest",
                "--method",
                "POST",
                "--url",
                f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_id}/appRoleAssignments",
                "--body",
                json.dumps(body),
            ]
        )


def create_sql_server_and_db(
    *,
    resource_group: str,
    location: str,
    server_name: str,
    database_name: str,
    tags: list[str] | None = None,
) -> dict:
    """
    Create an Azure SQL server with Entra-only auth and a database.

    Args:
        resource_group (str): The resource group name.
        location (str): The Azure region.
        server_name (str): The SQL server name.
        database_name (str): The database name.
        tags (list[str] | None): Tags in 'Key=Value' format.

    Returns:
        dict: A dict with keys 'server_fqdn' and 'database_name'.
    """
    # Get current user for Entra admin
    current_user = run_az_json(
        args=[
            "ad",
            "signed-in-user",
            "show",
            "--query",
            "{displayName:displayName, id:id}",
        ]
    )

    logger.info("Creating SQL server: %s (Entra admin: %s)", server_name, current_user["displayName"])
    sql_server_cmd = [
        "sql",
        "server",
        "create",
        "--name",
        server_name,
        "--resource-group",
        resource_group,
        "--location",
        location,
        "--enable-ad-only-auth",
        "--external-admin-principal-type",
        "User",
        "--external-admin-name",
        current_user["displayName"],
        "--external-admin-sid",
        current_user["id"],
    ]
    if tags:
        sql_server_cmd += ["--tags"] + tags
    run_az(args=sql_server_cmd)

    server_fqdn = run_az_json(
        args=[
            "sql",
            "server",
            "show",
            "--name",
            server_name,
            "--resource-group",
            resource_group,
            "--query",
            "fullyQualifiedDomainName",
        ]
    )

    logger.info("Creating database: %s on server %s", database_name, server_name)
    sql_db_cmd = [
        "sql",
        "db",
        "create",
        "--name",
        database_name,
        "--resource-group",
        resource_group,
        "--server",
        server_name,
        "--edition",
        "Basic",
        "--capacity",
        "5",
    ]
    if tags:
        sql_db_cmd += ["--tags"] + tags
    run_az(args=sql_db_cmd)

    # Allow Azure services to access the SQL server
    logger.info("Allowing Azure services to access SQL server")
    run_az(
        args=[
            "sql",
            "server",
            "firewall-rule",
            "create",
            "--resource-group",
            resource_group,
            "--server",
            server_name,
            "--name",
            "AllowAzureServices",
            "--start-ip-address",
            "0.0.0.0",
            "--end-ip-address",
            "0.0.0.0",
        ]
    )

    return {"server_fqdn": server_fqdn, "database_name": database_name}


# Matches AZURE_SQL_DB_CONNECTION_STRING=... (with or without quotes, whole line)
_SQL_CONN_RE = re.compile(r"^AZURE_SQL_DB_CONNECTION_STRING\s*=\s*.*$", re.MULTILINE)

# Connection string format expected by AzureSQLMemory (pyodbc + Entra MI auth)
_SQL_CONN_TEMPLATE = "mssql+pyodbc://@{server_fqdn}/{database_name}?driver=ODBC+Driver+18+for+SQL+Server"

# Matches AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL=... (with or without quotes, whole line)
_STORAGE_URL_RE = re.compile(r"^AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL\s*=\s*.*$", re.MULTILINE)

# Blob container URL format expected by AzureSQLMemory
_STORAGE_URL_TEMPLATE = "https://{account_name}.blob.core.windows.net/{container_name}"

# Container name follows the AIRT convention used by AzureSQLMemory
_STORAGE_CONTAINER_NAME = "dbdata"


def _storage_account_name(instance: str) -> str:
    """
    Derive a valid Azure storage account name from the instance name.

    Storage account names must be 3-24 lowercase alphanumeric characters
    (no hyphens, no uppercase). We strip non-alphanumerics from the instance
    name, then prepend ``copyrit`` and append ``sa`` to keep the naming
    pattern consistent with the rest of the per-instance resources.

    Args:
        instance (str): The instance name (e.g., ``partners-demo``).

    Returns:
        str: The derived storage account name (e.g., ``copyritpartnersdemosa``).
    """
    normalized = re.sub(r"[^a-z0-9]", "", instance.lower())
    return f"copyrit{normalized}sa"


def _prepare_env_content(
    *,
    env_file: Path,
    sql_server_fqdn: str,
    sql_database_name: str,
    storage_container_url: str,
) -> str:
    """
    Read the user-provided .env file and inject auto-generated values.

    The deploy script creates the SQL server/database and storage account, so
    those env values must point at the resources it provisioned. If the .env
    already contains either variable with a different value, it is overwritten
    with a warning.

    Args:
        env_file (Path): Path to the user-provided .env file.
        sql_server_fqdn (str): The SQL server FQDN (e.g., myserver.database.windows.net).
        sql_database_name (str): The SQL database name.
        storage_container_url (str): The blob container URL
            (e.g., https://myaccount.blob.core.windows.net/dbdata).

    Returns:
        str: The .env content with the correct SQL connection string and
        storage container URL injected.
    """
    content = env_file.read_text(encoding="utf-8")

    # SQL connection string
    sql_value = _SQL_CONN_TEMPLATE.format(
        server_fqdn=sql_server_fqdn,
        database_name=sql_database_name,
    )
    sql_line = f"AZURE_SQL_DB_CONNECTION_STRING={sql_value}"
    sql_match = _SQL_CONN_RE.search(content)
    if sql_match:
        existing_value = sql_match.group(0).split("=", 1)[1].strip().strip("\"'")
        if existing_value == sql_value:
            logger.info("AZURE_SQL_DB_CONNECTION_STRING already correct in .env")
        else:
            # Log only server/database, not the full connection string (may contain credentials).
            logger.warning(
                "Overwriting AZURE_SQL_DB_CONNECTION_STRING in .env to point at %s/%s",
                sql_server_fqdn,
                sql_database_name,
            )
        content = _SQL_CONN_RE.sub(sql_line, content)
    else:
        logger.info("Appending AZURE_SQL_DB_CONNECTION_STRING to .env")
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# ─── Database (auto-injected by deploy script) ───\n{sql_line}\n"

    # Storage container URL
    storage_line = f"AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL={storage_container_url}"
    storage_match = _STORAGE_URL_RE.search(content)
    if storage_match:
        existing_value = storage_match.group(0).split("=", 1)[1].strip().strip("\"'")
        if existing_value == storage_container_url:
            logger.info("AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL already correct in .env")
        else:
            logger.warning(
                "Overwriting AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL in .env to point at %s",
                storage_container_url,
            )
        content = _STORAGE_URL_RE.sub(storage_line, content)
    else:
        logger.info("Appending AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL to .env")
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# ─── Storage (auto-injected by deploy script) ───\n{storage_line}\n"

    return content


def create_storage_account(
    *,
    resource_group: str,
    location: str,
    account_name: str,
    container_name: str = _STORAGE_CONTAINER_NAME,
    tags: list[str] | None = None,
) -> dict:
    """
    Create a per-instance storage account and a private blob container.

    Used by ``AzureSQLMemory`` for blob-backed result data
    (the ``AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL`` env var). Container
    access is set to ``off`` (private) — the managed identity authenticates
    via ``Storage Blob Data Contributor``, granted in
    :func:`create_managed_identity_and_grant_roles`.

    Args:
        resource_group (str): The resource group name.
        location (str): The Azure region.
        account_name (str): The storage account name (3-24 lowercase alphanumeric).
        container_name (str): The blob container name. Defaults to ``dbdata``.
        tags (list[str] | None): Tags in 'Key=Value' format.

    Returns:
        dict: A dict with keys 'account_id' (resource ID) and 'container_url'
        (the full HTTPS URL the AIRT initializer expects).
    """
    logger.info("Creating storage account: %s", account_name)
    sa_cmd = [
        "storage",
        "account",
        "create",
        "--name",
        account_name,
        "--resource-group",
        resource_group,
        "--location",
        location,
        "--sku",
        "Standard_LRS",
        "--kind",
        "StorageV2",
        "--allow-blob-public-access",
        "false",
        "--min-tls-version",
        "TLS1_2",
    ]
    if tags:
        sa_cmd += ["--tags"] + tags
    run_az(args=sa_cmd)

    account_id = run_az_json(
        args=[
            "storage",
            "account",
            "show",
            "--name",
            account_name,
            "--resource-group",
            resource_group,
            "--query",
            "id",
        ]
    )

    logger.info("Creating blob container: %s/%s", account_name, container_name)
    # Use container-rm (ARM/control plane) instead of `container create` so we
    # don't need to grant the deployer a data plane role like Storage Blob Data
    # Contributor — Owner (control plane) is sufficient here.
    # Note: --public-access "off" is the correct enum for "no public access"
    # (the data-plane `container create` command uses "off" too; "None"/"none"
    # are rejected by the CLI).
    run_az(
        args=[
            "storage",
            "container-rm",
            "create",
            "--name",
            container_name,
            "--storage-account",
            account_name,
            "--resource-group",
            resource_group,
            "--public-access",
            "off",
        ]
    )

    container_url = _STORAGE_URL_TEMPLATE.format(
        account_name=account_name,
        container_name=container_name,
    )
    return {"account_id": account_id, "container_url": container_url}


def create_key_vault(
    *,
    resource_group: str,
    location: str,
    vault_name: str,
    env_content: str,
    tags: list[str] | None = None,
) -> str:
    """
    Create a Key Vault, populate it with the .env secret as a backup/audit
    snapshot, then apply the SFI network lockdown.

    The Container App does NOT read this secret at runtime — the .env contents
    are passed inline to the Container App via the Bicep `envFileContents`
    @secure() parameter. Azure Container Apps is not on Key Vault's
    "trusted services" list, so a locked-down KV would block runtime secret
    references. By passing the secret inline, we keep the runtime path
    independent of KV network access.

    The vault still receives the .env content as `env-global` to:
      - Provide a backup snapshot if the Container App is recreated.
      - Preserve the audit trail for the deployed configuration.

    The vault is created with network access restricted to the deployer's
    public IP only (defaultAction=Deny + deployer IP allowlisted). After the
    secret is uploaded, the deployer's IP rule is removed and public network
    access is fully disabled to satisfy the S360 / NS221 Secure PaaS alert.
    This avoids any window where the KV has unrestricted public access.

    Args:
        resource_group (str): The resource group name.
        location (str): The Azure region.
        vault_name (str): The Key Vault name.
        env_content (str): The prepared .env content to upload (with SQL connection string injected).
        tags (list[str] | None): Tags in 'Key=Value' format.

    Returns:
        str: The Key Vault resource ID.
    """
    # Determine the deployer's public IP so we can allowlist only that IP
    # instead of enabling unrestricted public access.
    logger.info("Detecting deployer public IP for Key Vault network rules")
    ip_result = subprocess.run(
        ["curl", "-s", "https://ipinfo.io/ip"],
        capture_output=True,
        text=True,
        check=True,
        shell=_SHELL,
    )
    deployer_ip = ip_result.stdout.strip()
    if not deployer_ip:
        raise RuntimeError("Failed to detect deployer public IP from https://ipinfo.io/ip")
    logger.info("Deployer IP: %s", deployer_ip)

    logger.info("Creating Key Vault: %s (locked down, deployer IP allowlisted)", vault_name)
    kv_cmd = [
        "keyvault",
        "create",
        "--name",
        vault_name,
        "--resource-group",
        resource_group,
        "--location",
        location,
        "--enable-rbac-authorization",
        "true",
        "--enable-purge-protection",
        "true",
        "--default-action",
        "Deny",
        "--bypass",
        "AzureServices",
        "--network-acls-ips",
        f"{deployer_ip}/32",
    ]
    if tags:
        kv_cmd += ["--tags"] + tags
    run_az(args=kv_cmd)

    kv_id = run_az_json(
        args=[
            "keyvault",
            "show",
            "--name",
            vault_name,
            "--query",
            "id",
        ]
    )

    # Grant current user Secrets Officer so we can write the secret
    current_user_id = run_az_json(
        args=[
            "ad",
            "signed-in-user",
            "show",
            "--query",
            "id",
        ]
    )
    logger.info("Granting Key Vault Secrets Officer to current user")
    run_az(
        args=[
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            current_user_id,
            "--assignee-principal-type",
            "User",
            "--role",
            "Key Vault Secrets Officer",
            "--scope",
            kv_id,
        ]
    )

    logger.info("Uploading .env to Key Vault secret: env-global")
    # Write prepared content to a temp file for upload (avoids shell escaping issues
    # with --value and keeps the user's original .env untouched).
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False, encoding="utf-8") as tmp:
            tmp_path = tmp.name
            tmp.write(env_content)

        # RBAC propagation can take up to a few minutes on a fresh vault.
        # Retry with backoff to handle the race condition.
        max_retries = 6
        for attempt in range(1, max_retries + 1):
            result = run_az(
                args=[
                    "keyvault",
                    "secret",
                    "set",
                    "--vault-name",
                    vault_name,
                    "--name",
                    "env-global",
                    "--file",
                    tmp_path,
                ],
                check=False,
            )
            if result.returncode == 0:
                break
            if attempt < max_retries:
                wait = 10 * attempt
                logger.warning(
                    "KV secret write failed (attempt %d/%d) — RBAC may still be propagating. Retrying in %ds...",
                    attempt,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
            else:
                logger.error("KV secret write failed after %d attempts. stderr: %s", max_retries, result.stderr)
                raise subprocess.CalledProcessError(result.returncode, result.args)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    # Remove deployer IP allowlist and fully disable public network access.
    # The KV was never publicly open — only the deployer's IP was allowed.
    # Matches the team standard observed on existing AIRT vaults
    # (e.g. airt-chatui-kv, AIRT-Blackhat-KV): publicNetworkAccess=Disabled,
    # default-deny ACL, bypass for trusted Azure services.
    logger.info("Removing deployer IP rule from Key Vault: %s", vault_name)
    run_az(
        args=[
            "keyvault",
            "network-rule",
            "remove",
            "--name",
            vault_name,
            "--ip-address",
            f"{deployer_ip}/32",
        ]
    )
    logger.info("Disabling public network access on Key Vault: %s", vault_name)
    run_az(
        args=[
            "keyvault",
            "update",
            "--name",
            vault_name,
            "--public-network-access",
            "Disabled",
        ]
    )

    return kv_id


def deploy_bicep(
    *,
    resource_group: str,
    app_name: str,
    container_image: str,
    tenant_id: str,
    client_id: str,
    group_ids: str,
    sql_server_fqdn: str,
    sql_database_name: str,
    kv_resource_id: str,
    acr_name: str,
    env_file_contents: str,
    owner_tag: str = "",
) -> dict:
    """
    Deploy the Bicep template.

    The .env contents are passed via a temp parameters file (not inline
    --parameters key=value) because the value is multi-line, contains '='
    characters, and is marked @secure() in Bicep — passing it inline is
    fragile and can leak the value into shell history. The temp file is
    deleted after deployment.

    Args:
        resource_group (str): The resource group name.
        app_name (str): The Container App name.
        container_image (str): The container image reference.
        tenant_id (str): The Entra tenant ID.
        client_id (str): The Entra app registration client ID.
        group_ids (str): Comma-separated group object IDs.
        sql_server_fqdn (str): The SQL server FQDN.
        sql_database_name (str): The SQL database name.
        kv_resource_id (str): The Key Vault resource ID (kept for the
            keyVaultName output; not referenced at container runtime).
        acr_name (str): The ACR name.
        env_file_contents (str): The prepared .env content to inject as
            the Container App's `env-file` secret.
        owner_tag (str): Value for the Owner tag on Bicep-managed resources.

    Returns:
        dict: The deployment outputs.
    """
    logger.info("Deploying Bicep template to resource group: %s", resource_group)

    parameters: dict = {
        "appName": {"value": app_name},
        "containerImage": {"value": container_image},
        "entraTenantId": {"value": tenant_id},
        "entraClientId": {"value": client_id},
        "allowedGroupObjectIds": {"value": group_ids},
        "sqlServerFqdn": {"value": sql_server_fqdn},
        "sqlDatabaseName": {"value": sql_database_name},
        "keyVaultResourceId": {"value": kv_resource_id},
        "acrName": {"value": acr_name},
        "enablePrivateEndpoint": {"value": False},
        "envFileContents": {"value": env_file_contents},
    }
    if owner_tag:
        parameters["tags"] = {"value": {"Service": "pyrit-gui", "Owner": owner_tag}}

    parameters_doc = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": parameters,
    }

    params_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".parameters.json", delete=False, encoding="utf-8") as tmp:
            params_path = tmp.name
            json.dump(parameters_doc, tmp)

        return run_az_json(
            args=[
                "deployment",
                "group",
                "create",
                "--resource-group",
                resource_group,
                "--template-file",
                str(BICEP_TEMPLATE),
                "--parameters",
                f"@{params_path}",
                "--query",
                "properties.outputs",
            ]
        )
    finally:
        if params_path:
            Path(params_path).unlink(missing_ok=True)


def post_deploy(
    *,
    app_object_id: str,
    fqdn: str,
) -> None:
    """
    Run post-deployment steps: SPA redirect URI.

    Args:
        app_object_id (str): The Entra app registration object ID (for Graph API).
        fqdn (str): The deployed app FQDN.
    """
    # Set SPA redirect URI via Graph REST API (more portable than --spa-redirect-uris flag)
    logger.info("Setting SPA redirect URI: https://%s", fqdn)
    spa_body = {"spa": {"redirectUris": [f"https://{fqdn}"]}}
    run_az(
        args=[
            "rest",
            "--method",
            "PATCH",
            "--url",
            f"https://graph.microsoft.com/v1.0/applications/{app_object_id}",
            "--body",
            json.dumps(spa_body),
        ]
    )


def create_managed_identity_and_grant_roles(
    *,
    resource_group: str,
    location: str,
    identity_name: str,
    acr_name: str,
    storage_account_id: str,
    tags: list[str] | None = None,
) -> str:
    """
    Create a user-assigned managed identity and grant it pre-deploy RBAC roles.

    The MI must have AcrPull and Storage Blob Data Contributor before the Bicep
    deployment can provision the container (it needs to pull the image and
    access blob storage at startup). Creating the MI here and granting roles
    before Bicep avoids the race condition of Bicep creating the MI but the
    container needing roles that don't exist yet.

    Note: Key Vault Secrets User is intentionally NOT granted. The Container
    App reads its .env contents from an inline secret passed via the Bicep
    `envFileContents` parameter, not from a Key Vault secret reference.

    Bicep's MI resource declaration is idempotent — it will adopt the existing MI.

    Args:
        resource_group (str): The resource group name.
        location (str): The Azure region.
        identity_name (str): The managed identity name.
        acr_name (str): The ACR name for AcrPull role.
        storage_account_id (str): The storage account resource ID for Storage
            Blob Data Contributor role.
        tags (list[str] | None): Tags in 'Key=Value' format.

    Returns:
        str: The managed identity principal ID.
    """
    logger.info("Creating managed identity: %s", identity_name)
    mi_cmd = [
        "identity",
        "create",
        "--name",
        identity_name,
        "--resource-group",
        resource_group,
        "--location",
        location,
    ]
    if tags:
        mi_cmd += ["--tags"] + tags
    run_az(args=mi_cmd)

    mi_principal_id = run_az_json(
        args=[
            "identity",
            "show",
            "--name",
            identity_name,
            "--resource-group",
            resource_group,
            "--query",
            "principalId",
        ]
    )

    # Grant AcrPull
    acr_id = run_az_json(
        args=[
            "acr",
            "show",
            "--name",
            acr_name,
            "--query",
            "id",
        ]
    )
    logger.info("Granting AcrPull to managed identity on ACR: %s", acr_name)
    run_az(
        args=[
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            mi_principal_id,
            "--assignee-principal-type",
            "ServicePrincipal",
            "--role",
            "AcrPull",
            "--scope",
            acr_id,
        ]
    )

    # Grant Storage Blob Data Contributor on the per-instance storage account
    logger.info("Granting Storage Blob Data Contributor to managed identity on storage account")
    run_az(
        args=[
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            mi_principal_id,
            "--assignee-principal-type",
            "ServicePrincipal",
            "--role",
            "Storage Blob Data Contributor",
            "--scope",
            storage_account_id,
        ]
    )

    return mi_principal_id


def _grant_aoai_roles(
    *,
    mi_principal_id: str,
    subscription: str,
    aoai_resource_names: list[str],
) -> int:
    """
    Grant Cognitive Services OpenAI User to the managed identity on AOAI resources.

    Looks up each resource by name in the subscription and grants the role.
    Skips resources that cannot be found with a warning.

    Args:
        mi_principal_id (str): The managed identity principal ID.
        subscription (str): The Azure subscription ID.
        aoai_resource_names (list[str]): Cognitive Services account names.

    Returns:
        int: The number of successful role assignments.
    """
    granted = 0
    for name in aoai_resource_names:
        resource_id = run_az_json(
            args=[
                "cognitiveservices",
                "account",
                "list",
                "--subscription",
                subscription,
                "--query",
                f"[?name=='{name}'].id | [0]",
            ]
        )
        if not resource_id:
            logger.warning("AOAI resource '%s' not found in subscription — skipping", name)
            continue

        logger.info("Granting Cognitive Services OpenAI User on: %s", name)
        run_az(
            args=[
                "role",
                "assignment",
                "create",
                "--assignee-object-id",
                mi_principal_id,
                "--assignee-principal-type",
                "ServicePrincipal",
                "--role",
                "Cognitive Services OpenAI User",
                "--scope",
                resource_id,
            ]
        )
        granted += 1

    return granted


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """
    Parse command-line arguments.

    Args:
        args (list[str] | None): Arguments to parse. Defaults to sys.argv.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Deploy an isolated CoPyRIT GUI instance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--instance-name",
        required=True,
        help="Name for this instance (used in resource naming, e.g., 'partners-demo')",
    )
    parser.add_argument(
        "--env-file",
        required=True,
        type=Path,
        help="Path to the .env file containing target endpoints and secrets",
    )
    parser.add_argument(
        "--subscription",
        required=True,
        help="Azure subscription name or ID",
    )
    parser.add_argument(
        "--location",
        default="eastus2",
        help="Azure region (default: eastus2)",
    )
    parser.add_argument(
        "--acr-name",
        required=True,
        help="Shared ACR name (the image must already be pushed)",
    )
    parser.add_argument(
        "--container-image",
        required=True,
        help="Container image reference (e.g., myacr.azurecr.io/pyrit:abc1234)",
    )
    parser.add_argument(
        "--allowed-groups",
        required=True,
        help="Comma-separated Entra group object IDs to grant access",
    )
    parser.add_argument(
        "--owner-tag",
        default="",
        help=(
            "Value for the Owner tag applied to all per-instance Azure resources. "
            "Required when the target subscription enforces a 'Require a tag on "
            "resources' Azure Policy (e.g., the AI Red Team Tooling subscription). "
            "Optional only on subscriptions without such a policy."
        ),
    )
    parser.add_argument(
        "--service-management-reference",
        default="",
        help="Service Tree ID for Entra app registration (required by some tenants)",
    )
    parser.add_argument(
        "--aoai-resource-names",
        default="",
        help=(
            "Comma-separated Cognitive Services account names to grant "
            "Cognitive Services OpenAI User to the managed identity. "
            "Optional — if omitted, AOAI RBAC must be granted manually."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without executing",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    """
    Main entry point for the deployment script.

    Args:
        args (list[str] | None): CLI arguments. Defaults to sys.argv.

    Returns:
        int: Exit code (0 for success).
    """
    parsed = parse_args(args)

    instance = parsed.instance_name
    env_file = parsed.env_file.resolve()

    if not env_file.exists():
        logger.error("Env file not found: %s", env_file)
        return 1

    if not BICEP_TEMPLATE.exists():
        logger.error("Bicep template not found: %s", BICEP_TEMPLATE)
        return 1

    # Derive resource names from instance name
    rg_name = f"copyrit-{instance}"
    app_name = f"copyrit-{instance}"
    sql_server_name = f"copyrit-{instance}-sql"
    sql_db_name = f"pyrit-{instance}"
    kv_name = f"copyrit-{instance}-kv"
    storage_account_name = _storage_account_name(instance)
    entra_app_name = f"CoPyRIT GUI ({instance})"
    group_ids = [g.strip() for g in parsed.allowed_groups.split(",") if g.strip()]

    # Validate Azure resource name length constraints
    if len(kv_name) > 24:
        logger.error(
            "Key Vault name '%s' exceeds 24-char limit (got %d). Use a shorter --instance-name (max 13 characters).",
            kv_name,
            len(kv_name),
        )
        return 1
    if len(app_name) > 32:
        logger.error(
            "Container App name '%s' exceeds 32-char limit (got %d). "
            "Use a shorter --instance-name (max 24 characters).",
            app_name,
            len(app_name),
        )
        return 1
    if len(storage_account_name) > 24 or len(storage_account_name) < 3:
        logger.error(
            "Storage account name '%s' is %d chars (must be 3-24). "
            "After stripping non-alphanumerics from --instance-name, the name "
            "is 'copyrit{normalized}sa'. Use an --instance-name whose alphanumeric "
            "length keeps the total under 24.",
            storage_account_name,
            len(storage_account_name),
        )
        return 1

    if parsed.dry_run:
        logger.info("=== DRY RUN ===")
        logger.info("Instance name: %s", instance)
        logger.info("Resource group: %s", rg_name)
        logger.info("App name: %s", app_name)
        logger.info("SQL server: %s", sql_server_name)
        logger.info("SQL database: %s", sql_db_name)
        logger.info("Key Vault: %s", kv_name)
        logger.info("Storage account: %s (container: %s)", storage_account_name, _STORAGE_CONTAINER_NAME)
        logger.info("Entra app: %s", entra_app_name)
        logger.info("Allowed groups: %s", group_ids)
        logger.info("Env file: %s", env_file)
        logger.info("Container image: %s", parsed.container_image)
        logger.info("ACR: %s", parsed.acr_name)
        logger.info("Location: %s", parsed.location)
        logger.info("Subscription: %s", parsed.subscription)
        if parsed.aoai_resource_names:
            logger.info("AOAI resources: %s", parsed.aoai_resource_names)
        else:
            logger.info("AOAI resources: (none — RBAC must be granted manually)")
        return 0

    try:
        # Build tags list from --owner-tag
        resource_tags = [f"Owner={parsed.owner_tag}"] if parsed.owner_tag else None

        # Step 1: Set subscription
        set_subscription(parsed.subscription)

        # Step 2: Create resource group
        create_resource_group(name=rg_name, location=parsed.location, tags=resource_tags)

        # Step 3: Create Entra app registration
        entra = create_entra_app(
            display_name=entra_app_name,
            service_management_reference=parsed.service_management_reference,
        )

        # Step 4: Assign groups to enterprise app
        assign_groups_to_app(sp_id=entra["sp_id"], group_ids=group_ids)

        # Step 5: Create SQL server + database
        sql = create_sql_server_and_db(
            resource_group=rg_name,
            location=parsed.location,
            server_name=sql_server_name,
            database_name=sql_db_name,
            tags=resource_tags,
        )

        # Step 6: Create storage account + blob container
        storage = create_storage_account(
            resource_group=rg_name,
            location=parsed.location,
            account_name=storage_account_name,
            tags=resource_tags,
        )

        # Step 6b: Prepare .env content with auto-injected SQL connection string + storage URL
        env_content = _prepare_env_content(
            env_file=env_file,
            sql_server_fqdn=sql["server_fqdn"],
            sql_database_name=sql["database_name"],
            storage_container_url=storage["container_url"],
        )

        # Step 7: Create Key Vault + upload .env (backup snapshot) + apply SFI lockdown
        kv_id = create_key_vault(
            resource_group=rg_name,
            location=parsed.location,
            vault_name=kv_name,
            env_content=env_content,
            tags=resource_tags,
        )

        # Step 8: Create managed identity + grant pre-deploy RBAC
        mi_name = f"{app_name}-identity"
        mi_principal_id = create_managed_identity_and_grant_roles(
            resource_group=rg_name,
            location=parsed.location,
            identity_name=mi_name,
            acr_name=parsed.acr_name,
            storage_account_id=storage["account_id"],
            tags=resource_tags,
        )

        # Step 8b: Grant AOAI roles (optional — only if --aoai-resource-names provided)
        aoai_names = [n.strip() for n in parsed.aoai_resource_names.split(",") if n.strip()]
        aoai_granted = 0
        if aoai_names:
            aoai_granted = _grant_aoai_roles(
                mi_principal_id=mi_principal_id,
                subscription=parsed.subscription,
                aoai_resource_names=aoai_names,
            )
            logger.info("Granted Cognitive Services OpenAI User on %d/%d AOAI resources", aoai_granted, len(aoai_names))

        # Step 9: Deploy Bicep (passes .env content inline as @secure() param)
        outputs = deploy_bicep(
            resource_group=rg_name,
            app_name=app_name,
            container_image=parsed.container_image,
            tenant_id=entra["tenant_id"],
            client_id=entra["app_id"],
            group_ids=parsed.allowed_groups,
            sql_server_fqdn=sql["server_fqdn"],
            sql_database_name=sql["database_name"],
            kv_resource_id=kv_id,
            acr_name=parsed.acr_name,
            env_file_contents=env_content,
            owner_tag=parsed.owner_tag,
        )

        fqdn = outputs["appFqdn"]["value"]

        # Step 10: Post-deploy (SPA redirect)
        post_deploy(
            app_object_id=entra["app_object_id"],
            fqdn=fqdn,
        )

        # Summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("DEPLOYMENT COMPLETE")
        logger.info("=" * 60)
        logger.info("Instance:     %s", instance)
        logger.info("URL:          https://%s", fqdn)
        logger.info("Resource group: %s", rg_name)
        logger.info("Entra app ID: %s", entra["app_id"])
        logger.info("SQL server:   %s", sql["server_fqdn"])
        logger.info("SQL database: %s", sql["database_name"])
        logger.info("Key Vault:    %s", kv_name)
        logger.info("Storage:      %s (container: %s)", storage_account_name, _STORAGE_CONTAINER_NAME)
        logger.info("")
        logger.info("MANUAL STEPS REQUIRED:")
        logger.info("  1. Create SQL contained user:")
        logger.info("     Connect to %s as Entra admin and run:", sql["server_fqdn"])
        logger.info("       CREATE USER [%s-identity] FROM EXTERNAL PROVIDER;", app_name)
        logger.info("       ALTER ROLE db_datareader ADD MEMBER [%s-identity];", app_name)
        logger.info("       ALTER ROLE db_datawriter ADD MEMBER [%s-identity];", app_name)
        logger.info("       ALTER ROLE db_ddladmin ADD MEMBER [%s-identity];", app_name)
        logger.info("  2. Add users to the Entra security group(s)")
        if not aoai_names:
            logger.info("  3. Grant Cognitive Services roles if using MI-auth for AOAI:")
            logger.info("     az role assignment create --assignee-object-id %s \\", mi_principal_id)
            logger.info("       --assignee-principal-type ServicePrincipal \\")
            logger.info("       --role 'Cognitive Services OpenAI User' --scope <aoai-resource-id>")
        else:
            logger.info(
                "  3. AOAI RBAC: %d/%d resources granted (via --aoai-resource-names)", aoai_granted, len(aoai_names)
            )
        logger.info("")
        logger.info("Restart the container app after completing step 1:")
        logger.info("  az containerapp revision restart -n %s -g %s \\", app_name, rg_name)
        logger.info("    --revision $(az containerapp show -n %s -g %s \\", app_name, rg_name)
        logger.info("      --query properties.latestRevisionName -o tsv)")
        logger.info("=" * 60)

        return 0

    except subprocess.CalledProcessError as e:
        logger.error("Command failed (exit code %d): %s", e.returncode, " ".join(e.cmd))
        if e.stderr:
            logger.error("stderr: %s", e.stderr.strip())
        return 1


if __name__ == "__main__":
    sys.exit(main())
