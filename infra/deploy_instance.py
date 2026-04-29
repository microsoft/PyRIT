# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CoPyRIT GUI — Deploy a new isolated instance.

Automates the full deployment of an isolated CoPyRIT GUI instance:
  1. Resource group
  2. Entra app registration + API scope + group claims
  3. Entra security group (optional — can use existing)
  4. Azure SQL server + database
  5. Key Vault + populate .env secret (auto-injects SQL connection string)
  6. Managed identity + RBAC role assignments (AcrPull, KV Secrets User)
  6b. AOAI RBAC (optional — Cognitive Services OpenAI User on specified resources)
  7. Bicep deployment (Container App, networking, logging)
  8. Post-deploy: SPA redirect URI

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


def _prepare_env_content(
    *,
    env_file: Path,
    sql_server_fqdn: str,
    sql_database_name: str,
) -> str:
    """
    Read the user-provided .env file and inject the correct SQL connection string.

    The deploy script creates the SQL server and database, so the connection string
    must match. If the .env already contains an AZURE_SQL_DB_CONNECTION_STRING that
    differs, it is overwritten with a warning.

    Args:
        env_file (Path): Path to the user-provided .env file.
        sql_server_fqdn (str): The SQL server FQDN (e.g., myserver.database.windows.net).
        sql_database_name (str): The SQL database name.

    Returns:
        str: The .env content with the correct SQL connection string injected.
    """
    content = env_file.read_text(encoding="utf-8")
    correct_value = _SQL_CONN_TEMPLATE.format(
        server_fqdn=sql_server_fqdn,
        database_name=sql_database_name,
    )
    new_line = f"AZURE_SQL_DB_CONNECTION_STRING={correct_value}"

    match = _SQL_CONN_RE.search(content)
    if match:
        existing_line = match.group(0).strip()
        existing_value = existing_line.split("=", 1)[1].strip().strip("\"'")
        if existing_value == correct_value:
            logger.info("AZURE_SQL_DB_CONNECTION_STRING already correct in .env")
        else:
            # Log only server/database, not the full connection string (may contain credentials).
            logger.warning(
                "Overwriting AZURE_SQL_DB_CONNECTION_STRING in .env to point at %s/%s",
                sql_server_fqdn,
                sql_database_name,
            )
        content = _SQL_CONN_RE.sub(new_line, content)
    else:
        logger.info("Appending AZURE_SQL_DB_CONNECTION_STRING to .env")
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# ─── Database (auto-injected by deploy script) ───\n{new_line}\n"

    return content


