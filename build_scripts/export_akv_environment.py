# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Export resolved Azure Key Vault bootstrap documents to ``~/.pyrit/.env_akv``."""

import argparse
import contextlib
import logging
import os
import pathlib
import tempfile
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING, Any

import dotenv
from dotenv.parser import parse_stream
from dotenv.variables import parse_variables

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential
    from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_FILE = pathlib.Path.home() / ".pyrit" / ".env_akv"
_REFERENCE_PREFIXES = frozenset({"akv", "kv", "azure_key_vault", "env_akv_ref"})
_VAULT_DNS_SUFFIXES = frozenset({"vault.azure.net", "vault.azure.cn", "vault.usgovcloudapi.net"})


@dataclass(frozen=True)
class _Document:
    content: str
    vault_url: str


@dataclass(frozen=True)
class _Candidate:
    document_index: int
    binding_index: int
    name: str
    value: str
    vault_url: str


def _parse_secret_url(url: str) -> tuple[str, str, str | None]:
    """Return vault URL, secret name, and optional version from a full AKV URL."""
    error_message = f"Invalid Azure Key Vault secret URL: {url}"
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError(error_message) from error
    hostname = parsed.hostname
    vault_name, separator, suffix = hostname.partition(".") if hostname else ("", "", "")
    valid_vault = 1 <= len(vault_name) <= 63 and all(
        char.isascii() and (char.isalnum() or char == "-") for char in vault_name
    )
    parts = parsed.path.split("/")
    valid_path = len(parts) in {3, 4} and parts[:2] == ["", "secrets"] and all(parts[2:])
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or separator != "."
        or suffix not in _VAULT_DNS_SUFFIXES
        or not valid_vault
        or not valid_path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(error_message)
    secret_name = parts[2]
    secret_version = parts[3] if len(parts) == 4 else None
    identifiers = [secret_name] + ([secret_version] if secret_version else [])
    if any(not (1 <= len(item) <= 127 and all(char.isalnum() or char == "-" for char in item)) for item in identifiers):
        raise ValueError(error_message)
    return f"https://{hostname}", secret_name, secret_version


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


def _validate_document(*, document: str, strict: bool, silent: bool) -> str:
    bindings = list(parse_stream(StringIO(document)))
    malformed = [str(binding.original.line) for binding in bindings if binding.error]
    valueless = [binding.key for binding in bindings if binding.key is not None and binding.value is None]
    issues: list[str] = []
    if malformed:
        issues.append("malformed entries at lines: " + ", ".join(malformed))
    if valueless:
        issues.append("variables without values: " + ", ".join(valueless))
    if not issues:
        return document
    details = "; ".join(issues)
    if strict:
        raise ValueError("AKV environment document contains " + details)
    message = "AKV environment document contains invalid entries that will be skipped: " + details
    if not silent:
        print(f"WARNING: {message}")
    logger.warning(message)
    return "".join(
        binding.original.string
        for binding in bindings
        if not binding.error and not (binding.key is not None and binding.value is None)
    )


def _fetch_documents(
    *,
    secret_urls: Sequence[str],
    credential: "TokenCredential",
    clients: dict[str, "SecretClient"],
    strict: bool,
    silent: bool,
) -> list[_Document]:
    documents: list[_Document] = []
    for url in secret_urls:
        vault_url, name, version = _parse_secret_url(url)
        secret = _client_for(vault_url=vault_url, credential=credential, clients=clients).get_secret(
            name, version=version
        )
        if not secret.value:
            raise ValueError(f"AKV environment secret has no value: {url}")
        content = _validate_document(document=secret.value, strict=strict, silent=silent)
        if not dotenv.dotenv_values(stream=StringIO(content), interpolate=False):
            raise ValueError(f"AKV environment secret contains no assignments: {url}")
        documents.append(_Document(content=content, vault_url=vault_url))
    return documents


def _resolve_interpolation(*, value: str, environment: Mapping[str, str | None]) -> str:
    return "".join(atom.resolve(environment) for atom in parse_variables(value))


