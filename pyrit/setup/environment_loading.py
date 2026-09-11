# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Load dotenv files and Azure Key Vault-backed environment documents."""

import asyncio
import contextlib
import logging
import os
import pathlib
import urllib.parse
from collections.abc import Mapping, Sequence
from io import StringIO
from typing import TYPE_CHECKING

import dotenv
from dotenv.main import DotEnv
from dotenv.parser import parse_stream

from pyrit.common import path
from pyrit.exceptions import KeyVaultInitializationException

if TYPE_CHECKING:
    from azure.core.credentials_async import AsyncTokenCredential
    from azure.keyvault.secrets.aio import SecretClient

logger = logging.getLogger(__name__)

__all__ = [
    "load_environment_async",
    "load_environment_files",
    "validate_env_akv_strict",
]

_AKV_REFERENCE_PREFIXES = frozenset({"akv", "kv", "azure_key_vault", "env_akv_ref"})
_AKV_VAULT_DNS_SUFFIXES = frozenset({"vault.azure.net", "vault.azure.cn", "vault.usgovcloudapi.net"})
_AKV_RETRY_TOTAL = 3
_AKV_RETRY_BACKOFF_FACTOR = 0.8
_DOTENV_DISABLED_VALUES = frozenset({"1", "true", "t", "yes", "y"})


def validate_env_akv_strict(*, env_akv_strict: object) -> None:
    """
    Require a real boolean for Key Vault strict-mode behavior.

    Raises:
        TypeError: If env_akv_strict is not a bool.
    """
    if not isinstance(env_akv_strict, bool):
        raise TypeError(f"env_akv_strict must be a bool, got {type(env_akv_strict).__name__}.")


def load_environment_files(
    env_files: Sequence[pathlib.Path] | None,
    *,
    silent: bool = False,
    include_default_base: bool = True,
    _ordinary_candidates: dict[str, list[tuple[str, str | None]]] | None = None,
    _override_candidates: dict[str, list[tuple[str, str | None]]] | None = None,
) -> bool:
    """
    Load environment files in the order they are provided.

    Files fill values missing from the process environment. A file named
    ``.env.local`` is the only local source that overrides existing values.

    Args:
        env_files: Optional sequence of environment file paths. If None, loads default
            .env and .env.local from PyRIT home directory (only if they exist).
        silent: If True, suppresses print statements about environment file loading.
            Defaults to False.
        include_default_base: If False and env_files is None, skips the default
            .env file while still loading .env.local. Defaults to True.
        _ordinary_candidates: Internal output mapping for non-overriding assignments.
        _override_candidates: Internal output mapping for ``.env.local`` assignments.

    Returns:
        True if at least one environment file was loaded, otherwise False.

    Raises:
        ValueError: If any provided env_files do not exist.
    """
    if env_files is None:
        selected_files = []
        base_file = path.CONFIGURATION_DIRECTORY_PATH / ".env"
        local_file = path.CONFIGURATION_DIRECTORY_PATH / ".env.local"
        if include_default_base and base_file.exists():
            _warn_about_dotenv_file(env_file=base_file, ignored_for_akv=False, silent=silent)
            selected_files.append(base_file)
        if local_file.exists():
            selected_files.append(local_file)
        if not silent:
            message = (
                f"Found default environment files: {[str(file) for file in selected_files]}"
                if selected_files
                else "No default environment files found. Using system environment variables only."
            )
            _print_msg(message, quiet=False, log=True)
    else:
        selected_files = list(env_files)
        if not silent:
            _print_msg(
                f"Loading custom environment files: {[str(file) for file in selected_files]}",
                quiet=False,
                log=True,
            )
        for env_file in selected_files:
            if not env_file.exists():
                raise ValueError(f"Environment file not found: {env_file}")

    for env_file in selected_files:
        _load_dotenv_source(
            dotenv_path=env_file,
            override=env_file.name == ".env.local",
            ordinary_candidates=_ordinary_candidates,
            override_candidates=_override_candidates,
        )
        if not silent:
            _print_msg(f"Loaded environment file: {env_file}", quiet=silent, log=True)

    return bool(selected_files)


