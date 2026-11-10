# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Read a caller's own target credential for the backend's personal targets.

Credentials come from a hidden prompt, an environment variable, or the Anthropic ``ant`` CLI.
They are never taken from command-line arguments, printed, or logged.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum


class TargetCredentialError(Exception):
    """Raised when the CLI cannot read a target credential from its source."""


class CredentialSourceKind(Enum):
    """Where the CLI reads a target credential from."""

    PROMPT = "prompt"
    ENVIRONMENT = "environment"
    ANT = "ant"


@dataclass(frozen=True)
class CredentialSource:
    """A target credential source selected on the command line."""

    kind: CredentialSourceKind
    environment_variable: str | None = None
    ant_profile: str | None = None

    ANT_TIMEOUT_SECONDS = 120.0
    ANT_PRECEDENCE_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    @property
    def description(self) -> str:
        """A short, secret-free description for messages."""
        if self.kind is CredentialSourceKind.ENVIRONMENT:
            return f"environment variable {self.environment_variable}"
        if self.kind is CredentialSourceKind.ANT:
            return f"ant profile '{self.ant_profile}'" if self.ant_profile else "the active ant profile"
        return "the prompt"


async def read_target_credential_async(*, source: CredentialSource, target_name: str) -> str:
    """
    Read a credential from its source.

    Args:
        source (CredentialSource): Where to read the credential.
        target_name (str): The target the credential is for, shown in the prompt.

    Returns:
        str: The credential, without surrounding whitespace.

    Raises:
        TargetCredentialError: If the source is unavailable or yields no credential.
    """
    if source.kind is CredentialSourceKind.PROMPT:
        credential = await _read_prompt_async(target_name=target_name)
    elif source.kind is CredentialSourceKind.ENVIRONMENT:
        credential = _read_environment(variable=source.environment_variable)
    else:
        credential = await _read_ant_async(profile=source.ant_profile)
    return _require_single_token(credential=credential, source=source)


async def _read_prompt_async(*, target_name: str) -> str:
    if not sys.stdin.isatty():
        raise TargetCredentialError("--prompt requires an interactive terminal. Use --from-env or --from-ant instead.")
    return await asyncio.to_thread(getpass.getpass, f"Your credential for target '{target_name}' (hidden): ")


def _read_environment(*, variable: str | None) -> str:
    if not variable:
        raise TargetCredentialError("--from-env requires an environment variable name.")
    value = os.environ.get(variable)
    if value is None:
        raise TargetCredentialError(f"Environment variable {variable} is not set.")
    return value


async def _read_ant_async(*, profile: str | None) -> str:
    executable = shutil.which("ant")
    if executable is None:
        raise TargetCredentialError(
            "The ant CLI was not found on PATH. Install it, then run 'ant auth login --profile <name>'."
        )
    overriding = [name for name in CredentialSource.ANT_PRECEDENCE_VARIABLES if os.environ.get(name)]
    if overriding:
        print(
            f"Warning: {', '.join(overriding)} is set, so ant prints that credential instead of any profile's.",
            file=sys.stderr,
        )
    arguments = ["auth", "print-credentials", *(["--profile", profile] if profile else []), "--access-token"]
    process = await asyncio.create_subprocess_exec(
        executable,
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=CredentialSource.ANT_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise TargetCredentialError("ant did not print a credential in time.") from exc
    if process.returncode != 0:
        reason = stderr.decode(errors="replace").strip().splitlines()
        hint = f" ({reason[-1]})" if reason else ""
        login = f"ant auth login --profile {profile}" if profile else "ant auth login"
        raise TargetCredentialError(f"ant could not print a credential{hint}. Run '{login}' and try again.")
    return stdout.decode(errors="replace")


def _require_single_token(*, credential: str, source: CredentialSource) -> str:
    token = credential.strip()
    if not token:
        raise TargetCredentialError(f"No credential was read from {source.description}.")
    if any(character.isspace() for character in token):
        raise TargetCredentialError(f"The credential from {source.description} is not a single value.")
    return token
