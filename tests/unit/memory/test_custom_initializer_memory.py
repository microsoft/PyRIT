# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.memory.memory_models import CustomInitializerEntry
from pyrit.models import CustomInitializer


@pytest.mark.usefixtures("patch_central_database")
class TestCustomInitializerMemory:
    def test_custom_initializer_round_trips_real_domain_model(self, sqlite_instance) -> None:
        initializer = CustomInitializer(
            initializer_name="custom_target",
            script_content="class CustomTargetInitializer: pass",
        )

        sqlite_instance.add_custom_initializer(initializer=initializer)

        entries = sqlite_instance._query_entries(CustomInitializerEntry)
        assert len(entries) == 1
        assert entries[0].to_domain_model() == initializer
        assert sqlite_instance.get_custom_initializers() == [initializer]

    def test_add_custom_initializer_upserts_by_name(self, sqlite_instance) -> None:
        sqlite_instance.add_custom_initializer(
            initializer=CustomInitializer(initializer_name="custom_target", script_content="old")
        )
        updated = CustomInitializer(initializer_name="custom_target", script_content="new")

        sqlite_instance.add_custom_initializer(initializer=updated)

        assert sqlite_instance.get_custom_initializers() == [updated]

    def test_delete_custom_initializer_is_idempotent(self, sqlite_instance) -> None:
        initializer = CustomInitializer(initializer_name="custom_target", script_content="source")
        sqlite_instance.add_custom_initializer(initializer=initializer)

        sqlite_instance.delete_custom_initializer(initializer_name="custom_target")
        sqlite_instance.delete_custom_initializer(initializer_name="custom_target")

        assert sqlite_instance.get_custom_initializers() == []
