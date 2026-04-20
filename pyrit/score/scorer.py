# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import asyncio
import json
import logging
import uuid
from abc import abstractmethod
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Sequence,
    Union,
    cast,
)

from pyrit.exceptions import (
    InvalidJsonException,
    PyritException,
    pyrit_json_retry,
    remove_markdown_json,
)
from pyrit.identifiers import Identifiable, ScorerIdentifier
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import (
    ChatMessageRole,
    Message,
    MessagePiece,
    PromptDataType,
    Score,
    ScoreType,
    UnvalidatedScore,
)
from pyrit.prompt_target import PromptChatTarget, PromptTarget
from pyrit.prompt_target.batch_helper import batch_task_async
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

if TYPE_CHECKING:
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyrit.score.scorer_evaluation.metrics_type import RegistryUpdateBehavior
    from pyrit.score.scorer_evaluation.scorer_evaluator import (
        ScorerEvalDatasetFiles,
    )
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics


class Scorer(Identifiable[ScorerIdentifier], abc.ABC):
    """
    Abstract base class for scorers.
    """

    # Evaluation configuration - maps input dataset files to a result file
    # Specifies glob patterns for datasets and a result file name
    evaluation_file_mapping: Optional["ScorerEvalDatasetFiles"] = None

    _identifier: Optional[ScorerIdentifier] = None

    def __init__(self, *, validator: ScorerPromptValidator):
        """
        Initialize the Scorer.

        Args:
            validator (ScorerPromptValidator): Validator for message pieces and scorer configuration.
        """
        self._validator = validator

    @property
    def scorer_type(self) -> ScoreType:
        """
        Get the scorer type based on class hierarchy.

        Returns:
            ScoreType: "true_false" for TrueFalseScorer subclasses,
                      "float_scale" for FloatScaleScorer subclasses,
                      "unknown" for other scorers.
        """
        # Import here to avoid circular imports
        from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
        from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

        if isinstance(self, TrueFalseScorer):
            return "true_false"
        elif isinstance(self, FloatScaleScorer):
            return "float_scale"
        else:
            return "unknown"

    def get_identifier(self) -> ScorerIdentifier:
        """
        Get the scorer identifier. Built lazily on first access.

        Returns:
            ScorerIdentifier: The identifier containing all configuration parameters.
        """
        if self._identifier is None:
            self._build_identifier()
            assert self._identifier is not None, "_build_identifier must set _identifier"
        return self._identifier

    @property
    def _memory(self) -> MemoryInterface:
        return CentralMemory.get_memory_instance()

    def _set_identifier(
        self,
        *,
        system_prompt_template: Optional[str] = None,
        user_prompt_template: Optional[str] = None,
        sub_scorers: Optional[Sequence["Scorer"]] = None,
        score_aggregator: Optional[str] = None,
        scorer_specific_params: Optional[Dict[str, Any]] = None,
        prompt_target: Optional[PromptTarget] = None,
    ) -> None:
        """
        Construct the scorer evaluation identifier.

        Args:
            system_prompt_template (Optional[str]): The system prompt template used by this scorer. Defaults to None.
            user_prompt_template (Optional[str]): The user prompt template used by this scorer. Defaults to None.
            sub_scorers (Optional[Sequence[Scorer]]): List of sub-scorers for composite scorers. Defaults to None.
            score_aggregator (Optional[str]): The name of the score aggregator function. Defaults to None.
            scorer_specific_params (Optional[Dict[str, Any]]): Additional scorer-specific parameters.
                Defaults to None.
            prompt_target (Optional[PromptTarget]): The prompt target used by this scorer. Defaults to None.
        """
        # Build sub_identifier from sub_scorers (store as dicts for storage)
        sub_identifier: Optional[List[ScorerIdentifier]] = None
        if sub_scorers:
            sub_identifier = [scorer.get_identifier() for scorer in sub_scorers]
        # Extract target_info from prompt_target
        target_info: Optional[Dict[str, Any]] = None
        if prompt_target:
            target_id = prompt_target.get_identifier()
            # Extract standard fields for scorer evaluation
            target_info = {}
            for key in ["__type__", "model_name", "temperature", "top_p"]:
                if key in target_id:
                    target_info[key] = target_id[key]

        self._identifier = ScorerIdentifier(
            class_name=self.__class__.__name__,
            class_module=self.__class__.__module__,
            class_description=self.__class__.__doc__ or "",
            identifier_type="instance",
            scorer_type=self.scorer_type,
            system_prompt_template=system_prompt_template,
            user_prompt_template=user_prompt_template,
            sub_identifier=sub_identifier,
            target_info=target_info,
            score_aggregator=score_aggregator,
            scorer_specific_params=scorer_specific_params,
        )

    async def score_async(
        self,
        message: Message,
        *,
        objective: Optional[str] = None,
        role_filter: Optional[ChatMessageRole] = None,
        skip_on_error_result: bool = False,
        infer_objective_from_request: bool = False,
    ) -> list[Score]:
        """
        Score the message, add the results to the database, and return a list of Score objects.

        Args:
            message (Message): The message to be scored.
            objective (Optional[str]): The task or objective based on which the message should be scored.
                Defaults to None.
            role_filter (Optional[ChatMessageRole]): Only score messages with this exact stored role.
                Use "assistant" to score only real assistant responses, or "simulated_assistant"
                to score only simulated responses. Defaults to None (no filtering).
            skip_on_error_result (bool): If True, skip scoring if the message contains an error. Defaults to False.
            infer_objective_from_request (bool): If True, infer the objective from the message's previous request
                when objective is not provided. Defaults to False.

        Returns:
            list[Score]: A list of Score objects representing the results.

        Raises:
            PyritException: If scoring raises a PyRIT exception (re-raised with enhanced context).
            RuntimeError: If scoring raises a non-PyRIT exception (wrapped with scorer context).
        """
        self._validator.validate(message, objective=objective)

        if role_filter is not None and message.get_piece().get_role_for_storage() != role_filter:
            logger.debug("Skipping scoring due to role filter mismatch.")
            return []

        if skip_on_error_result and message.is_error():
            logger.debug("Skipping scoring due to error in message and skip_on_error=True.")
            return []

        if infer_objective_from_request and (not objective):
            objective = self._extract_objective_from_response(message)

        try:
            scores = await self._score_async(
                message,
                objective=objective,
            )
        except PyritException as e:
            # Re-raise PyRIT exceptions with enhanced context while preserving type for retry decorators
            e.message = f"Error in scorer {self.__class__.__name__}: {e.message}"
            e.args = (f"Status Code: {e.status_code}, Message: {e.message}",)
            raise
        except Exception as e:
            # Wrap non-PyRIT exceptions for better error tracing
            raise RuntimeError(f"Error in scorer {self.__class__.__name__}: {str(e)}") from e

        self.validate_return_scores(scores=scores)
        self._memory.add_scores_to_memory(scores=scores)

        return scores

    async def _score_async(self, message: Message, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score the given request response asynchronously.

        This default implementation scores all supported pieces in the message
        and returns a flattened list of scores. Subclasses can override this method
        to implement custom scoring logic (e.g., aggregating scores).

        Args:
            message (Message): The message to score.
            objective (Optional[str]): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list of Score objects.
        """
        if not message.message_pieces:
            return []

        # Score only the supported pieces
        supported_pieces = self._get_supported_pieces(message)

        tasks = [self._score_piece_async(message_piece=piece, objective=objective) for piece in supported_pieces]

        if not tasks:
            return []

        # Run all piece-level scorings concurrently
        piece_score_lists = await asyncio.gather(*tasks)

        # Flatten list[list[Score]] -> list[Score]
        return [score for sublist in piece_score_lists for score in sublist]

    @abstractmethod
    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        raise NotImplementedError()

    def _get_supported_pieces(self, message: Message) -> list[MessagePiece]:
        """
        Get a list of supported message pieces for this scorer.

        Returns:
            list[MessagePiece]: List of message pieces that are supported by this scorer's validator.
        """
        return [
            piece for piece in message.message_pieces if self._validator.is_message_piece_supported(message_piece=piece)
        ]

    @abstractmethod
    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate the scores returned by the scorer. Because some scorers may require
        specific Score types or values.

        Args:
            scores (list[Score]): The scores to be validated.
        """
        raise NotImplementedError()

    async def evaluate_async(
        self,
        file_mapping: Optional["ScorerEvalDatasetFiles"] = None,
        *,
        num_scorer_trials: int = 3,
        update_registry_behavior: "RegistryUpdateBehavior" = None,
        max_concurrency: int = 10,
    ) -> Optional["ScorerMetrics"]:
        """
        Evaluate this scorer against human-labeled datasets.

        Uses file mapping to determine which datasets to evaluate and how to aggregate results.

        Args:
            file_mapping: Optional ScorerEvalDatasetFiles configuration.
                If not provided, uses the scorer's configured evaluation_file_mapping.
                Maps input file patterns to an output result file.
            num_scorer_trials: Number of times to score each response (for measuring variance). Defaults to 3.
            update_registry_behavior: Controls how existing registry entries are handled.
                - SKIP_IF_EXISTS (default): Check registry for existing results. If found, return cached metrics.
                - ALWAYS_UPDATE: Always run evaluation and overwrite any existing registry entry.
                - NEVER_UPDATE: Always run evaluation but never write to registry (for debugging).
                Defaults to RegistryUpdateBehavior.SKIP_IF_EXISTS.
            max_concurrency: Maximum number of concurrent scoring requests. Defaults to 10.

        Returns:
            ScorerMetrics: The evaluation metrics, or None if no datasets found.

        Raises:
            ValueError: If no file_mapping is provided and no evaluation_file_mapping is configured.
        """
        from pyrit.score import ScorerEvaluator
        from pyrit.score.scorer_evaluation.metrics_type import RegistryUpdateBehavior

        # Handle default for update_registry_behavior (can't use enum in signature due to forward ref)
        if update_registry_behavior is None:
            update_registry_behavior = RegistryUpdateBehavior.SKIP_IF_EXISTS

        # Use provided mapping or fall back to scorer's configured mapping
        mapping = file_mapping if file_mapping is not None else self.evaluation_file_mapping

        if mapping is None:
            raise ValueError(
                f"No file_mapping provided and no evaluation_file_mapping configured for {self.__class__.__name__}. "
                "Either provide file_mapping parameter or configure evaluation_file_mapping on the scorer class."
            )

        scorer_evaluator = ScorerEvaluator.from_scorer(self)
        return await scorer_evaluator.run_evaluation_async(
            dataset_files=mapping,
            num_scorer_trials=num_scorer_trials,
            update_registry_behavior=update_registry_behavior,
            max_concurrency=max_concurrency,
        )

    @abstractmethod
    def get_scorer_metrics(self) -> Optional["ScorerMetrics"]:
        """
        Get evaluation metrics for this scorer from the configured evaluation result file.

        Looks up metrics by this scorer's identity hash in the JSONL result file.
        The result file may contain entries for multiple scorer configurations.

        Subclasses must implement this to return the appropriate metrics type:
        - TrueFalseScorer subclasses should return ObjectiveScorerMetrics
        - FloatScaleScorer subclasses should return HarmScorerMetrics

        Returns:
            ScorerMetrics: The metrics for this scorer, or None if not found or not configured.
        """
        raise NotImplementedError("Subclasses must implement get_scorer_metrics")

    async def score_text_async(self, text: str, *, objective: Optional[str] = None) -> list[Score]:
        """
        Scores the given text based on the task using the chat target.

        Args:
            text (str): The text to be scored.
            objective (Optional[str]): The task based on which the text should be scored

        Returns:
            list[Score]: A list of Score objects representing the results.
        """
        request = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value=text,
                )
            ]
        )

        request.message_pieces[0].id = None
        return await self.score_async(request, objective=objective)

    async def score_image_async(self, image_path: str, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score the given image using the chat target.

        Args:
            image_path (str): The path to the image file to be scored.
            objective (Optional[str]): The objective based on which the image should be scored. Defaults to None.

        Returns:
            list[Score]: A list of Score objects representing the results.
        """
        request = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value=image_path,
                    original_value_data_type="image_path",
                )
            ]
        )

        request.message_pieces[0].id = None
        return await self.score_async(request, objective=objective)

    async def score_prompts_batch_async(
        self,
        *,
        messages: Sequence[Message],
        objectives: Optional[Sequence[str]] = None,
        batch_size: int = 10,
        role_filter: Optional[ChatMessageRole] = None,
        skip_on_error_result: bool = False,
        infer_objective_from_request: bool = False,
    ) -> list[Score]:
        """
        Score multiple prompts in batches using the provided objectives.

        Args:
            messages (Sequence[Message]): The messages to be scored.
            objectives (Sequence[str]): The objectives/tasks based on which the prompts should be scored.
                Must have the same length as messages.
            batch_size (int): The maximum batch size for processing prompts. Defaults to 10.
            role_filter (Optional[ChatMessageRole]): If provided, only score pieces with this role.
                Defaults to None (no filtering).
            skip_on_error_result (bool): If True, skip scoring pieces that have errors. Defaults to False.
            infer_objective_from_request (bool): If True and objective is empty, attempt to infer
                the objective from the request. Defaults to False.

        Returns:
            list[Score]: A flattened list of Score objects from all scored prompts.

        Raises:
            ValueError: If objectives is empty or if the number of objectives doesn't match
                the number of messages.
        """
        if not objectives:
            objectives = [""] * len(messages)

        elif len(objectives) != len(messages):
            raise ValueError("The number of tasks must match the number of messages.")

        if len(messages) == 0:
            return []

        # Some scorers do not have an associated prompt target; batch helper validates RPM only when present
        prompt_target = getattr(self, "_prompt_target", None)
        results = await batch_task_async(
            task_func=self.score_async,
            task_arguments=["message", "objective"],
            prompt_target=cast(PromptTarget, prompt_target),
            batch_size=batch_size,
            items_to_batch=[messages, objectives],
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
            infer_objective_from_request=infer_objective_from_request,
        )

        # results is a list[list[Score]] and needs to be flattened
        return [score for sublist in results for score in sublist]

    async def score_image_batch_async(
        self, *, image_paths: Sequence[str], objectives: Optional[Sequence[str]] = None, batch_size: int = 10
    ) -> list[Score]:
        """
        Score a batch of images asynchronously.

        Args:
            image_paths (Sequence[str]): Sequence of paths to image files to be scored.
            objectives (Optional[Sequence[str]]): Optional sequence of objectives corresponding to each image.
                If provided, must match the length of image_paths. Defaults to None.
            batch_size (int): Maximum number of images to score concurrently. Defaults to 10.

        Returns:
            list[Score]: A list of Score objects representing the scoring results for all images.

        Raises:
            ValueError: If the number of objectives does not match the number of image_paths.
        """
        if objectives:
            if len(objectives) != len(image_paths):
                raise ValueError("The number of objectives must match the number of image_paths.")

        if len(image_paths) == 0:
            return []

        prompt_target = getattr(self, "_prompt_target", None)
        results = await batch_task_async(
            task_func=self.score_image_async,
            task_arguments=["image_path", "objective"] if objectives else ["image_path"],
            prompt_target=prompt_target,
            batch_size=batch_size,
            items_to_batch=[image_paths, objectives] if objectives else [image_paths],
        )

        return [score for sublist in results for score in sublist]

    def scale_value_float(self, value: float, min_value: float, max_value: float) -> float:
        """
        Scales a value from 0 to 1 based on the given min and max values. E.g. 3 stars out of 5 stars would be .5.

        Args:
            value (float): The value to be scaled.
            min_value (float): The minimum value of the range.
            max_value (float): The maximum value of the range.

        Returns:
            float: The scaled value.
        """
        if max_value == min_value:
            return 0.0

        normalized_value = (value - min_value) / (max_value - min_value)
        return normalized_value

    @pyrit_json_retry
    async def _score_value_with_llm(
        self,
        *,
        prompt_target: PromptChatTarget,
        system_prompt: str,
        message_value: str,
        message_data_type: PromptDataType,
        scored_prompt_id: str,
        prepended_text_message_piece: Optional[str] = None,
        category: Optional[Sequence[str] | str] = None,
        objective: Optional[str] = None,
        score_value_output_key: str = "score_value",
        rationale_output_key: str = "rationale",
        description_output_key: str = "description",
        metadata_output_key: str = "metadata",
        category_output_key: str = "category",
        attack_identifier: Optional[Dict[str, str]] = None,
    ) -> UnvalidatedScore:
        """
        Send a request to a target, and take care of retries.

        The scorer target response should be JSON with value, rationale, and optional metadata and
        description fields.

        Args:
            prompt_target (PromptChatTarget): The target LLM to send the message to.
            system_prompt (str): The system-level prompt that guides the behavior of the target LLM.
            message_value (str): The actual value or content to be scored by the LLM (e.g., text, image path,
                audio path).
            message_data_type (PromptDataType): The type of the data being sent in the message (e.g., "text",
                "image_path", "audio_path").
            scored_prompt_id (str): The ID of the scored prompt.
            prepended_text_message_piece (Optional[str]): Text context to prepend before the main
                message_value. When provided, creates a multi-piece message with this text first, followed
                by the message_value. Useful for adding objective/context when scoring non-text content.
                Defaults to None.
            category (Optional[Sequence[str] | str]): The category of the score. Can also be parsed from
                the JSON response if not provided. Defaults to None.
            objective (Optional[str]): A description of the objective that is associated with the score,
                used for contextualizing the result. Defaults to None.
            score_value_output_key (str): The key in the JSON response that contains the score value.
                Defaults to "score_value".
            rationale_output_key (str): The key in the JSON response that contains the rationale.
                Defaults to "rationale".
            description_output_key (str): The key in the JSON response that contains the description.
                Defaults to "description".
            metadata_output_key (str): The key in the JSON response that contains the metadata.
                Defaults to "metadata".
            category_output_key (str): The key in the JSON response that contains the category.
                Defaults to "category".
            attack_identifier (Optional[Dict[str, str]]): A dictionary containing attack-specific identifiers.
                Defaults to None.

        Returns:
            UnvalidatedScore: The score object containing the response from the target LLM.
                score_value still needs to be normalized and validated.

        Raises:
            ValueError: If required keys are missing from the response or if the response format is invalid.
            InvalidJsonException: If the response is not valid JSON.
            Exception: For other unexpected errors during scoring.
        """
        conversation_id = str(uuid.uuid4())

        if attack_identifier:
            attack_identifier["scored_prompt_id"] = str(scored_prompt_id)

        prompt_target.set_system_prompt(
            system_prompt=system_prompt,
            conversation_id=conversation_id,
            attack_identifier=attack_identifier,
        )
        prompt_metadata: dict[str, str | int] = {"response_format": "json"}

        # Build message pieces - prepended text context first (if provided), then the main message being scored
        message_pieces: list[MessagePiece] = []

        # Add prepended text context piece if provided (e.g., objective context for non-text scoring)
        if prepended_text_message_piece:
            message_pieces.append(
                MessagePiece(
                    role="user",
                    original_value=prepended_text_message_piece,
                    original_value_data_type="text",
                    converted_value_data_type="text",
                    conversation_id=conversation_id,
                    prompt_target_identifier=prompt_target.get_identifier(),
                    prompt_metadata=prompt_metadata,
                )
            )

        # Add the main message piece being scored
        message_pieces.append(
            MessagePiece(
                role="user",
                original_value=message_value,
                original_value_data_type=message_data_type,
                converted_value_data_type=message_data_type,
                conversation_id=conversation_id,
                prompt_target_identifier=prompt_target.get_identifier(),
                prompt_metadata=prompt_metadata,
            )
        )

        scorer_llm_request = Message(message_pieces)
        try:
            response = await prompt_target.send_prompt_async(message=scorer_llm_request)
        except Exception as ex:
            raise Exception(f"Error scoring prompt with original prompt ID: {scored_prompt_id}") from ex

        response_json: str = ""
        try:
            response_json = response[0].get_value()

            response_json = remove_markdown_json(response_json)
            parsed_response = json.loads(response_json)
            category_response = parsed_response.get(category_output_key)

            if category_response and category:
                raise ValueError("Category is present in the response and an argument")

            # Validate and normalize category to a list of strings
            cat_val = category_response if category_response is not None else category
            normalized_category: Optional[list[str]]
            if cat_val is None:
                normalized_category = None
            elif isinstance(cat_val, str):
                normalized_category = [cat_val]
            elif isinstance(cat_val, list):
                if not all(isinstance(x, str) for x in cat_val):
                    raise ValueError("'category' must be a string or a list of strings")
                normalized_category = cat_val
            else:
                # JSON must yield either a string or a list of strings
                raise ValueError("'category' must be a string or a list of strings")

            # Normalize metadata to a dictionary with string keys and string/int/float values
            raw_md = parsed_response.get(metadata_output_key)
            normalized_md: Optional[Dict[str, Union[str, int, float]]]
            if raw_md is None:
                normalized_md = None
            elif isinstance(raw_md, dict):
                # Coerce keys to str and filter to str/int/float values only
                normalized_md = {str(k): v for k, v in raw_md.items() if isinstance(v, (str, int, float))}
                # If dictionary becomes empty after filtering, keep as empty dict
            elif isinstance(raw_md, (str, int, float)):
                # Wrap primitive metadata into a namespaced field
                normalized_md = {"metadata": raw_md}
            else:
                # Unrecognized metadata shape; drop to avoid downstream errors
                normalized_md = None

            score = UnvalidatedScore(
                raw_score_value=str(parsed_response[score_value_output_key]),
                score_value_description=parsed_response.get(description_output_key),
                score_category=normalized_category,
                score_rationale=parsed_response[rationale_output_key],
                scorer_class_identifier=self.get_identifier(),
                score_metadata=normalized_md,
                message_piece_id=scored_prompt_id,
                objective=objective,
            )

        except json.JSONDecodeError:
            raise InvalidJsonException(message=f"Invalid JSON response: {response_json}")

        except KeyError:
            raise InvalidJsonException(message=f"Invalid JSON response, missing Key: {response_json}")

        return score

    def _extract_objective_from_response(self, response: Message) -> str:
        """
        Extract an objective from the response using the last request (if it exists).

        Args:
            response (Message): The response to extract the objective from.

        Returns:
            str: The objective extracted from the response, or empty string if not found.
        """
        if not response.message_pieces:
            return ""

        piece = response.get_piece()

        if piece.api_role != "assistant":
            return ""

        conversation = self._memory.get_message_pieces(conversation_id=piece.conversation_id)
        last_prompt = max(conversation, key=lambda x: x.sequence)

        # Every text message piece from the last turn
        last_turn_text = "\n".join(
            [
                piece.original_value
                for piece in conversation
                if piece.sequence == last_prompt.sequence - 1 and piece.original_value_data_type == "text"
            ]
        )

        return last_turn_text

    @staticmethod
    async def score_response_async(
        *,
        response: Message,
        objective_scorer: Optional[Scorer] = None,
        auxiliary_scorers: Optional[List[Scorer]] = None,
        role_filter: ChatMessageRole = "assistant",
        objective: Optional[str] = None,
        skip_on_error_result: bool = True,
    ) -> Dict[str, List[Score]]:
        """
        Score a response using an objective scorer and optional auxiliary scorers.

        Args:
            response (Message): Response containing pieces to score.
            objective_scorer (Optional[Scorer]): The main scorer to determine success. Defaults to None.
            auxiliary_scorers (Optional[List[Scorer]]): List of auxiliary scorers to apply. Defaults to None.
            role_filter (ChatMessageRole): Only score pieces with this exact stored role.
                Defaults to "assistant" (real responses only, not simulated).
            objective (Optional[str]): Task/objective for scoring context. Defaults to None.
            skip_on_error_result (bool): If True, skip scoring pieces that have errors. Defaults to True.

        Returns:
            Dict[str, List[Score]]: Dictionary with keys `auxiliary_scores` and `objective_scores`
                containing lists of scores from each type of scorer.

        Raises:
            ValueError: If response is not provided.
        """
        result: Dict[str, List[Score]] = {"auxiliary_scores": [], "objective_scores": []}

        if not response:
            raise ValueError("Response must be provided for scoring.")

        # If no objective_scorer is provided, only run auxiliary_scorers if present
        if objective_scorer is None:
            if auxiliary_scorers:
                aux_scores = await Scorer.score_response_multiple_scorers_async(
                    response=response,
                    scorers=auxiliary_scorers,
                    role_filter=role_filter,
                    objective=objective,
                    skip_on_error_result=skip_on_error_result,
                )
                result["auxiliary_scores"] = aux_scores
            # objective_scores remains empty
            return result

        # Run auxiliary and objective scoring in parallel if auxiliary_scorers is provided
        if auxiliary_scorers:
            aux_task = Scorer.score_response_multiple_scorers_async(
                response=response,
                scorers=auxiliary_scorers,
                role_filter=role_filter,
                objective=objective,
                skip_on_error_result=skip_on_error_result,
            )
            obj_task = objective_scorer.score_async(
                message=response,
                objective=objective,
                skip_on_error_result=skip_on_error_result,
                role_filter=role_filter,
            )
            aux_scores, obj_scores = await asyncio.gather(aux_task, obj_task)
            result["auxiliary_scores"] = aux_scores
            result["objective_scores"] = obj_scores
        else:
            obj_scores = await objective_scorer.score_async(
                message=response,
                objective=objective,
                skip_on_error_result=skip_on_error_result,
                role_filter=role_filter,
            )
            result["objective_scores"] = obj_scores
        return result

    @staticmethod
    async def score_response_multiple_scorers_async(
        *,
        response: Message,
        scorers: List[Scorer],
        role_filter: ChatMessageRole = "assistant",
        objective: Optional[str] = None,
        skip_on_error_result: bool = True,
    ) -> List[Score]:
        """
        Score a response using multiple scorers in parallel.

        This method applies each scorer to the first scorable response piece (filtered by role and error),
        and returns all scores. This is typically used for auxiliary scoring where all results are needed.

        Args:
            response (Message): The response containing pieces to score.
            scorers (List[Scorer]): List of scorers to apply.
            role_filter (ChatMessageRole): Only score pieces with this exact stored role.
                Defaults to "assistant" (real responses only, not simulated).
            objective (Optional[str]): Optional objective description for scoring context.
            skip_on_error_result (bool): If True, skip scoring pieces that have errors (default: True).

        Returns:
            List[Score]: All scores from all scorers
        """
        if not scorers:
            return []

        # Create all scoring tasks, note TEMPORARY fix to prevent multi-piece responses from breaking scoring logic
        tasks = []

        for scorer in scorers:
            tasks.append(
                scorer.score_async(
                    message=response,
                    objective=objective,
                    role_filter=role_filter,
                    skip_on_error_result=skip_on_error_result,
                )
            )

        if not tasks:
            return []

        # Execute all tasks in parallel
        score_lists = await asyncio.gather(*tasks)

        # Flatten the list of lists into a single list
        return [score for scores in score_lists for score in scores]
