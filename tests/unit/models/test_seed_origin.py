# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.models import SeedObjective, SeedOrigin


@pytest.mark.parametrize("origin", list(SeedOrigin))
def test_seed_origin_string_contract(origin: SeedOrigin) -> None:
    value = origin.value
    assert isinstance(origin, str)
    assert str(origin) == value
    assert f"{origin}" == value
    assert format(origin) == value
    assert f"{origin:>12}" == f"{value:>12}"
    assert repr(origin) == f"<SeedOrigin.{origin.name}: {value!r}>"
    assert origin == value
    assert hash(origin) == hash(value)
    assert {origin: "seed"}[value] == "seed"
    assert SeedOrigin(value) is origin
    assert json.dumps(origin) == json.dumps(value)


@pytest.mark.parametrize("value", ["local", "remote", "generated", "user", "unknown"])
def test_seed_origin_json_compatibility(value: str) -> None:
    seed = SeedObjective.model_validate_json(json.dumps({"value": "An objective", "origin": value}))
    assert seed.origin is SeedOrigin(value)
    assert seed.model_dump()["origin"] is seed.origin
    assert seed.model_dump(mode="json")["origin"] == value
    assert json.loads(seed.model_dump_json())["origin"] == value
    assert SeedObjective.model_validate_json(seed.model_dump_json()).origin is seed.origin


def test_seed_origin_schema_compatibility() -> None:
    schema = SeedObjective.model_json_schema()
    assert schema["$defs"]["SeedOrigin"]["enum"] == ["local", "remote", "generated", "user", "unknown"]
    assert schema["$defs"]["SeedOrigin"]["type"] == "string"
    assert schema["properties"]["origin"]["default"] == "unknown"
