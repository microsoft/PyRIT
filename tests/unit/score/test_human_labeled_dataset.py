# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.models import Message, MessagePiece
from pyrit.score import (
    HarmHumanLabeledEntry,
    HumanLabeledDataset,
    MetricsType,
    ObjectiveHumanLabeledEntry,
)


@pytest.fixture
def sample_messages():
    return [
        Message(
            message_pieces=[
                MessagePiece(role="assistant", original_value="test response", original_value_data_type="text")
            ]
        )
    ]


# =============================================================================
# HarmHumanLabeledEntry Tests
# =============================================================================


def test_harm_human_labeled_entry_valid(sample_messages):
    entry = HarmHumanLabeledEntry(sample_messages, [0.0, 0.5, 1.0], "hate_speech")
    assert entry.harm_category == "hate_speech"
    assert entry.human_scores == [0.0, 0.5, 1.0]


def test_harm_human_labeled_entry_invalid_score_too_low(sample_messages):
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        HarmHumanLabeledEntry(sample_messages, [-0.1, 0.5], "hate_speech")


def test_harm_human_labeled_entry_invalid_score_too_high(sample_messages):
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        HarmHumanLabeledEntry(sample_messages, [0.5, 1.1], "hate_speech")


def test_harm_human_labeled_entry_empty_harm_category(sample_messages):
    with pytest.raises(ValueError, match="harm_category must not be None or empty"):
        HarmHumanLabeledEntry(sample_messages, [0.5], "")


def test_harm_human_labeled_entry_empty_conversation():
    with pytest.raises(ValueError, match="conversation must not be None or empty"):
        HarmHumanLabeledEntry([], [0.5], "hate_speech")


def test_harm_human_labeled_entry_empty_human_scores(sample_messages):
    with pytest.raises(ValueError, match="human_scores must not be None or empty"):
        HarmHumanLabeledEntry(sample_messages, [], "hate_speech")


# =============================================================================
# ObjectiveHumanLabeledEntry Tests
# =============================================================================


def test_objective_human_labeled_entry_valid(sample_messages):
    entry = ObjectiveHumanLabeledEntry(sample_messages, [True, False], "Was this a refusal?")
    assert entry.objective == "Was this a refusal?"
    assert entry.human_scores == [True, False]


def test_objective_human_labeled_entry_empty_objective(sample_messages):
    with pytest.raises(ValueError, match="objective must not be None or empty"):
        ObjectiveHumanLabeledEntry(sample_messages, [True], "")


def test_objective_human_labeled_entry_whitespace_objective(sample_messages):
    with pytest.raises(ValueError, match="objective must not be None or empty"):
        ObjectiveHumanLabeledEntry(sample_messages, [True], "   ")


# =============================================================================
# HumanLabeledDataset Init Tests
# =============================================================================


def test_human_labeled_dataset_init_harm_type(sample_messages):
    entry = HarmHumanLabeledEntry(sample_messages, [0.1, 0.2], "hate_speech")
    dataset = HumanLabeledDataset(name="hate_speech", entries=[entry], metrics_type=MetricsType.HARM, version="1.0")
    assert dataset.name == "hate_speech"
    assert dataset.metrics_type == MetricsType.HARM
    assert len(dataset.entries) == 1
    assert isinstance(dataset.entries[0], HarmHumanLabeledEntry)


def test_human_labeled_dataset_init_objective_type(sample_messages):
    entry = ObjectiveHumanLabeledEntry(sample_messages, [True, False], "objective")
    dataset = HumanLabeledDataset(
        name="sample_objective", entries=[entry], metrics_type=MetricsType.OBJECTIVE, version="1.0"
    )
    assert dataset.name == "sample_objective"
    assert dataset.metrics_type == MetricsType.OBJECTIVE
    assert len(dataset.entries) == 1
    assert isinstance(dataset.entries[0], ObjectiveHumanLabeledEntry)


def test_human_labeled_dataset_version_required(sample_messages):
    entry = HarmHumanLabeledEntry(sample_messages, [0.1], "hate_speech")
    with pytest.raises(TypeError):
        HumanLabeledDataset(name="test", entries=[entry], metrics_type=MetricsType.HARM)


def test_human_labeled_dataset_version_set(sample_messages):
    entry = HarmHumanLabeledEntry(sample_messages, [0.1], "hate_speech")
    dataset = HumanLabeledDataset(name="test", entries=[entry], metrics_type=MetricsType.HARM, version="2.5")
    assert dataset.version == "2.5"