def _load_dotenv_source(
    *,
    override: bool,
    ordinary_candidates: dict[str, list[tuple[str, str | None]]] | None,
    override_candidates: dict[str, list[tuple[str, str | None]]] | None,
    dotenv_path: pathlib.Path | None = None,
    document: str | None = None,
    expected_vault_url: str | None = None,
) -> None:
    """
    Load one dotenv source and record values that participate in precedence.

    Raises:
        ValueError: If both or neither source representations are provided.
    """
    if (dotenv_path is None) == (document is None):
        raise ValueError("Exactly one dotenv_path or document must be provided.")
    if os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in _DOTENV_DISABLED_VALUES:
        return

    source = DotEnv(
        dotenv_path=dotenv_path,
        stream=StringIO(document or "") if document is not None else None,
        override=override,
        interpolate=True,
    )
    assignment_values = source.dict()
    source.set_as_environment_variables()
    if ordinary_candidates is None or override_candidates is None:
        return

    candidates = override_candidates if override else ordinary_candidates
    for variable_name, loaded_value in assignment_values.items():
        if loaded_value is None:
            continue
        candidates.setdefault(variable_name, []).append((loaded_value, expected_vault_url))


def _print_msg(message: str, quiet: bool, log: bool) -> None:
    """
    Print a standard initialization message unless quiet is True.

    Args:
        message (str): The message to print and/or log.
        quiet (bool): If True, suppresses the initialization message.
        log (bool): If True, logs the message using the logger.
    """
    if not quiet:
        print(message)
    if log:
        logger.info(message)


def _warn_about_dotenv_file(*, env_file: pathlib.Path, ignored_for_akv: bool, silent: bool) -> None:
    """Warn that Azure Key Vault is safer than an auto-discovered plaintext ``.env`` file."""
    behavior = "will be ignored because env_akv_ref is configured" if ignored_for_akv else "will be loaded"
    message = (
        f"Auto-discovered plaintext environment file {env_file} {behavior}. Azure Key Vault through env_akv_ref "
        "is more secure for shared or deployed secrets; use .env.local only for deliberate local overrides. "
        "To inspect a resolved AKV-only configuration from a source checkout, run "
        "`python -m build_scripts.export_akv_environment`; it writes ~/.pyrit/.env_akv."
    )
    if not silent:
        print(f"WARNING: {message}")
    logger.warning(message)


def _parse_akv_secret_url(secret_url: str) -> tuple[str, str, str | None]:
    """
    Parse an AKV secret URL into vault URL, secret name, and optional version.

    Args:
        secret_url (str): Full AKV secret URL in the format
            ``https://{vault}.vault.azure.net/secrets/{name}[/{version}]``.

    Returns:
        tuple[str, str, str | None]: (vault_url, secret_name, secret_version)

    Raises:
        ValueError: If the URL does not match the expected format.
    """
    error_message = (
        f"Invalid AKV secret URL: '{secret_url}'. Expected an HTTPS Azure Key Vault URL in the format "
        "https://{vault}.{vault-dns-suffix}/secrets/{name}[/{version}]."
    )
    try:
        parsed_url = urllib.parse.urlsplit(secret_url)
        port = parsed_url.port
    except (TypeError, ValueError) as error:
        raise ValueError(error_message) from error

    hostname = parsed_url.hostname
    vault_name, separator, dns_suffix = hostname.partition(".") if hostname else ("", "", "")
    valid_vault_name = 1 <= len(vault_name) <= 63 and all(
        char.isascii() and (char.isalnum() or char == "-") for char in vault_name
    )
    valid_authority = (
        parsed_url.scheme.casefold() == "https"
        and parsed_url.username is None
        and parsed_url.password is None
        and port is None
        and separator == "."
        and dns_suffix in _AKV_VAULT_DNS_SUFFIXES
        and valid_vault_name
    )
    path_parts = parsed_url.path.split("/")
    valid_path = (
        len(path_parts) in {3, 4} and path_parts[0] == "" and path_parts[1] == "secrets" and all(path_parts[2:])
    )
    if not valid_authority or not valid_path or parsed_url.query or parsed_url.fragment:
        raise ValueError(error_message)

    secret_name, secret_version = path_parts[2], path_parts[3] if len(path_parts) == 4 else None
    identifiers = [secret_name] + ([secret_version] if secret_version else [])
    if any(
        not 1 <= len(identifier) <= 127
        or not all(char.isascii() and (char.isalnum() or char == "-") for char in identifier)
        for identifier in identifiers
    ):
        raise ValueError(error_message)

    return f"https://{hostname}", secret_name, secret_version