def _build_candidates(documents: Sequence[_Document]) -> tuple[list[list[Any]], dict[str, list[_Candidate]]]:
    effective: dict[str, str | None] = {}
    chains: dict[str, list[_Candidate]] = {}
    all_bindings: list[list[Any]] = []
    for document_index, document in enumerate(documents):
        bindings = list(parse_stream(StringIO(document.content)))
        all_bindings.append(bindings)
        current: dict[str, str | None] = {}
        final_indexes: dict[str, int] = {}
        for binding_index, binding in enumerate(bindings):
            if binding.key is None or binding.value is None:
                continue
            environment = dict(current)
            environment.update(effective)
            current[binding.key] = _resolve_interpolation(value=binding.value, environment=environment)
            final_indexes[binding.key] = binding_index
        for name, value in current.items():
            if value is None:
                continue
            chains.setdefault(name, []).append(
                _Candidate(document_index, final_indexes[name], name, value, document.vault_url)
            )
            effective.setdefault(name, value)
    return all_bindings, chains


def _reference_target(value: str) -> str | None:
    prefix, separator, target = value.partition(":")
    return target.strip() if separator and prefix in _REFERENCE_PREFIXES else None


def _serialize(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("${", "${:-$}{")
    return f"'{escaped}'"


def _render(
    *,
    documents: Sequence[_Document],
    credential: "TokenCredential",
    clients: dict[str, "SecretClient"],
    strict: bool,
    silent: bool,
) -> str:
    all_bindings, chains = _build_candidates(documents)
    selected: dict[str, _Candidate] = {}
    resolved: dict[tuple[int, int], str] = {}
    for name, candidates in chains.items():
        for candidate in candidates:
            target = _reference_target(candidate.value)
            if target is None:
                selected[name] = candidate
                break
            try:
                vault_url, secret_name, version = _parse_secret_url(target)
                if vault_url.casefold() != candidate.vault_url.casefold():
                    raise ValueError(f"Cross-vault AKV reference for '{name}' is not supported")
            except ValueError as error:
                if strict:
                    raise
                message = f"Invalid AKV reference for '{name}' will be skipped: {error}"
                if not silent:
                    print(f"WARNING: {message}")
                logger.warning(message)
                continue
            secret = _client_for(vault_url=vault_url, credential=credential, clients=clients).get_secret(
                secret_name, version=version
            )
            if secret.value is None:
                raise ValueError(f"AKV secret '{secret_name}' referenced by '{name}' has no value")
            selected[name] = candidate
            resolved[(candidate.document_index, candidate.binding_index)] = secret.value
            break

    output: list[str] = []
    for document_index, bindings in enumerate(all_bindings):
        for binding_index, binding in enumerate(bindings):
            name = binding.key
            if name is None:
                output.append(binding.original.string)
                continue
            winner = selected.get(name)
            if winner is None or winner.document_index != document_index:
                continue
            value = resolved.get((document_index, binding_index))
            if value is None:
                output.append(binding.original.string)
                continue
            original = binding.original.string
            export = "export " if original.lstrip().startswith("export ") else ""
            newline = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
            output.append(f"{export}{name}={_serialize(value)}{newline}")
    return "".join(output).rstrip("\r\n") + "\n"


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
        documents = _fetch_documents(
            secret_urls=secret_urls,
            credential=active_credential,
            clients=clients,
            strict=strict,
            silent=silent,
        )
        document = _render(
            documents=documents,
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
    parser.add_argument("--secret-url", dest="secret_urls", action="append", required=True)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--non-strict", action="store_true")
    parser.add_argument("--silent", action="store_true")
    args = parser.parse_args()
    try:
        export_akv_environment(
            secret_urls=args.secret_urls,
            output_file=args.output,
            strict=not args.non_strict,
            silent=args.silent,
        )
    except Exception as error:
        parser.exit(1, f"Export failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
