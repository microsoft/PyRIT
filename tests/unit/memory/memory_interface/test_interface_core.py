# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.memory import MemoryInterface


def test_memory(sqlite_instance: MemoryInterface):
    assert sqlite_instance


def test_print_schema_raises_when_engine_none():
    # Test the MemoryInterface.print_schema guard; use AzureSQLMemory which inherits it without override
    from pyrit.memory import AzureSQLMemory

    obj = AzureSQLMemory.__new__(AzureSQLMemory)
    obj.engine = None
    with pytest.raises(RuntimeError, match="Engine is not initialized"):
        obj.print_schema()