def _create_akv_secret_client(*, vault_url: str, credential: "AsyncTokenCredential") -> "SecretClient":
    """
    Create an asynchronous Key Vault client with an explicit retry policy.

    Returns:
        SecretClient: Configured asynchronous secret client.
    """
    from azure.core.pipeline.policies import AsyncRetryPolicy
    from azure.keyvault.secrets.aio import SecretClient

    retry_policy = AsyncRetryPolicy(
        retry_total=_AKV_RETRY_TOTAL,
        retry_connect=_AKV_RETRY_TOTAL,
        retry_read=_AKV_RETRY_TOTAL,
        retry_status=_AKV_RETRY_TOTAL,
        retry_backoff_factor=_AKV_RETRY_BACKOFF_FACTOR,
    )
    return SecretClient(vault_url=vault_url, credential=credential, retry_policy=retry_policy)


def _key_vault_initialization_error(*, message: str, error: Exception) -> KeyVaultInitializationException:
    """
    Create a contextual Key Vault exception without losing the original cause.

    An upstream HTTP status is preserved when available. Failures without an
    HTTP response use PyRIT's generic 500 status for opaque internal failures.

    Returns:
        KeyVaultInitializationException: Wrapped contextual exception.
    """
    status_code = getattr(error, "status_code", None)
    return KeyVaultInitializationException(
        status_code=status_code if isinstance(status_code, int) else 500,
        message=f"{message}: {error}",
    )


def _validate_dotenv_document(
    document: str,
    *,
    strict: bool = True,
    silent: bool = False,
) -> str:
    """
    Validate that every dotenv binding uses ``NAME=VALUE`` syntax.

    Args:
        document (str): The dotenv document to validate.
        strict (bool): If True, reject any invalid entry. If False, warn and
            allow python-dotenv to skip invalid entries. Defaults to True.
        silent (bool): If True, suppress the console warning. Defaults to False.

    Returns:
        str: The original document, or a sanitized document when strict is False.

    Raises:
        ValueError: If strict is True and the document contains invalid entries.
    """
    bindings = list(parse_stream(StringIO(document)))
    malformed_lines = [str(binding.original.line) for binding in bindings if binding.error]
    valueless_names = [binding.key for binding in bindings if binding.key is not None and binding.value is None]
    issues: list[str] = []
    if malformed_lines:
        issues.append("malformed entries at lines: " + ", ".join(malformed_lines))
    if valueless_names:
        issues.append("variables without values: " + ", ".join(valueless_names))
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


