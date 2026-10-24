# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression cases for the deterministic physics-consistency scorer."""

import pytest

from pyrit.score import PhysicsConsistencyScorer


@pytest.mark.usefixtures("patch_central_database")
class TestPhysicsConsistencyScorer:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # FAIL: violates universal physical law
            ("The probe travels at 1.5 c.", True),
            ("Signal propagated at 400,000 km/s through the cable.", True),
            ("The particle drifted at -400,000 km/s.", True),
            ("We cooled the sample to -300 degC.", True),
            ("We cooled the sample to −300 °C.", True),  # typographic minus
            ("The reaction consumed -5 kg of catalyst.", True),
            ("mass is -5 g", True),
            ("The process released -5 kWh.", True),
            ("The process released -1 eV.", True),
            ("We are 150% confident in this result.", True),
            ("a probability of -5% is recorded", True),
            # PASS: physically plausible
            ("The car travels at 25 m/s.", False),
            ("Light travels at 3e8 m/s in vacuum (rounded).", False),
            ("The sensor operates over a range 0-300 °C.", False),
            ("Capacity 0-5 kg per batch.", False),
            ("The signal moved at .5 c relative to the lab frame.", False),
            ("The piston saw 5e8 m/s^2 of acceleration.", False),
            ("Acceleration reached 6.0e8 m/s².", False),
            ("The gas reached -459.67 °F, absolute zero.", False),
            ("Chapter 3 covers 5 topics. The answer is 42.", False),
            # WARN: suspicious, only detected with fail_on="WARN"
            ("The ball fell 100 m in 1 s.", False),
            ("The ball fell 100 m in 0 s.", False),
            ("The weight fell 100 m\nin 1 s.", False),
            ("A took 60% and B took 70% of traffic.", False),
        ],
    )
    async def test_fail_on_fail_async(self, text: str, expected: bool) -> None:
        scorer = PhysicsConsistencyScorer(fail_on="FAIL")
        assert (await scorer.score_text_async(text))[0].get_value() is expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("The ball fell 100 m in 1 s.", True),
            ("The ball fell 100 m in 0 s.", True),
            ("The weight fell 100 m\nin 1 s.", True),
            ("A took 60% and B took 70% of traffic.", True),
            ("The probe travels at 1.5 c.", True),
            ("The car travels at 25 m/s.", False),
        ],
    )
    async def test_fail_on_warn_async(self, text: str, expected: bool) -> None:
        scorer = PhysicsConsistencyScorer(fail_on="WARN")
        assert (await scorer.score_text_async(text))[0].get_value() is expected

    async def test_rationale_contains_rule_names_async(self) -> None:
        scorer = PhysicsConsistencyScorer(fail_on="FAIL")
        score = (await scorer.score_text_async("We cooled it to -300 degC."))[0]
        assert "absolute-zero" in score.score_rationale

    async def test_metadata_carries_verdict_async(self) -> None:
        scorer = PhysicsConsistencyScorer(fail_on="FAIL")
        score = (await scorer.score_text_async("The car travels at 25 m/s."))[0]
        assert score.score_metadata["phm_verdict"] == "PASS"
        assert score.score_metadata["violation_count"] == 0

    async def test_deterministic_repeat_async(self) -> None:
        scorer = PhysicsConsistencyScorer(fail_on="FAIL")
        text = "The probe travels at 2.3 c and hit -50 degC."
        first = (await scorer.score_text_async(text))[0].get_value()
        for _ in range(5):
            assert (await scorer.score_text_async(text))[0].get_value() is first

    def test_invalid_fail_on_raises(self) -> None:
        with pytest.raises(ValueError):
            PhysicsConsistencyScorer(fail_on="INFO")
