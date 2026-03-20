# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import logging
from typing import Any, Optional, Union

from pyrit.identifiers import ComponentIdentifier, Identifiable
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Message
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities

logger = logging.getLogger(__name__)


class PromptTarget(Identifiable):
    """
    Abstract base class for prompt targets.

    A prompt target is a destination where prompts can be sent to interact with various services,
    models, or APIs. This class defines the interface that all prompt targets must implement.
    """

    _memory: MemoryInterface

    # A list of PromptConverters that are supported by the prompt target.
    # An empty list implies that the prompt target supports all converters.
    supported_converters: list[Any]

    _identifier: Optional[ComponentIdentifier] = None

    # Class-level default capabilities for this target type.
    #
    # Subclasses **should** override this when their capabilities differ from the base
    # defaults (e.g., to declare multi-turn support or non-text modalities).
    # Overriding is *optional* — if a subclass does not define ``_DEFAULT_CAPABILITIES``,
    # it inherits the base-class default (text-only, single-turn, no JSON response).
    #
    # Per-instance overrides are also possible via the ``custom_capabilities``
    # constructor parameter, which takes precedence over the class-level value.
    _DEFAULT_CAPABILITIES: TargetCapabilities = TargetCapabilities()

    def __init__(
        self,
        verbose: bool = False,
        max_requests_per_minute: Optional[int] = None,
        endpoint: str = "",
        model_name: str = "",
        underlying_model: Optional[str] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
    ) -> None:
        """
        Initialize the PromptTarget.

        Args:
            verbose (bool): Enable verbose logging. Defaults to False.
            max_requests_per_minute (int, Optional): Maximum number of requests per minute.
            endpoint (str): The endpoint URL. Defaults to empty string.
            model_name (str): The model name. Defaults to empty string.
            underlying_model (str, Optional): The underlying model name (e.g., "gpt-4o") for
                identification purposes. This is useful when the deployment name in Azure differs
                from the actual model. If not provided, `model_name` will be used for the identifier.
                Defaults to None.
            custom_capabilities (TargetCapabilities, Optional): Override the default capabilities for
                this target instance. Useful for targets whose capabilities depend on deployment
                configuration (e.g., Playwright, HTTP). If None, uses the class-level
                ``_DEFAULT_CAPABILITIES``. Defaults to None.
        """
        self._memory = CentralMemory.get_memory_instance()
        self._verbose = verbose
        self._max_requests_per_minute = max_requests_per_minute
        self._endpoint = endpoint
        self._model_name = model_name
        self._underlying_model = underlying_model
        self._capabilities = (
            custom_capabilities
            if custom_capabilities is not None
            else type(self).get_default_capabilities(underlying_model)
        )

        if self._verbose:
            logging.basicConfig(level=logging.INFO)

    @abc.abstractmethod
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Send a normalized prompt async to the prompt target.

        Returns:
            list[Message]: A list of message responses. Most targets return a single message,
                but some (like response target with tool calls) may return multiple messages.
        """

    def _validate_request(self, *, message: Message) -> None:
        """
        Validate the provided message.

        Args:
            message: The message to validate.

        Raises:
            ValueError: if the target does not support the provided message pieces or if the message
                violates any constraints based on the target's capabilities. This includes checks
                for the number of message pieces, supported data types, and multi-turn conversation support.

        """
        n_pieces = len(message.message_pieces)
        if n_pieces == 0:
            raise ValueError("Message must contain at least one message piece. Received: 0 pieces.")

        custom_capabilities_message = (
            "If your target does support this, set the custom_capabilities parameter accordingly."
        )
        if not self.capabilities.supports_multi_message_pieces and n_pieces != 1:
            raise ValueError(
                f"This target only supports a single message piece. Received: {n_pieces} pieces. "
                f"{custom_capabilities_message}"
            )

        for piece in message.message_pieces:
            piece_type = piece.converted_value_data_type
            supported_types_flat = {t for combo in self.capabilities.input_modalities for t in combo}
            if piece_type not in supported_types_flat:
                supported_types = ", ".join(sorted(supported_types_flat))
                raise ValueError(
                    f"This target supports only the following data types: {supported_types}. Received: {piece_type}. "
                    f"{custom_capabilities_message}"
                )

        if not self.capabilities.supports_multi_turn:
            request = message.message_pieces[0]
            messages = self._memory.get_message_pieces(conversation_id=request.conversation_id)

            if len(messages) > 0:
                raise ValueError(f"This target only supports a single turn conversation. {custom_capabilities_message}")

    def set_model_name(self, *, model_name: str) -> None:
        """
        Set the model name for this target.

        Args:
            model_name (str): The model name to set.
        """
        self._model_name = model_name

    def dispose_db_engine(self) -> None:
        """
        Dispose database engine to release database connections and resources.
        """
        self._memory.dispose_engine()

    def _create_identifier(
        self,
        *,
        params: Optional[dict[str, Any]] = None,
        children: Optional[dict[str, Union[ComponentIdentifier, list[ComponentIdentifier]]]] = None,
    ) -> ComponentIdentifier:
        """
        Construct the target identifier.

        Builds a ComponentIdentifier with the base target parameters (endpoint,
        model_name, max_requests_per_minute) and merges in any additional params
        or children provided by subclasses.

        Subclasses should call this method in their _build_identifier() implementation
        to set the identifier with their specific parameters.

        Args:
            params (Optional[Dict[str, Any]]): Additional behavioral parameters from
                the subclass (e.g., temperature, top_p). Merged into the base params.
            children (Optional[Dict[str, Union[ComponentIdentifier, List[ComponentIdentifier]]]]):
                Named child component identifiers.

        Returns:
            ComponentIdentifier: The identifier for this prompt target.
        """
        model_name = self._underlying_model or self._model_name or ""

        all_params: dict[str, Any] = {
            "endpoint": self._endpoint,
            "model_name": model_name,
            "max_requests_per_minute": self._max_requests_per_minute,
            "supports_multi_turn": self.capabilities.supports_multi_turn,
        }
        if params:
            all_params.update(params)

        return ComponentIdentifier.of(self, params=all_params, children=children)

    @property
    def capabilities(self) -> TargetCapabilities:
        """
        The capabilities of this target instance.

        Defaults to the class-level ``_DEFAULT_CAPABILITIES``. Can be overridden
        per instance via the ``capabilities`` constructor parameter, which is useful
        for targets whose capabilities depend on deployment configuration
        (e.g., Playwright, HTTP).

        Returns:
            TargetCapabilities: The capabilities for this target.
        """
        return self._capabilities

    @classmethod
    def get_default_capabilities(cls, underlying_model: Optional[str]) -> TargetCapabilities:
        """
        Return the capabilities for the given underlying model, falling back to
        the class-level ``_DEFAULT_CAPABILITIES`` when the model is not recognized.

        Args:
            underlying_model (str | None): The underlying model name (e.g., "gpt-4o"),
                or None if not specified.

        Returns:
            TargetCapabilities: Known capabilities for the model, or the class's own
            ``_DEFAULT_CAPABILITIES`` if the model is unrecognized or not provided.
        """
        if underlying_model:
            known = TargetCapabilities.get_known_capabilities(underlying_model)
            if known is not None:
                return known
            logger.warning(
                "No known capabilities for model '%s'. Falling back to %s._DEFAULT_CAPABILITIES.",
                underlying_model,
                cls.__name__,
            )
        return cls._DEFAULT_CAPABILITIES

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this target.

        Subclasses can override this method to call _create_identifier() with
        their specific params and children.

        The base implementation calls _create_identifier() with no extra parameters,
        which works for targets that don't have model-specific settings.

        Returns:
            ComponentIdentifier: The identifier for this prompt target.
        """
        return self._create_identifier()
