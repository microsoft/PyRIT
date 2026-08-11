# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.memory.memory_models import AdditionalInitializerEntry
from pyrit.models import AdditionalInitializer


@pytest.mark.usefixtures("patch_central_database")
class TestAdditionalInitializerMemory:
    def test_additional_initializer_entry_round_trips_real_domain_model(self, sqlite_instance) -> None:
        initializer = AdditionalInitializer(
            initializer_name="target",
            parameters={"tags": ["default"]},
            order_index=3,
        )

        sqlite_instance.add_additional_initializer(initializer=initializer)

        entries = sqlite_instance._query_entries(AdditionalInitializerEntry)

        assert len(entries) == 1
        assert entries[0].to_domain_model() == initializer
        assert sqlite_instance.get_additional_initializers() == [initializer]

    def test_add_additional_initializer_upserts_by_id(self, sqlite_instance) -> None:
        initializer = AdditionalInitializer(initializer_name="target", order_index=1)
        sqlite_instance.add_additional_initializer(initializer=initializer)

        updated = AdditionalInitializer(id=initializer.id, initializer_name="target", order_index=4)
        sqlite_instance.add_additional_initializer(initializer=updated)

        assert sqlite_instance.get_additional_initializers() == [updated]

    def test_multiple_rows_per_initializer_name_are_kept(self, sqlite_instance) -> None:
        first = AdditionalInitializer(initializer_name="target", order_index=0)
        second = AdditionalInitializer(initializer_name="target", order_index=1)

        sqlite_instance.add_additional_initializer(initializer=first)
        sqlite_instance.add_additional_initializer(initializer=second)

        assert sqlite_instance.get_additional_initializers() == [first, second]

    def test_get_additional_initializers_orders_by_order_index_then_id(self, sqlite_instance) -> None:
        second = AdditionalInitializer(initializer_name="target", order_index=5)
        first = AdditionalInitializer(initializer_name="scorer", order_index=2)

        sqlite_instance.add_additional_initializer(initializer=second)
        sqlite_instance.add_additional_initializer(initializer=first)

        assert sqlite_instance.get_additional_initializers() == [first, second]

    def test_delete_additional_initializer_is_idempotent(self, sqlite_instance) -> None:
        initializer = AdditionalInitializer(initializer_name="target")

        sqlite_instance.delete_additional_initializer(initializer_id=initializer.id)
        sqlite_instance.add_additional_initializer(initializer=initializer)
        sqlite_instance.delete_additional_initializer(initializer_id=initializer.id)
        sqlite_instance.delete_additional_initializer(initializer_id=initializer.id)

        assert sqlite_instance.get_additional_initializers() == []
