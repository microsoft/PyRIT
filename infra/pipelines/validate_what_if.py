# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Reject ARM what-if results that can replace protected deployment topology."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Literal, cast


class WhatIfFormatError(ValueError):
    """Raised when Azure returns a what-if payload that cannot be validated safely."""


_CORE_RESOURCE_ID_PATTERN = re.compile(
    r"/providers/microsoft\."
    r"(?:network/(?:publicipaddresses/[^/]+|natgateways/[^/]+|virtualnetworks/[^/]+(?:/subnets/[^/]+)?)"
    r"|app/(?:managedenvironments|containerapps)/[^/]+"
    r"|operationalinsights/workspaces/[^/]+)$",
    re.IGNORECASE,
)


def _expect_object(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise WhatIfFormatError(f"{context} must be a JSON object")
    return cast("dict[str, object]", value)


def _expect_array(value: object, *, context: str) -> list[object]:
    if not isinstance(value, list):
        raise WhatIfFormatError(f"{context} must be a JSON array")
    return cast("list[object]", value)


def _expect_string(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise WhatIfFormatError(f"{context} must be a non-empty string")
    return value


def validate_what_if(
    payload: object,
    *,
    deployment_resource_group_id: str,
    expected_pip_id: str,
    expected_nat_id: str,
    expected_vnet_id: str,
    expected_subnet_id: str,
    expected_environment_id: str,
    deployment_mode: Literal["app", "infra"] | None = None,
    expected_app_id: str | None = None,
) -> list[str]:
    """Return destructive, cross-scope, protected-resource, and deployment-phase violations."""
    document = _expect_object(payload, context="what-if result")
    changes = _expect_array(document.get("changes"), context="what-if changes")
    resource_group_prefix = f"{deployment_resource_group_id.rstrip('/').casefold()}/"
    if deployment_mode not in {None, "app", "infra"}:
        raise WhatIfFormatError("deployment mode must be app or infra")
    app_id = expected_app_id.rstrip("/").casefold() if expected_app_id else ""
    if deployment_mode and not re.fullmatch(
        re.escape(resource_group_prefix) + r"providers/microsoft\.app/containerapps/[^/]+", app_id
    ):
        raise WhatIfFormatError("deployment mode requires an expected app ID in the deployment resource group")
    protected_paths = {
        expected_pip_id.rstrip("/").casefold(): {"sku.tier"},
        expected_nat_id.rstrip("/").casefold(): {"properties.scope", "sku.tier"},
        expected_vnet_id.rstrip("/").casefold(): set(),
        expected_subnet_id.rstrip("/").casefold(): set(),
        expected_environment_id.rstrip("/").casefold(): {
            "properties.appLogsConfiguration.logAnalyticsConfiguration.customerId",
            "properties.publicNetworkAccess",
        },
    }
    violations: list[str] = []

    for index, value in enumerate(changes):
        change = _expect_object(value, context=f"what-if change {index}")
        change_type = _expect_string(change.get("changeType"), context=f"what-if change {index} type")
        resource_id = _expect_string(change.get("resourceId"), context=f"what-if change {index} resource ID")
        normalized_resource_id = resource_id.casefold().rstrip("/")

        if change_type == "Delete":
            violations.append(f"delete: {resource_id}")

        if change_type != "Ignore" and not normalized_resource_id.startswith(resource_group_prefix):
            violations.append(f"cross-resource-group write: {resource_id}")

        if change_type == "Create" and _CORE_RESOURCE_ID_PATTERN.search(normalized_resource_id):
            violations.append(f"core resource create: {resource_id}")

        if change_type not in {"NoChange", "Ignore"}:
            if deployment_mode == "app" and (normalized_resource_id != app_id or change_type != "Modify"):
                violations.append(f"app-only write outside the existing app: {resource_id}")
            elif deployment_mode == "infra" and (
                normalized_resource_id == app_id or normalized_resource_id.startswith(f"{app_id}/")
            ):
                violations.append(f"infrastructure-only write to the app: {resource_id}")

        if normalized_resource_id not in protected_paths or change_type in {"NoChange", "Ignore"}:
            continue

        delta_value = change.get("delta")
        if delta_value is None:
            violations.append(f"opaque protected-resource change: {resource_id}")
            continue

        deltas = _expect_array(delta_value, context=f"protected change delta for {resource_id}")
        if not deltas:
            violations.append(f"opaque protected-resource change: {resource_id}")
            continue

        allowed_paths = protected_paths[normalized_resource_id]
        for delta_index, delta_value in enumerate(deltas):
            delta = _expect_object(delta_value, context=f"delta {delta_index} for {resource_id}")
            path = _expect_string(delta.get("path"), context=f"delta {delta_index} path for {resource_id}")
            if path not in allowed_paths:
                violations.append(f"protected-resource delta {path}: {resource_id}")

    return violations


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--what-if-file", required=True)
    parser.add_argument("--deployment-resource-group-id", required=True)
    parser.add_argument("--expected-pip-id", required=True)
    parser.add_argument("--expected-nat-id", required=True)
    parser.add_argument("--expected-vnet-id", required=True)
    parser.add_argument("--expected-subnet-id", required=True)
    parser.add_argument("--expected-environment-id", required=True)
    parser.add_argument("--deployment-mode", choices=("app", "infra"))
    parser.add_argument("--expected-app-id")
    return parser.parse_args()


def main() -> int:
    """Validate a FullResourcePayloads what-if file for the internal update path."""
    parsed = _parse_args()
    what_if_file = Path(cast("str", parsed.what_if_file))

    try:
        payload: object = json.loads(what_if_file.read_text(encoding="utf-8"))
        violations = validate_what_if(
            payload,
            deployment_resource_group_id=cast("str", parsed.deployment_resource_group_id),
            expected_pip_id=cast("str", parsed.expected_pip_id),
            expected_nat_id=cast("str", parsed.expected_nat_id),
            expected_vnet_id=cast("str", parsed.expected_vnet_id),
            expected_subnet_id=cast("str", parsed.expected_subnet_id),
            expected_environment_id=cast("str", parsed.expected_environment_id),
            deployment_mode=cast("Literal['app', 'infra'] | None", parsed.deployment_mode),
            expected_app_id=cast("str | None", parsed.expected_app_id),
        )
    except (OSError, json.JSONDecodeError, WhatIfFormatError) as error:
        print(f"What-if validation failed closed: {error}", file=sys.stderr)
        return 2

    for violation in violations:
        print(f"What-if rejected: {violation}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