def test_human_labeled_dataset_empty_name_raises(sample_messages):
    entry = ObjectiveHumanLabeledEntry(sample_messages, [True, False], "objective")
    with pytest.raises(ValueError, match="Dataset name cannot be an empty string"):
        HumanLabeledDataset(name="", entries=[entry], metrics_type=MetricsType.OBJECTIVE, version="1.0")


# =============================================================================
# HumanLabeledDataset.validate() Tests
# =============================================================================


def test_validate_harm_dataset_with_objective_entry_raises(sample_messages):
    """Validate raises when HARM dataset contains ObjectiveHumanLabeledEntry."""
    entry = ObjectiveHumanLabeledEntry(sample_messages, [True], "objective")
    dataset = HumanLabeledDataset(
        name="test",
        entries=[entry],
        metrics_type=MetricsType.HARM,
        version="1.0",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    with pytest.raises(ValueError, match="not a HarmHumanLabeledEntry"):
        dataset.validate()


def test_validate_objective_dataset_with_harm_entry_raises(sample_messages):
    """Validate raises when OBJECTIVE dataset contains HarmHumanLabeledEntry."""
    entry = HarmHumanLabeledEntry(sample_messages, [0.5], "hate_speech")
    dataset = HumanLabeledDataset(name="test", entries=[entry], metrics_type=MetricsType.OBJECTIVE, version="1.0")
    with pytest.raises(ValueError, match="not an ObjectiveHumanLabeledEntry"):
        dataset.validate()


def test_validate_harm_dataset_multiple_harm_categories_raises(sample_messages):
    """Validate raises when HARM dataset has entries with different harm categories."""
    entry1 = HarmHumanLabeledEntry(sample_messages, [0.1], "hate_speech")
    entry2 = HarmHumanLabeledEntry(sample_messages, [0.2], "violence")
    dataset = HumanLabeledDataset(
        name="mixed",
        entries=[entry1, entry2],
        metrics_type=MetricsType.HARM,
        version="1.0",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    with pytest.raises(ValueError, match="multiple harm categories"):
        dataset.validate()


def test_validate_harm_dataset_missing_harm_definition_raises(sample_messages):
    """Validate raises when HARM dataset is missing harm_definition."""
    entry = HarmHumanLabeledEntry(sample_messages, [0.1], "hate_speech")
    dataset = HumanLabeledDataset(
        name="hate_speech",
        entries=[entry],
        metrics_type=MetricsType.HARM,
        version="1.0",
        harm_definition_version="1.0",
    )
    with pytest.raises(ValueError, match="harm_definition and harm_definition_version must be specified"):
        dataset.validate()


def test_validate_harm_dataset_missing_harm_definition_version_raises(sample_messages):
    """Validate raises when HARM dataset is missing harm_definition_version."""
    entry = HarmHumanLabeledEntry(sample_messages, [0.1], "hate_speech")
    dataset = HumanLabeledDataset(
        name="hate_speech",
        entries=[entry],
        metrics_type=MetricsType.HARM,
        version="1.0",
        harm_definition="hate_speech.yaml",
    )
    with pytest.raises(ValueError, match="harm_definition_version must be specified"):
        dataset.validate()


def test_validate_harm_dataset_same_harm_category_succeeds(sample_messages):
    """Validate succeeds when all HARM entries have same harm category."""
    entry1 = HarmHumanLabeledEntry(sample_messages, [0.1], "hate_speech")
    entry2 = HarmHumanLabeledEntry(sample_messages, [0.2], "hate_speech")
    dataset = HumanLabeledDataset(
        name="hate_speech",
        entries=[entry1, entry2],
        metrics_type=MetricsType.HARM,
        version="1.0",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    # Should not raise
    dataset.validate()


def test_validate_objective_dataset_different_objectives_succeeds(sample_messages):
    """Validate succeeds when OBJECTIVE entries have different objectives."""
    entry1 = ObjectiveHumanLabeledEntry(sample_messages, [True], "Was this a refusal?")
    entry2 = ObjectiveHumanLabeledEntry(sample_messages, [False], "Did it provide instructions?")
    dataset = HumanLabeledDataset(
        name="mixed_objectives", entries=[entry1, entry2], metrics_type=MetricsType.OBJECTIVE, version="1.0"
    )
    # Should not raise - different objectives are allowed
    dataset.validate()


def test_validate_empty_dataset_succeeds(sample_messages):
    """Validate succeeds for empty dataset (no entries means no harm_definition needed)."""
    dataset = HumanLabeledDataset(name="empty", entries=[], metrics_type=MetricsType.HARM, version="1.0")
    # Should not raise - empty datasets skip validation
    dataset.validate()


# =============================================================================
# HumanLabeledDataset._validate_csv_columns() Tests
# =============================================================================


def test_validate_csv_columns_harm_requires_harm_category():
    """Harm datasets require harm_category column."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "assistant_response": ["response"],
            "human_score": [0.5],
            "objective": ["some objective"],  # Has objective but not harm_category
        }
    )
    with pytest.raises(ValueError, match="'harm_category' is missing"):
        HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.HARM)


def test_validate_csv_columns_objective_requires_objective():
    """Objective datasets require objective column."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "assistant_response": ["response"],
            "human_score": [1],
            "harm_category": ["hate_speech"],  # Has harm_category but not objective
        }
    )
    with pytest.raises(ValueError, match="'objective' is missing"):
        HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.OBJECTIVE)


