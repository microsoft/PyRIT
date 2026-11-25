# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import (
    ScenarioRunPlanGroupKind,
    ScorerCandidateVerdict,
    ScorerSelectionEntry,
    ScorerSelectionReport,
    ScorerVerdictStatus,
)
from pyrit.output.scorer_selection.pretty import PrettyScorerSelectionReportPrinter


def _entry(**updates) -> ScorerSelectionEntry:
    fields = {
        "atomic_attack_name": "flip_airt_violence",
        "display_group": "flip",
        "technique": "flip",
        "required_capabilities": ["true_false"],
        "required_evaluation": "harm.violence",
        "candidates": [
            ScorerCandidateVerdict(
                candidate_index=0,
                candidate_label="SubStringScorer [harm.hate]",
                status=ScorerVerdictStatus.REJECTED,
                reason_kind="evaluation",
                reason="is bound to 'harm.hate'; the population requires 'harm.violence'.",
            ),
            ScorerCandidateVerdict(
                candidate_index=1,
                candidate_label="SubStringScorer [harm.violence]",
                status=ScorerVerdictStatus.SELECTED,
            ),
        ],
    }
    return ScorerSelectionEntry(**{**fields, **updates})


async def test_render_async_names_the_selection_its_requirements_and_each_rejection() -> None:
    printer = PrettyScorerSelectionReportPrinter(enable_colors=False)

    output = await printer.render_async(ScorerSelectionReport(entries=[_entry()]))

    assert "1 atomic group(s) selected from a pool." in output
    assert "flip_airt_violence: SubStringScorer [harm.violence]" in output
    assert "group flip; technique flip; requires true_false, evaluation 'harm.violence'" in output
    assert (
        "rejected #0 SubStringScorer [harm.hate] (evaluation): "
        "is bound to 'harm.hate'; the population requires 'harm.violence'."
    ) in output


async def test_render_async_marks_baselines_and_groups_without_a_technique() -> None:
    printer = PrettyScorerSelectionReportPrinter(enable_colors=False)
    entry = _entry(
        atomic_attack_name="baseline_airt_violence",
        display_group="baseline_airt_violence",
        technique=None,
        group_kind=ScenarioRunPlanGroupKind.BASELINE,
        required_capabilities=[],
        required_evaluation=None,
    )

    output = await printer.render_async(ScorerSelectionReport(entries=[entry]))

    assert "baseline_airt_violence (baseline): SubStringScorer [harm.violence]" in output
    assert "group baseline_airt_violence; no requirements" in output
    assert "technique" not in output


async def test_render_async_says_when_nothing_was_selected_from_a_pool() -> None:
    printer = PrettyScorerSelectionReportPrinter(enable_colors=False)

    output = await printer.render_async(ScorerSelectionReport())

    assert "No atomic group selected its scorer from a pool." in output
