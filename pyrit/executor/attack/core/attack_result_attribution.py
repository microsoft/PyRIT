# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Generic attribution metadata an orchestrator stamps onto an
``AttackContext`` so the persisted ``AttackResult`` carries linkage back to
whatever produced it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttackResultAttribution:
    """
    Attribution copied onto an ``AttackResult`` by the persistence path so the
    DB row records its lineage.

    Both fields are opaque to the attack layer; the orchestrator chooses what
    they mean. ``Scenario`` uses ``parent_id`` for the scenario result UUID and
    ``parent_collection`` for the atomic attack name.

    Attributes:
        parent_id (str): The ID of the parent entity. Persisted to
            ``AttackResultEntry.attribution_parent_id`` (foreign key to
            ``ScenarioResultEntries.id``) so per-parent hydration is indexed.
        parent_collection (str): Free-form label naming the per-parent
            collection this result belongs to. Persisted into
            ``AttackResultEntry.attribution_data``.
    """

    parent_id: str
    parent_collection: str
