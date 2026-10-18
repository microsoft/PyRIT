# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Export a resolved Azure Key Vault bootstrap document to ``~/.pyrit/.env_akv``."""

import argparse
import contextlib
import logging
import os
import pathlib
import tempfile
from collections.abc import Mapping, Sequence
from io import StringIO
from typing import TYPE_CHECKING

import dotenv
from dotenv.parser import parse_stream
from dotenv.variables import parse_variables

from pyrit.setup.environment_loading import (
    _parse_akv_reference,
    _parse_akv_secret_url,
    _validate_dotenv_document,
)

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential
    from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_FILE = pathlib.Path.home() / ".pyrit" / ".env_akv"


def _create_client(*, vault_url: str, credential: "TokenCredential") -> "SecretClient":
    """Create a Key Vault client with explicit retry settings."""
    from azure.core.pipeline.policies import RetryPolicy
    from azure.keyvault.secrets import SecretClient

    return SecretClient(
        vault_url=vault_url,
        credential=credential,
        retry_policy=RetryPolicy(
            retry_total=3,
            retry_connect=3,
            retry_read=3,
            retry_status=3,
            retry_backoff_factor=0.8,
        ),
    )


def _client_for(*, vault_url: str, credential: "TokenCredential", clients: dict[str, "SecretClient"]) -> "SecretClient":
    client = clients.get(vault_url)
    if client is None:
        client = _create_client(vault_url=vault_url, credential=credential)
        clients[vault_url] = client
    return client


def _fetch_document(
    *,
    secret_url: str,
    credential: "TokenCredential",
    clients: dict[str, "SecretClient"],
    strict: bool,
    silent: bool,
) -> tuple[str, str]:
    vault_url, name, version = _parse_akv_secret_url(secret_url)
    secret = _client_for(vault_url=vault_url, credential=credential, clients=clients).get_secret(name, version=version)
    if not secret.value:
        raise ValueError(f"AKV environment secret has no value: {secret_url}")
    content = _validate_dotenv_document(secret.value, strict=strict, silent=silent)
    if not dotenv.dotenv_values(stream=StringIO(content), interpolate=False):
        raise ValueError(f"AKV environment secret contains no assignments: {secret_url}")
    return content, vault_url


def _resolve_interpolation(*, value: str, environment: Mapping[str, str | None]) -> str:
    return "".join(atom.resolve(environment) for atom in parse_variables(value))


def _build_candidates(document: tuple[str, str]) -> dict[str, tuple[str, str]]:
    content, vault_url = document
    values: dict[str, str] = {}
    for binding in parse_stream(StringIO(content)):
        if binding.key is not None and binding.value is not None:
            values[binding.key] = _resolve_interpolation(value=binding.value, environment=values)
    return {name: (value, vault_url) for name, value in values.items()}


def _serialize(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("${", "${:-$}{")
    return f"'{escaped}'"


def _render(
    *,
    document: tuple[str, str],
    credential: "TokenCredential",
    clients: dict[str, "SecretClient"],
    strict: bool,
    silent: bool,
) -> str:
    resolved: dict[str, str] = {}
    for name, (value, source_vault_url) in _build_candidates(document).items():
        try:
            reference = _parse_akv_reference(
                value=value,
                variable_name=name,
                expected_vault_url=source_vault_url,
            )
        except ValueError as error:
            if strict:
                raise
            message = f"Invalid AKV reference for '{name}' will be skipped: {error}"
            if not silent:
                print(f"WARNING: {message}")
            logger.warning(message)
            continue
        if reference is None:
            resolved[name] = value
            continue
        vault_url, secret_name, version = reference
        secret = _client_for(vault_url=vault_url, credential=credential, clients=clients).get_secret(
            secret_name, version=version
        )
        if secret.value is None:
            raise ValueError(f"AKV secret '{secret_name}' referenced by '{name}' has no value")
        resolved[name] = secret.value

    return "".join(f"{name}={_serialize(value)}\n" for name, value in resolved.items())


def _ensure_output_available(output_file: pathlib.Path) -> pathlib.Path:
    output_file = output_file.expanduser()
    if output_file.is_symlink():
        raise ValueError(f"Output path is a symbolic link: {output_file}")
    if output_file.exists():
        raise ValueError(f"Output already exists: {output_file}. Rename or remove it before exporting")
    return output_file


def _write_output(*, output_file: pathlib.Path, document: str) -> pathlib.Path:
    output_file = _ensure_output_available(output_file)
    output_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: pathlib.Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f"{output_file.name}.", suffix=".tmp", dir=output_file.parent)
        temporary = pathlib.Path(name)
        file_chmod = getattr(os, "fchmod", None)
        if file_chmod is not None:
            file_chmod(descriptor, 0o600)
        else:
            os.chmod(temporary, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = None
        with stream:
            stream.write(document)
        try:
            os.link(temporary, output_file)
        except FileExistsError as error:
            raise ValueError(f"Output already exists: {output_file}. Rename or remove it before exporting") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
    return output_file


def export_akv_environment(
    *,
    secret_urls: Sequence[str],
    output_file: pathlib.Path = DEFAULT_OUTPUT_FILE,
    strict: bool = True,
    silent: bool = False,
    credential: "TokenCredential | None" = None,
) -> pathlib.Path:
    """Fetch, resolve, and securely export AKV-only configuration.

    A caller-provided credential remains caller-owned and is not closed.
    """
    if not secret_urls:
        raise ValueError("At least one secret URL is required")
    if len(secret_urls) > 1:
        raise ValueError("Only one Azure Key Vault bootstrap secret URL is supported")
    output_file = _ensure_output_available(output_file)
    from azure.identity import DefaultAzureCredential

    owned_credential = None
    if credential is None:
        owned_credential = DefaultAzureCredential()
        active_credential = owned_credential
    else:
        active_credential = credential
    clients: dict[str, SecretClient] = {}
    try:
        document = _fetch_document(
            secret_url=secret_urls[0],
            credential=active_credential,
            clients=clients,
            strict=strict,
            silent=silent,
        )
        document = _render(
            document=document,
            credential=active_credential,
            clients=clients,
            strict=strict,
            silent=silent,
        )
        output = _write_output(output_file=output_file, document=document)
    finally:
        for client in clients.values():
            client.close()
        if owned_credential is not None:
            owned_credential.close()
    if not silent:
        print(f"Exported resolved AKV environment to {output}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-url", required=True)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--non-strict", action="store_true")
    parser.add_argument("--silent", action="store_true")
    args = parser.parse_args()
    try:
        export_akv_environment(
            secret_urls=[args.secret_url],
            output_file=args.output,
            strict=not args.non_strict,
            silent=args.silent,
        )
    except Exception as error:
        parser.exit(1, f"Export failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
