# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CoPyRIT GUI — Tear down an isolated instance.

Removes all Azure resources for an instance deployed by deploy_instance.py.
Entra resources (app registration, service principal) must be deleted separately
since they live outside the resource group.

Usage:
    python infra/teardown_instance.py --instance-name partners-demo \\
        --subscription "AI Red Team Tooling"

    # Include Entra cleanup:
    python infra/teardown_instance.py --instance-name partners-demo \\
        --subscription "AI Red Team Tooling" --delete-entra-app

"""

import argparse
import json
import logging
import platform
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

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


def run_az_json(*, args: list[str]) -> dict | list | str | None:
    """
    Run an Azure CLI command and parse JSON output.

    Args:
        args (list[str]): The az CLI arguments (without the leading 'az').

    Returns:
        dict | list | str | None: The parsed JSON output, or None on failure.
    """
    result = run_az(args=args + ["-o", "json"], check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


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
        "--delete-entra-app",
        action="store_true",
        help="Also delete the Entra app registration and service principal",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
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

    instance = parsed.instance_name
    rg_name = f"copyrit-{instance}"
    entra_app_name = f"CoPyRIT GUI ({instance})"

    logger.info("Instance:       %s", instance)
    logger.info("Resource group: %s", rg_name)
    if parsed.delete_entra_app:
        logger.info("Entra app:      %s (will be deleted)", entra_app_name)

    if not parsed.yes:
        confirm = input(f"\nDelete resource group '{rg_name}' and all its resources? [y/N] ")
        if confirm.lower() != "y":
            logger.info("Aborted.")
            return 0

    try:
        # Set subscription
        logger.info("Setting subscription to: %s", parsed.subscription)
        run_az(args=["account", "set", "--subscription", parsed.subscription])

        # Delete resource group (and all Azure resources in it)
        logger.info("Deleting resource group: %s (this may take several minutes)", rg_name)
        run_az(args=["group", "delete", "--name", rg_name, "--yes", "--no-wait"])
        logger.info("Resource group deletion initiated (running in background)")

        # Delete Entra app registration if requested
        if parsed.delete_entra_app:
            logger.info("Looking up Entra app: %s", entra_app_name)
            app_info = run_az_json(
                args=[
                    "ad",
                    "app",
                    "list",
                    "--display-name",
                    entra_app_name,
                    "--query",
                    "[0].appId",
                ]
            )

            if app_info:
                logger.info("Deleting Entra app registration: %s", app_info)
                run_az(args=["ad", "app", "delete", "--id", app_info])
                logger.info("Entra app deleted")
            else:
                logger.warning("Entra app '%s' not found — skipping", entra_app_name)

        logger.info("")
        logger.info("=" * 60)
        logger.info("TEARDOWN COMPLETE")
        logger.info("=" * 60)
        logger.info("Resource group '%s' is being deleted.", rg_name)
        logger.info("This includes: Container App, SQL server, Key Vault, MI, networking, logs.")
        logger.info("")
        logger.info("Note: Key Vault uses purge protection. The vault name '%s'", f"copyrit-{instance}-kv")
        logger.info("will be reserved for ~90 days after deletion.")
        logger.info("=" * 60)

        return 0

    except subprocess.CalledProcessError as e:
        logger.error("Command failed (exit code %d): %s", e.returncode, " ".join(e.cmd))
        if e.stderr:
            logger.error("stderr: %s", e.stderr.strip())
        return 1


if __name__ == "__main__":
    sys.exit(main())
