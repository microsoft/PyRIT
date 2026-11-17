# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Live SQL Server coverage, only against a configured isolated test database."""

import pytest

from pyrit.memory import AzureSQLMemory
from test_support.text_search import LITERAL_CASES, PATTERN_CASES, assert_search_contract_async


@pytest.mark.run_only_if_all_tests
@pytest.mark.parametrize("value, expected", LITERAL_CASES)
async def test_literal_contract_azure_sql_async(
    *, azuresql_instance: AzureSQLMemory, value: str | None, expected: set[str]
) -> None:
    await assert_search_contract_async(memory=azuresql_instance, value=value, expected=expected)


@pytest.mark.run_only_if_all_tests
@pytest.mark.parametrize("pattern, expected", PATTERN_CASES)
async def test_pattern_contract_azure_sql_async(
    *, azuresql_instance: AzureSQLMemory, pattern: str, expected: set[str]
) -> None:
    await assert_search_contract_async(memory=azuresql_instance, pattern=pattern, expected=expected)
