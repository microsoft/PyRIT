# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os

import pytest

from pyrit.setup import IN_MEMORY, initialize_pyrit_async

_SECRET_URL_ENV = "PYRIT_AKV_INTEGRATION_TEST_SECRET_URL"
_VARIABLE_NAME_ENV = "PYRIT_AKV_INTEGRATION_TEST_VARIABLE"
_EXPECTED_VALUE_ENV = "PYRIT_AKV_INTEGRATION_TEST_EXPECTED_VALUE"


def _get_bootstrap_secret_url() -> str:
    """
    Get the explicitly configured integration bootstrap URL.

    Returns:
        str: Key Vault bootstrap secret URL.
    """
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
