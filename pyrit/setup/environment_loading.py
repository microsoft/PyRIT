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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _EnvironmentValueCandidate:
    """An ordered environment value and its Key Vault resolution policy."""

    value: str
    resolve_akv_reference: bool
    expected_vault_url: str | None = None


@dataclass(frozen=True)
class _AkvEnvironmentDocument:
    """A validated Key Vault bootstrap document and its source vault."""

    content: str
    vault_url: str


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
) -> bool:
    """
    Load local environment files using PyRIT's standard precedence.

    Returns:
        bool: Whether at least one environment file was selected.
    """
    return _load_environment_files(
        env_files=env_files,
        silent=silent,
        include_default_base=include_default_base,
        assignment_candidates=None,
    )


def _load_environment_files(
    env_files: Sequence[pathlib.Path] | None,
    *,
    silent: bool,
    include_default_base: bool,
    assignment_candidates: dict[str, list[_EnvironmentValueCandidate]] | None = None,
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
        assignment_candidates: Optional output mapping containing each applicable
            local value in ascending precedence order. Existing process or AKV values
            are retained as non-resolvable baseline candidates.

    Returns:
        True if at least one environment file was loaded, otherwise False.

    Raises:
        ValueError: If any provided env_files do not exist.
    """
    selected_files = _select_environment_files(
        env_files=env_files,
        silent=silent,
        include_default_base=include_default_base,
    )
    for env_file in selected_files:
        loaded = _load_dotenv_source(
            dotenv_path=env_file,
            override=env_file.name == ".env.local",
            assignment_candidates=assignment_candidates,
        )
        if not silent:
            _print_msg(f"Loaded environment file: {env_file}", quiet=silent, log=True)

    return bool(selected_files)


def _load_dotenv_source(
    *,
    override: bool,
    assignment_candidates: dict[str, list[_EnvironmentValueCandidate]] | None,
    dotenv_path: pathlib.Path | None = None,
    document: str | None = None,
    expected_vault_url: str | None = None,
) -> bool:
    """
    Load one dotenv source and record values that participate in precedence.

    Returns:
        bool: Whether python-dotenv loaded at least one assignment.

    Raises:
        ValueError: If both or neither source representations are provided.
    """
    if (dotenv_path is None) == (document is None):
        raise ValueError("Exactly one dotenv_path or document must be provided.")

    if dotenv_path is not None:
        assignment_values = DotEnv(
            dotenv_path=dotenv_path,
            override=override,
            interpolate=True,
        ).dict()
    else:
        assignment_values = DotEnv(
            dotenv_path=None,
            stream=StringIO(document or ""),
            override=override,
            interpolate=True,
        ).dict()
    previous_values = {variable_name: os.environ.get(variable_name) for variable_name in assignment_values}

    if dotenv_path is not None:
        loaded = dotenv.load_dotenv(dotenv_path=dotenv_path, override=override, interpolate=True)
    else:
        loaded = dotenv.load_dotenv(stream=StringIO(document or ""), override=override, interpolate=True)
    if assignment_candidates is None or not loaded:
        return loaded

    for variable_name, loaded_value in assignment_values.items():
        if loaded_value is None:
            continue
        candidates = assignment_candidates.setdefault(variable_name, [])
        previous_value = previous_values[variable_name]
        if not candidates and previous_value is not None:
            candidates.append(_EnvironmentValueCandidate(value=previous_value, resolve_akv_reference=False))
        candidate = _EnvironmentValueCandidate(
            value=loaded_value,
            resolve_akv_reference=True,
            expected_vault_url=expected_vault_url,
        )
        if override:
            candidates.append(candidate)
        elif candidates and not candidates[-1].resolve_akv_reference:
            continue
        elif candidates:
            candidates.insert(0, candidate)
        else:
            candidates.append(candidate)
    return loaded


def _select_environment_files(
    env_files: Sequence[pathlib.Path] | None,
    *,
    silent: bool,
    include_default_base: bool,
) -> list[pathlib.Path]:
    """
    Select and validate environment files without reading their contents.

    Returns:
        list[pathlib.Path]: Environment files in load order.

    Raises:
        ValueError: If an explicitly provided environment file does not exist.
    """
    if env_files is not None:
        if not silent:
            _print_msg(f"Loading custom environment files: {[str(f) for f in env_files]}", quiet=silent, log=True)
        for env_file in env_files:
            if not env_file.exists():
                raise ValueError(f"Environment file not found: {env_file}")

    # By default load .env and .env.local from home directory of the package
    else:
        default_files = []
        base_file = path.CONFIGURATION_DIRECTORY_PATH / ".env"
        local_file = path.CONFIGURATION_DIRECTORY_PATH / ".env.local"

        if include_default_base and base_file.exists():
            _warn_about_dotenv_file(env_file=base_file, ignored_for_akv=False, silent=silent)
            default_files.append(base_file)
        if local_file.exists():
            default_files.append(local_file)

        if not silent:
            if default_files:
                _print_msg(
                    f"Found default environment files: {[str(f) for f in default_files]}", quiet=silent, log=True
                )
            else:
                _print_msg(
                    "No default environment files found. Using system environment variables only.",
                    quiet=silent,
                    log=True,
                )

        env_files = default_files

    return list(env_files)


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

    secret_name = path_parts[2]
    secret_version = path_parts[3] if len(path_parts) == 4 else None
    if not _is_valid_akv_identifier(secret_name) or (
        secret_version is not None and not _is_valid_akv_identifier(secret_version)
    ):
        raise ValueError(error_message)

    return f"https://{hostname}", secret_name, secret_version


def _is_valid_akv_identifier(identifier: str) -> bool:
    """
    Check whether a Key Vault secret name or version uses URL-safe characters.

    Returns:
        bool: True when the identifier is valid.
    """
    return 1 <= len(identifier) <= 127 and all(
        char.isascii() and (char.isalnum() or char == "-") for char in identifier
    )


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


async def _fetch_akv_secret_value_async(
    *,
    client: "SecretClient",
    secret_name: str,
    secret_version: str | None,
    variable_name: str,
) -> str:
    """
    Fetch a referenced Key Vault secret value.

    Returns:
        str: The secret value, including an empty string.

    Raises:
        ValueError: If the referenced secret has no value.
    """
    referenced_secret = await client.get_secret(secret_name, version=secret_version)
    if referenced_secret.value is None:
        raise ValueError(
            f"AKV secret '{secret_name}' referenced by environment variable '{variable_name}' has no value."
        )
    return referenced_secret.value


def _key_vault_initialization_error(*, message: str, error: Exception) -> KeyVaultInitializationException:
    """
    Create a contextual Key Vault exception without losing the original cause.

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
) -> _AkvEnvironmentDocument:
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
        _AkvEnvironmentDocument: Validated document text and source vault metadata.

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
                return _AkvEnvironmentDocument(content=validated_document, vault_url=vault_url)
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
    if isinstance(env_akv_ref, str):
        raise ValueError("env_akv_ref must be a sequence of Azure Key Vault secret URLs.")
    assignment_candidates: dict[str, list[_EnvironmentValueCandidate]] = {}
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
        for secret_url in env_akv_ref:
            document = await _fetch_akv_document_async(
                secret_url=secret_url,
                strict=env_akv_strict,
                silent=silent,
            )
            await asyncio.to_thread(
                _load_dotenv_source,
                document=document.content,
                override=False,
                assignment_candidates=assignment_candidates,
                expected_vault_url=document.vault_url,
            )

    await asyncio.to_thread(
        _load_environment_files,
        env_files=env_files,
        silent=silent,
        include_default_base=not (env_akv_ref and env_files is None),
        assignment_candidates=assignment_candidates,
    )
    await _resolve_environment_candidates_async(
        assignment_candidates=assignment_candidates,
        strict=env_akv_strict,
        silent=silent,
    )


