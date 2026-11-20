# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Validate the app deployment's Entra and Blob inputs and locate its configuration seed."""

from __future__ import annotations

import argparse
import re
import uuid
from urllib.parse import urlparse

_BLOB_SUFFIXES = (
    ".blob.core.windows.net",
    ".blob.core.chinacloudapi.cn",
    ".blob.core.usgovcloudapi.net",
    ".blob.core.cloudapi.de",
)
_BRANCH = re.compile(r"refs/heads/((?:[A-Za-z0-9][A-Za-z0-9._-]*/)*[A-Za-z0-9][A-Za-z0-9._-]*)")


def _blob_location(uri: str, *, name: str) -> tuple[str, str, str]:
    """Return the account, container, and path of a credential-free Azure Blob Storage URI."""
    parsed = urlparse(uri)
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not any(hostname.endswith(suffix) and hostname != suffix[1:] for suffix in _BLOB_SUFFIXES)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be a credential-free Azure Blob Storage URI")
    container, _, path = parsed.path.strip("/").partition("/")
    return hostname.split(".")[0], container, path


def _is_plain_folder(folder: str) -> bool:
    return all(re.fullmatch(r"[A-Za-z0-9._-]+", part) and part not in (".", "..") for part in folder.split("/"))


def validate_identities(*, tenant_id: str, client_id: str, allowed_group_ids: str, admin_group_id: str) -> None:
    """Require the Entra tenant, client, and group object IDs to be GUIDs, with at least one allowed group."""
    groups = [value.strip() for value in allowed_group_ids.split(",") if value.strip()]
    try:
        for value in (tenant_id, client_id, admin_group_id, *groups):
            uuid.UUID(value)
    except ValueError:
        raise ValueError("Invalid Entra tenant, client, or group ID") from None
    if not groups:
        raise ValueError("Invalid Entra tenant, client, or group ID")


def seed_location(*, seed_uri: str, config_uri: str, source_branch: str) -> tuple[str, ...]:
    """
    Return where each deployment copies the deployed branch's configuration seed from and to.

    The seed holds one starting configuration per deployed branch, such as <seed>/main/ or <seed>/releases/v1.2.3/.
    Each deployment replaces the whole folder that holds the config file with it, so that folder and the seed must
    be separate, plainly named folders.

    Returns:
        tuple[str, ...]: Seed account, container, and folder, then config account, container, folder, and file name.
    """
    if not config_uri:
        raise ValueError("pyritConfigSeedUri requires pyritConfigFileUri")
    branch = _BRANCH.fullmatch(source_branch)
    if branch is None:
        source = source_branch or "this source"
        raise ValueError(f"pyritConfigSeedUri selects the configuration by branch; {source} is not a branch")
    seed_account, seed_container, seed_root = _blob_location(seed_uri, name="pyritConfigSeedUri")
    config_account, config_container, config_path = _blob_location(config_uri, name="pyritConfigFileUri")
    seed_folder = f"{seed_root}/{branch[1]}".lstrip("/")
    config_folder, _, config_name = config_path.rpartition("/")
    nested = f"{seed_folder}/".startswith(f"{config_folder}/") or f"{config_folder}/".startswith(f"{seed_folder}/")
    plain = _is_plain_folder(f"{seed_container}/{seed_folder}") and _is_plain_folder(
        f"{config_container}/{config_folder}"
    )
    if not plain or ((seed_account, seed_container) == (config_account, config_container) and nested):
        raise ValueError(
            "pyritConfigSeedUri and pyritConfigFileUri need separate folders named with letters, digits, "
            "'.', '_' or '-', and the config file must be in its own folder"
        )
    return seed_account, seed_container, seed_folder, config_account, config_container, config_folder, config_name


def main(argv: list[str] | None = None) -> int:
    """
    Print the seed location when a seed is configured, or the reason the inputs are invalid.

    Returns:
        int: 0 when the inputs are valid, otherwise 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant-id", "client-id", "allowed-group-ids", "admin-group-id"):
        parser.add_argument(f"--{name}", required=True)
    for name in ("config-uri", "seed-uri", "source-branch"):
        parser.add_argument(f"--{name}", default="")
    args = parser.parse_args(argv)
    try:
        validate_identities(
            tenant_id=args.tenant_id,
            client_id=args.client_id,
            allowed_group_ids=args.allowed_group_ids,
            admin_group_id=args.admin_group_id,
        )
        if args.config_uri and not _blob_location(args.config_uri, name="pyritConfigFileUri")[2]:
            raise ValueError("pyritConfigFileUri must name a file inside a Blob container")
        if args.seed_uri:
            location = seed_location(
                seed_uri=args.seed_uri, config_uri=args.config_uri, source_branch=args.source_branch
            )
            print("\t".join(location))
    except ValueError as error:
        print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
