# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from pydantic import ValidationError

from pyrit.models import GCGSeedMetadata, SeedObjective


def test_gcg_seed_metadata_round_trip() -> None:
    prefix = GCGSeedMetadata(target_prefix="  Supplied prefix  ")
    seed = SeedObjective(value="objective", metadata={"unrelated": "retained", **prefix.to_metadata()})
    restored = SeedObjective.model_validate_json(seed.model_dump_json())

    assert GCGSeedMetadata.from_metadata(metadata=restored.metadata) == prefix
    assert restored.metadata["unrelated"] == "retained"
    assert json.loads(json.dumps(prefix.to_metadata())) == {
        "pyrit_gcg": {"target_prefix": "  Supplied prefix  "},
    }


def test_gcg_seed_metadata_absent() -> None:
    assert GCGSeedMetadata.from_metadata(metadata={"completion": "external prefix"}) is None


@pytest.mark.parametrize("payload", [{}, {"target_prefix": None}, {"target_prefix": ""}, {"target_prefix": " \n "}])
def test_gcg_seed_metadata_normalizes_missing_prefix(payload: dict[str, str | None]) -> None:
    metadata = GCGSeedMetadata.from_metadata(metadata={GCGSeedMetadata.METADATA_KEY: payload})

    assert metadata is not None
    assert metadata.target_prefix is None


@pytest.mark.parametrize(
    "payload",
    [None, "prefix", [], {"target_prefix": False}, {"target_prefix": 1}, {"target_prefix": []}, {"extra": "prefix"}],
)
def test_gcg_seed_metadata_rejects_malformed_payload(payload: object) -> None:
    with pytest.raises(ValidationError):
        GCGSeedMetadata.from_metadata(metadata={GCGSeedMetadata.METADATA_KEY: payload})
