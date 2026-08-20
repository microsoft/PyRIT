# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Load dotenv files and Azure Key Vault-backed environment documents."""

import asyncio
import contextlib
import io
import logging
import os
import pathlib
import tempfile
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import dotenv
from dotenv.parser import parse_stream

from pyrit.common import path, print_deprecation_message
from pyrit.exceptions import KeyVaultInitializationException

if TYPE_CHECKING:
    from azure.keyvault.secrets.aio import SecretClient

logger = logging.getLogger(__name__)

_AKV_REFERENCE_PREFIXES = frozenset({"akv", "kv", "azure_key_vault", "env_akv_ref"})
_AKV_VAULT_DNS_SUFFIXES = frozenset({"vault.azure.net", "vault.azure.cn", "vault.usgovcloudapi.net"})
_AKV_RETRY_TOTAL = 3
_AKV_RETRY_BACKOFF_FACTOR = 0.8
_AKV_ENV_FILE_NAME = ".env"
_LEGACY_ENV_REMOVED_IN = "1.3.0"


def _validate_akv_boolean_options(*, env_akv_strict: object, env_akv_write_env: object) -> None:
    """
    Require real booleans for Key Vault behavior flags.

    Raises:
        TypeError: If either option is not a bool.
    """
    for option_name, option_value in (
        ("env_akv_strict", env_akv_strict),
        ("env_akv_write_env", env_akv_write_env),
    ):
        if not isinstance(option_value, bool):
            raise TypeError(f"{option_name} must be a bool, got {type(option_value).__name__}.")


