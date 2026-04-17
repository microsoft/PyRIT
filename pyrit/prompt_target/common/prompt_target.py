# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import logging
import warnings
from typing import Any, Union, final

from pyrit.identifiers import ComponentIdentifier, Identifiable
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Message
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration, resolve_configuration_compat

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

    _identifier: ComponentIdentifier | None = None

    # Class-level default configuration for this target type.
    #
    # Subclasses **should** override this when their capabilities differ from the base
    # defaults (e.g., to declare multi-turn support or non-text modalities).
    # Overriding is *optional* — if a subclass does not define ``_DEFAULT_CONFIGURATION``,
    # it inherits the base-class default (text-only, single-turn, no JSON response).
    #
    # Per-instance overrides are also possible via the ``custom_configuration``
    # constructor parameter, which takes precedence over the class-level value.
    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(capabilities=TargetCapabilities())

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Auto-promote the deprecated ``_DEFAULT_CAPABILITIES`` class attribute.

        If a subclass defines ``_DEFAULT_CAPABILITIES`` directly, this hook wraps it
        in a ``TargetConfiguration`` and assigns it to ``_DEFAULT_CONFIGURATION``,
        emitting a ``DeprecationWarning`` to guide migration.
        """
        super().__init_subclass__(**kwargs)
        if "_DEFAULT_CAPABILITIES" in cls.__dict__:
            warnings.warn(
                f"{cls.__name__}._DEFAULT_CAPABILITIES is deprecated and will be removed in v0.14.0. "
                "Use _DEFAULT_CONFIGURATION = TargetConfiguration(capabilities=...) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            cls._DEFAULT_CONFIGURATION = TargetConfiguration(capabilities=cls.__dict__["_DEFAULT_CAPABILITIES"])

    def __init__(
        self,
        verbose: bool = False,
        max_requests_per_minute: int | None = None,
        endpoint: str = "",
        model_name: str = "",
        underlying_model: str | None = None,
        custom_configuration: TargetConfiguration | None = None,
        custom_capabilities: TargetCapabilities | None = None,
    ) -> None:
        """
        Initialize the PromptTarget.

        Args:
            verbose (bool): Enable verbose logging. Defaults to False.
            max_requests_per_minute (int | None): Maximum number of requests per minute.
            endpoint (str): The endpoint URL. Defaults to empty string.
            model_name (str): The model name. Defaults to empty string.
            underlying_model (str | None): The underlying model name (e.g., "gpt-4o") for
                identification purposes. This is useful when the deployment name in Azure differs
                from the actual model. If not provided, ``model_name`` will be used for the identifier.
                Defaults to None.
            custom_configuration (TargetConfiguration | None): Override the default configuration
                for this target instance. Useful for targets whose capabilities depend on deployment
                configuration (e.g., Playwright, HTTP). If None, uses the class-level
                ``_DEFAULT_CONFIGURATION``. Defaults to None.
            custom_capabilities (TargetCapabilities | None): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
        """
        custom_configuration = resolve_configuration_compat(
            custom_configuration=custom_configuration,
            custom_capabilities=custom_capabilities,
        )
        self._memory = CentralMemory.get_memory_instance()
        self._verbose = verbose
        self._max_requests_per_minute = max_requests_per_minute
        self._endpoint = endpoint
        self._model_name = model_name
        self._underlying_model = underlying_model
        self._configuration = (
            custom_configuration
            if custom_configuration is not None
            else type(self).get_default_configuration(self._underlying_model)
        )

        if self._verbose:
            logging.basicConfig(level=logging.INFO)

    @final
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Validate, normalize, and send a prompt to the target.

        This is the public entry point called by the prompt normalizer. It:

        1. Validates the message, fetches the conversation from memory, appends ``message``, and runs
           the normalization pipeline (system‑squash, history‑squash, etc.).
        2. Validates the normalized conversation against the target's capabilities.
        3. Delegates to :meth:`_send_prompt_to_target_async` with the normalized
           conversation.

        Subclasses MUST NOT override this method. Override
        :meth:`_send_prompt_to_target_async` instead.

        Args:
            message (Message): The message to send.

        Returns:
            list[Message]: Response messages from the target.

        Raises:
            ValueError: If the message or normalized conversation are empty.
        """
        message.validate()
        normalized_conversation = await self._get_normalized_conversation_async(message=message)
        if not normalized_conversation:
            raise ValueError("Normalization pipeline returned an empty conversation. Cannot send an empty request.")
        self._validate_request(normalized_conversation=normalized_conversation)
        return await self._send_prompt_to_target_async(normalized_conversation=normalized_conversation)

    @abc.abstractmethod
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Target-specific send logic.

        Called by :meth:`send_prompt_async` after validation and normalization.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: Response messages from the target.
        """

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        """
        Validate the normalized conversation before sending to the target.

        Called after the normalization pipeline has run. Validates the last
        message (the current request) for piece count, data types, and checks
        whether the full conversation violates multi-turn constraints.

        Args:
            normalized_conversation: The normalized conversation to validate.
                The last element is the current request message.

        Raises:
            ValueError: if the target does not support the provided message pieces or if the
                conversation violates any constraints based on the target's capabilities.
        """
        message = normalized_conversation[-1]
        n_pieces = len(message.message_pieces)

        custom_configuration_message = (
            "If your target does support this, set the custom_configuration parameter accordingly."
        )
        if not self.capabilities.supports_multi_message_pieces and n_pieces != 1:
            raise ValueError(
                f"This target only supports a single message piece. Received: {n_pieces} pieces. "
                f"{custom_configuration_message}"
            )

        for piece in message.message_pieces:
            piece_type = piece.converted_value_data_type
            supported_types_flat = {t for combo in self.capabilities.input_modalities for t in combo}
            if piece_type not in supported_types_flat:
                supported_types = ", ".join(sorted(supported_types_flat))
                raise ValueError(
                    f"This target supports only the following data types: {supported_types}. Received: {piece_type}. "
                    f"{custom_configuration_message}"
                )

        if not self.capabilities.supports_multi_turn and len(normalized_conversation) > 1:
            raise ValueError(f"This target only supports a single turn conversation. {custom_configuration_message}")

    async def _get_normalized_conversation_async(self, *, message: Message) -> list[Message]:
        """
        Fetch the conversation from memory, append the current message, and run the
        normalization pipeline.

        The original conversation in memory is never mutated. The returned list is an
        ephemeral copy intended only for building the API request body.

        After normalization, the metadata from the original ``message`` is copied
        onto the last normalized message so that downstream code (e.g.
        ``construct_response_from_request``) propagates the correct
        ``conversation_id``, ``labels``, ``attack_identifier``, etc. to the response.

        Args:
            message (Message): The current message to append.

        Returns:
            list[Message]: The normalized conversation (possibly with system prompt squashed,
                history squashed, etc.).
        """
        conversation_id = message.message_pieces[0].conversation_id
        conversation = list(self._memory.get_conversation(conversation_id=conversation_id))
        conversation.append(message)
        normalized = await self.configuration.normalize_async(messages=conversation)
        if normalized:
            # Normalizers may create new Message objects (via Message.from_prompt) with
            # random conversation_ids.  Stamp the correct conversation_id on every
            # message (idempotent for originals, fixes new ones).  Full lineage is only
            # propagated to the last message — it's the one targets use to build the
            # response, and earlier messages carry their own legitimate metadata.
            for msg in normalized:
                for piece in msg.message_pieces:
                    piece.conversation_id = conversation_id
            self._propagate_lineage(source=message, target_message=normalized[-1])
            if len(normalized) > len(conversation):
                logger.warning(
                    "Normalization produced more messages than the input conversation "
                    "(%d → %d). Only the last normalized message has full lineage "
                    "(labels, attack_identifier, etc.). Additional new messages have "
                    "conversation_id set but require manual lineage updates if needed.",
                    len(conversation),
                    len(normalized),
                )
        return normalized

    @staticmethod
    def _propagate_lineage(*, source: Message, target_message: Message) -> None:
        """
        Copy request-lineage metadata from ``source`` onto every piece in ``target_message``.

        Normalizers may create brand-new ``Message`` objects (e.g. ``HistorySquashNormalizer``
        uses ``Message.from_prompt``) that carry fresh random ``conversation_id`` values and
        lack ``labels``, ``attack_identifier``, etc.  This method restores the original
        metadata so that the response built from the normalized message stays part of the
        correct conversation and retains traceability.

        Args:
            source: The original (pre-normalization) message whose metadata is authoritative.
            target_message: The normalized message whose pieces will be updated in place.
        """
        source_piece = source.message_pieces[0]
        for piece in target_message.message_pieces:
            piece.copy_lineage_from(source_piece)

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
        params: dict[str, Any] | None = None,
        children: dict[str, Union[ComponentIdentifier, list[ComponentIdentifier]]] | None = None,
    ) -> ComponentIdentifier:
        """
        Construct the target identifier.

        Builds a ComponentIdentifier with the base target parameters (endpoint,
        model_name, max_requests_per_minute) and merges in any additional params
        or children provided by subclasses.

        Subclasses should call this method in their _build_identifier() implementation
        to set the identifier with their specific parameters.

        Args:
            params (dict[str, Any] | None): Additional behavioral parameters from
                the subclass (e.g., temperature, top_p). Merged into the base params.
            children (dict[str, Union[ComponentIdentifier, list[ComponentIdentifier]]] | None):
                Named child component identifiers.

        Returns:
            ComponentIdentifier: The identifier for this prompt target.
        """
        all_params: dict[str, Any] = {
            "endpoint": self._endpoint,
            "model_name": self._model_name or "",
            "underlying_model_name": self._underlying_model or "",
            "max_requests_per_minute": self._max_requests_per_minute,
            "supports_multi_turn": self.capabilities.supports_multi_turn,
        }
        if params:
            all_params.update(params)

        return ComponentIdentifier.of(self, params=all_params, children=children)

    @property
    def configuration(self) -> TargetConfiguration:
        """
        The configuration of this target instance.

        Defaults to the class-level ``_DEFAULT_CONFIGURATION``. Can be overridden
        per instance via the ``custom_configuration`` constructor parameter, which is useful
        for targets whose capabilities depend on deployment configuration
        (e.g., Playwright, HTTP).

        Returns:
            TargetConfiguration: The configuration for this target.
        """
        return self._configuration

    @property
    def capabilities(self) -> TargetCapabilities:
        """
        The capabilities of this target instance.

        Shorthand for ``self.configuration.capabilities``.

        Returns:
            TargetCapabilities: The capabilities for this target.
        """
        return self._configuration.capabilities

    @classmethod
    def get_default_configuration(cls, underlying_model: str | None = None) -> TargetConfiguration:
        """
        Return the configuration for the given underlying model, falling back to
        the class-level ``_DEFAULT_CONFIGURATION`` when the model is not recognized.

        Args:
            underlying_model (str | None): The underlying model name (e.g., "gpt-4o"),
                or None if not specified.

        Returns:
            TargetConfiguration: Known configuration for the model, or the class's own
            ``_DEFAULT_CONFIGURATION`` if the model is unrecognized or not provided.
        """
        if underlying_model:
            known = TargetCapabilities.get_known_capabilities(underlying_model)
            if known is not None:
                return TargetConfiguration(capabilities=known)
            logger.info(
                "No known capabilities for model '%s'. Falling back to %s._DEFAULT_CONFIGURATION.",
                underlying_model,
                cls.__name__,
            )
        return cls._DEFAULT_CONFIGURATION

    @classmethod
    def get_default_capabilities(cls, underlying_model: str | None = None) -> TargetCapabilities:
        """
        Return the default capabilities for the given model.

        **Deprecated.** Use :meth:`get_default_configuration` instead.
        Will be removed in v0.14.0.

        Returns:
            TargetCapabilities: The capabilities for the given model or class default.
        """
        warnings.warn(
            "get_default_capabilities() is deprecated and will be removed in v0.14.0. "
            "Use get_default_configuration() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_configuration(underlying_model).capabilities

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
