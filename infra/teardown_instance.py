# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CoPyRIT GUI — Tear down an isolated instance.

Removes all Azure resources for an instance deployed by deploy_instance.py.
Entra resources (app registration, service principal) must be deleted separately
since they live outside the resource group.

Usage:
    python infra/teardown_instance.py --instance-name partners-demo \\
        --subscription "<subscription-id>" \
        --resource-group-id "/subscriptions/<subscription-id>/resourceGroups/copyrit-partners-demo" \
        --acknowledge-egress-ip-release

    # Include Entra cleanup:
    python infra/teardown_instance.py --instance-name partners-demo \\
        --subscription "<subscription-id>" \
        --resource-group-id "/subscriptions/<subscription-id>/resourceGroups/copyrit-partners-demo" \
        --acknowledge-egress-ip-release \
        --delete-entra-app --entra-app-id "<application-client-id>"

"""

import argparse
import json
import logging
import platform
import re
import subprocess
import sys
from typing import cast

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# On Windows, az CLI is a .cmd script that requires shell=True for subprocess to find it.
_SHELL = platform.system() == "Windows"
_INSTANCE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,11}[a-z0-9])?$")
_RESOURCE_GROUP_ID_RE = re.compile(
    r"^/subscriptions/([^/]+)/resourceGroups/([^/]+)$",
    re.IGNORECASE,
)
_OWNERSHIP_TAGS: dict[str, str] = {
    "Service": "pyrit-gui",
    "ManagedBy": "infra/deploy_instance.py",
}


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


def _expect_json_object(value: object, *, context: str) -> dict[str, object]:
    """Require a JSON object with string keys at an Azure CLI response boundary."""
    if not isinstance(value, dict):
        raise RuntimeError(f"Azure CLI returned invalid {context} data")
    return cast("dict[str, object]", value)


def _expect_json_array(value: object, *, context: str) -> list[object]:
    """Require a JSON array at an Azure CLI response boundary."""
    if not isinstance(value, list):
        raise RuntimeError(f"Azure CLI returned invalid {context} data")
    return cast("list[object]", value)


def _expect_string(value: object, *, context: str) -> str:
    """Require a nonempty string at an Azure CLI response boundary."""
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Azure CLI returned invalid {context} data")
    return value


def run_az_json(*, args: list[str]) -> object | None:
    """
    Run an Azure CLI command and parse JSON output.

    Args:
        args (list[str]): The az CLI arguments (without the leading 'az').

    Returns:
        object | None: The parsed JSON output, or None on command failure.
    """
    result = run_az(args=args + ["-o", "json"], check=False)
    if result.returncode != 0:
        return None
    parsed: object = json.loads(result.stdout)
    return parsed


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """
    Parse command-line arguments.

    Args:
        args (list[str] | None): Arguments to parse. Defaults to sys.argv.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Tear down an isolated CoPyRIT GUI instance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--instance-name",
        required=True,
        help="Instance name (must match the name used during deployment)",
    )
    parser.add_argument(
        "--subscription",
        required=True,
        help="Azure subscription name or ID",
    )
    parser.add_argument(
        "--resource-group-id",
        required=True,
        help="Exact resource ID of the instance resource group",
    )
    parser.add_argument(
        "--delete-entra-app",
        action="store_true",
        help="Also delete the Entra app registration and service principal",
    )
    parser.add_argument(
        "--entra-app-id",
        default="",
        help="Exact Application (client) ID; required with --delete-entra-app",
    )
    parser.add_argument(
        "--acknowledge-egress-ip-release",
        action="store_true",
        help="Confirm that external allowlists have been updated before the static egress IP is released",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive prompt; ownership and egress-release checks still apply",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    """
    Main entry point for the teardown script.

    Args:
        args (list[str] | None): CLI arguments. Defaults to sys.argv.

    Returns:
        int: Exit code (0 for success).
    """
    parsed = parse_args(args)

    instance = _expect_string(parsed.instance_name, context="instance name")
    rg_name = f"copyrit-{instance}"
    entra_app_name = f"CoPyRIT GUI ({instance})"

    if not _INSTANCE_NAME_RE.fullmatch(instance):
        logger.error(
            "--instance-name must be 1-13 lowercase letters, numbers, or internal hyphens, "
            "and must start and end with a letter or number"
        )
        return 1
    if not parsed.acknowledge_egress_ip_release:
        logger.error(
            "--acknowledge-egress-ip-release is required after removing the instance IP from external allowlists"
        )
        return 1
    if parsed.delete_entra_app != bool(parsed.entra_app_id):
        logger.error("--delete-entra-app and --entra-app-id must be provided together")
        return 1

    try:
        logger.info("Setting subscription to: %s", parsed.subscription)
        run_az(args=["account", "set", "--subscription", parsed.subscription])

        account = _expect_json_object(
            run_az_json(args=["account", "show", "--query", "{id:id,name:name}"]),
            context="active subscription",
        )
        account_id = _expect_string(account.get("id"), context="active subscription ID")
        account_name = _expect_string(account.get("name"), context="active subscription name")

        resource_group_match = _RESOURCE_GROUP_ID_RE.fullmatch(parsed.resource_group_id)
        if resource_group_match is None:
            raise RuntimeError("--resource-group-id is not a canonical Azure resource group ID")
        resource_group_subscription_id, resource_group_name = resource_group_match.groups()
        if resource_group_subscription_id.casefold() != account_id.casefold() or resource_group_name != rg_name:
            raise RuntimeError("--resource-group-id does not match the active subscription and derived instance name")

        group_info = _expect_json_object(
            run_az_json(args=["group", "show", "--name", rg_name, "--query", "{id:id,name:name,tags:tags}"]),
            context="resource group",
        )
        group_id = _expect_string(group_info.get("id"), context="resource group ID")
        if group_id.casefold() != parsed.resource_group_id.casefold():
            raise RuntimeError("Azure returned a resource group ID different from --resource-group-id")
        tags = _expect_json_object(group_info.get("tags"), context="resource group tags")
        expected_tags = {**_OWNERSHIP_TAGS, "Instance": instance}
        if any(tags.get(key) != value for key, value in expected_tags.items()):
            raise RuntimeError(
                "Resource group ownership tags do not match deploy_instance.py; refuse automatic deletion"
            )

        entra_app: dict[str, object] | None = None
        if parsed.delete_entra_app:
            entra_app = _expect_json_object(
                run_az_json(
                    args=[
                        "ad",
                        "app",
                        "show",
                        "--id",
                        parsed.entra_app_id,
                        "--query",
                        "{appId:appId,displayName:displayName}",
                    ]
                ),
                context="Entra application",
            )
            if entra_app.get("appId") != parsed.entra_app_id or entra_app.get("displayName") != entra_app_name:
                raise RuntimeError("--entra-app-id does not identify the expected instance application")

        egress_ip_value = run_az_json(
            args=[
                "network",
                "public-ip",
                "show",
                "--resource-group",
                rg_name,
                "--name",
                f"{rg_name}-egress-pip",
                "--query",
                "ipAddress",
            ]
        )
        egress_ip = egress_ip_value if isinstance(egress_ip_value, str) and egress_ip_value else None
        principal_id = _expect_string(
            run_az_json(
                args=[
                    "identity",
                    "show",
                    "--resource-group",
                    rg_name,
                    "--name",
                    f"{rg_name}-identity",
                    "--query",
                    "principalId",
                ]
            ),
            context="managed identity principal ID",
        )
        assignment_values = _expect_json_array(
            run_az_json(
                args=[
                    "role",
                    "assignment",
                    "list",
                    "--assignee-object-id",
                    principal_id,
                    "--all",
                    "--fill-principal-name",
                    "false",
                    "--query",
                    "[].{id:id,scope:scope}",
                ]
            ),
            context="managed identity role assignments",
        )
        assignments: list[dict[str, str]] = []
        for value in assignment_values:
            assignment = _expect_json_object(value, context="role assignment")
            assignment_id = _expect_string(assignment.get("id"), context="role assignment ID")
            scope_value = assignment.get("scope")
            scope = scope_value if isinstance(scope_value, str) and scope_value else "<unknown scope>"
            assignments.append({"id": assignment_id, "scope": scope})

        logger.info("Instance:          %s", instance)
        logger.info("Subscription:      %s (%s)", account_name, account_id)
        logger.info("Resource group ID: %s", group_id)
        logger.info("Static egress IP:  %s (will be released)", egress_ip or "<not allocated>")
        logger.info("Role assignments:  %d (will be removed)", len(assignments))
        if entra_app is not None:
            logger.info("Entra app ID:      %s (will be deleted)", parsed.entra_app_id)

        if not parsed.yes:
            confirm = input(f"\nDelete verified instance resource group '{rg_name}' and all its resources? [y/N] ")
            if confirm.lower() != "y":
                logger.info("Aborted.")
                return 0

        for assignment in assignments:
            logger.info("Deleting role assignment on %s", assignment["scope"])
            run_az(args=["role", "assignment", "delete", "--ids", assignment["id"]])

        logger.info("Deleting resource group: %s (this may take several minutes)", rg_name)
        run_az(args=["group", "delete", "--name", rg_name, "--yes"])
        group_exists = run_az_json(args=["group", "exists", "--name", rg_name])
        if group_exists is not False:
            raise RuntimeError(f"Resource group '{rg_name}' still exists after deletion returned")

        if parsed.delete_entra_app:
            logger.info("Deleting Entra app registration: %s", parsed.entra_app_id)
            run_az(args=["ad", "app", "delete", "--id", parsed.entra_app_id])
            logger.info("Entra app deleted")

        logger.info("")
        logger.info("=" * 60)
        logger.info("TEARDOWN COMPLETE")
        logger.info("=" * 60)
        logger.info("Resource group '%s' was deleted.", rg_name)
        logger.info("This includes: Container App, SQL server, Key Vault, MI, networking, logs.")
        logger.info("Static egress IP '%s' was released.", egress_ip or "<not allocated>")
        logger.info("")
        logger.info("Note: Key Vault uses purge protection. The vault name '%s'", f"copyrit-{instance}-kv")
        logger.info("will be reserved for ~90 days after deletion.")
        logger.info("=" * 60)

        return 0

    except RuntimeError as error:
        logger.error("%s", error)
        return 1
    except subprocess.CalledProcessError as e:
        logger.error("Command failed (exit code %d): %s", e.returncode, " ".join(e.cmd))
        if e.stderr:
            logger.error("stderr: %s", e.stderr.strip())
        return 1


if __name__ == "__main__":
    sys.exit(main())
