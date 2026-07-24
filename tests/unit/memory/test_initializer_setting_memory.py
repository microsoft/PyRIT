# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.memory.memory_models import InitializerSettingEntry
from pyrit.models import InitializerSetting


@pytest.mark.usefixtures("patch_central_database")
class TestInitializerSettingMemory:
    def test_initializer_setting_entry_round_trips_real_domain_model(self, sqlite_instance) -> None:
        setting = InitializerSetting(
            initializer_name="target",
            enabled=False,
            parameters={"tags": ["default"]},
            order_index=3,
        )

        sqlite_instance.add_initializer_setting(setting=setting)

        entries = sqlite_instance._query_entries(InitializerSettingEntry)

        assert len(entries) == 1
        assert entries[0].to_domain_model() == setting
        assert sqlite_instance.get_initializer_settings() == [setting]

    def test_add_initializer_setting_uses_upsert_semantics(self, sqlite_instance) -> None:
        sqlite_instance.add_initializer_setting(
            setting=InitializerSetting(initializer_name="target", enabled=True, order_index=1)
        )

        sqlite_instance.add_initializer_setting(
            setting=InitializerSetting(initializer_name="target", enabled=False, order_index=4)
        )

        assert sqlite_instance.get_initializer_settings() == [
            InitializerSetting(initializer_name="target", enabled=False, order_index=4)
        ]

    def test_delete_initializer_setting_is_idempotent(self, sqlite_instance) -> None:
        sqlite_instance.delete_initializer_setting(initializer_name="target")

        sqlite_instance.add_initializer_setting(setting=InitializerSetting(initializer_name="target", enabled=True))
        sqlite_instance.delete_initializer_setting(initializer_name="target")
        sqlite_instance.delete_initializer_setting(initializer_name="target")

        assert sqlite_instance.get_initializer_settings() == []