async def _fetch_akv_document_async(
    *,
    secret_url: str,
    strict: bool = True,
    silent: bool = False,
) -> tuple[str, str]:
    """
    Fetch and validate one Key Vault bootstrap dotenv document.

    Authentication uses ``DefaultAzureCredential``, which silently tries managed
    identity, Azure CLI, VS Code credentials, etc., and falls back to interactive
    browser authentication when running locally.

    Args:
        secret_url (str): AKV secret URL in the format
            ``https://{vault}.vault.azure.net/secrets/{name}[/{version}]``.
        strict (bool): If True, reject malformed or valueless dotenv entries.
            If False, warn and skip those entries. Defaults to True.
        silent (bool): If True, suppresses print statements. Defaults to False.

    Returns:
        tuple[str, str]: Validated document text and source vault URL.

    Raises:
        ImportError: If ``azure-keyvault-secrets`` is not installed.
        KeyVaultInitializationException: If the root URL is malformed or the bootstrap
            document cannot be fetched and validated.
        ValueError: Compatibility base of ``KeyVaultInitializationException``.
    """
    from azure.identity.aio import DefaultAzureCredential

    try:
        _print_msg(f"Loading environment from AKV secret: {secret_url}", quiet=silent, log=True)
        vault_url, secret_name, secret_version = _parse_akv_secret_url(secret_url)
        async with DefaultAzureCredential() as credential:
            async with _create_akv_secret_client(vault_url=vault_url, credential=credential) as client:
                secret = await client.get_secret(secret_name, version=secret_version)

                if not secret.value:
                    raise ValueError(f"AKV environment secret has no value: {secret_url}")

                validated_document = _validate_dotenv_document(secret.value, strict=strict, silent=silent)
                parsed_environment = dotenv.dotenv_values(stream=StringIO(validated_document), interpolate=False)
                if not parsed_environment:
                    raise ValueError(f"AKV environment secret contains no environment entries: {secret_url}")
                return validated_document, vault_url
    except KeyVaultInitializationException:
        raise
    except Exception as error:
        wrapped_error = _key_vault_initialization_error(
            message=f"Failed to load Key Vault bootstrap secret '{secret_url}'",
            error=error,
        )
        raise wrapped_error from error


async def load_environment_async(
    *,
    env_akv_ref: Sequence[str] | None,
    env_files: Sequence[pathlib.Path] | None,
    env_akv_strict: bool,
    silent: bool,
) -> None:
    """
    Load environment sources in precedence order.

    Args:
        env_akv_ref (Sequence[str] | None): Optional ordered Key Vault bootstrap secret URLs.
        env_files (Sequence[pathlib.Path] | None): Optional ordered local environment files.
        env_akv_strict (bool): Whether bootstrap dotenv validation is strict.
        silent (bool): Whether initialization messages are suppressed.

    Raises:
        ValueError: If a configured source or reference is invalid.
    """
    if os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in _DOTENV_DISABLED_VALUES:
        return

    if isinstance(env_akv_ref, str):
        raise ValueError("env_akv_ref must be a sequence of Azure Key Vault secret URLs.")
    if env_akv_ref is not None and len(env_akv_ref) > 1:
        raise ValueError("env_akv_ref supports at most one Azure Key Vault bootstrap secret URL.")
    process_environment = dict(os.environ)
    ordinary_candidates: dict[str, list[tuple[str, str | None]]] = {}
    override_candidates: dict[str, list[tuple[str, str | None]]] = {}
    if env_akv_ref:
        if any(not isinstance(secret_url, str) or not secret_url.strip() for secret_url in env_akv_ref):
            raise ValueError("env_akv_ref must contain only non-empty Azure Key Vault secret URLs.")
        if env_files is None:
            dotenv_file = path.CONFIGURATION_DIRECTORY_PATH / ".env"
            if dotenv_file.exists():
                await asyncio.to_thread(
                    _warn_about_dotenv_file,
                    env_file=dotenv_file,
                    ignored_for_akv=True,
                    silent=silent,
                )
        document, vault_url = await _fetch_akv_document_async(
            secret_url=env_akv_ref[0],
            strict=env_akv_strict,
            silent=silent,
        )
        await asyncio.to_thread(
            _load_dotenv_source,
            document=document,
            override=False,
            ordinary_candidates=ordinary_candidates,
            override_candidates=override_candidates,
            expected_vault_url=vault_url,
        )

    await asyncio.to_thread(
        load_environment_files,
        env_files=env_files,
        silent=silent,
        include_default_base=not (env_akv_ref and env_files is None),
        _ordinary_candidates=ordinary_candidates,
        _override_candidates=override_candidates,
    )
    await _resolve_environment_candidates_async(
        process_environment=process_environment,
        ordinary_candidates=ordinary_candidates,
        override_candidates=override_candidates,
        strict=env_akv_strict,
        silent=silent,
    )


