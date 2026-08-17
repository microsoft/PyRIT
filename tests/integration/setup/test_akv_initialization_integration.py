# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import os
import pathlib

import pytest
from dotenv import dotenv_values

from pyrit.setup import IN_MEMORY, initialize_pyrit_async

_AKV_ENVIRONMENT_ENV = "PYRIT_AKV_INTEGRATION_TEST_ENV"
_AKV_ENVIRONMENT_REQUIRED_ENV = "PYRIT_AKV_INTEGRATION_TEST_REQUIRED"
_ENV_EXAMPLE_PATH_ENV = "PYRIT_ENV_EXAMPLE_PATH"
_SECRET_URL_ENV = "PYRIT_AKV_INTEGRATION_TEST_SECRET_URL"
_VARIABLE_NAME_ENV = "PYRIT_AKV_INTEGRATION_TEST_VARIABLE"
_EXPECTED_VALUE_ENV = "PYRIT_AKV_INTEGRATION_TEST_EXPECTED_VALUE"


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


def _get_bootstrap_secret_url() -> str:
    """Get the explicitly configured integration bootstrap URL."""
    configured_url = os.getenv(_SECRET_URL_ENV)
    if not configured_url:
        pytest.skip(f"Set {_SECRET_URL_ENV} to run this integration test.")
    return configured_url


@pytest.mark.run_only_if_all_tests
async def test_akv_bootstrap_initialization_populates_process_environment() -> None:
    variable_name = os.getenv(_VARIABLE_NAME_ENV, "TEST_KEY")
    expected_value = os.getenv(_EXPECTED_VALUE_ENV, "surprise")
    bootstrap_secret_url = _get_bootstrap_secret_url()

    missing = object()
    original_value: object = os.environ.pop(variable_name, missing)
    try:
        await initialize_pyrit_async(
            memory_db_type=IN_MEMORY,
            env_akv_ref=[bootstrap_secret_url],
            env_files=[],
            load_defaults=False,
            silent=True,
        )

        if os.environ.get(variable_name) != expected_value:
            raise AssertionError(f"{variable_name} did not resolve to the expected integration-test sentinel.")
    finally:
        if original_value is missing:
            os.environ.pop(variable_name, None)
        else:
            os.environ[variable_name] = str(original_value)


def test_akv_environment_keys_are_represented_in_env_example() -> None:
    """Ensure the public example covers every environment name in the new AKV bootstrap document."""
    env_example_keys = _get_env_example_keys()
    akv_environment_keys = _get_akv_environment_keys()

    missing_from_example = akv_environment_keys - env_example_keys
    assert not missing_from_example, (
        "The env-new Key Vault secret contains names absent from .env_example: "
        + ", ".join(sorted(missing_from_example))
    )
