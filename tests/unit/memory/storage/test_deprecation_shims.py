# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the Phase 9 deprecation shims.

``pyrit.models.storage_io`` and ``pyrit.models.data_type_serializer`` moved to
``pyrit.memory.storage.storage`` / ``pyrit.memory.storage.serializers``. The old module paths, the
``pyrit.models`` package-root re-exports, and the
``MessagePiece.set_sha256_values_async`` / ``Seed.set_sha256_value_async``
method shims all still work but emit a ``DeprecationWarning`` pointing at the
new ``pyrit.memory.storage`` location. These tests pin that contract. The shims will be
removed in 0.17.0.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pyrit.memory.storage.serializers as new_serializers
import pyrit.memory.storage.storage as new_storage
import pyrit.models as models_pkg
import pyrit.models.data_type_serializer as serializer_shim
import pyrit.models.storage_io as storage_shim
from pyrit.models.messages.message_piece import MessagePiece
from pyrit.models.seeds.seed import Seed

MODULE_SHIM_PAIRS = [
    (storage_shim, new_storage, "pyrit.models.storage_io", "pyrit.memory.storage.storage"),
    (serializer_shim, new_serializers, "pyrit.models.data_type_serializer", "pyrit.memory.storage.serializers"),
]


@pytest.fixture(autouse=True)
def _reset_models_warned():
    """Reset the ``pyrit.models`` package-root warn-once cache so each test starts clean."""
    saved = set(models_pkg._warned)
    models_pkg._warned.clear()
    try:
        yield
    finally:
        models_pkg._warned.clear()
        models_pkg._warned.update(saved)


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_forwards_every_name(shim_mod, new_mod, old_path, new_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in shim_mod.__all__:
            assert getattr(shim_mod, name) is getattr(new_mod, name), f"{old_path}.{name} did not forward"


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_warns_once_per_name(shim_mod, new_mod, old_path, new_path):
    # Reload the shim to reset its internal warn-once closure for a clean count.
    shim_mod = importlib.reload(shim_mod)
    for name in shim_mod.__all__:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            getattr(shim_mod, name)
            getattr(shim_mod, name)

        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) == 1, f"Expected 1 DeprecationWarning for {old_path}.{name}, got {len(dep)}"
        message = str(dep[0].message)
        assert f"{old_path}.{name}" in message
        assert f"{new_path}.{name}" in message
        assert "0.17.0" in message


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_attribute_error_for_unknown_name(shim_mod, new_mod, old_path, new_path):
    with pytest.raises(AttributeError, match=f"module {old_path!r} has no attribute"):
        _ = shim_mod.definitely_not_a_real_name


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_dir_returns_sorted_all(shim_mod, new_mod, old_path, new_path):
    assert dir(shim_mod) == sorted(shim_mod.__all__)


def test_moved_to_memory_storage_contains_expected_root_exports():
    # Guards against accidentally dropping a previously root-importable name from the
    # forwarding table. These are exactly the names that used to be importable from
    # ``pyrit.models`` and now live in ``pyrit.memory.storage``. URLDataTypeSerializer and
    # SupportedContentType were never root-exported, so they are intentionally absent.
    expected = {
        "AllowedCategories",
        "AudioPathDataTypeSerializer",
        "BinaryPathDataTypeSerializer",
        "DataTypeSerializer",
        "ErrorDataTypeSerializer",
        "ImagePathDataTypeSerializer",
        "TextDataTypeSerializer",
        "VideoPathDataTypeSerializer",
        "data_serializer_factory",
        "AzureBlobStorageIO",
        "DiskStorageIO",
        "StorageIO",
    }
    assert set(models_pkg._MOVED_TO_MEMORY_STORAGE) == expected


@pytest.mark.parametrize("name", sorted(models_pkg._MOVED_TO_MEMORY_STORAGE))
def test_models_package_root_forwards_and_warns_once(name):
    target_module = models_pkg._MOVED_TO_MEMORY_STORAGE[name]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        first = getattr(models_pkg, name)
        second = getattr(models_pkg, name)

    assert first is second
    assert first is getattr(importlib.import_module(target_module), name)

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1, f"Expected 1 DeprecationWarning for pyrit.models.{name}, got {len(dep)}"
    message = str(dep[0].message)
    assert f"pyrit.models.{name}" in message
    assert f"{target_module}.{name}" in message
    assert "0.17.0" in message


def test_importing_pyrit_models_does_not_warn():
    # Use a subprocess so the import is genuinely fresh and reloading the core
    # package can't contaminate other tests in this worker. Filter to warnings
    # that reference the moved paths so unrelated third-party DeprecationWarnings
    # emitted at import time don't make this flaky.
    script = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import pyrit.models\n"
        "offenders = [str(w.message) for w in caught\n"
        "             if issubclass(w.category, DeprecationWarning)\n"
        "             and ('pyrit.memory.storage' in str(w.message) or 'pyrit.models.storage_io' in str(w.message)\n"
        "                  or 'pyrit.models.data_type_serializer' in str(w.message))]\n"
        "assert not offenders, offenders\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, f"Importing pyrit.models warned about moved names:\n{result.stderr}"


async def test_message_piece_method_shim_warns_and_delegates():
    fake_self = MagicMock(spec=MessagePiece)
    delegate = AsyncMock()
    with patch.object(new_serializers, "set_message_piece_sha256_async", delegate):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            await MessagePiece.set_sha256_values_async(fake_self)

    delegate.assert_awaited_once_with(fake_self)
    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1
    message = str(dep[0].message)
    assert "MessagePiece.set_sha256_values_async" in message
    assert "pyrit.memory.storage.serializers.set_message_piece_sha256_async" in message
    assert "0.17.0" in message


async def test_seed_method_shim_warns_and_delegates():
    fake_self = MagicMock(spec=Seed)
    delegate = AsyncMock()
    with patch.object(new_serializers, "set_seed_sha256_async", delegate):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            await Seed.set_sha256_value_async(fake_self)

    delegate.assert_awaited_once_with(fake_self)
    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1
    message = str(dep[0].message)
    assert "Seed.set_sha256_value_async" in message
    assert "pyrit.memory.storage.serializers.set_seed_sha256_async" in message
    assert "0.17.0" in message