def _parse_akv_reference(
    *,
    value: str,
    variable_name: str,
    expected_vault_url: str | None = None,
) -> tuple[str, str, str | None] | None:
    """
    Parse and validate an exact whole-value Key Vault reference.

    Returns:
        tuple[str, str, str | None] | None: Parsed reference, or None for a literal value.

    Raises:
        ValueError: If the reference is malformed or violates the expected vault constraint.
    """
    prefix, separator, target = value.partition(":")
    if not separator or prefix not in _AKV_REFERENCE_PREFIXES:
        return None
    target = target.strip()
    if not target.casefold().startswith("https://"):
        raise ValueError(
            f"AKV reference for environment variable '{variable_name}' must use a full secret URL, "
            "for example kv:https://my-vault.vault.azure.net/secrets/my-secret."
        )

    referenced_vault_url, secret_name, secret_version = _parse_akv_secret_url(target)
    if expected_vault_url and referenced_vault_url.rstrip("/").casefold() != expected_vault_url.rstrip("/").casefold():
        raise ValueError(
            f"Cross-vault AKV reference for environment variable '{variable_name}' is not supported. "
            f"Expected vault '{expected_vault_url}', got '{referenced_vault_url}'."
        )

    return referenced_vault_url, secret_name, secret_version


async def _resolve_environment_candidates_async(
    *,
    process_environment: Mapping[str, str],
    ordinary_candidates: Mapping[str, Sequence[tuple[str, str | None]]],
    override_candidates: Mapping[str, Sequence[tuple[str, str | None]]],
    strict: bool,
    silent: bool,
) -> None:
    """
    Resolve complete Key Vault references from winning environment assignments.

    Raises:
        KeyVaultInitializationException: If strict validation or secret retrieval fails.
        ValueError: If a referenced secret has no value.
    """
    parsed_references: list[tuple[str, str, str, str | None]] = []
    variable_names = ordinary_candidates.keys() | override_candidates.keys()
    for variable_name in variable_names:
        candidates = [
            (value, vault_url, True) for value, vault_url in reversed(override_candidates.get(variable_name, ()))
        ]
        if variable_name in process_environment:
            candidates.append((process_environment[variable_name], None, False))
        candidates.extend((value, vault_url, True) for value, vault_url in ordinary_candidates.get(variable_name, ()))
        for value, expected_vault_url, resolve_reference in candidates:
            if not resolve_reference:
                os.environ[variable_name] = value
                break
            try:
                reference = _parse_akv_reference(
                    value=value,
                    variable_name=variable_name,
                    expected_vault_url=expected_vault_url,
                )
            except ValueError as error:
                if strict:
                    wrapped_error = _key_vault_initialization_error(
                        message=f"Invalid AKV reference for environment variable '{variable_name}'",
                        error=error,
                    )
                    raise wrapped_error from error
                message = f"Invalid AKV reference for environment variable '{variable_name}' will be skipped: {error}"
                if not silent:
                    print(f"WARNING: {message}")
                logger.warning(message)
                continue
            if reference is None:
                os.environ[variable_name] = value
                break
            vault_url, secret_name, secret_version = reference
            os.environ[variable_name] = value
            parsed_references.append((variable_name, vault_url, secret_name, secret_version))
            break
        else:
            os.environ.pop(variable_name, None)

    if not parsed_references:
        return

    from azure.identity.aio import DefaultAzureCredential

    async with DefaultAzureCredential() as credential:
        async with contextlib.AsyncExitStack() as client_stack:
            clients: dict[str, SecretClient] = {}
            for variable_name, vault_url, secret_name, secret_version in parsed_references:
                try:
                    client = clients.get(vault_url)
                    if client is None:
                        client = await client_stack.enter_async_context(
                            _create_akv_secret_client(vault_url=vault_url, credential=credential)
                        )
                        clients[vault_url] = client
                    secret = await client.get_secret(secret_name, version=secret_version)
                    if secret.value is None:
                        raise ValueError(
                            f"AKV secret '{secret_name}' referenced by environment variable "
                            f"'{variable_name}' has no value."
                        )
                    os.environ[variable_name] = secret.value
                except KeyVaultInitializationException:
                    raise
                except Exception as error:
                    wrapped_error = _key_vault_initialization_error(
                        message=f"Failed to resolve Key Vault reference for environment variable '{variable_name}'",
                        error=error,
                    )
                    raise wrapped_error from error