def create_key_vault(
    *,
    resource_group: str,
    location: str,
    vault_name: str,
    env_content: str,
    tags: list[str] | None = None,
) -> str:
    """
    Create a Key Vault and populate it with the .env secret.

    Args:
        resource_group (str): The resource group name.
        location (str): The Azure region.
        vault_name (str): The Key Vault name.
        env_content (str): The prepared .env content to upload (with SQL connection string injected).
        tags (list[str] | None): Tags in 'Key=Value' format.

    Returns:
        str: The Key Vault resource ID.
    """
    logger.info("Creating Key Vault: %s", vault_name)
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
    owner_tag: str = "",
) -> dict:
    """
    Deploy the Bicep template.

    Args:
        resource_group (str): The resource group name.
        app_name (str): The Container App name.
        container_image (str): The container image reference.
        tenant_id (str): The Entra tenant ID.
        client_id (str): The Entra app registration client ID.
        group_ids (str): Comma-separated group object IDs.
        sql_server_fqdn (str): The SQL server FQDN.
        sql_database_name (str): The SQL database name.
        kv_resource_id (str): The Key Vault resource ID.
        acr_name (str): The ACR name.
        owner_tag (str): Value for the Owner tag on Bicep-managed resources.

    Returns:
        dict: The deployment outputs.
    """
    logger.info("Deploying Bicep template to resource group: %s", resource_group)
    params = [
        "deployment",
        "group",
        "create",
        "--resource-group",
        resource_group,
        "--template-file",
        str(BICEP_TEMPLATE),
        "--parameters",
        f"appName={app_name}",
        f"containerImage={container_image}",
        f"entraTenantId={tenant_id}",
        f"entraClientId={client_id}",
        f"allowedGroupObjectIds={group_ids}",
        f"sqlServerFqdn={sql_server_fqdn}",
        f"sqlDatabaseName={sql_database_name}",
        f"keyVaultResourceId={kv_resource_id}",
        f"acrName={acr_name}",
        "enablePrivateEndpoint=false",
    ]
    if owner_tag:
        bicep_tags = {"Service": "pyrit-gui", "Owner": owner_tag}
        params.append(f"tags={json.dumps(bicep_tags)}")
    params += [
        "--query",
        "properties.outputs",
    ]
    return run_az_json(args=params)


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
    kv_resource_id: str,
    acr_name: str,
    tags: list[str] | None = None,
) -> str:
    """
    Create a user-assigned managed identity and grant it pre-deploy RBAC roles.

    The MI must have KV Secrets User and AcrPull before the Bicep deployment
    can provision the container (it needs to pull the image and read the KV secret).
    Creating the MI here and granting roles before Bicep avoids the race condition
    of Bicep creating the MI but the container needing roles that don't exist yet.

    Bicep's MI resource declaration is idempotent — it will adopt the existing MI.

    Args:
        resource_group (str): The resource group name.
        location (str): The Azure region.
        identity_name (str): The managed identity name.
        kv_resource_id (str): The Key Vault resource ID for Secrets User role.
        acr_name (str): The ACR name for AcrPull role.
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

    # Grant KV Secrets User
    logger.info("Granting Key Vault Secrets User to managed identity")
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
            "Key Vault Secrets User",
            "--scope",
            kv_resource_id,
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
        help="Value for the Owner tag on Azure resources (required by some subscriptions)",
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

    if parsed.dry_run:
        logger.info("=== DRY RUN ===")
        logger.info("Instance name: %s", instance)
        logger.info("Resource group: %s", rg_name)
        logger.info("App name: %s", app_name)
        logger.info("SQL server: %s", sql_server_name)
        logger.info("SQL database: %s", sql_db_name)
        logger.info("Key Vault: %s", kv_name)
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

        # Step 5b: Prepare .env content with correct SQL connection string
        env_content = _prepare_env_content(
            env_file=env_file,
            sql_server_fqdn=sql["server_fqdn"],
            sql_database_name=sql["database_name"],
        )

        # Step 6: Create Key Vault + upload .env
        kv_id = create_key_vault(
            resource_group=rg_name,
            location=parsed.location,
            vault_name=kv_name,
            env_content=env_content,
            tags=resource_tags,
        )

        # Step 7: Create managed identity + grant pre-deploy RBAC
        mi_name = f"{app_name}-identity"
        mi_principal_id = create_managed_identity_and_grant_roles(
            resource_group=rg_name,
            location=parsed.location,
            identity_name=mi_name,
            kv_resource_id=kv_id,
            acr_name=parsed.acr_name,
            tags=resource_tags,
        )

        # Step 7b: Grant AOAI roles (optional — only if --aoai-resource-names provided)
        aoai_names = [n.strip() for n in parsed.aoai_resource_names.split(",") if n.strip()]
        aoai_granted = 0
        if aoai_names:
            aoai_granted = _grant_aoai_roles(
                mi_principal_id=mi_principal_id,
                subscription=parsed.subscription,
                aoai_resource_names=aoai_names,
            )
            logger.info("Granted Cognitive Services OpenAI User on %d/%d AOAI resources", aoai_granted, len(aoai_names))

        # Step 8: Deploy Bicep
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
            owner_tag=parsed.owner_tag,
        )

        fqdn = outputs["appFqdn"]["value"]

        # Step 9: Post-deploy (SPA redirect)
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