def test_validate_csv_columns_missing_assistant_response():
    """Both dataset types require assistant_response column."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "human_score": [0.5],
            "harm_category": ["hate_speech"],
        }
    )
    with pytest.raises(ValueError, match="'assistant_response' is missing"):
        HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.HARM)


def test_validate_csv_columns_missing_human_score():
    """Both dataset types require at least one human_score column."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "assistant_response": ["response"],
            "harm_category": ["hate_speech"],
        }
    )
    with pytest.raises(ValueError, match="No human score columns found"):
        HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.HARM)


def test_validate_csv_columns_nan_in_human_score():
    """Human score columns cannot have NaN values."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "assistant_response": ["response"],
            "human_score": [None],
            "harm_category": ["hate_speech"],
        }
    )
    with pytest.raises(ValueError, match="All human score cells must be filled."):
        HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.HARM)


def test_validate_csv_columns_duplicate_columns():
    """Column names must be unique."""
    import pandas as pd

    # Create DataFrame with duplicate column names
    df = pd.DataFrame([[1, 2, 3, "a"]], columns=["col", "col", "human_score", "harm_category"])
    with pytest.raises(ValueError, match="must be unique"):
        HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.HARM)


def test_validate_csv_columns_valid_harm_dataset():
    """Valid harm dataset passes validation."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "assistant_response": ["response"],
            "human_score_1": [0.5],
            "human_score_2": [0.7],
            "harm_category": ["hate_speech"],
        }
    )
    # Should not raise
    HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.HARM)


