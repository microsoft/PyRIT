# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for validating CSV schema compliance in scorer evaluation datasets.

These tests ensure all CSV files in the scorer_evals directory follow the
standardized column naming conventions defined in scorer_evaluator.py.
They also validate data integrity (encoding, score ranges, required values)
to catch dataset issues before evals are run.

Test organisation:
- Type-specific classes (TestObjective/Harm/RefusalScorerEvalCSVSchema) contain
  ALL validation for that CSV type: schema, score ranges, metadata, parsability.
- Cross-cutting classes (TestCSVVersionMetadata, TestCSVEncodingValidation,
  TestCSVDataIntegrity) validate properties that apply to every CSV regardless
  of type.
"""

import csv
from pathlib import Path

import pandas as pd
import pytest

from pyrit.common.path import (
    SCORER_EVALS_HARM_PATH,
    SCORER_EVALS_OBJECTIVE_PATH,
    SCORER_EVALS_REFUSAL_SCORER_PATH,
)
from pyrit.models.literals import PromptDataType
from pyrit.score.scorer_evaluation.scorer_evaluator import (
    STANDARD_ASSISTANT_RESPONSE_COL,
    STANDARD_DATA_TYPE_COL,
    STANDARD_HARM_COL,
    STANDARD_HUMAN_LABEL_COL,
    STANDARD_OBJECTIVE_COL,
)

# Valid data_type values derived from PromptDataType literal
VALID_DATA_TYPES = set(PromptDataType.__args__)  # type: ignore[attr-defined]

# Collect all CSV paths once for cross-cutting tests
ALL_CSV_FILES = (
    list(Path(SCORER_EVALS_OBJECTIVE_PATH).glob("*.csv"))
    + list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv"))
    + list(Path(SCORER_EVALS_REFUSAL_SCORER_PATH).glob("*.csv"))
)


def _skip_comment_lines(f) -> None:
    """Advance the file cursor past all leading ``#`` comment lines."""
    while True:
        pos = f.tell()
        line = f.readline()
        if not line.startswith("#"):
            f.seek(pos)
            return


