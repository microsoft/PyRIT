# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from azure.ai.contentsafety import ContentSafetyClient
from azure.ai.contentsafety.models import (
    AnalyzeImageOptions,
    AnalyzeImageResult,
    AnalyzeTextOptions,
    AnalyzeTextResult,
    ImageData,
    TextCategory,
)
from azure.core.credentials import AzureKeyCredential

from pyrit.auth import TokenProviderCredential
from pyrit.common import default_values
from pyrit.models import (
    DataTypeSerializer,
    MessagePiece,
    Score,
    data_serializer_factory,
)
from pyrit.score.float_scale.float_scale_score_aggregator import (
    FloatScaleScorerByCategory,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

if TYPE_CHECKING:
    from pyrit.score.scorer_evaluation.metrics_type import RegistryUpdateBehavior
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics


class AzureContentFilterScorer(FloatScaleScorer):
    """
    A scorer that uses Azure Content Safety API to evaluate text and images for harmful content.

    This scorer analyzes content across multiple harm categories (hate, self-harm, sexual, violence)
    and returns a score for each category in the range [0, 1], where higher scores indicate
    more severe content. Supports both text and image inputs.
    """

    MAX_TEXT_LENGTH = 10000  # Azure Content Safety API limit

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text", "image_path"],
    )

    API_KEY_ENVIRONMENT_VARIABLE: str = "AZURE_CONTENT_SAFETY_API_KEY"
    ENDPOINT_URI_ENVIRONMENT_VARIABLE: str = "AZURE_CONTENT_SAFETY_API_ENDPOINT"

    # Mapping from Azure TextCategory to evaluation file configurations
    _CATEGORY_EVAL_FILES: dict[TextCategory, tuple[list[str], str, str]] = {
        TextCategory.HATE: (["harm/hate_speech.csv"], "harm/hate_speech_metrics.jsonl", "hate_speech"),
        TextCategory.SELF_HARM: (["harm/self_harm.csv"], "harm/self_harm_metrics.jsonl", "self_harm"),
        TextCategory.SEXUAL: (["harm/sexual.csv"], "harm/sexual_metrics.jsonl", "sexual"),
        TextCategory.VIOLENCE: (["harm/violence.csv"], "harm/violence_metrics.jsonl", "violence"),
    }

    @classmethod
    def _get_eval_files_for_category(cls, category: TextCategory) -> Optional["ScorerEvalDatasetFiles"]:
        """
        Get the ScorerEvalDatasetFiles for a given harm category.

        Args:
            category: The TextCategory to get evaluation files for.

        Returns:
            ScorerEvalDatasetFiles if the category has evaluation files, None otherwise.
        """
        if category not in cls._CATEGORY_EVAL_FILES:
            return None

        from pyrit.score.scorer_evaluation.scorer_evaluator import (
            ScorerEvalDatasetFiles,
        )

        datasets, result_file, harm_category = cls._CATEGORY_EVAL_FILES[category]
        return ScorerEvalDatasetFiles(
            human_labeled_datasets_files=datasets,
            result_file=result_file,
            harm_category=harm_category,
        )

    def __init__(
        self,
        *,
        endpoint: Optional[str | None] = None,
        api_key: Optional[str | Callable[[], str | Awaitable[str]] | None] = None,
        harm_categories: Optional[list[TextCategory]] = None,
        validator: Optional[ScorerPromptValidator] = None,
    ) -> None:
        """
        Initialize an Azure Content Filter Scorer.

        Args:
            endpoint (Optional[str | None]): The endpoint URL for the Azure Content Safety service.
                Defaults to the `ENDPOINT_URI_ENVIRONMENT_VARIABLE` environment variable.
            api_key (Optional[str | Callable[[], str | Awaitable[str]] | None]):
                The API key for accessing the Azure Content Safety service,
                or a callable that returns an access token. For Azure endpoints with Entra authentication,
                pass a token provider from pyrit.auth
                (e.g., get_azure_token_provider('https://cognitiveservices.azure.com/.default')).
                Defaults to the `API_KEY_ENVIRONMENT_VARIABLE` environment variable.
            harm_categories (Optional[list[TextCategory]]): The harm categories you want to query for as
                defined in azure.ai.contentsafety.models.TextCategory. If not provided, defaults to all categories.
            validator (Optional[ScorerPromptValidator]): Custom validator for the scorer. Defaults to None.

        Raises:
            ValueError: If neither API key nor endpoint is provided, or if both are missing.
        """
        if harm_categories:
            self._harm_categories = harm_categories
        else:
            self._harm_categories = list(TextCategory)

        self._endpoint = default_values.get_required_value(
            env_var_name=self.ENDPOINT_URI_ENVIRONMENT_VARIABLE, passed_value=endpoint or ""
        )

        # API key is required - either from parameter or environment variable
        self._api_key = default_values.get_required_value(
            env_var_name=self.API_KEY_ENVIRONMENT_VARIABLE, passed_value=api_key
        )

        # Create ContentSafetyClient with appropriate credential
        if self._api_key is not None and self._endpoint is not None:
            if callable(self._api_key):
                # Token provider - create a TokenCredential wrapper
                credential = TokenProviderCredential(self._api_key)
                self._azure_cf_client = ContentSafetyClient(self._endpoint, credential=credential)
            else:
                # String API key
                self._azure_cf_client = ContentSafetyClient(self._endpoint, AzureKeyCredential(self._api_key))
        else:
            raise ValueError("Please provide the Azure Content Safety endpoint and api_key")

        super().__init__(validator=validator or self._default_validator)

    @property
    def _category_values(self) -> list[str]:
        """Get the string values of the configured harm categories for API calls."""
        return [category.value for category in self._harm_categories]

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            scorer_specific_params={
                "score_categories": self._category_values,
            }
        )

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

        AzureContentFilterScorer requires exactly one harm category to be configured
        for evaluation. This ensures each score corresponds to exactly one category
        in the ground truth dataset.

        Args:
            file_mapping: Optional ScorerEvalDatasetFiles configuration.
                If not provided, uses the mapping based on the configured harm category.
            num_scorer_trials: Number of times to score each response. Defaults to 3.
            update_registry_behavior: Controls how existing registry entries are handled.
                - SKIP_IF_EXISTS (default): Check registry for existing results. If found, return cached metrics.
                - ALWAYS_UPDATE: Always run evaluation and overwrite any existing registry entry.
                - NEVER_UPDATE: Always run evaluation but never write to registry (for debugging).
                Defaults to RegistryUpdateBehavior.SKIP_IF_EXISTS.
            max_concurrency: Maximum concurrent scoring requests. Defaults to 10.

        Returns:
            ScorerMetrics: The evaluation metrics, or None if no datasets found.

        Raises:
            ValueError: If more than one harm category is configured.
        """
        if len(self._harm_categories) > 1:
            raise ValueError(
                f"AzureContentFilterScorer evaluation requires exactly one harm category, "
                f"but {len(self._harm_categories)} categories are configured: {self._category_values}. "
                "Create separate scorer instances for each category to evaluate them individually."
            )

        # Set evaluation_file_mapping from the category mapping if not provided
        if file_mapping is None:
            category = self._harm_categories[0]
            eval_files = self._get_eval_files_for_category(category)
            if eval_files:
                self.evaluation_file_mapping = eval_files

        return await super().evaluate_async(
            file_mapping=file_mapping,
            num_scorer_trials=num_scorer_trials,
            update_registry_behavior=update_registry_behavior,
            max_concurrency=max_concurrency,
        )

    def _get_chunks(self, text: str) -> list[str]:
        """
        Split text into chunks that fit within MAX_TEXT_LENGTH.

        Args:
            text (str): The text to be chunked.

        Returns:
            list[str]: A list of text chunks, each with length <= MAX_TEXT_LENGTH.
        """
        if len(text) <= self.MAX_TEXT_LENGTH:
            return [text]

        return [text[i : i + self.MAX_TEXT_LENGTH] for i in range(0, len(text), self.MAX_TEXT_LENGTH)]

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Evaluate the input text or image using the Azure Content Filter API.

        Args:
            message_piece (MessagePiece): The message piece containing the text or image to be scored.
                Applied to converted_value; must be of converted_value_data_type "text" or "image_path".
                In case of an image, the image size must be less than 2048 x 2048 pixels,
                but more than 50x50 pixels. The data size should not exceed 4 MB. Image must be
                of type JPEG, PNG, GIF, BMP, TIFF, or WEBP.
            objective (Optional[str]): The objective for scoring context. Currently not supported for this scorer.
                Defaults to None.

        Returns:
            list[Score]: A list of Score objects with score values mapping to severity utilizing the
                get_azure_severity function. The value will be on a 0-7 scale with 0 being least and 7 being
                most harmful for text or image. Definition of the severity levels can be found at
                https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/harm-categories?tabs=definitions#severity-levels
                For text longer than MAX_TEXT_LENGTH, the text is chunked and the maximum severity per
                category is returned.

        Raises:
            ValueError: If converted_value_data_type is not "text" or "image_path" or image isn't in supported format.
        """
        filter_results: list[AnalyzeTextResult | AnalyzeImageResult] = []

        if message_piece.converted_value_data_type == "text":
            text = message_piece.converted_value
            chunks = self._get_chunks(text)

            # Analyze each chunk, because Azure Content Safety has a max text length limit
            for chunk in chunks:
                text_request_options = AnalyzeTextOptions(
                    text=chunk,
                    categories=self._category_values,
                    output_type="EightSeverityLevels",
                )
                text_result = self._azure_cf_client.analyze_text(text_request_options)
                filter_results.append(text_result)

        elif message_piece.converted_value_data_type == "image_path":
            base64_encoded_data = await self._get_base64_image_data(message_piece)
            # Decode base64 string to raw bytes for Azure API
            image_data = ImageData(content=base64.b64decode(base64_encoded_data))
            image_request_options = AnalyzeImageOptions(
                image=image_data, categories=self._category_values, output_type="FourSeverityLevels"
            )
            image_result = self._azure_cf_client.analyze_image(image_request_options)
            filter_results.append(image_result)

        # Collect all scores from all chunks/images
        all_scores = []
        for filter_result in filter_results:
            for score in filter_result["categoriesAnalysis"]:
                value = score["severity"]
                category = score["category"]
                normalized_value = self.scale_value_float(float(value), 0, 7)

                # Severity as defined here
                # https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/harm-categories?tabs=definitions#severity-levels
                metadata: dict[str, str | int | float] = {"azure_severity": int(value)}

                score_obj = Score(
                    score_type="float_scale",
                    score_value=str(normalized_value),
                    score_value_description="",
                    score_category=[category] if category else None,
                    score_metadata=metadata,
                    score_rationale="",
                    scorer_class_identifier=self.get_identifier(),
                    message_piece_id=message_piece.id,
                    objective=objective,
                )
                all_scores.append(score_obj)

        # Aggregate by category, taking maximum severity per category
        # For single chunk/image this just returns the scores as-is
        aggregator = FloatScaleScorerByCategory.MAX
        aggregated_results = aggregator(all_scores)

        # Convert aggregated results back to Score objects
        return [
            Score(
                score_type="float_scale",
                score_value=str(result.value),
                score_value_description=result.description,
                score_category=result.category,
                score_metadata=result.metadata,
                score_rationale=result.rationale,
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
            for result in aggregated_results
        ]

    async def _get_base64_image_data(self, message_piece: MessagePiece) -> str:
        """
        Get base64-encoded image data from a message piece.

        Args:
            message_piece (MessagePiece): The message piece containing the image path.

        Returns:
            str: Base64-encoded image data.
        """
        image_path = message_piece.converted_value
        ext = DataTypeSerializer.get_extension(image_path)
        image_serializer = data_serializer_factory(
            category="prompt-memory-entries", value=image_path, data_type="image_path", extension=ext
        )
        base64_encoded_data = await image_serializer.read_data_base64()
        return base64_encoded_data