def _load_environment_files(
    env_files: Sequence[pathlib.Path] | None,
    *,
    silent: bool = False,
    include_default_base: bool = True,
    assignment_fallbacks: dict[str, str | None] | None = None,
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
        assignment_fallbacks: Optional output mapping from assignments that win
            precedence to the value they replaced, if any.

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
        override = env_file.name == ".env.local"
        if assignment_fallbacks is not None:
            assignment_names = dotenv.dotenv_values(dotenv_path=env_file, interpolate=False)
            for variable_name in assignment_names:
                if override or variable_name not in os.environ:
                    assignment_fallbacks[variable_name] = os.environ.get(variable_name)
        dotenv.load_dotenv(
            dotenv_path=env_file,
            override=override,
            interpolate=True,
        )
        if not silent:
            _print_msg(f"Loaded environment file: {env_file}", quiet=silent, log=True)

    return bool(selected_files)


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
            _warn_about_legacy_env(env_file=base_file, ignored_for_akv=False, silent=silent)
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


def _warn_about_akv_environment_files(
    env_files: Sequence[pathlib.Path] | None,
    *,
    silent: bool = False,
) -> None:
    """Warn when an auto-discovered legacy environment file coexists with AKV."""
    if env_files is not None:
        return

    base_file = path.CONFIGURATION_DIRECTORY_PATH / ".env"
    if base_file.exists():
        _warn_about_legacy_env(env_file=base_file, ignored_for_akv=True, silent=silent)


def _warn_about_legacy_env(*, env_file: pathlib.Path, ignored_for_akv: bool, silent: bool) -> None:
    """Emit the standard and visible warnings for auto-discovered legacy ``.env`` loading."""
    print_deprecation_message(
        old_item=f"Auto-discovered {env_file}",
        new_item="env_akv_ref or ~/.pyrit/.env.local",
        removed_in=_LEGACY_ENV_REMOVED_IN,
    )
    behavior = "will be ignored because env_akv_ref is configured" if ignored_for_akv else "will still be loaded"
    message = (
        f"Auto-discovered {env_file} is deprecated and {behavior}. "
        f"Support will be removed in {_LEGACY_ENV_REMOVED_IN}. Use env_akv_ref or ~/.pyrit/.env.local instead."
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


def _create_akv_secret_client(*, vault_url: str, credential: Any) -> "SecretClient":
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
    client: Any,
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
    bindings = list(parse_stream(io.StringIO(document)))
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


async def _load_env_from_akv_async(
    *,
    secret_url: str,
    strict: bool = True,
    silent: bool = False,
    resolve_references_for_output: bool = False,
) -> str:
    """
    Load a bootstrap dotenv document and resolve its same-vault secret references.

    References are resolved once. Referenced secret values are treated as terminal
    strings and are not interpreted as additional references.

    Authentication uses ``DefaultAzureCredential``, which silently tries managed
    identity, Azure CLI, VS Code credentials, etc., and falls back to interactive
    browser authentication when running locally.

    Args:
        secret_url (str): AKV secret URL in the format
            ``https://{vault}.vault.azure.net/secrets/{name}[/{version}]``.
        strict (bool): If True, reject malformed or valueless dotenv entries.
            If False, warn and skip those entries. Defaults to True.
        silent (bool): If True, suppresses print statements. Defaults to False.
        resolve_references_for_output (bool): If True, resolve child references even
            when their runtime assignment loses to an existing process value, and
            return a native dotenv document containing those resolved values.

    Returns:
        str: The validated bootstrap dotenv document, with child-secret references
            replaced when ``resolve_references_for_output`` is True.

    Raises:
        ImportError: If ``azure-keyvault-secrets`` is not installed.
        KeyVaultInitializationException: If the root URL is malformed or the bootstrap environment
            document cannot be fully resolved.
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
                parsed_environment = dotenv.dotenv_values(stream=io.StringIO(validated_document), interpolate=True)
                if not parsed_environment:
                    raise ValueError(f"AKV environment secret contains no environment entries: {secret_url}")
                existing_environment_names = set(os.environ)
                loaded = dotenv.load_dotenv(
                    stream=io.StringIO(validated_document),
                    override=False,
                    interpolate=True,
                )
                if not loaded:
                    return validated_document

                resolved_reference_values: dict[str, str] = {}
                skipped_reference_names: set[str] = set()
                for variable_name, value in parsed_environment.items():
                    if value is None:
                        continue
                    target = _parse_akv_reference(value)
                    if target is None:
                        continue
                    assignment_wins = variable_name not in existing_environment_names
                    if not assignment_wins and not resolve_references_for_output:
                        continue
                    try:
                        _, referenced_name, referenced_version = _parse_akv_reference_url(
                            target=target,
                            variable_name=variable_name,
                            expected_vault_url=vault_url,
                        )
                    except ValueError as error:
                        if strict:
                            wrapped_error = _key_vault_initialization_error(
                                message=f"Invalid AKV reference for environment variable '{variable_name}'",
                                error=error,
                            )
                            raise wrapped_error from error
                        if assignment_wins:
                            os.environ.pop(variable_name, None)
                        skipped_reference_names.add(variable_name)
                        _warn_about_invalid_akv_reference(
                            variable_name=variable_name,
                            error=error,
                            silent=silent,
                        )
                        continue
                    try:
                        resolved_value = await _fetch_akv_secret_value_async(
                            client=client,
                            secret_name=referenced_name,
                            secret_version=referenced_version,
                            variable_name=variable_name,
                        )
                        resolved_reference_values[variable_name] = resolved_value
                        if assignment_wins:
                            os.environ[variable_name] = resolved_value
                    except KeyVaultInitializationException:
                        raise
                    except Exception as error:
                        wrapped_error = _key_vault_initialization_error(
                            message=f"Failed to resolve Key Vault reference for environment variable '{variable_name}'",
                            error=error,
                        )
                        raise wrapped_error from error
                if resolve_references_for_output:
                    return _render_resolved_akv_document(
                        document=validated_document,
                        resolved_reference_values=resolved_reference_values,
                        skipped_reference_names=skipped_reference_names,
                    )
                return validated_document
    except KeyVaultInitializationException:
        raise
    except Exception as error:
        wrapped_error = _key_vault_initialization_error(
            message=f"Failed to load Key Vault bootstrap secret '{secret_url}'",
            error=error,
        )
        raise wrapped_error from error


async def _load_environment_async(
    *,
    env_akv_ref: Sequence[str] | None,
    env_files: Sequence[pathlib.Path] | None,
    env_akv_strict: bool,
    env_akv_write_env: bool = False,
    silent: bool,
) -> None:
    """
    Load environment sources in precedence order.

    Args:
        env_akv_ref (Sequence[str] | None): Optional ordered Key Vault bootstrap secret URLs.
        env_files (Sequence[pathlib.Path] | None): Optional ordered local environment files.
        env_akv_strict (bool): Whether bootstrap dotenv validation is strict.
        env_akv_write_env (bool): Whether to save fetched bootstrap documents to
            ``~/.pyrit/.env``. Defaults to False.
        silent (bool): Whether initialization messages are suppressed.

    Raises:
        ValueError: If a configured source or reference is invalid.
    """
    if isinstance(env_akv_ref, str):
        raise ValueError("env_akv_ref must be a sequence of Azure Key Vault secret URLs.")
    bootstrap_documents: list[str] = []
    if env_akv_ref:
        if any(not isinstance(secret_url, str) or not secret_url.strip() for secret_url in env_akv_ref):
            raise ValueError("env_akv_ref must contain only non-empty Azure Key Vault secret URLs.")
        env_file = path.CONFIGURATION_DIRECTORY_PATH / _AKV_ENV_FILE_NAME
        if env_akv_write_env and (env_file.exists() or env_file.is_symlink()):
            raise ValueError(
                f"Cannot write the resolved Key Vault environment because {env_file} already exists; "
                "rename or remove it before enabling env_akv_write_env."
            )
        await asyncio.to_thread(
            _warn_about_akv_environment_files,
            env_files=env_files,
            silent=silent,
        )
        bootstrap_documents.extend(
            [
                await _load_env_from_akv_async(
                    secret_url=secret_url,
                    strict=env_akv_strict,
                    silent=silent,
                    resolve_references_for_output=env_akv_write_env,
                )
                for secret_url in env_akv_ref
            ]
        )

    written_env_file: pathlib.Path | None = None
    if env_akv_write_env and bootstrap_documents:
        written_env_file = await asyncio.to_thread(
            _write_akv_env_file,
            documents=bootstrap_documents,
            silent=silent,
        )

    selected_env_files = env_files
    if written_env_file is not None and env_files is not None:
        written_path = written_env_file.resolve()
        selected_env_files = [env_file for env_file in env_files if env_file.expanduser().resolve() != written_path]

    assignment_fallbacks: dict[str, str | None] = {}
    await asyncio.to_thread(
        _load_environment_files,
        env_files=selected_env_files,
        silent=silent,
        include_default_base=not (env_akv_ref and env_files is None),
        assignment_fallbacks=assignment_fallbacks,
    )
    await _resolve_local_akv_references_async(
        assignment_fallbacks=assignment_fallbacks,
        strict=env_akv_strict,
        silent=silent,
    )


def _write_akv_env_file(*, documents: Sequence[str], silent: bool) -> pathlib.Path:
    """
    Write fetched bootstrap documents with resolved child-secret values.

    Returns:
        pathlib.Path: Path to the written dotenv file.

    Raises:
        ValueError: If the destination is a symbolic link.
    """
    env_file = path.CONFIGURATION_DIRECTORY_PATH / _AKV_ENV_FILE_NAME
    env_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if env_file.is_symlink():
        raise ValueError(f"Refusing to write the AKV environment through a symbolic link: {env_file}")

    content = _merge_akv_documents_for_debug(documents=documents)
    file_descriptor: int | None = None
    temporary_file: pathlib.Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{env_file.name}.",
            suffix=".tmp",
            dir=env_file.parent,
        )
        temporary_file = pathlib.Path(temporary_name)
        file_chmod = getattr(os, "fchmod", None)
        if file_chmod is not None:
            file_chmod(file_descriptor, 0o600)
        else:
            os.chmod(temporary_file, 0o600)
        stream = os.fdopen(file_descriptor, "w", encoding="utf-8", newline="")
        file_descriptor = None
        with stream:
            stream.write(content)
        if env_file.is_symlink():
            raise ValueError(f"Refusing to replace a symbolic link with the AKV environment: {env_file}")
        os.replace(temporary_file, env_file)
        temporary_file = None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if temporary_file is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary_file.unlink()

    _print_msg(f"Saved Key Vault bootstrap environment file: {env_file}", quiet=silent, log=True)
    return env_file


def _merge_akv_documents_for_debug(*, documents: Sequence[str]) -> str:
    """
    Merge resolved bootstrap documents using runtime first-document precedence.

    Duplicate assignments within one document are retained because interpolation
    depends on assignment order. Assignments established by an earlier document
    are omitted from later documents.

    Returns:
        str: A native dotenv document with equivalent bootstrap precedence.
    """
    established_names: set[str] = set()
    merged_bindings: list[str] = []
    for document in documents:
        document_names: set[str] = set()
        for binding in parse_stream(io.StringIO(document)):
            if binding.key is None or binding.key not in established_names:
                merged_bindings.append(binding.original.string)
            if binding.key is not None:
                document_names.add(binding.key)
        established_names.update(document_names)

    return "".join(merged_bindings).rstrip("\r\n") + "\n"


def _render_resolved_akv_document(
    *,
    document: str,
    resolved_reference_values: Mapping[str, str],
    skipped_reference_names: set[str] | None = None,
) -> str:
    """
    Replace resolved Key Vault reference assignments with native dotenv values.

    Returns:
        str: Dotenv text that preserves non-reference bindings and comments.
    """
    skipped_reference_names = skipped_reference_names or set()
    rendered_bindings: list[str] = []
    for binding in parse_stream(io.StringIO(document)):
        variable_name = binding.key
        is_reference = binding.value is not None and _parse_akv_reference(binding.value) is not None
        if variable_name in skipped_reference_names and is_reference:
            continue
        if variable_name is not None and variable_name in resolved_reference_values and is_reference:
            original = binding.original.string
            export_prefix = "export " if original.lstrip().startswith("export ") else ""
            if original.endswith("\r\n"):
                newline = "\r\n"
            elif original.endswith("\n"):
                newline = "\n"
            else:
                newline = ""
            rendered_bindings.append(
                f"{export_prefix}{variable_name}="
                f"{_serialize_terminal_dotenv_value(resolved_reference_values[variable_name])}{newline}"
            )
        else:
            rendered_bindings.append(binding.original.string)
    return "".join(rendered_bindings)


def _serialize_terminal_dotenv_value(value: str) -> str:
    """
    Quote a terminal secret value for a native python-dotenv round trip.

    The empty-name default expression produces a literal dollar sign during
    interpolation, preventing terminal ``${NAME}`` text from being reinterpreted.

    Returns:
        str: A single-quoted dotenv value.
    """
    escaped_value = value.replace("'", "\\'").replace("${", "${:-$}{")
    return f"'{escaped_value}'"


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


def _validate_akv_secret_name(*, secret_name: str, variable_name: str) -> None:
    if not _is_valid_akv_identifier(secret_name):
        raise ValueError(
            f"Invalid same-vault secret name '{secret_name}' referenced by environment variable '{variable_name}'. "
            "Secret names must contain only letters, numbers, and hyphens."
        )


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

    _validate_akv_secret_name(secret_name=secret_name, variable_name=variable_name)
    return referenced_vault_url, secret_name, secret_version


async def _resolve_local_akv_references_async(
    *,
    assignment_fallbacks: Mapping[str, str | None],
    strict: bool,
    silent: bool,
) -> None:
    """
    Resolve complete Key Vault references from winning local assignments.

    Raises:
        KeyVaultInitializationException: If strict validation or secret retrieval fails.
    """
    parsed_references: list[tuple[str, str, str, str | None]] = []
    for variable_name, fallback_value in assignment_fallbacks.items():
        value = os.environ.get(variable_name)
        if value is None:
            continue
        target = _parse_akv_reference(value)
        if target is None:
            continue
        try:
            vault_url, secret_name, secret_version = _parse_akv_reference_url(
                target=target,
                variable_name=variable_name,
            )
        except ValueError as error:
            if strict:
                wrapped_error = _key_vault_initialization_error(
                    message=f"Invalid AKV reference for environment variable '{variable_name}'",
                    error=error,
                )
                raise wrapped_error from error
            if fallback_value is None:
                os.environ.pop(variable_name, None)
            else:
                os.environ[variable_name] = fallback_value
            _warn_about_invalid_akv_reference(
                variable_name=variable_name,
                error=error,
                silent=silent,
            )
            continue
        parsed_references.append((variable_name, vault_url, secret_name, secret_version))

    if not parsed_references:
        return

    from azure.identity.aio import DefaultAzureCredential

    async with DefaultAzureCredential() as credential:
        async with contextlib.AsyncExitStack() as client_stack:
            clients: dict[str, Any] = {}
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


def _resolve_akv_secret_reference(
    *,
    target: str,
    variable_name: str,
    vault_url: str,
) -> tuple[str, str | None]:
    """
    Resolve a full same-vault secret URI.

    Args:
        target (str): Full Key Vault secret URI.
        variable_name (str): The environment variable receiving the secret.
        vault_url (str): The bootstrap document's vault URL.

    Returns:
        tuple[str, str | None]: Secret name and optional version.

    Raises:
        ValueError: If the target is not a full URI, is invalid, or references another vault.
    """
    _, secret_name, secret_version = _parse_akv_reference_url(
        target=target,
        variable_name=variable_name,
        expected_vault_url=vault_url,
    )
    return secret_name, secret_version
