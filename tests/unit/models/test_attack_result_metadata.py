# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any

import pytest

from pyrit.models import AttackResultMetadata, AttackResultRole


@pytest.mark.parametrize("result_role", list(AttackResultRole))
@pytest.mark.parametrize("attempt_index", [None, 1, 2])
def test_metadata_round_trip(*, result_role: AttackResultRole, attempt_index: int | None) -> None:
    value = AttackResultMetadata(result_role=result_role, attempt_index=attempt_index)

    metadata = json.loads(json.dumps(value.to_metadata()))

    expected: dict[str, Any] = {"result_role": result_role.value}
    if attempt_index is not None:
        expected["attempt_index"] = attempt_index
    assert metadata == expected
    assert AttackResultMetadata.from_metadata(metadata=metadata) == value


@pytest.mark.parametrize("metadata", [None, {}, {"parent_collection": "legacy"}])
def test_from_metadata_without_semantic_fields(metadata: dict[str, Any] | None) -> None:
    assert AttackResultMetadata.from_metadata(metadata=metadata) == AttackResultMetadata()


@pytest.mark.parametrize("role", [None, "future_role", ["orchestration"], {}, 7, True])
def test_from_metadata_unrecognized_role_stays_unknown(role: object) -> None:
    value = AttackResultMetadata.from_metadata(metadata={"result_role": role, "attempt_index": 2})

    assert value.result_role is AttackResultRole.UNKNOWN
    assert value.attempt_index == 2


@pytest.mark.parametrize("attempt_index", [None, 0, -1, True, False, 2.0, "2", [], {}])
def test_from_metadata_invalid_position_stays_absent(attempt_index: object) -> None:
    value = AttackResultMetadata.from_metadata(
        metadata={"result_role": "target_facing", "attempt_index": attempt_index}
    )

    assert value.result_role is AttackResultRole.TARGET_FACING
    assert value.attempt_index is None


def test_metadata_fragment_keeps_parent_linkage_separate() -> None:
    parent = {"parent_collection": "adaptive", "parent_eval_hash": "eval", "seed_group_id": "seed"}
    stored = {
        **parent,
        **AttackResultMetadata(result_role=AttackResultRole.ORCHESTRATION, attempt_index=2).to_metadata(),
    }

    value = AttackResultMetadata.from_metadata(metadata=stored)
    fragment = value.to_metadata()
    fragment["attempt_index"] = 3

    assert value == AttackResultMetadata(result_role=AttackResultRole.ORCHESTRATION, attempt_index=2)
    assert stored == {**parent, "result_role": "orchestration", "attempt_index": 2}
