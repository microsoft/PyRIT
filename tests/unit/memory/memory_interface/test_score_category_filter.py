# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from sqlalchemy.dialects import mssql

from pyrit.memory import AzureSQLMemory, MemoryInterface
from pyrit.memory.memory_models import ScoreEntry
from pyrit.models import ComponentIdentifier, Score


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("category", ["harmful_response", "quotes'\"[]", "percent_%", "Bøølean"])
async def test_category_filter_matches_a_complete_array_element(
    sqlite_instance: MemoryInterface, category: str
) -> None:
    identifier = ComponentIdentifier(class_name="CategoryScorer", class_module=__name__)
    matching = Score(
        score_value="true",
        score_type="true_false",
        score_category=["other", category],
        scorer_class_identifier=identifier,
    )
    nonmatching = Score(
        score_value="false",
        score_type="true_false",
        score_category=[f"prefix_{category}_suffix"],
        scorer_class_identifier=identifier,
    )
    await sqlite_instance.add_scores_to_memory_async(scores=[matching, nonmatching])

    scores = await sqlite_instance.get_scores_async(score_category=category.upper())
    assert [score.id for score in scores] == [matching.id]
    assert scores[0].score_category == ["other", category]

    scores_by_id = await sqlite_instance.get_scores_async(
        score_ids=[str(matching.id), str(nonmatching.id)],
        score_category=category,
    )
    assert [score.id for score in scores_by_id] == [matching.id]


def test_category_filter_sql_server_uses_bound_json_membership() -> None:
    memory = object.__new__(AzureSQLMemory)
    condition = memory._get_condition_json_array_match(
        json_column=ScoreEntry.score_category,
        property_path="$",
        array_to_match=["quotes'\"[]"],
    )
    compiled = condition.compile(dialect=mssql.dialect())
    assert "OPENJSON" in str(compiled)
    assert "quotes'\"[]" not in str(compiled)
    assert "quotes'\"[]" in compiled.params.values()
    assert "$" in compiled.params.values()
