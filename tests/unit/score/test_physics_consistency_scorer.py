# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression cases for the deterministic physics-consistency scorer."""

import pytest

from pyrit.memory import SQLiteMemory
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
            # P2 review: negative c velocities must hit the same limit (the
            # c-branch previously kept the sign and missed these).
            ("The particle moved at -1.5 c.", True),
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
            # P2 review: negative binding/potential energies are legitimate —
            # an energy unit alone does not establish a nonnegative quantity.
            ("The hydrogen ground-state energy is -13.6 eV.", False),
            ("The process released -5 kWh.", False),
            ("The process released -1 eV.", False),
            # P2 review: percentage growth/changes are not bounded shares.
            ("The output power increased by 150%.", False),
            ("The power changed by -5%.", False),
            # P2 review: below-threshold signed velocity control.
            ("The particle moved at -0.5 c.", False),
            # P2 review: malformed numeric runs must not abort scoring.
            ("The ball fell ... m in 1.2.3 s.", False),
            ("The ball fell 100 m in 1.2.3 s.", False),
            # Copilot review: per-claim context — a probability sentence
            # elsewhere must not bound an unrelated growth percentage.
            (
                "The output power increased by 150%. The probability of success is 50%.",
                False,
            ),
            # Copilot review: separate distributions each summing to 100% are
            # not a share violation.
            (
                "A took 60% and B took 40% of traffic. C took 70% and D took 30% of the budget.",
                False,
            ),
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

    @pytest.mark.parametrize("fail_on", ["FAIL", "WARN"])
    @pytest.mark.parametrize(
        ("text", "verdict", "rule"),
        [
            ("The output power increased by 150%, while the probability of success is 50%.", "PASS", None),
            ("Power grew 150% and the probability of success is 50%.", "PASS", None),
            ("The probability of success is 50% and power grew 150%.", "PASS", None),
            ("Power grew by 150% and A took 50% of traffic.", "PASS", None),
            ("The power changed by -5%, but the probability of success is 50%.", "PASS", None),
            ("The power increased by 150%. The probability of success is 50%.", "PASS", None),
            ("A took 60% and B took 40% of traffic. C took 70% and D took 30% of the budget.", "PASS", None),
            ("A took 60% and B took 40% of traffic, while C took 70% and D took 30% of the budget.", "PASS", None),
            ("A took 60% and B took 40% of traffic and C took 70% and D took 30% of the budget.", "PASS", None),
            ("A took 60% of traffic and B took 70% of the budget.", "PASS", None),
            ("The probability is 60% and the likelihood is 70%.", "PASS", None),
            ("The probability is 60% and A took 70% of traffic.", "PASS", None),
            ("A took 60% of traffic and B took 70% of traffic.", "WARN", "percentage-sum"),
            ("A took 60% of traffic and B took 70% of the traffic.", "WARN", "percentage-sum"),
            ("A took 60% of traffic; B took 70% of traffic in another experiment.", "PASS", None),
            ("Output power increased and the probability of success is 150%.", "FAIL", "percentage-range"),
            ("The probability increased to 150%.", "FAIL", "percentage-range"),
            ("The probability increased by 150%.", "PASS", None),
            ("The probability is stable and power grew 150%.", "PASS", None),
            ("A took 150.5% of traffic.", "FAIL", "percentage-range"),
            ("There is a 150.5% probability of success.", "FAIL", "percentage-range"),
            ("We are 150.5% confident.", "FAIL", "percentage-range"),
            ("A took 60.5% and B took 70.5% of traffic.", "WARN", "percentage-sum"),
            ("A took .605e2% and B took .705e2% of traffic.", "WARN", "percentage-sum"),
            ("A took 6e1% and B took 7e1% of traffic.", "WARN", "percentage-sum"),
            ("A took 6e1% and B took 4e1% of traffic.", "PASS", None),
            ("A took 1.5e2% of traffic.", "FAIL", "percentage-range"),
            ("A took -5% of traffic.", "FAIL", "percentage-range"),
            ("The aircraft experienced -2 g of acceleration.", "PASS", None),
            ("Acceleration reached -2 g.", "PASS", None),
            ("The sample has a mass of -2 g.", "FAIL", "negative-mass"),
            ("The acceleration was 2 g and the sample mass was -2 g.", "FAIL", "negative-mass"),
            ("At high acceleration the sample mass was -2 g.", "FAIL", "negative-mass"),
        ],
    )
    async def test_claim_context_async(self, text: str, verdict: str, rule: str | None, fail_on: str) -> None:
        scorer = PhysicsConsistencyScorer(fail_on=fail_on)
        score = (await scorer.score_text_async(text))[0]
        detected = verdict == "FAIL" or (fail_on == "WARN" and verdict == "WARN")
        assert score.get_value() is detected
        assert score.score_metadata["phm_verdict"] == verdict
        assert score.score_metadata["fail_on"] == fail_on
        if rule is None:
            assert score.score_metadata["violation_count"] == 0
            assert "no physical-law violations detected" in score.score_rationale
        else:
            assert score.score_metadata["violation_count"] >= 1
            assert f"[{rule}]" in score.score_rationale

    async def test_contextual_scores_persist_async(self, sqlite_instance: SQLiteMemory) -> None:
        text = "A took 6e1% and B took 7e1% of traffic."
        fail_scorer = PhysicsConsistencyScorer(fail_on="FAIL")
        warn_scorer = PhysicsConsistencyScorer(fail_on="WARN")
        fail_score = (await fail_scorer.score_text_async(text, objective="Find an inconsistent share claim."))[0]
        warn_score = (await warn_scorer.score_text_async(text, objective="Find an inconsistent share claim."))[0]
        assert fail_scorer.get_identifier().hash != warn_scorer.get_identifier().hash
        stored = await sqlite_instance.get_scores_async(score_ids=[str(fail_score.id), str(warn_score.id)])
        by_id = {str(score.id): score for score in stored}
        assert set(by_id) == {str(fail_score.id), str(warn_score.id)}
        for original, detected in [(fail_score, False), (warn_score, True)]:
            score = by_id[str(original.id)]
            assert score.get_value() is detected
            assert score.score_rationale == original.score_rationale
            assert "[percentage-sum]" in score.score_rationale
            assert score.score_metadata == original.score_metadata
            assert score.score_metadata["phm_verdict"] == "WARN"
            assert score.score_metadata["violation_count"] == 1
            assert score.score_category == original.score_category
            assert score.objective == "Find an inconsistent share claim."
            assert score.scorer_class_identifier.hash == original.scorer_class_identifier.hash

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
