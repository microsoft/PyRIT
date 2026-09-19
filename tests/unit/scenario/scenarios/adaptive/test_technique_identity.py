# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.scenario.scenarios.adaptive.technique_identity import (
    AdaptiveTechniqueIdentifier,
    get_history_eval_hash,
)


def test_adaptive_technique_identifier_round_trip() -> None:
    identifier = AdaptiveTechniqueIdentifier(
        factory_hash="factory-hash",
        technique_eval_hash="technique-eval-hash",
    )

    serialized = identifier.serialize()

    assert AdaptiveTechniqueIdentifier.parse(serialized) == identifier
    assert get_history_eval_hash(technique_identifier=serialized) == "technique-eval-hash"


def test_history_eval_hash_falls_back_for_custom_selector_identifier() -> None:
    assert get_history_eval_hash(technique_identifier="custom-arm") == "custom-arm"
