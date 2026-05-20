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

    All fields are opaque to the attack layer; the orchestrator chooses what
    they mean. Together, ``(parent_collection, parent_eval_hash)`` form the
    "what was running" key the orchestrator can use to scope per-parent
    work on resume. Two ``AtomicAttack`` instances sharing a name but using
    different techniques (e.g. base64 vs hex encoders) get distinct
    ``parent_eval_hash`` values, so resume bookkeeping never cross-pollinates.

    Attributes:
        parent_id (str): The ID of the parent entity. Persisted to
            ``AttackResultEntry.attribution_parent_id`` (foreign key to
            ``ScenarioResultEntries.id``) so per-parent loading is indexed.
            ``Scenario`` sets this to the scenario result UUID, e.g.
            ``self._scenario_result_id``
            (``"8a8d17b9-b671-4a3d-8170-e65ea9b44053"``).
        parent_collection (str): Free-form label naming the per-parent
            collection this result belongs to. Persisted into
            ``AttackResultEntry.attribution_data``. ``Scenario`` sets this
            to the atomic attack name, e.g. ``self.atomic_attack_name``
            (``"encoded_jailbreaks"``).
        parent_eval_hash (str | None): Optional content-addressed hash that
            disambiguates configurations sharing the same
            ``parent_collection``. Persisted into
            ``AttackResultEntry.attribution_data``. ``Scenario`` sets this
            to the atomic attack's technique evaluation hash, e.g.
            ``self.technique_eval_hash`` (computed via
            ``AtomicAttackEvaluationIdentifier``).
    """

    parent_id: str
    parent_collection: str
    parent_eval_hash: str | None = None
