# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import uuid4

import pandas as pd

from pyrit.common.utils import verify_and_resolve_path
from pyrit.models import Message, MessagePiece, PromptDataType
from pyrit.score.scorer_evaluation.metrics_type import MetricsType

if TYPE_CHECKING:
    from collections.abc import Hashable

    from pyrit.models.harm_definition import HarmDefinition

logger = logging.getLogger(__name__)


@dataclass
class HumanLabeledEntry:
    """
    A class that represents an entry in a dataset of assistant responses that have been scored by humans. It is used
    to evaluate PyRIT scorer performance as measured by degree of alignment with human labels. This class includes
    the Messages and a list of human-assigned scores, which are floats between 0.0 and 1.0 inclusive
    (representing degree of severity) for harm datasets, and booleans for objective datasets.

    Parameters:
        conversation (list[Message]): A list of Message objects representing the
            conversation to be scored. This can contain one Message object if you are just
            scoring individual assistant responses.
        human_scores (List): A list of human-assigned scores for the responses. Each entry in the list corresponds to
            a different person's score for the same response/conversation.
    """

    conversation: list[Message]
    human_scores: list[Any]

    def __post_init__(self) -> None:
        """
        Validate that conversation and human_scores are not None and have positive lengths.

        Raises:
            ValueError: If conversation or human_scores is None or empty.
        """
        if self.conversation is None or len(self.conversation) == 0:
            raise ValueError("conversation must not be None or empty.")
        if self.human_scores is None or len(self.human_scores) == 0:
            raise ValueError("human_scores must not be None or empty.")


@dataclass
class HarmHumanLabeledEntry(HumanLabeledEntry):
    """
    A class that represents a human-labeled dataset entry for a specific harm category. This class includes the
    Messages and a list of human scores, which are floats between 0.0 and 1.0 inclusive,
    representing the degree of harm severity where 0.0 is minimal and 1.0 is maximal. The harm category is a
    string that represents the type of harm (e.g., "hate_speech", "misinformation", etc.).
    """

    human_scores: list[float]
    # For now, this is a string, but may be enum or Literal in the future.
    harm_category: str

    def __post_init__(self) -> None:
        """
        Validate harm category and human scores.

        Raises:
            ValueError: If harm_category is None or empty, or if any human score is not between 0.0 and 1.0 inclusive.
        """
        super().__post_init__()

        if not self.harm_category or not self.harm_category.strip():
            raise ValueError("harm_category must not be None or empty.")

        if not all(score >= 0.0 and score <= 1.0 for score in self.human_scores):
            raise ValueError("All human scores must be between 0.0 and 1.0 inclusive.")


@dataclass
class ObjectiveHumanLabeledEntry(HumanLabeledEntry):
    """
    A human-labeled entry for one assistant response and a specific objective.

    The conversation must contain exactly one assistant message. Other messages provide
    context for scorers that read conversation history. Each human score indicates
    whether the labeled response meets the objective.
    """

    human_scores: list[bool]
    objective: str

    def __post_init__(self) -> None:
        """
        Validate objective field.

        Raises:
            ValueError: If objective is None or empty.
        """
        super().__post_init__()

        if not self.objective or not self.objective.strip():
            raise ValueError("objective must not be None or empty.")


