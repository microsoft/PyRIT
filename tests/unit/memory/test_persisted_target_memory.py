# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.memory.memory_models import PersistedTargetEntry
from pyrit.models import PersistedTarget


def test_persisted_target_round_trips_without_api_key(sqlite_instance) -> None:
    target = PersistedTarget(
        target_registry_name="OpenAIChatTarget-abc123",
        target_type="OpenAIChatTarget",
        parameters={
            "endpoint": "https://example.openai.azure.com",
            "model_name": "gpt-4o",
        },
        secret_uri="https://targets.vault.azure.net/secrets/pyrit-target-secret",
    )

    sqlite_instance.add_persisted_target(target=target)

    entries = sqlite_instance._query_entries(PersistedTargetEntry)
    assert len(entries) == 1
    assert entries[0].to_domain_model() == target
    assert sqlite_instance.get_persisted_targets() == [target]
    assert "api_key" not in entries[0].parameters


def test_add_persisted_target_upserts_by_id(sqlite_instance) -> None:
    target = PersistedTarget(
        id="target-id",
        target_registry_name="target-name",
        target_type="TextTarget",
    )
    sqlite_instance.add_persisted_target(target=target)

    updated = target.model_copy(update={"parameters": {"value": "updated"}})
    sqlite_instance.add_persisted_target(target=updated)

    assert sqlite_instance.get_persisted_targets() == [updated]