def test_validate_csv_columns_valid_objective_dataset():
    """Valid objective dataset passes validation."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "assistant_response": ["response"],
            "human_score": [1],
            "objective": ["Was this a refusal?"],
        }
    )
    # Should not raise
    HumanLabeledDataset._validate_csv_columns(eval_df=df, metrics_type=MetricsType.OBJECTIVE)


# =============================================================================
# HumanLabeledDataset._construct_harm_entry() Tests
# =============================================================================


def test_construct_harm_entry(sample_messages):
    entry = HumanLabeledDataset._construct_harm_entry(
        messages=sample_messages, harm="hate_speech", human_scores=["0.1", 0.2]
    )
    assert isinstance(entry, HarmHumanLabeledEntry)
    assert entry.harm_category == "hate_speech"
    assert entry.human_scores == [0.1, 0.2]


def test_construct_harm_entry_converts_strings_to_floats(sample_messages):
    entry = HumanLabeledDataset._construct_harm_entry(
        messages=sample_messages, harm="violence", human_scores=["0.5", "0.75"]
    )
    assert entry.human_scores == [0.5, 0.75]
    assert all(isinstance(s, float) for s in entry.human_scores)


# =============================================================================
# HumanLabeledDataset._construct_objective_entry() Tests
# =============================================================================


def test_construct_objective_entry(sample_messages):
    entry = HumanLabeledDataset._construct_objective_entry(
        messages=sample_messages, objective="Was this a refusal?", human_scores=["1", "0"]
    )
    assert isinstance(entry, ObjectiveHumanLabeledEntry)
    assert entry.objective == "Was this a refusal?"
    assert entry.human_scores == [True, False]


def test_construct_objective_entry_converts_strings_to_bools(sample_messages):
    entry = HumanLabeledDataset._construct_objective_entry(
        messages=sample_messages, objective="obj", human_scores=["1", "0", "1"]
    )
    assert entry.human_scores == [True, False, True]
    assert all(isinstance(s, bool) for s in entry.human_scores)


# =============================================================================
# HumanLabeledDataset.from_csv() Tests
# =============================================================================


def test_from_csv_harm_dataset():
    """Load a harm dataset from the standard mini_hate_speech.csv."""
    csv_path = SCORER_EVALS_PATH / "harm" / "mini_hate_speech.csv"
    dataset = HumanLabeledDataset.from_csv(
        csv_path=csv_path,
        metrics_type=MetricsType.HARM,
    )
    assert isinstance(dataset, HumanLabeledDataset)
    assert dataset.metrics_type == MetricsType.HARM
    assert dataset.name == "mini_hate_speech"
    assert len(dataset.entries) > 0
    assert all(isinstance(e, HarmHumanLabeledEntry) for e in dataset.entries)
    # All entries should have the same harm category
    assert all(e.harm_category == "hate_speech" for e in dataset.entries)


def test_from_csv_objective_dataset():
    """Load an objective dataset from the standard mini_refusal.csv."""
    csv_path = SCORER_EVALS_PATH / "sample" / "mini_refusal.csv"
    dataset = HumanLabeledDataset.from_csv(
        csv_path=csv_path,
        metrics_type=MetricsType.OBJECTIVE,
    )
    assert isinstance(dataset, HumanLabeledDataset)
    assert dataset.metrics_type == MetricsType.OBJECTIVE
    assert dataset.name == "mini_refusal"
    assert len(dataset.entries) > 0
    assert all(isinstance(e, ObjectiveHumanLabeledEntry) for e in dataset.entries)


def test_from_csv_dataset_version_from_comment(tmp_path):
    """Dataset version can be read from # comment line in CSV."""
    csv_file = tmp_path / "versioned.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=2.3\n")
        f.write("assistant_response,human_score,harm_category\n")
        f.write("response1,0.5,hate_speech\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
    )
    assert dataset.version == "2.3"


def test_from_csv_version_from_parameter_overrides_comment(tmp_path):
    """Version parameter overrides version in CSV comment."""
    csv_file = tmp_path / "versioned.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=2.3\n")
        f.write("assistant_response,human_score,harm_category\n")
        f.write("response1,0.5,hate_speech\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
        version="5.0",
    )
    assert dataset.version == "5.0"


def test_from_csv_no_version_raises(tmp_path):
    """Raises if no version provided and none in CSV."""
    csv_file = tmp_path / "no_version.csv"
    with open(csv_file, "w") as f:
        f.write("assistant_response,human_score,harm_category\n")
        f.write("response1,0.5,hate_speech\n")

    with pytest.raises(ValueError, match="Version not specified"):
        HumanLabeledDataset.from_csv(
            csv_path=str(csv_file),
            metrics_type=MetricsType.HARM,
        )


def test_from_csv_harm_definition_from_comment(tmp_path):
    """harm_definition can be read from # comment line in CSV."""
    csv_file = tmp_path / "with_harm_def.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=1.0, harm_definition=hate_speech.yaml\n")
        f.write("assistant_response,human_score,harm_category\n")
        f.write("response1,0.5,hate_speech\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
    )
    assert dataset.version == "1.0"
    assert dataset.harm_definition == "hate_speech.yaml"


def test_from_csv_harm_definition_from_parameter_overrides_comment(tmp_path):
    """harm_definition parameter overrides harm_definition in CSV comment."""
    csv_file = tmp_path / "with_harm_def.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=1.0, harm_definition=hate_speech.yaml\n")
        f.write("assistant_response,human_score,harm_category\n")
        f.write("response1,0.5,hate_speech\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
        harm_definition="custom/path.yaml",
    )
    assert dataset.harm_definition == "custom/path.yaml"


def test_from_csv_custom_dataset_name(tmp_path):
    """Dataset name can be overridden with parameter."""
    csv_file = tmp_path / "sample.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=1.0\n")
        f.write("assistant_response,human_score,harm_category\n")
        f.write("response1,0.5,hate_speech\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
        dataset_name="my_custom_name",
    )
    assert dataset.name == "my_custom_name"


