# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Generic attribution metadata an orchestrator stamps onto an
``AttackContext`` so the persisted ``AttackResult`` carries linkage back to
whatever produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models import AtomicAttackEvaluationIdentifier, AtomicAttackIdentifier

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface
    from pyrit.models import AttackResult, AttackSeedGroup, ComponentIdentifier


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
        seed_group_id (str | None): Optional logical seed-group fingerprint for
            per-task progress attribution.
        attempt_index (int | None): Optional 1-based position of this result among the
            children of an orchestration parent. Persisted into
            ``AttackResultEntry.attribution_data``. ``SequentialAttack`` sets it for each
            child attack it dispatches, e.g. ``2`` for the second child.
    """

    parent_id: str
    parent_collection: str
    parent_eval_hash: str | None = None
    seed_group_id: str | None = None
    attempt_index: int | None = None


async def enrich_attack_result_identifier_async(
    *,
    result: AttackResult,
    technique_identifier: ComponentIdentifier,
    seed_group: AttackSeedGroup,
    memory: MemoryInterface,
) -> None:
    """
    Enrich a result with its packaged technique and source seeds.

    Results with an ID are updated in memory before their in-process identity is changed.

    Args:
        result (AttackResult): The result to enrich.
        technique_identifier (ComponentIdentifier): The configured technique identity.
        seed_group (AttackSeedGroup): Source seeds, before technique seeds are merged.
        memory (MemoryInterface): Memory holding the result.
    """
    identifier = AtomicAttackIdentifier.build(
        technique_identifier=technique_identifier,
        seed_group=seed_group,
    )
    identifier = identifier.with_eval_hash(AtomicAttackEvaluationIdentifier(identifier).eval_hash)
    if result.attack_result_id:
        await memory.update_attack_result_by_id_async(
            attack_result_id=result.attack_result_id,
            update_fields={"atomic_attack_identifier": identifier.model_dump()},
        )
    result.atomic_attack_identifier = identifier