def _read_csv_as_dataframe(csv_file: Path) -> pd.DataFrame:
    """
    Read a CSV file into a DataFrame, skipping the comment/version line.

    This helper mirrors the production code's read logic in
    HumanLabeledDataset.from_csv: try UTF-8 first, fall back to latin-1,
    then drop all-NaN rows (blank separator lines).

    Args:
        csv_file (Path): Path to the CSV file.

    Returns:
        pd.DataFrame: The parsed DataFrame with comment lines skipped.
    """
    try:
        df = pd.read_csv(csv_file, comment="#", encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(csv_file, comment="#", encoding="latin-1")
    # Drop rows where every column is NaN (blank separator rows), mirroring
    # the logic in HumanLabeledDataset.from_csv.
    return df.dropna(how="all").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Type-specific test classes
# ---------------------------------------------------------------------------


class TestObjectiveScorerEvalCSVSchema:
    """Test that all objective scorer evaluation CSVs have the correct schema."""

    @pytest.fixture(scope="class")
    def objective_csv_files(self) -> list[Path]:
        """Get all CSV files in the objective scorer evals directory."""
        return list(Path(SCORER_EVALS_OBJECTIVE_PATH).glob("*.csv"))

    def test_objective_csv_files_exist(self, objective_csv_files: list[Path]) -> None:
        """Verify that objective CSV files exist."""
        assert len(objective_csv_files) > 0, "No objective CSV files found"

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_OBJECTIVE_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_objective_csv_has_required_columns(self, csv_file: Path) -> None:
        """
        Test that each objective CSV has all required columns.

        Required columns for objective scorer evaluation:
        - objective: The objective being evaluated
        - assistant_response: The model's response
        - human_score: The human-labeled ground truth score
        - data_type: The type of data (e.g., "text")
        """
        with open(csv_file, encoding="utf-8") as f:
            _skip_comment_lines(f)

            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])

            required_columns = {
                STANDARD_OBJECTIVE_COL,
                STANDARD_ASSISTANT_RESPONSE_COL,
                STANDARD_HUMAN_LABEL_COL,
                STANDARD_DATA_TYPE_COL,
            }

            missing_columns = required_columns - columns
            assert not missing_columns, (
                f"CSV {csv_file.name} is missing required columns: {missing_columns}. Found columns: {columns}"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_OBJECTIVE_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_objective_csv_column_names_exact(self, csv_file: Path) -> None:
        """
        Test that objective CSVs use only expected column names.

        This ensures no typos or legacy column names remain.
        """
        with open(csv_file, encoding="utf-8") as f:
            _skip_comment_lines(f)

            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])

            # Objective CSVs may have harm_category for reference but it's optional
            allowed_columns = {
                STANDARD_OBJECTIVE_COL,
                STANDARD_ASSISTANT_RESPONSE_COL,
                STANDARD_HUMAN_LABEL_COL,
                STANDARD_DATA_TYPE_COL,
                STANDARD_HARM_COL,  # Optional reference column
            }

            unexpected_columns = columns - allowed_columns
            assert not unexpected_columns, (
                f"CSV {csv_file.name} has unexpected columns: {unexpected_columns}. Allowed columns: {allowed_columns}"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_OBJECTIVE_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_objective_csv_scores_are_binary(self, csv_file: Path) -> None:
        """
        Verify that objective scores are binary (0 or 1).

        ObjectiveHumanLabeledEntry casts scores to bool(int(score)), so only
        0 and 1 are valid. Non-integer values would cause unexpected behavior.
        """
        df = _read_csv_as_dataframe(csv_file)
        human_score_cols = [col for col in df.columns if col.startswith(STANDARD_HUMAN_LABEL_COL)]

        for col in human_score_cols:
            scores = pd.to_numeric(df[col], errors="coerce").dropna()
            invalid = scores[~scores.isin([0, 1])]
            assert invalid.empty, (
                f"CSV {csv_file.name}: column '{col}' has non-binary scores: "
                f"{invalid.tolist()}. Objective scores must be 0 or 1."
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_OBJECTIVE_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_objective_csv_loads_via_from_csv(self, csv_file: Path) -> None:
        """
        Verify that each objective CSV can be parsed by HumanLabeledDataset.from_csv.

        This is the ultimate integration check: if from_csv succeeds, the file
        is guaranteed to work in the evaluation pipeline.
        """
        from pyrit.score.scorer_evaluation.human_labeled_dataset import HumanLabeledDataset
        from pyrit.score.scorer_evaluation.metrics_type import MetricsType

        try:
            dataset = HumanLabeledDataset.from_csv(
                csv_path=csv_file,
                metrics_type=MetricsType.OBJECTIVE,
            )
            assert len(dataset.entries) > 0, f"CSV {csv_file.name} produced no entries"
        except Exception as e:
            pytest.fail(f"CSV {csv_file.name} failed to load via from_csv: {e}")


class TestHarmScorerEvalCSVSchema:
    """Test that all harm scorer evaluation CSVs have the correct schema."""

    @pytest.fixture(scope="class")
    def harm_csv_files(self) -> list[Path]:
        """Get all CSV files in the harm scorer evals directory."""
        return list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv"))

    def test_harm_csv_files_exist(self, harm_csv_files: list[Path]) -> None:
        """Verify that harm CSV files exist."""
        assert len(harm_csv_files) > 0, "No harm CSV files found"

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_csv_has_required_columns(self, csv_file: Path) -> None:
        """
        Test that each harm CSV has all required columns.

        Required columns for harm scorer evaluation:
        - harm_category: The harm category being evaluated
        - objective: The objective/prompt context
        - assistant_response: The model's response
        - data_type: The type of data (e.g., "text")
        - At least one column starting with 'human_score' (e.g., human_score, human_score_1, etc.)

        Note: Harm CSVs may have additional human_score_2, human_score_3, etc.
        for multi-annotator datasets.
        """
        with open(csv_file, encoding="utf-8") as f:
            _skip_comment_lines(f)

            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])

            required_columns = {
                STANDARD_HARM_COL,
                STANDARD_OBJECTIVE_COL,
                STANDARD_ASSISTANT_RESPONSE_COL,
                STANDARD_DATA_TYPE_COL,
            }

            missing_columns = required_columns - columns
            assert not missing_columns, (
                f"CSV {csv_file.name} is missing required columns: {missing_columns}. Found columns: {columns}"
            )

            # Check for at least one human_score column (matches production logic in from_csv)
            human_score_cols = {col for col in columns if col.startswith(STANDARD_HUMAN_LABEL_COL)}
            assert human_score_cols, (
                f"CSV {csv_file.name} has no human score columns. "
                f"Expected at least one column starting with '{STANDARD_HUMAN_LABEL_COL}'. "
                f"Found columns: {columns}"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_csv_has_human_score_columns(self, csv_file: Path) -> None:
        """
        Test that harm CSVs have at least one human score column.

        Harm CSVs support multiple annotators with columns starting with
        STANDARD_HUMAN_LABEL_COL (e.g., human_score, human_score_1, human_score_2).
        """
        with open(csv_file, encoding="utf-8") as f:
            _skip_comment_lines(f)

            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])

            # Find all human_score* columns (matches production logic in from_csv)
            human_score_cols = {col for col in columns if col.startswith(STANDARD_HUMAN_LABEL_COL)}

            assert human_score_cols, (
                f"CSV {csv_file.name} has no human score columns. "
                f"Expected at least one column starting with '{STANDARD_HUMAN_LABEL_COL}'. "
                f"Found columns: {columns}"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_csv_has_harm_definition(self, csv_file: Path) -> None:
        """
        Test that each harm CSV has a harm_definition path in its metadata.

        Format: # dataset_version=X.Y, harm_definition=path/to/definition.yaml
        Every harm CSV must reference a harm definition file so the scorer
        knows which scale to use for evaluation.
        """
        with open(csv_file, encoding="utf-8") as f:
            first_line = f.readline().strip()

            assert "harm_definition=" in first_line, (
                f"Harm CSV {csv_file.name} is missing harm_definition metadata. "
                f"First line should contain 'harm_definition=path/to/file.yaml', but got: '{first_line}'"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_definition_file_exists_and_is_valid(self, csv_file: Path) -> None:
        """
        Test that the harm_definition file referenced in each harm CSV exists and is valid YAML.

        This validates:
        1. The harm_definition path can be parsed from the CSV
        2. The referenced YAML file exists in the harm_definition directory
        3. The YAML file contains valid harm definition structure
        """
        from pyrit.models.harm_definition import HarmDefinition

        with open(csv_file, encoding="utf-8") as f:
            first_line = f.readline().strip()

        # Parse harm_definition from the comment line
        harm_definition_path = None
        content = first_line[1:].strip()  # Remove leading #
        for part in content.split(","):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                if key.strip() == "harm_definition":
                    harm_definition_path = value.strip()
                    break

        assert harm_definition_path is not None, (
            f"Could not parse harm_definition from {csv_file.name}. First line: '{first_line}'"
        )

        # HarmDefinition.from_yaml will raise FileNotFoundError if file doesn't exist,
        # or ValueError if the YAML is invalid
        try:
            harm_def = HarmDefinition.from_yaml(harm_definition_path)
            assert harm_def.version, f"Harm definition {harm_definition_path} is missing version"
            assert harm_def.category, f"Harm definition {harm_definition_path} is missing category"
            assert len(harm_def.scale_descriptions) > 0, (
                f"Harm definition {harm_definition_path} has no scale descriptions"
            )
        except FileNotFoundError as e:
            pytest.fail(f"Harm definition file not found for {csv_file.name}: {e}")
        except ValueError as e:
            pytest.fail(f"Invalid harm definition YAML for {csv_file.name}: {e}")

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_csv_scores_in_valid_range(self, csv_file: Path) -> None:
        """
        Verify that harm scores are between 0.0 and 1.0 inclusive.

        HarmHumanLabeledEntry.__post_init__ enforces this constraint. If a CSV
        has out-of-range scores, from_csv will raise ValueError at runtime.
        """
        df = _read_csv_as_dataframe(csv_file)
        human_score_cols = [col for col in df.columns if col.startswith(STANDARD_HUMAN_LABEL_COL)]

        for col in human_score_cols:
            scores = pd.to_numeric(df[col], errors="coerce").dropna()
            out_of_range = scores[(scores < 0.0) | (scores > 1.0)]
            assert out_of_range.empty, (
                f"CSV {csv_file.name}: column '{col}' has scores outside [0.0, 1.0]: {out_of_range.tolist()}"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_csv_single_harm_category(self, csv_file: Path) -> None:
        """
        Verify that each harm CSV contains only one harm_category value.

        HumanLabeledDataset.validate() raises ValueError if a dataset has
        multiple harm categories. Each CSV should contain data for exactly
        one category.
        """
        df = _read_csv_as_dataframe(csv_file)
        assert STANDARD_HARM_COL in df.columns, f"CSV {csv_file.name} is missing required column '{STANDARD_HARM_COL}'"

        unique_categories = df[STANDARD_HARM_COL].dropna().unique()
        assert len(unique_categories) == 1, (
            f"CSV {csv_file.name} has multiple harm categories: {list(unique_categories)}. "
            f"Each harm CSV should contain data for exactly one category."
        )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_csv_definition_version_matches_yaml(self, csv_file: Path) -> None:
        """
        Verify that harm_definition_version in CSV metadata matches the YAML file.

        HumanLabeledDataset.validate() checks this at runtime. If versions
        are mismatched, the evaluation will fail with a ValueError.
        Every harm CSV must have both harm_definition and harm_definition_version
        in its metadata line.
        """
        from pyrit.models.harm_definition import HarmDefinition

        with open(csv_file, encoding="utf-8") as f:
            first_line = f.readline().strip()

        assert first_line.startswith("#"), f"CSV {csv_file.name} is missing its metadata comment line"

        # Parse metadata
        harm_definition_path = None
        harm_definition_version = None
        content = first_line[1:].strip()
        for part in content.split(","):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key == "harm_definition":
                    harm_definition_path = value
                elif key == "harm_definition_version":
                    harm_definition_version = value

        assert harm_definition_path is not None, (
            f"CSV {csv_file.name} is missing 'harm_definition' in metadata line. First line: '{first_line}'"
        )
        assert harm_definition_version is not None, (
            f"CSV {csv_file.name} is missing 'harm_definition_version' in metadata line. First line: '{first_line}'"
        )

        harm_def = HarmDefinition.from_yaml(harm_definition_path)
        assert harm_def.version == harm_definition_version, (
            f"CSV {csv_file.name}: harm_definition_version in metadata ('{harm_definition_version}') "
            f"does not match YAML file version ('{harm_def.version}'). "
            f"Update the CSV metadata or YAML to match."
        )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_harm_csv_loads_via_from_csv(self, csv_file: Path) -> None:
        """
        Verify that each harm CSV can be parsed by HumanLabeledDataset.from_csv.

        This is the ultimate integration check: if from_csv succeeds, the file
        is guaranteed to work in the evaluation pipeline.
        """
        from pyrit.score.scorer_evaluation.human_labeled_dataset import HumanLabeledDataset
        from pyrit.score.scorer_evaluation.metrics_type import MetricsType

        try:
            dataset = HumanLabeledDataset.from_csv(
                csv_path=csv_file,
                metrics_type=MetricsType.HARM,
            )
            assert len(dataset.entries) > 0, f"CSV {csv_file.name} produced no entries"
        except Exception as e:
            pytest.fail(f"CSV {csv_file.name} failed to load via from_csv: {e}")


class TestRefusalScorerEvalCSVSchema:
    """Test that all refusal scorer evaluation CSVs have the correct schema."""

    @pytest.fixture(scope="class")
    def refusal_csv_files(self) -> list[Path]:
        """Get all CSV files in the refusal scorer evals directory."""
        return list(Path(SCORER_EVALS_REFUSAL_SCORER_PATH).glob("*.csv"))

    def test_refusal_csv_files_exist(self, refusal_csv_files: list[Path]) -> None:
        """Verify that refusal CSV files exist."""
        assert len(refusal_csv_files) > 0, "No refusal CSV files found"

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_REFUSAL_SCORER_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_refusal_csv_has_required_columns(self, csv_file: Path) -> None:
        """
        Test that each refusal CSV has all required columns.

        Required columns for refusal scorer evaluation:
        - objective: The objective being evaluated
        - assistant_response: The model's response
        - human_score: The human-labeled ground truth score
        - data_type: The type of data (e.g., "text")
        """
        with open(csv_file, encoding="utf-8") as f:
            _skip_comment_lines(f)

            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])

            required_columns = {
                STANDARD_OBJECTIVE_COL,
                STANDARD_ASSISTANT_RESPONSE_COL,
                STANDARD_HUMAN_LABEL_COL,
                STANDARD_DATA_TYPE_COL,
            }

            missing_columns = required_columns - columns
            assert not missing_columns, (
                f"CSV {csv_file.name} is missing required columns: {missing_columns}. Found columns: {columns}"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_REFUSAL_SCORER_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_refusal_csv_column_names_exact(self, csv_file: Path) -> None:
        """
        Test that refusal CSVs use only expected column names.

        This ensures no typos or legacy column names remain.
        """
        with open(csv_file, encoding="utf-8") as f:
            _skip_comment_lines(f)

            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])

            allowed_columns = {
                STANDARD_OBJECTIVE_COL,
                STANDARD_ASSISTANT_RESPONSE_COL,
                STANDARD_HUMAN_LABEL_COL,
                STANDARD_DATA_TYPE_COL,  # Optional column for data type
            }

            unexpected_columns = columns - allowed_columns
            assert not unexpected_columns, (
                f"CSV {csv_file.name} has unexpected columns: {unexpected_columns}. Allowed columns: {allowed_columns}"
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_REFUSAL_SCORER_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_refusal_csv_scores_are_binary(self, csv_file: Path) -> None:
        """
        Verify that refusal scores are binary (0 or 1).

        Refusal CSVs use the same ObjectiveHumanLabeledEntry as objective CSVs,
        which casts scores to bool(int(score)). Only 0 and 1 are valid.
        """
        df = _read_csv_as_dataframe(csv_file)
        human_score_cols = [col for col in df.columns if col.startswith(STANDARD_HUMAN_LABEL_COL)]

        for col in human_score_cols:
            scores = pd.to_numeric(df[col], errors="coerce").dropna()
            invalid = scores[~scores.isin([0, 1])]
            assert invalid.empty, (
                f"CSV {csv_file.name}: column '{col}' has non-binary scores: "
                f"{invalid.tolist()}. Refusal scores must be 0 or 1."
            )

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_REFUSAL_SCORER_PATH).glob("*.csv")),
        ids=lambda p: p.name,
    )
    def test_refusal_csv_loads_via_from_csv(self, csv_file: Path) -> None:
        """
        Verify that each refusal CSV can be parsed by HumanLabeledDataset.from_csv.

        This is the ultimate integration check: if from_csv succeeds, the file
        is guaranteed to work in the evaluation pipeline.
        """
        from pyrit.score.scorer_evaluation.human_labeled_dataset import HumanLabeledDataset
        from pyrit.score.scorer_evaluation.metrics_type import MetricsType

        try:
            dataset = HumanLabeledDataset.from_csv(
                csv_path=csv_file,
                metrics_type=MetricsType.OBJECTIVE,
            )
            assert len(dataset.entries) > 0, f"CSV {csv_file.name} produced no entries"
        except Exception as e:
            pytest.fail(f"CSV {csv_file.name} failed to load via from_csv: {e}")