def test_from_csv_with_data_type_column(tmp_path):
    """data_type column is respected when present."""
    csv_file = tmp_path / "with_data_type.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=1.0\n")
        f.write("assistant_response,human_score,harm_category,data_type\n")
        f.write("response1,0.5,hate_speech,text\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
    )
    assert dataset.entries[0].conversation[0].message_pieces[0].original_value_data_type == "text"


def test_from_csv_multiple_human_raters(tmp_path):
    """Multiple human_score columns are all captured."""
    csv_file = tmp_path / "multi_rater.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=1.0\n")
        f.write("assistant_response,human_score_1,human_score_2,human_score_3,harm_category\n")
        f.write("response1,0.1,0.2,0.3,hate_speech\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
    )
    assert len(dataset.entries) == 1
    assert dataset.entries[0].human_scores == [0.1, 0.2, 0.3]


def test_from_csv_harm_dataset_ignores_objective_column(tmp_path):
    """Harm dataset reads from harm_category, not objective column."""
    csv_file = tmp_path / "harm_with_objective.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=1.0, harm_definition=hate_speech.yaml, harm_definition_version=1.0\n")
        f.write("assistant_response,human_score,harm_category,objective\n")
        f.write("response1,0.5,hate_speech,Write hate speech\n")
        f.write("response2,0.3,hate_speech,Write more hate speech\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.HARM,
    )
    # Should use harm_category (all same), not objective (different)
    assert all(e.harm_category == "hate_speech" for e in dataset.entries)
    # Validate should pass since all harm categories are the same
    dataset.validate()


def test_from_csv_objective_dataset_ignores_harm_category_column(tmp_path):
    """Objective dataset reads from objective, not harm_category column."""
    csv_file = tmp_path / "objective_with_harm.csv"
    with open(csv_file, "w") as f:
        f.write("# dataset_version=1.0\n")
        f.write("assistant_response,human_score,objective,harm_category\n")
        f.write("response1,1,Was this a refusal?,hate_speech\n")
        f.write("response2,0,Did it comply?,violence\n")

    dataset = HumanLabeledDataset.from_csv(
        csv_path=str(csv_file),
        metrics_type=MetricsType.OBJECTIVE,
    )
    # Should use objective column
    assert dataset.entries[0].objective == "Was this a refusal?"
    assert dataset.entries[1].objective == "Did it comply?"


# =============================================================================
# CSV File Data Quality Validation Tests
# =============================================================================

# Mapping of directory names to MetricsType for validation
# Note: 'sample' directory is excluded as it contains test fixtures, not production eval data
_DIRECTORY_METRICS_TYPE = {
    "harm": MetricsType.HARM,
    "objective": MetricsType.OBJECTIVE,
    "refusal_scorer": MetricsType.OBJECTIVE,
}


def _get_all_csv_files_with_metrics_type():
    """Collect all CSV files from scorer_evals directory with their MetricsType for parametrization."""
    csv_files = []
    for csv_file in SCORER_EVALS_PATH.rglob("*.csv"):
        # Determine the directory name to get the MetricsType
        parent_dir = csv_file.parent.name
        if parent_dir in _DIRECTORY_METRICS_TYPE:
            csv_files.append((csv_file, _DIRECTORY_METRICS_TYPE[parent_dir]))
    return csv_files


@pytest.mark.parametrize(
    "csv_file,metrics_type",
    _get_all_csv_files_with_metrics_type(),
    ids=lambda x: x.relative_to(SCORER_EVALS_PATH).as_posix() if hasattr(x, "relative_to") else str(x),
)
def test_scorer_eval_csv_loads_with_human_labeled_dataset(csv_file, metrics_type):
    """Validate that all scorer_evals CSV files can be loaded by HumanLabeledDataset.from_csv().

    This test ensures data quality by using the actual validation logic in HumanLabeledDataset,
    which checks for required columns, NaN values, and proper data types.
    """
    # This will raise ValueError if there are any data quality issues like:
    # - Missing required columns (assistant_response, human_score, harm_category/objective)
    # - NaN values in any required column
    # - Invalid data formats
    dataset = HumanLabeledDataset.from_csv(csv_path=csv_file, metrics_type=metrics_type)
    assert len(dataset.entries) > 0, f"Dataset {csv_file.name} has no entries"
