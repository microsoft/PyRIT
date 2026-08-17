# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import os
import pathlib

import pytest
from dotenv import dotenv_values

_AKV_ENVIRONMENT_ENV = "PYRIT_AKV_INTEGRATION_TEST_ENV"
_AKV_ENVIRONMENT_REQUIRED_ENV = "PYRIT_AKV_INTEGRATION_TEST_REQUIRED"
_ENV_EXAMPLE_PATH_ENV = "PYRIT_ENV_EXAMPLE_PATH"


def _get_env_example_path() -> pathlib.Path:
    configured_path = os.getenv(_ENV_EXAMPLE_PATH_ENV)
    if configured_path:
        path = pathlib.Path(configured_path)
        if path.is_file():
            return path
        raise AssertionError(f"{_ENV_EXAMPLE_PATH_ENV} does not identify a file: {path}")

    candidates = [pathlib.Path.cwd() / ".env_example"]
    candidates.extend(parent / ".env_example" for parent in pathlib.Path(__file__).resolve().parents)
    for path in candidates:
        if path.is_file():
            return path

    raise AssertionError("Could not locate .env_example.")


def _get_akv_environment_keys() -> set[str]:
    document = os.getenv(_AKV_ENVIRONMENT_ENV)
    if not document:
        if os.getenv(_AKV_ENVIRONMENT_REQUIRED_ENV, "").casefold() == "true":
            raise AssertionError(f"{_AKV_ENVIRONMENT_ENV} is required but was not populated.")
        pytest.skip(f"Set {_AKV_ENVIRONMENT_ENV} to run the AKV schema integration test.")

    keys = set(dotenv_values(stream=io.StringIO(document), interpolate=False))
    if not keys:
        raise AssertionError("The env-new Key Vault secret contains no dotenv assignments.")
    return keys


def _get_env_example_keys() -> set[str]:
    keys = set(dotenv_values(dotenv_path=_get_env_example_path(), interpolate=False))
    if not keys:
        raise AssertionError(".env_example contains no dotenv assignments.")
    return keys


def test_env_example_keys_are_represented_in_akv_environment() -> None:
    """Ensure the new AKV bootstrap document covers every environment name in the public example."""
    env_example_keys = _get_env_example_keys()
    akv_environment_keys = _get_akv_environment_keys()

    missing_from_akv = env_example_keys - akv_environment_keys
    assert not missing_from_akv, "The env-new Key Vault secret is missing names defined in .env_example: " + ", ".join(
        sorted(missing_from_akv)
    )
