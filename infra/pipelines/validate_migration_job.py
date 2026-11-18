# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Validate ACA job metadata without interpreting logs or inferring success from missing state."""

import argparse
import json
import sys

_MODE_ARGS = {"check": ["--check-only"], "upgrade-required": ["--check-upgrade-required"], "migrate": []}


def job_presence(*, jobs: object, job_id: str, allow_missing: bool) -> str:
    """
    Establish job presence from a successful resource-group listing.

    Returns:
        str: Whether the expected job is present or explicitly absent.

    Raises:
        ValueError: If listing or provisioning state is ambiguous.
    """
    if not isinstance(jobs, list):
        raise ValueError("Malformed migration job listing.")
    expected_parent = job_id.rsplit("/", 1)[0].casefold()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise ValueError("Malformed migration job listing.")
        parent, _, name = job["id"].rpartition("/")
        if parent.casefold() != expected_parent or not name:
            raise ValueError("Unrecognized job ID in migration job listing.")
    matches = [job for job in jobs if job["id"].casefold() == job_id.casefold()]
    if not matches and allow_missing:
        return "missing"
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("properties"), dict)
        or matches[0]["properties"].get("provisioningState") != "Succeeded"
    ):
        raise ValueError("Migration job is missing, ambiguous, or not successfully provisioned.")
    return "present"


def require_idle(executions: object) -> None:
    """
    Reject active, unknown, and malformed executions, including canceled-pipeline orphans.

    Raises:
        ValueError: If idle state cannot be established.
    """
    if not isinstance(executions, list):
        raise ValueError("Malformed migration execution listing.")
    for execution in executions:
        if (
            not isinstance(execution, dict)
            or not isinstance(execution.get("name"), str)
            or not execution["name"]
            or not isinstance(execution.get("properties"), dict)
            or execution["properties"].get("status") not in ("Succeeded", "Failed", "Stopped")
        ):
            raise ValueError("Migration job has an active or unreadable execution; do not overwrite or start it.")


def execution_result(
    *, execution: object, execution_id: str, image: str, mode: str, expected_server: str, expected_database: str
) -> str:
    """
    Verify the selected command/SQL target and classify the documented execution status.

    Returns:
        str: Pending, succeeded, or failed state, never an inferred migration decision.

    Raises:
        ValueError: If execution identity, container result, or status cannot prove a safe result.
    """
    if (
        not isinstance(execution, dict)
        or not isinstance(execution.get("id"), str)
        or execution["id"].casefold() != execution_id.casefold()
    ):
        raise ValueError("Migration execution identity is missing or does not match.")
    properties = execution.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("Migration execution properties are missing.")
    status = properties.get("status")
    if isinstance(status, str) and status in {"Running", "Processing"}:
        return "pending"
    if not isinstance(status, str) or status not in {"Succeeded", "Failed"}:
        raise ValueError("Migration execution ended with an unknown or unsafe status.")
    validate_template(
        template=properties.get("template"),
        image=image,
        mode=mode,
        expected_server=expected_server,
        expected_database=expected_database,
    )
    return "succeeded" if status == "Succeeded" else "failed"


def validate_template(*, template: object, image: str, mode: str, expected_server: str, expected_database: str) -> None:
    """Require the single migration entry point, its exact arguments, and the intended SQL target."""
    if not isinstance(template, dict):
        raise ValueError("Migration execution template is missing or malformed.")
    containers = template.get("containers")
    if (
        not isinstance(containers, list)
        or len(containers) != 1
        or not isinstance(containers[0], dict)
        or containers[0].get("name") != "pyrit-migrate"
        or containers[0].get("image") != image
        or template.get("initContainers") not in (None, [])
        or containers[0].get("command") not in (None, [], ["/app/start.sh"])
    ):
        raise ValueError("Migration execution template does not match the selected image and command.")
    args = containers[0].get("args")
    if ([] if args is None else args) != _MODE_ARGS[mode]:
        raise ValueError("Migration execution arguments do not match the selected operation.")
    environment = containers[0].get("env", [])
    if not isinstance(environment, list) or any(not isinstance(entry, dict) for entry in environment):
        raise ValueError("Migration execution environment is missing or malformed.")
    for name, value in (
        ("PYRIT_MODE", "migrate"),
        ("AZURE_SQL_SERVER", expected_server),
        ("AZURE_SQL_DATABASE", expected_database),
    ):
        matches = [entry for entry in environment if entry.get("name") == name]
        if len(matches) != 1 or matches[0].get("value") != value or matches[0].get("secretRef"):
            raise ValueError("Migration execution mode or SQL target is missing, ambiguous, or does not match.")


def main() -> None:
    """Validate metadata read by the deployment's authenticated Azure CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    presence = commands.add_parser("job-presence")
    presence.add_argument("--job-id", required=True)
    presence.add_argument("--allow-missing", action="store_true")
    commands.add_parser("idle-executions")
    result = commands.add_parser("execution-result")
    result.add_argument("--execution-id", required=True)
    template = commands.add_parser("prepare-template")
    for operation in (template, result):
        operation.add_argument("--image", required=True)
        operation.add_argument("--mode", choices=_MODE_ARGS, required=True)
        operation.add_argument("--expected-server", required=True)
        operation.add_argument("--expected-database", required=True)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    if args.command == "job-presence":
        sys.stdout.write(job_presence(jobs=payload, job_id=args.job_id, allow_missing=args.allow_missing))
    elif args.command == "idle-executions":
        require_idle(payload)
    elif args.command == "prepare-template":
        validate_template(
            template=payload,
            image=args.image,
            mode="migrate",
            expected_server=args.expected_server,
            expected_database=args.expected_database,
        )
        if args.mode != "migrate":
            payload["containers"][0]["args"] = _MODE_ARGS[args.mode]
        json.dump(payload, sys.stdout)
    else:
        sys.stdout.write(
            execution_result(
                execution=payload,
                execution_id=args.execution_id,
                image=args.image,
                mode=args.mode,
                expected_server=args.expected_server,
                expected_database=args.expected_database,
            )
        )


if __name__ == "__main__":
    main()