class HumanLabeledDataset:
    """
    A class that represents a human-labeled dataset, including the entries and each of their corresponding
    human scores. This dataset is used to evaluate PyRIT scorer performance via the ScorerEvaluator class.
    HumanLabeledDatasets can be constructed from a CSV file.
    """

    # Standard column names for evaluation datasets
    STANDARD_HUMAN_LABEL_COL: ClassVar[str] = "human_score"
    STANDARD_OBJECTIVE_COL: ClassVar[str] = "objective"
    STANDARD_HARM_COL: ClassVar[str] = "harm_category"
    STANDARD_ASSISTANT_RESPONSE_COL: ClassVar[str] = "assistant_response"
    STANDARD_DATA_TYPE_COL: ClassVar[str] = "data_type"

    def __init__(
        self,
        *,
        name: str,
        entries: list[HumanLabeledEntry],
        metrics_type: MetricsType,
        version: str,
        harm_definition: str | None = None,
        harm_definition_version: str | None = None,
    ) -> None:
        """
        Initialize the HumanLabeledDataset.

        Args:
            name (str): The name of the human-labeled dataset. For datasets of uniform type, this is often the harm
                category (e.g. hate_speech) or objective. It will be used in the naming of metrics (JSON) and
                model scores (CSV) files when evaluation is run on this dataset.
            entries (list[HumanLabeledEntry]): A list of entries in the dataset.
            metrics_type (MetricsType): The type of the human-labeled dataset, either HARM or
                OBJECTIVE.
            version (str): The version of the human-labeled dataset.
            harm_definition (str, optional): Path to the harm definition YAML file for HARM datasets.
            harm_definition_version (str, optional): Version of the harm definition YAML file.
                Used to ensure the human labels match the scoring criteria version.

        Raises:
            ValueError: If the dataset name is an empty string.
        """
        if not name:
            raise ValueError("Dataset name cannot be an empty string.")

        self.name = name
        self.entries = entries
        self.metrics_type = metrics_type
        self.version = version
        self.harm_definition = harm_definition
        self.harm_definition_version = harm_definition_version
        self._harm_definition_obj: HarmDefinition | None = None

    def get_harm_definition(self) -> "HarmDefinition | None":
        """
        Load and return the HarmDefinition object for this dataset.

        For HARM datasets, this loads the harm definition YAML file specified in
        harm_definition and returns it as a HarmDefinition object. The result is
        cached after the first load.

        Returns:
            HarmDefinition: The loaded harm definition object, or None if this is not
                a HARM dataset or harm_definition is not set.

        Raises:
            FileNotFoundError: If the harm definition file does not exist.
            ValueError: If the harm definition file is invalid.
        """
        if self.metrics_type != MetricsType.HARM or not self.harm_definition:
            return None

        if self._harm_definition_obj is None:
            from pyrit.models.harm_definition import HarmDefinition

            self._harm_definition_obj = HarmDefinition.from_yaml(self.harm_definition)

        return self._harm_definition_obj

    @staticmethod
    def _read_leading_comment_block(csv_path: str | Path) -> tuple[list[str], int]:
        """
        Scan the leading "#" comment and blank lines of a CSV file.

        Reading the comment text and the skip count from a single scan keeps metadata
        parsing and the pandas ``skiprows`` count from disagreeing about where the
        header row starts. "utf-8-sig" drops a byte-order mark, which does not start a
        "#" line but would otherwise leave the metadata line looking like data.

        Args:
            csv_path (str | Path): The path to the CSV file.

        Returns:
            tuple[list[str], int]: The comment lines with their leading "#" removed, and
                the number of physical lines pandas must skip to reach the header row.
        """
        for encoding in ("utf-8-sig", "latin-1"):
            comments: list[str] = []
            skiprows = 0
            try:
                with open(csv_path, encoding=encoding) as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            comments.append(stripped[1:].strip())
                        elif stripped:
                            break
                        skiprows += 1
            except UnicodeDecodeError:
                continue
            return comments, skiprows
        return [], 0

    @classmethod
    def from_csv(
        cls,
        *,
        csv_path: str | Path,
        metrics_type: MetricsType,
        dataset_name: str | None = None,
        version: str | None = None,
        harm_definition: str | None = None,
        harm_definition_version: str | None = None,
    ) -> "HumanLabeledDataset":
        """
        Load a human-labeled dataset from a CSV file with standard column names.

        Expected CSV format:
        - 'assistant_response': The assistant's response text
        - 'human_score': Human-assigned label (can have multiple columns for multiple raters)
        - 'objective': For OBJECTIVE datasets, the objective being evaluated
        - 'data_type': Optional data type (defaults to 'text' if not present)

        Objective labels are validated without numeric rounding and must represent
        exactly 0 or 1, or a boolean. Harm labels are floats between 0.0 and 1.0.

        You can optionally include # comment lines at the top of the CSV file to specify
        the dataset version and harm definition path; the metadata may appear on any of
        them. Only leading # lines are treated as comments; a # anywhere else is
        preserved as literal data. The format is:
        - For harm datasets: # dataset_version=x.y, harm_definition=path/to/definition.yaml, harm_definition_version=x.y
        - For objective datasets: # dataset_version=x.y

        Args:
            csv_path (str | Path): The path to the CSV file.
            metrics_type (MetricsType): The type of the human-labeled dataset, either HARM or OBJECTIVE.
            dataset_name (str, Optional): The name of the dataset. If not provided, it will be inferred
                from the CSV file name.
            version (str, Optional): The version of the dataset. If not provided here, it will be inferred
                from the CSV file if a dataset_version comment line is present.
            harm_definition (str, Optional): Path to the harm definition YAML file. If not provided here,
                it will be inferred from the CSV file if a harm_definition comment is present.
            harm_definition_version (str, Optional): Version of the harm definition YAML file. If not provided
                here, it will be inferred from the CSV file if a harm_definition_version comment is present.

        Returns:
            HumanLabeledDataset: The human-labeled dataset object.

        Raises:
            FileNotFoundError: If the CSV file does not exist.
            ValueError: If version is not provided and not found in the CSV file,
                required cells are missing, or objective labels are not binary.
        """
        csv_path = verify_and_resolve_path(csv_path)
        # One scan of the leading block yields both the metadata comments and the number
        # of rows pandas must skip, so the two can never disagree.
        comment_lines, skiprows = cls._read_leading_comment_block(csv_path)
        parsed_version = None
        parsed_harm_definition = None
        parsed_harm_definition_version = None
        for content in comment_lines:
            # Parse key=value pairs from the comment line
            # Format: # dataset_version=x.y, harm_definition=path/to/file.yaml, harm_definition_version=x.y
            # The first occurrence of a key wins, so a later prose comment that happens to
            # contain "key=value" cannot override metadata declared above it.
            for part in content.split(","):
                part = part.strip()
                if "=" in part:
                    key, value = part.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if key == "dataset_version" and parsed_version is None:
                        parsed_version = value
                    elif key == "harm_definition" and parsed_harm_definition is None:
                        parsed_harm_definition = value
                    elif key == "harm_definition_version" and parsed_harm_definition_version is None:
                        parsed_harm_definition_version = value

        # Use provided values or fall back to parsed values
        if not version:
            if parsed_version:
                version = parsed_version
            else:
                raise ValueError("Version not specified and not found in CSV file.")

        if not harm_definition and parsed_harm_definition:
            harm_definition = parsed_harm_definition

        if not harm_definition_version and parsed_harm_definition_version:
            harm_definition_version = parsed_harm_definition_version

        # Skipping the counted leading rows, rather than passing comment="#", keeps a
        # literal "#" inside an unquoted cell from truncating the rest of the row (#2974).
        # Preserve text before pandas can infer numeric or boolean values.
        text_dtypes: dict[Hashable, type[str]] = dict.fromkeys(
            (
                cls.STANDARD_ASSISTANT_RESPONSE_COL,
                cls.STANDARD_OBJECTIVE_COL,
                cls.STANDARD_HARM_COL,
                cls.STANDARD_DATA_TYPE_COL,
            ),
            str,
        )
        # Keep objective labels as text until validation, before rounding or underflow can occur.
        csv_dtypes = str if metrics_type == MetricsType.OBJECTIVE else text_dtypes
        # Try UTF-8 first, fall back to latin-1 for files with special characters
        try:
            eval_df = pd.read_csv(csv_path, skiprows=skiprows or None, encoding="utf-8", dtype=csv_dtypes)
        except UnicodeDecodeError:
            eval_df = pd.read_csv(csv_path, skiprows=skiprows or None, encoding="latin-1", dtype=csv_dtypes)

        # Drop rows where every column is NaN (e.g. trailing blank lines in the CSV)
        eval_df = eval_df.dropna(how="all")

        # Validate required columns exist and have no NaN values
        cls._validate_csv_columns(eval_df=eval_df, metrics_type=metrics_type)

        # Determine human label columns (all columns starting with standard prefix)
        human_label_col_names = [col for col in eval_df.columns if col.startswith(cls.STANDARD_HUMAN_LABEL_COL)]
        if not human_label_col_names:
            raise ValueError(
                f"No human score columns found. Expected columns starting with '{cls.STANDARD_HUMAN_LABEL_COL}'."
            )

        # Get data type column if it exists, otherwise default to 'text'
        has_data_type_col = cls.STANDARD_DATA_TYPE_COL in eval_df.columns

        responses_to_score = eval_df[cls.STANDARD_ASSISTANT_RESPONSE_COL].tolist()
        all_human_scores = eval_df[human_label_col_names].values.tolist()
        # Use appropriate column based on metrics type
        objective_or_harm_col = (
            cls.STANDARD_HARM_COL if metrics_type == MetricsType.HARM else cls.STANDARD_OBJECTIVE_COL
        )
        objectives_or_harms = eval_df[objective_or_harm_col].tolist()
        if has_data_type_col:
            data_types = eval_df[cls.STANDARD_DATA_TYPE_COL].tolist()
        else:
            data_types = ["text"] * len(eval_df[cls.STANDARD_ASSISTANT_RESPONSE_COL])

        entries: list[HumanLabeledEntry] = []
        for response_to_score, human_scores, objective_or_harm, data_type in zip(
            responses_to_score, all_human_scores, objectives_or_harms, data_types, strict=False
        ):
            response_to_score = str(response_to_score).strip()
            objective_or_harm = str(objective_or_harm).strip()
            data_type = str(data_type).strip()

            # Each list of messages consists only of a single assistant response since each row
            # is treated as a single turn conversation.
            messages = [
                Message(
                    message_pieces=[
                        MessagePiece(
                            role="assistant",
                            original_value=response_to_score,
                            original_value_data_type=cast("PromptDataType", data_type),
                            conversation_id=str(uuid4()),
                        )
                    ],
                )
            ]
            entry: HumanLabeledEntry
            if metrics_type == MetricsType.HARM:
                entry = cls._construct_harm_entry(
                    messages=messages,
                    harm=objective_or_harm,
                    human_scores=human_scores,
                )
            else:
                entry = cls._construct_objective_entry(
                    messages=messages,
                    objective=objective_or_harm,
                    human_scores=human_scores,
                )
            entries.append(entry)

        dataset_name = dataset_name or Path(csv_path).stem
        return cls(
            entries=entries,
            name=dataset_name,
            metrics_type=metrics_type,
            version=version,
            harm_definition=harm_definition,
            harm_definition_version=harm_definition_version,
        )

    def validate(self) -> None:
        """
        Validate that the dataset is internally consistent.

        Checks that all entries match the dataset's metrics_type and, for HARM datasets,
        that all entries have the same harm_category, that harm_definition is specified,
        and that the harm definition file exists and is loadable.

        Raises:
            ValueError: If entries don't match metrics_type, harm categories are inconsistent,
                or harm_definition is missing for HARM datasets.
            FileNotFoundError: If the harm definition file does not exist.
        """
        if not self.entries:
            return

        if self.metrics_type == MetricsType.HARM:
            if not self.harm_definition or not self.harm_definition_version:
                raise ValueError(
                    "harm_definition and harm_definition_version must be specified for HARM datasets. "
                    "Provide a path to the harm definition YAML file."
                )

            # Validate that the harm definition file exists and is loadable
            # This will raise FileNotFoundError or ValueError if invalid
            harm_def = self.get_harm_definition()

            # Validate that harm_definition_version matches the actual YAML file version
            if self.harm_definition_version and harm_def and harm_def.version != self.harm_definition_version:
                raise ValueError(
                    f"harm_definition_version mismatch: CSV specifies '{self.harm_definition_version}' "
                    f"but '{self.harm_definition}' has version '{harm_def.version}'. "
                    f"Please update the CSV or YAML to match."
                )

            harm_categories = set()
            for index, entry in enumerate(self.entries):
                if not isinstance(entry, HarmHumanLabeledEntry):
                    raise ValueError(
                        f"Entry at index {index} is not a HarmHumanLabeledEntry, "
                        "but the HumanLabeledDataset type is HARM."
                    )
                harm_categories.add(entry.harm_category)

            if len(harm_categories) > 1:
                raise ValueError("Evaluating a dataset with multiple harm categories is not currently supported.")

        else:
            for index, entry in enumerate(self.entries):
                if not isinstance(entry, ObjectiveHumanLabeledEntry):
                    raise ValueError(
                        f"Entry at index {index} is not an ObjectiveHumanLabeledEntry, "
                        "but the HumanLabeledDataset type is OBJECTIVE."
                    )

    @classmethod
    def _validate_csv_columns(cls, *, eval_df: pd.DataFrame, metrics_type: MetricsType) -> None:
        """
        Validate required columns and binary labels for objective datasets.

        Args:
            eval_df (pd.DataFrame): The DataFrame to validate.
            metrics_type (MetricsType): The type of dataset (HARM or OBJECTIVE).

        Raises:
            ValueError: If required columns are missing, names are not unique, or objective labels are not binary.
        """
        if len(eval_df.columns) != len(set(eval_df.columns)):
            raise ValueError("Column names in the dataset must be unique.")

        # Required columns depend on metrics type
        objective_or_harm_col = (
            cls.STANDARD_HARM_COL if metrics_type == MetricsType.HARM else cls.STANDARD_OBJECTIVE_COL
        )
        required_columns = [cls.STANDARD_ASSISTANT_RESPONSE_COL, objective_or_harm_col]

        for column in required_columns:
            if column not in eval_df.columns:
                raise ValueError(
                    f"Required column '{column}' is missing from the dataset. Found columns: {list(eval_df.columns)}"
                )
            if eval_df[column].isna().any():
                raise ValueError(f"Column '{column}' contains NaN values.")

        # Check for at least one human score column
        human_score_cols = [col for col in eval_df.columns if col.startswith(cls.STANDARD_HUMAN_LABEL_COL)]
        if not human_score_cols:
            raise ValueError(
                f"No human score columns found. "
                f"Expected at least one column starting with '{cls.STANDARD_HUMAN_LABEL_COL}'."
            )

        # Validate human score columns don't have any NaN values.
        for col in human_score_cols:
            if eval_df[col].isna().any():
                nan_count = eval_df[col].isna().sum()
                raise ValueError(
                    f"Human score column '{col}' contains {nan_count} NaN value(s). "
                    f"All human score cells must be filled."
                )
            if metrics_type == MetricsType.OBJECTIVE:
                try:
                    for score in eval_df[col]:
                        cls._parse_objective_label(score)
                except ValueError as error:
                    raise ValueError(
                        f"Human score column '{col}' must contain only binary values (0 or 1) for OBJECTIVE datasets."
                    ) from error

    @staticmethod
    def _construct_harm_entry(*, messages: list[Message], harm: str, human_scores: list[Any]) -> HarmHumanLabeledEntry:
        float_scores = [float(score) for score in human_scores]
        return HarmHumanLabeledEntry(messages, float_scores, harm)

    @staticmethod
    def _parse_objective_label(score: object) -> bool:
        normalized = str(score).strip().lower()
        if normalized in ("true", "false"):
            return normalized == "true"
        try:
            numeric_score = Decimal(normalized)
        except InvalidOperation as error:
            raise ValueError("Objective labels must be binary (0 or 1).") from error
        if not numeric_score.is_finite() or numeric_score not in (0, 1):
            raise ValueError("Objective labels must be binary (0 or 1).")
        return bool(numeric_score)

    @classmethod
    def _construct_objective_entry(
        cls, *, messages: list[Message], objective: str, human_scores: list[Any]
    ) -> "ObjectiveHumanLabeledEntry":
        bool_scores = [cls._parse_objective_label(score) for score in human_scores]
        return ObjectiveHumanLabeledEntry(messages, bool_scores, objective)
