# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Generic attribution metadata that an upstream orchestrator can stamp onto an
``AttackContext`` so the persisted ``AttackResult`` carries the linkage back
to whatever produced it.

The attack layer treats this as opaque infrastructure — three string-typed
fields, no scenario semantics. The orchestrator (e.g. ``Scenario``) interprets
them however it likes. Keeping the type in ``executor`` rather than
``scenario`` means the persistence path has no dependency on the
``pyrit.scenario`` package.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttackResultAttribution:
    """
    Attribution stamped onto an ``AttackContext`` by an upstream caller (the
    ``AttackExecutor`` populates it via an ``attribution_factory``) and copied
    onto the resulting ``AttackResult`` by the attack persistence path so the
    DB row records its lineage.

    Both fields are opaque to the attack layer. The orchestrator chooses
    what they mean and how to query them back later. For example,
    ``Scenario`` uses ``parent_id`` for the scenario result UUID and
    ``parent_collection`` for the atomic attack name. Result identity within
    a collection is reconstructed from the row's own ``objective_sha256``
    rather than a positional index, so resume is stable under reordering or
    re-sampling.

    Attributes:
        parent_id (str): The ID of the parent entity that owns this attack
            execution. Persisted to ``AttackResultEntry.attribution_parent_id``
            and indexed (with a foreign key to ``ScenarioResultEntries.id``)
            so per-parent hydration and resume lookups are direct.
        parent_collection (str): A free-form label naming the per-parent
            collection this result belongs to. Persisted into
            ``AttackResultEntry.attribution_data``.
    """

    parent_id: str
    parent_collection: str