# ---------------------------------------------------------------------------
# Cross-cutting test classes (apply to all CSV types)
# ---------------------------------------------------------------------------


class TestCSVVersionMetadata:
    """Test that all CSV files have version metadata."""

    @pytest.mark.parametrize(
        "csv_file",
        list(Path(SCORER_EVALS_OBJECTIVE_PATH).glob("*.csv"))
        + list(Path(SCORER_EVALS_HARM_PATH).glob("*.csv"))
        + list(Path(SCORER_EVALS_REFUSAL_SCORER_PATH).glob("*.csv")),
        ids=lambda p: f"{p.parent.name}/{p.name}",
    )
    def test_csv_has_dataset_version_line(self, csv_file: Path) -> None:
        """
        Test that each CSV has a dataset_version metadata line.

        Version line format: # dataset_version=X.Y
        """
        with open(csv_file, encoding="utf-8") as f:
            first_line = f.readline().strip()

            assert first_line.startswith("#") and "dataset_version=" in first_line, (
                f"CSV {csv_file} is missing dataset_version metadata line. "
                f"First line should contain '# dataset_version=X.Y', but got: '{first_line}'"
            )


class TestCSVEncodingValidation:
    """Test that all CSV files are valid UTF-8.

    The production code in HumanLabeledDataset.from_csv falls back to latin-1
    on UnicodeDecodeError, which can silently produce garbled characters. Every
    CSV should be committed as clean UTF-8 so the fallback path is never hit.
    """

    @pytest.mark.parametrize("csv_file", ALL_CSV_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_csv_is_valid_utf8(self, csv_file: Path) -> None:
        """
        Verify that the CSV file is readable as strict UTF-8.

        Files with Windows-1252 smart quotes (bytes 0x91-0x97) or other
        non-UTF-8 bytes will fail this test. Fix by replacing them with
        ASCII equivalents.
        """
        try:
            with open(csv_file, encoding="utf-8") as f:
                f.read()
        except UnicodeDecodeError as e:
            pytest.fail(
                f"CSV {csv_file.name} is not valid UTF-8: {e}. "
                f"Likely contains Windows-1252 smart quotes. "
                f"Replace them with ASCII equivalents before committing."
            )


class TestCSVDataIntegrity:
    """Test that CSV data values are valid for the evaluation pipeline.

    These tests catch issues that would cause runtime errors or incorrect
    metrics when ScorerEvaluator.evaluate_dataset_async processes the data.
    """

    @pytest.mark.parametrize("csv_file", ALL_CSV_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_csv_has_at_least_one_data_row(self, csv_file: Path) -> None:
        """Verify that each CSV has at least one data row beyond the header."""
        df = _read_csv_as_dataframe(csv_file)
        assert len(df) > 0, f"CSV {csv_file.name} has no data rows"

    @pytest.mark.parametrize("csv_file", ALL_CSV_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_csv_assistant_response_not_empty(self, csv_file: Path) -> None:
        """
        Verify that assistant_response column has no empty or NaN values.

        Empty responses would produce meaningless scores and break the
        evaluation pipeline.
        """
        df = _read_csv_as_dataframe(csv_file)
        if STANDARD_ASSISTANT_RESPONSE_COL not in df.columns:
            pytest.skip(f"CSV {csv_file.name} missing {STANDARD_ASSISTANT_RESPONSE_COL} column")

        nan_rows = df[STANDARD_ASSISTANT_RESPONSE_COL].isna()
        assert not nan_rows.any(), (
            f"CSV {csv_file.name} has NaN assistant_response values at rows: {list(df.index[nan_rows])}"
        )

        empty_rows = df[STANDARD_ASSISTANT_RESPONSE_COL].astype(str).str.strip().eq("")
        assert not empty_rows.any(), (
            f"CSV {csv_file.name} has empty assistant_response values at rows: {list(df.index[empty_rows])}"
        )

    @pytest.mark.parametrize("csv_file", ALL_CSV_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_csv_human_score_columns_have_no_nan(self, csv_file: Path) -> None:
        """
        Verify that human score columns contain no NaN values.

        Mirrors the strict production validation in
        HumanLabeledDataset._validate_csv_columns which rejects any NaN
        in human score columns. Every cell must have a score.
        """
        df = _read_csv_as_dataframe(csv_file)
        human_score_cols = [col for col in df.columns if col.startswith(STANDARD_HUMAN_LABEL_COL)]
        if not human_score_cols:
            pytest.skip(f"CSV {csv_file.name} has no human score columns")

        for col in human_score_cols:
            nan_count = df[col].isna().sum()
            assert nan_count == 0, (
                f"CSV {csv_file.name}: human score column '{col}' has {nan_count} NaN value(s). "
                f"All human score cells must be filled."
            )

    @pytest.mark.parametrize("csv_file", ALL_CSV_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_csv_data_type_values_are_valid(self, csv_file: Path) -> None:
        """
        Verify that data_type values match PromptDataType literals.

        Invalid data_type values would be cast to PromptDataType without
        validation, potentially causing downstream errors in scoring.
        """
        df = _read_csv_as_dataframe(csv_file)
        if STANDARD_DATA_TYPE_COL not in df.columns:
            pytest.skip(f"CSV {csv_file.name} has no {STANDARD_DATA_TYPE_COL} column")

        unique_types = set(df[STANDARD_DATA_TYPE_COL].dropna().astype(str).str.strip())
        invalid_types = unique_types - VALID_DATA_TYPES
        assert not invalid_types, (
            f"CSV {csv_file.name} has invalid data_type values: {invalid_types}. Valid values: {VALID_DATA_TYPES}"
        )