def _warn_about_invalid_akv_reference(*, variable_name: str, error: ValueError, silent: bool) -> None:
    """Warn that a malformed Key Vault reference assignment is being skipped."""
    message = f"Invalid AKV reference for environment variable '{variable_name}' will be skipped: {error}"
    if not silent:
        print(f"WARNING: {message}")
    logger.warning(message)


def _parse_akv_reference(value: str) -> str | None:
    """
    Parse an exact whole-value Key Vault reference.

    Returns:
        The referenced secret URL, or None for a literal value.
    """
    prefix, separator, target = value.partition(":")
    return target.strip() if separator and prefix in _AKV_REFERENCE_PREFIXES else None


def _parse_akv_reference_url(
    *,
    target: str,
    variable_name: str,
    expected_vault_url: str | None = None,
) -> tuple[str, str, str | None]:
    """
    Parse and optionally constrain a complete Key Vault secret reference.

    Returns:
        tuple[str, str, str | None]: Vault URL, secret name, and optional secret version.

    Raises:
        ValueError: If the reference is malformed or violates the expected vault constraint.
    """
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
    assignment_candidates: Mapping[str, Sequence[_EnvironmentValueCandidate]],
    strict: bool,
    silent: bool,
) -> None:
    """
    Resolve complete Key Vault references from winning environment assignments.

    Raises:
        KeyVaultInitializationException: If strict validation or secret retrieval fails.
    """
    parsed_references: list[tuple[str, str, str, str | None]] = []
    for variable_name, candidates in assignment_candidates.items():
        for candidate in reversed(candidates):
            if not candidate.resolve_akv_reference:
                os.environ[variable_name] = candidate.value
                break
            target = _parse_akv_reference(candidate.value)
            if target is None:
                os.environ[variable_name] = candidate.value
                break
            try:
                vault_url, secret_name, secret_version = _parse_akv_reference_url(
                    target=target,
                    variable_name=variable_name,
                    expected_vault_url=candidate.expected_vault_url,
                )
            except ValueError as error:
                if strict:
                    wrapped_error = _key_vault_initialization_error(
                        message=f"Invalid AKV reference for environment variable '{variable_name}'",
                        error=error,
                    )
                    raise wrapped_error from error
                _warn_about_invalid_akv_reference(
                    variable_name=variable_name,
                    error=error,
                    silent=silent,
                )
                continue
            os.environ[variable_name] = candidate.value
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
                    os.environ[variable_name] = await _fetch_akv_secret_value_async(
                        client=client,
                        secret_name=secret_name,
                        secret_version=secret_version,
                        variable_name=variable_name,
                    )
                except KeyVaultInitializationException:
                    raise
                except Exception as error:
                    wrapped_error = _key_vault_initialization_error(
                        message=f"Failed to resolve Key Vault reference for environment variable '{variable_name}'",
                        error=error,
                    )
                    raise wrapped_error from error
