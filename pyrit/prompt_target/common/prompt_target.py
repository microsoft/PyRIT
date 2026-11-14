# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import logging
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any, ClassVar, Self, final

from pyrit.common import AuthMode, print_deprecation_message
from pyrit.common.async_compatibility import legacy_sync_override
from pyrit.common.attack_result_scope import get_current_attack_result_id
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.message_normalizer import MessageListNormalizer
from pyrit.models import (
    ComponentIdentifier,
    Conversation,
    Identifiable,
    JsonResponseConfig,
    Message,
    MessagePiece,
    RequestGenerationConfig,
    RequestGenerationInfo,
    RequestTraceContext,
    TargetIdentifier,
    WebSearchConfig,
    WebSearchMode,
)
from pyrit.models.messages.tool_content import validate_tool_conversation
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityName,
    TargetCapabilities,
    get_known_capabilities,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.target_history import filter_non_replayable_messages
from pyrit.prompt_target.common.target_send_context import TargetSendContext
from pyrit.prompt_target.common.target_trace_config import TargetTraceConfig, target_trace_context

logger = logging.getLogger(__name__)

# ``AuthMode`` is imported above (and re-exported from this module for backward
# compatibility); it is canonically defined in ``pyrit.common.auth_mode`` so
# ``pyrit.auth`` resolvers can consume it without depending on the target layer.


class PromptTarget(Identifiable):
    """
    Abstract base class for prompt targets.

    A prompt target is a destination where prompts can be sent to interact with various services,
    models, or APIs. This class defines the interface that all prompt targets must implement.
    """

    _memory: MemoryInterface

    # A list of Converters that are supported by the prompt target.
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
    _DEFAULT_TRACE_ENABLED: ClassVar[bool] = False
    _SUPPORTS_TOOL_CALL_HISTORY: ClassVar[bool] = False
    _SUPPORTED_GENERATION_CAPABILITIES: ClassVar[frozenset[CapabilityName]] = frozenset()
    _DISPATCH_CAPABILITIES: ClassVar[ContextVar[tuple["PromptTarget", TargetCapabilities] | None]] = ContextVar(
        "target_dispatch_capabilities", default=None
    )
    _CONFIGURATION_SCOPES: ClassVar[ContextVar[tuple[tuple["PromptTarget", TargetConfiguration], ...]]] = ContextVar(
        "target_configuration_scopes", default=()
    )

    # Declarative auth facts consumed by the create-target service and catalog.
    # Kept off ``TargetCapabilities`` (auth is a construction/credential axis, not
    # a message-handling capability) and out of the identity hash. It is surfaced
    # on ``TargetIdentifier`` only as a ``Param.ClassAttr`` (``Evaluate.Exclude``)
    # so the registry can read it into ``TargetMetadata`` without building an
    # instance — never as an identity input or a constructor argument.
    #
    # ``supported_auth_modes`` lists the auth modes the create-target API accepts
    # for this type. Base default is api-key only; targets that can authenticate
    # via an ambient Azure identity (e.g. OpenAI, Azure ML, Azure Blob Storage,
    # Prompt Shield) override this to add ``"identity"``, and OpenAI targets also
    # add ``"none"`` for OpenAI-compatible servers that need no key.
    supported_auth_modes: ClassVar[tuple[AuthMode, ...]] = ("api_key",)

    # Targets that upload files from the machine running PyRIT name the constructor parameter that
    # confines those uploads. The create-target API sets it to the folder the server operator
    # configured and does not let callers choose it.
    upload_directory_parameter: ClassVar[str | None] = None

    # Path parameters callers may set through the API, when creating a target or overriding a saved
    # one, such as a folder the target's own session works in. The upload folder above is not one,
    # since the server sets it. The API rejects every other parameter that can name a file or folder
    # on the server; direct Python use is unaffected.
    caller_path_parameters: ClassVar[frozenset[str]] = frozenset()

    # Targets whose construction can run model repository code name the boolean constructor
    # parameter that enables it (e.g. ``"trust_remote_code"``). The create-target API builds
    # them only for an administrator when that parameter resolves true; direct Python use is
    # unaffected.
    code_execution_parameter: ClassVar[str | None] = None

    # Targets that can authenticate with this server's own stored credentials (e.g. an ambient
    # managed identity) name the constructor parameter a caller supplies to use their own
    # credential instead (e.g. ``"sas_token"``). When a caller does not supply it, the
    # create-target API builds the target only for an administrator, so a non-administrator
    # cannot point the server's credentials at a caller-chosen resource; direct Python use is
    # unaffected.
    server_credential_parameter: ClassVar[str | None] = None

    # Credential parameters that, left empty, let the target fall back to a credential from this
    # server (an environment variable, or the server's Azure identity), each mapped to the value
    # that turns that off, or None when only an auth mode does: ``"identity"`` doesn't read a
    # parameter mapped to None, and ``"none"`` reads none of them. A saved target never falls back
    # to this server's credentials, so a saved entry that would is refused. Choosing ``"identity"``
    # isn't a fallback.
    server_fallback_parameters: ClassVar[Mapping[str, str | None]] = MappingProxyType({})

    # Parameters that say where the target sends requests (a URL, or a path the target adds to
    # one), each mapped to whether an empty value takes this server's setting. A saved target sets
    # each one that would, since a server setting can carry a credential, and none can carry a
    # credential itself: a user name or password, a SAS token, a fragment, or a query other than
    # ``api-version``.
    destination_parameters: ClassVar[Mapping[str, bool]] = MappingProxyType({})

    # Environment variables the target's client reads extra headers from, whatever the target's own
    # settings say. A server can put a credential there, so a saved target isn't built while one is set.
    server_header_variables: ClassVar[tuple[str, ...]] = ()

    # Why this target type can't be saved even without a credential, or None when it can.
    not_saveable_reason: ClassVar[str | None] = None

    # The parameter a server credential from the administrator's list fills with its key, and the parameter
    # whose address must be the credential's endpoint; None when the target can't use one.
    listed_credential_parameters: ClassVar[tuple[str, str] | None] = None

    # Constructor parameters that choose where requests go or how they authenticate. A private
    # copy built from API input keeps its source's values for these, so a credential stays
    # with the endpoint it was configured for.
    COPY_LOCKED_PARAMETER_NAMES: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """
        Validate that subclasses follow the keyword-only ``__init__`` contract.

        Args:
            **kwargs: Additional keyword arguments passed to the superclass.

        Raises:
            TypeError: If the subclass ``__init__`` accepts positional parameters
                after ``self``.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="PromptTarget")

    @classmethod
    def get_auth_mode_parameters(cls, *, auth_mode: AuthMode) -> dict[str, object]:
        """
        Translate request-level authentication intent into constructor parameters.

        Targets that must retain explicit auth intent override this hook. Most
        targets infer authentication from their credential parameters and need no
        additional constructor input.

        Args:
            auth_mode (AuthMode): Authentication mode selected by the caller.

        Returns:
            dict[str, object]: Additional constructor parameters.
        """
        return {}

    @classmethod
    async def create_async(cls, **kwargs: Any) -> Self:
        """
        Build the target on the calling event loop.

        Targets whose construction needs slow setup, such as a first import, override this to do that setup in a
        worker thread first. The constructor itself still runs on the calling loop, so the tasks and locks it creates
        belong to that loop.

        Args:
            **kwargs (Any): The constructor arguments.

        Returns:
            Self: The new target.
        """
        return cls(**kwargs)

    @classmethod
    def _validate_auth_mode(cls, auth_mode: AuthMode | None) -> None:
        """
        Reject an authentication mode this target type does not support.

        Args:
            auth_mode (AuthMode | None): Authentication mode selected by the caller, or None for the default.

        Raises:
            ValueError: If ``auth_mode`` is set and is not one of ``supported_auth_modes``.
        """
        if auth_mode is not None and auth_mode not in cls.supported_auth_modes:
            raise ValueError(
                f"{cls.__name__} does not support auth_mode={auth_mode!r}. "
                f"Use one of: {', '.join(cls.supported_auth_modes)}."
            )

    def __init__(
        self,
        *,
        verbose: bool = False,
        max_requests_per_minute: int | None = None,
        endpoint: str = "",
        model_name: str = "",
        underlying_model: str | None = None,
        custom_configuration: TargetConfiguration | None = None,
        trace_config: TargetTraceConfig | None = None,
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
            trace_config: Request tracing configuration. Defaults to the target's tracing policy.
        """
        self._memory = CentralMemory.get_memory_instance()
        self._verbose = verbose
        self._max_requests_per_minute = max_requests_per_minute
        self._endpoint = endpoint
        self._model_name = model_name
        self._underlying_model = underlying_model
        self._trace_config = trace_config or TargetTraceConfig(enabled=self._DEFAULT_TRACE_ENABLED)
        self._configuration = (
            custom_configuration
            if custom_configuration is not None
            else type(self).get_default_configuration(self._underlying_model)
        )
        capabilities = self._mask_generation_capabilities(capabilities=self.capabilities)
        if capabilities is not self.capabilities:
            self.apply_capabilities(capabilities=capabilities)
        if custom_configuration is None and underlying_model is None and self._SUPPORTS_TOOL_CALL_HISTORY:
            known = get_known_capabilities(model_name)
            if known is not None:
                self.apply_capabilities(
                    capabilities=self.capabilities.model_copy(
                        update={
                            "input_modalities": self.capabilities.input_modalities
                            | frozenset(
                                combo
                                for combo in known.input_modalities
                                if combo & {"function_call", "function_call_output"}
                            )
                        }
                    )
                )

        if self._verbose:
            logging.basicConfig(level=logging.INFO)

    def validate_history(self, messages: Sequence[Message]) -> None:
        """
        Check history data types and tool payloads without sending or changing history.

        This checks native input support, not normalization policy. Empty histories
        and histories ending with an assistant message or unanswered call are permitted.
        It does not load media or validate a future request.

        Args:
            messages: Complete ordered history to replay.

        Raises:
            ValueError: An effective data type is unsupported or tool history is invalid.
        """
        supported = set(self.capabilities.supported_input_modalities)
        unsupported = {
            piece.converted_value_data_type for message in messages for piece in message.message_pieces
        } - supported
        if unsupported:
            raise ValueError(f"The target does not support these history data types: {', '.join(sorted(unsupported))}.")
        self.validate_tool_history(messages)

    def validate_tool_history(self, messages: Sequence[Message]) -> None:
        """
        Check stored tool history without sending, normalizing, or retrieving media.

        Empty histories and histories ending with an unanswered call are permitted.
        Targets extend this check with provider-specific payload constraints.
        Callers check capability requirements separately before replaying a draft.

        Args:
            messages: Complete ordered history, including calls for any results.

        Raises:
            ValueError: Tool content, roles, or call/result links are invalid.
        """
        validate_tool_conversation(messages)

    def validate_web_search_config(self, config: WebSearchConfig) -> None:
        """
        Check whether this adapter can enforce a request's search policy.

        Args:
            config (WebSearchConfig): Requested target-hosted search policy.

        Raises:
            ValueError: If search is required, or a search-enabled adapter cannot disable it.
        """
        if config.mode == WebSearchMode.DEFAULT:
            return
        if config.mode == WebSearchMode.DISABLED and not self.capabilities.supports_web_search:
            return
        raise ValueError(
            f"{type(self).__name__} cannot enforce web search mode {config.mode.value!r}. "
            "Use an adapter that implements this policy and declares supports_web_search."
        )

    def validate_history_replay(self) -> None:
        """
        Check configuration support for initializing a new conversation from stored history.

        Native editable history or an explicit history adapter is sufficient.
        Targets with a provider-specific restoration path can override this check.
        This does not validate message content or guarantee that a live session exists.

        Raises:
            ValueError: If neither native support nor a configured history adapter is available.
        """
        if not self.configuration.includes(
            capability=CapabilityName.MULTI_TURN
        ) and self.configuration.pipeline.has_normalizer_for(capability=CapabilityName.MULTI_TURN):
            return
        self.configuration.ensure_can_handle(capability=CapabilityName.EDITABLE_HISTORY)

    @final
    async def send_prompt_async(
        self,
        *,
        message: Message,
        normalizer_overrides: Mapping[CapabilityName, MessageListNormalizer[Message]] | None = None,
        send_context: TargetSendContext | None = None,
    ) -> list[Message]:
        """
        Validate, normalize, and send a prompt to the target.

        This is the public entry point called by the prompt normalizer. It:

        1. Validates the message.
        2. Loads memory history and runs the target's normalization pipeline.
        3. Validates the normalized conversation against the target's capabilities.
        4. Delegates to ``_send_prompt_to_target_async`` with the normalized conversation.

        Subclasses MUST NOT override this method. Override
        ``_send_prompt_to_target_async`` instead.

        Args:
            message (Message): The message to send.
            normalizer_overrides: Optional per-send target normalizer overrides.
            send_context: Optional internal coordination contract for caller-owned
                history selection and send lifecycle state.

        Returns:
            list[Message]: Response messages from the target.

        Raises:
            ValueError: If the message or normalized conversation are empty.
        """
        for piece in message.message_pieces:
            piece.prompt_metadata.pop(RequestTraceContext.METADATA_KEY, None)
            RequestGenerationInfo.clear_metadata(piece.prompt_metadata)
            piece.prompt_metadata[RequestTraceContext.REQUEST_METADATA_KEY] = 1
        message.validate()
        generation_config = self.validate_request_generation_config(message)
        for piece in message.message_pieces:
            piece_config = RequestGenerationConfig.from_metadata(metadata=piece.prompt_metadata)
            RequestGenerationConfig.clear_metadata(piece.prompt_metadata)
            piece.prompt_metadata.update(piece_config.to_metadata())
        web_search_config = self._get_web_search_config(message)
        self.validate_web_search_config(web_search_config)
        conversation_id = message.get_piece().conversation_id or ""
        if send_context and send_context.conversation_id != conversation_id:
            raise ValueError("Target send context conversation_id does not match the current request conversation_id.")
        if send_context:
            send_context.begin_send()

        send_succeeded = False
        try:
            normalized_conversation = await self._get_normalized_conversation_async(
                message=message,
                normalizer_overrides=normalizer_overrides,
                send_context=send_context,
            )
            if not normalized_conversation:
                raise ValueError("Normalization pipeline returned an empty conversation. Cannot send an empty request.")
            # Normalizers can merge history or discard piece metadata. Only the original
            # outbound message selects this send's options, never normalized history.
            normalized_request = normalized_conversation[-1]
            for piece in normalized_request.message_pieces:
                RequestGenerationConfig.clear_metadata(piece.prompt_metadata)
                RequestGenerationInfo.clear_metadata(piece.prompt_metadata)
            normalized_request.get_piece().prompt_metadata.update(generation_config.to_metadata())
            if self._get_web_search_config(normalized_request) != web_search_config:
                raise ValueError("Normalization changed the request's web search policy.")
            self._validate_request(normalized_conversation=normalized_conversation)
            self.validate_request_generation_config(normalized_request)
            with target_trace_context(
                config=self._trace_config, request=message, normalized_request=normalized_conversation[-1]
            ):
                if send_context:
                    send_context.mark_target_invoked()
                try:
                    response = await self._send_prompt_to_target_async(normalized_conversation=normalized_conversation)
                finally:
                    generation_info = RequestGenerationInfo.from_metadata(
                        metadata=normalized_request.get_piece().prompt_metadata
                    )
                    if generation_info is not None:
                        generation_info = generation_info.model_copy(
                            update={"request_piece_ids": tuple(str(piece.id) for piece in message.message_pieces)}
                        )
                        for piece in message.message_pieces:
                            piece.prompt_metadata.update(generation_info.to_metadata())
            self._validate_request_generation_info(config=generation_config, info=generation_info)
            for response_message in response:
                for piece in response_message.message_pieces:
                    piece.prompt_metadata = {
                        key: value
                        for key, value in piece.prompt_metadata.items()
                        if key not in (RequestTraceContext.METADATA_KEY, RequestTraceContext.REQUEST_METADATA_KEY)
                    }
                    RequestGenerationConfig.clear_metadata(piece.prompt_metadata)
                    RequestGenerationInfo.clear_metadata(piece.prompt_metadata)
                    if generation_info is not None:
                        piece.prompt_metadata.update(generation_info.to_metadata())
            send_succeeded = True
            return response
        finally:
            if send_context:
                send_context.finish_send(succeeded=send_succeeded)

    def validate_request_generation_config(self, message: Message) -> RequestGenerationConfig:
        """
        Validate current-message options without loading history or performing provider I/O.

        Returns:
            RequestGenerationConfig: The validated request options.

        Raises:
            ValueError: The override is invalid or unsupported.
        """
        config = RequestGenerationConfig.from_message(message)
        for capability in sorted(config.required_capabilities):
            if capability not in self._SUPPORTED_GENERATION_CAPABILITIES or not self.capabilities.includes(
                capability=capability
            ):
                raise ValueError(
                    f"{type(self).__name__} does not support runtime {capability.name.lower()} "
                    "for this model configuration."
                )
        for name, capability in config.FIELD_CAPABILITIES.items():
            value = getattr(config, name)
            if value is not None:
                bounds = self.generation_range(capability=capability)
                if bounds is not None and not bounds[0] <= value <= bounds[1]:
                    raise ValueError(f"{name} must be between {bounds[0]:g} and {bounds[1]:g} inclusive.")
        self._validate_request_generation_config(config)
        return config

    def _validate_request_generation_config(self, config: RequestGenerationConfig) -> None:
        """Validate adapter-specific joint parameter and model/mode restrictions."""

    def _validate_request_generation_info(
        self, *, config: RequestGenerationConfig, info: RequestGenerationInfo | None
    ) -> None:
        """
        Check that adapter evidence agrees with explicit overrides.

        Raises:
            RuntimeError: The adapter omitted or contradicted the selected override.
        """
        recorded = info.model_dump() if info is not None else {}
        for name, value in config.model_dump(exclude_none=True).items():
            if recorded.get(name) != {"requested": value, "resolved": value, "source": "request"}:
                raise RuntimeError(
                    f"{type(self).__name__} returned missing or inconsistent generation evidence for {name}."
                )

    @abc.abstractmethod
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Target-specific send logic.

        Called by ``send_prompt_async`` after validation and normalization.

        Adapters opting into ``_SUPPORTED_GENERATION_CAPABILITIES`` declare bounds via
        ``generation_range`` and validate modes in ``_validate_request_generation_config``.
        After asynchronous payload preparation, revalidate before provider I/O,
        apply overrides to the actual payload (including passthrough settings),
        and write ``RequestGenerationInfo`` to the normalized request's first piece.
        Record it immediately before I/O and reuse the resolution on transport
        retries or tool continuations. The base associates evidence with original
        request pieces and responses; missing or inconsistent override evidence
        is an adapter error, not a successful send.
        Wrappers dispatching directly to children must clear incoming generation
        evidence before each child invocation, not between that child's retries.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: Response messages from the target.
        """

    async def _send_with_capabilities_async(
        self, *, normalized_conversation: list[Message], capabilities: TargetCapabilities
    ) -> list[Message]:
        """
        Send already-normalized messages with the owning composite's capability flags.

        Args:
            normalized_conversation: Messages normalized by the owning composite.
            capabilities: The composite's effective capability flags.

        Returns:
            list[Message]: Responses from the child target.
        """
        token = self._DISPATCH_CAPABILITIES.set((self, capabilities))
        try:
            return await self._send_prompt_to_target_async(normalized_conversation=normalized_conversation)
        finally:
            self._DISPATCH_CAPABILITIES.reset(token)

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
        supported_types_flat = {t for combo in self.capabilities.input_modalities for t in combo}
        for turn in normalized_conversation:
            for piece in turn.message_pieces:
                piece_type = piece.converted_value_data_type
                if (
                    piece_type in {"function_call", "function_call_output", "tool_call"}
                    and piece_type not in supported_types_flat
                ):
                    raise ValueError(
                        f"This target does not support tool-history modality '{piece_type}'. "
                        f"{custom_configuration_message}"
                    )
        if not self.configuration.includes(capability=CapabilityName.MULTI_MESSAGE_PIECES) and n_pieces != 1:
            raise ValueError(
                f"This target only supports a single message piece. Received: {n_pieces} pieces. "
                f"{custom_configuration_message}"
            )

        for piece in message.message_pieces:
            piece_type = piece.converted_value_data_type
            if piece_type not in supported_types_flat:
                supported_types = ", ".join(sorted(supported_types_flat))
                raise ValueError(
                    f"This target supports only the following data types: {supported_types}. Received: {piece_type}. "
                    f"{custom_configuration_message}"
                )

        if not self.configuration.includes(capability=CapabilityName.MULTI_TURN) and len(normalized_conversation) > 1:
            raise ValueError(f"This target only supports a single turn conversation. {custom_configuration_message}")

    @staticmethod
    def _get_web_search_config(message: Message) -> WebSearchConfig:
        configs = {
            WebSearchConfig.from_metadata(metadata=piece.prompt_metadata)
            for piece in message.message_pieces
            if WebSearchConfig.METADATA_KEY in piece.prompt_metadata
        }
        if len(configs) > 1:
            raise ValueError("Message pieces have conflicting web search policies.")
        return next(iter(configs), WebSearchConfig())

    async def _get_normalized_conversation_async(
        self,
        *,
        message: Message,
        normalizer_overrides: Mapping[CapabilityName, MessageListNormalizer[Message]] | None = None,
        send_context: TargetSendContext | None = None,
    ) -> list[Message]:
        """
        Build the target-facing conversation and run the normalization pipeline.

        Memory history is loaded and the current message is appended before the
        target normalization pipeline runs.

        The original conversation in memory is never mutated. The returned list is an
        ephemeral copy intended only for building the API request body.

        After normalization, every output piece is stamped with the current
        conversation ID. Normalizers own all other output metadata; removed
        ``prompt_metadata`` keys are not restored.

        Args:
            message (Message): The current message to append.
            normalizer_overrides: Optional per-send target normalizer overrides.
            send_context: Optional internal coordination contract for caller-approved
                persisted history.

        Returns:
            list[Message]: The normalized conversation (possibly with system prompt squashed,
                history squashed, etc.).
        """
        conversation_id = message.message_pieces[0].conversation_id
        persisted_messages = (
            list(await self._memory.get_conversation_messages_async(conversation_id=conversation_id))
            if conversation_id
            else []
        )
        persisted_messages = filter_non_replayable_messages(messages=persisted_messages)
        conversation = send_context.select_history(messages=persisted_messages) if send_context else persisted_messages
        conversation.append(message)
        normalized = await self.configuration.normalize_async(
            messages=conversation,
            normalizer_overrides=normalizer_overrides,
        )
        if normalized:
            for msg in normalized:
                for piece in msg.message_pieces:
                    piece.conversation_id = conversation_id
        return normalized

    def set_model_name(self, *, model_name: str) -> None:
        """
        Set the model name for this target.

        Args:
            model_name (str): The model name to set.
        """
        self._model_name = model_name

    def set_system_prompt(
        self,
        *,
        system_prompt: str,
        conversation_id: str,
    ) -> None:
        """
        Inject a system prompt into memory for the given conversation.

        Writes a ``system``-role message so the target's normalization pipeline
        (or the target itself, when it natively supports system prompts) will
        pick it up on the next ``send_prompt_async`` call.

        If the target does not natively support system prompts, whether this
        call is ultimately honored depends on the target's
        ``CapabilityHandlingPolicy``:

        * ``ADAPT`` — the normalization pipeline (e.g. system squash) will
          fold the system message into user content on the wire.
        * ``RAISE`` — the first send after the system prompt is set will
          raise, because the pipeline cannot adapt the missing capability.

        Args:
            system_prompt (str): The system prompt text to set.
            conversation_id (str): The conversation id to attach the prompt to.

        Raises:
            ValueError: If the target does not support multi-turn conversations, or
                supports neither editable history nor native system prompts.
            RuntimeError: If the conversation already has messages.
        """
        print_deprecation_message(
            old_item="PromptTarget.set_system_prompt",
            new_item="PromptTarget.set_system_prompt_async",
            removed_in="1.4.0",
        )
        if not self.capabilities.supports_multi_turn or not (
            self.capabilities.supports_editable_history or self.capabilities.supports_system_prompt
        ):
            raise ValueError(
                f"Target {type(self).__name__} does not support setting a system prompt. "
                "It must support multi-turn conversations and either editable history or native system prompts."
            )

        messages = self._memory.get_conversation_messages(conversation_id=conversation_id)

        if messages:
            raise RuntimeError("Conversation already exists, system prompt needs to be set at the beginning")

        self._memory.add_conversation_to_memory(
            conversation=Conversation(
                conversation_id=conversation_id,
                target_identifier=self.get_identifier(),
                attack_result_id=get_current_attack_result_id(),
            )
        )
        self._memory.add_message_to_memory(
            request=MessagePiece(
                role="system",
                conversation_id=conversation_id,
                original_value=system_prompt,
                converted_value=system_prompt,
            ).to_message(),
        )

    @legacy_sync_override(lambda: PromptTarget.set_system_prompt)
    async def set_system_prompt_async(
        self,
        *,
        system_prompt: str,
        conversation_id: str,
    ) -> None:
        """
        Inject a system prompt into memory for the given conversation.

        Writes a ``system``-role message so the target's normalization pipeline
        (or the target itself, when it natively supports system prompts) will
        pick it up on the next ``send_prompt_async`` call.

        If the target does not natively support system prompts, whether this
        call is ultimately honored depends on the target's
        ``CapabilityHandlingPolicy``:

        * ``ADAPT`` — the normalization pipeline (e.g. system squash) will
          fold the system message into user content on the wire.
        * ``RAISE`` — the first send after the system prompt is set will
          raise, because the pipeline cannot adapt the missing capability.

        Args:
            system_prompt (str): The system prompt text to set.
            conversation_id (str): The conversation id to attach the prompt to.

        Raises:
            ValueError: If the target does not support multi-turn conversations, or
                supports neither editable history nor native system prompts.
            RuntimeError: If the conversation already has messages.
        """
        if not self.capabilities.supports_multi_turn or not (
            self.capabilities.supports_editable_history or self.capabilities.supports_system_prompt
        ):
            raise ValueError(
                f"Target {type(self).__name__} does not support setting a system prompt. "
                "It must support multi-turn conversations and either editable history or native system prompts."
            )

        messages = await self._memory.get_conversation_messages_async(conversation_id=conversation_id)

        if messages:
            raise RuntimeError("Conversation already exists, system prompt needs to be set at the beginning")

        (
            await self._memory.add_conversation_to_memory_async(
                conversation=Conversation(
                    conversation_id=conversation_id,
                    target_identifier=self.get_identifier(),
                    attack_result_id=get_current_attack_result_id(),
                )
            )
        )
        (
            await self._memory.add_message_to_memory_async(
                request=MessagePiece(
                    role="system",
                    conversation_id=conversation_id,
                    original_value=system_prompt,
                    converted_value=system_prompt,
                ).to_message()
            )
        )

    async def reset_conversation_async(self, *, conversation_id: str) -> None:
        """
        Release any target-side state held for a conversation.

        The attack execution scope calls this for objective-target conversations
        recorded at the common dispatch boundary. Targets that keep external state
        keyed by conversation (a websocket connection, a browser page, an upstream
        session) override this to close or discard it. Targets that are stateless
        between calls need not override it.

        This is best-effort cleanup, so implementations should not raise for a
        conversation id they do not recognize, and should be safe to call more
        than once for the same id.

        Args:
            conversation_id (str): The conversation id to release state for.
        """

    def dispose_db_engine(self) -> None:
        """
        Dispose database engine to release database connections and resources.
        """
        print_deprecation_message(
            old_item="PromptTarget.dispose_db_engine",
            new_item="PromptTarget.dispose_db_engine_async",
            removed_in="1.4.0",
        )
        self._memory.dispose_engine()

    @legacy_sync_override(lambda: PromptTarget.dispose_db_engine)
    async def dispose_db_engine_async(self) -> None:
        """
        Dispose database engine to release database connections and resources.
        """
        (await self._memory.dispose_engine_async())

    def _create_identifier(
        self,
        *,
        params: dict[str, Any] | None = None,
        targets: list[ComponentIdentifier] | None = None,
    ) -> ComponentIdentifier:
        """
        Construct the target identifier.

        Builds a ``TargetIdentifier`` with the base target params (endpoint,
        model_name, max_requests_per_minute) and the target's promoted child slot.
        The child slot is exposed as an explicit named parameter (mirroring
        ``TargetIdentifier``'s promoted field) so it cannot drift into an untyped
        ``children`` dict.

        Subclasses should call this method in their _build_identifier() implementation
        to set the identifier with their specific parameters.

        Args:
            params (dict[str, Any] | None): Additional behavioral parameters from
                the subclass (e.g., temperature, top_p). Merged into the base params.
            targets (list[ComponentIdentifier] | None): Inner targets of a
                multi-target (e.g., ``RoundRobinTarget``), promoted to
                ``TargetIdentifier.targets``.

        Returns:
            ComponentIdentifier: The identifier for this prompt target.
        """
        return TargetIdentifier.of(
            self,
            params=params,
            endpoint=self._endpoint,
            model_name=self._model_name or "",
            underlying_model_name=self._underlying_model or "",
            max_requests_per_minute=self._max_requests_per_minute,
            targets=targets,
        )

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
        for target, configuration in reversed(self._CONFIGURATION_SCOPES.get()):
            if target is self:
                return configuration
        return self._configuration

    @contextmanager
    def capabilities_scope(self, capabilities: TargetCapabilities) -> Generator[None, None, None]:
        """
        Use saved capabilities for this task without replacing the live target or its state.

        Args:
            capabilities: Capabilities used for normalization, validation, and provider serialization.

        Yields:
            None: The task-local configuration scope, restored even on cancellation.
        """
        configuration = self.configuration
        scoped = TargetConfiguration(
            capabilities=capabilities,
            policy=configuration.policy,
            normalizer_overrides=configuration.normalizer_overrides,
        )
        token = self._CONFIGURATION_SCOPES.set((*self._CONFIGURATION_SCOPES.get(), (self, scoped)))
        try:
            yield
        finally:
            self._CONFIGURATION_SCOPES.reset(token)

    @property
    def capabilities(self) -> TargetCapabilities:
        """
        The effective capabilities of this target for the current dispatch.

        Normally these are ``self.configuration.capabilities``. A composite can
        supply task-local flags during child dispatch without changing the
        child's configuration or independently initiated sends.

        Returns:
            TargetCapabilities: The capabilities for this target.
        """
        override = self._DISPATCH_CAPABILITIES.get()
        if override is not None and override[0] is self:
            return override[1]
        return self.configuration.capabilities

    @property
    def generation_ranges(self) -> dict[str, tuple[float, float]]:
        """Known runtime bounds keyed by generation option name, independent of capability support."""
        return {
            name: bounds
            for name, capability in RequestGenerationConfig.FIELD_CAPABILITIES.items()
            if (bounds := self.generation_range(capability=capability)) is not None
        }

    def generation_range(self, *, capability: CapabilityName) -> tuple[float, float] | None:
        """
        Look up inclusive runtime bounds independently of capability support.

        Args:
            capability (CapabilityName): The generation option to look up.

        Returns:
            tuple[float, float] | None: Inclusive bounds, or None when unspecified.
        """
        return None

    def apply_capabilities(self, *, capabilities: TargetCapabilities) -> None:
        """
        Replace this target's capabilities, preserving the existing handling policy.
        The normalization pipeline is rebuilt from the input capabilities and the
        current policy.

        Policy is preserved because it expresses user intent (ADAPT vs RAISE),
        independent of what the probe found. To change policy or normalizer
        overrides, build a new ``TargetConfiguration`` and pass it via
        ``custom_configuration`` at construction time instead.

        Note:
            This mutates the target's identifier (derived from the configuration).

        Args:
            capabilities (TargetCapabilities): The capabilities to install on
                this instance.
        """
        self._configuration = TargetConfiguration(
            capabilities=self._mask_generation_capabilities(capabilities=capabilities),
            policy=self._configuration.policy,
            normalizer_overrides=self._configuration.normalizer_overrides,
        )

    @classmethod
    def _mask_generation_capabilities(cls, *, capabilities: TargetCapabilities) -> TargetCapabilities:
        """
        Disable generation options that this adapter does not implement.

        Returns:
            TargetCapabilities: Capabilities restricted to the adapter's implemented generation options.
        """
        unsupported = {
            capability.value: False
            for capability in RequestGenerationConfig.FIELD_CAPABILITIES.values()
            if capability not in cls._SUPPORTED_GENERATION_CAPABILITIES and capabilities.includes(capability=capability)
        }
        return capabilities.model_copy(update=unsupported) if unsupported else capabilities

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
            known = get_known_capabilities(underlying_model)
            if known is not None:
                return TargetConfiguration(
                    capabilities=cls._mask_generation_capabilities(
                        capabilities=known.model_copy(
                            update={
                                "input_modalities": frozenset(
                                    combo
                                    for combo in known.input_modalities
                                    if cls._SUPPORTS_TOOL_CALL_HISTORY
                                    or not combo & {"function_call", "function_call_output"}
                                ),
                            }
                        )
                    )
                )
            logger.info(
                "No known capabilities for model '%s'. Falling back to %s._DEFAULT_CONFIGURATION.",
                underlying_model,
                cls.__name__,
            )
        configuration = cls._DEFAULT_CONFIGURATION
        capabilities = cls._mask_generation_capabilities(capabilities=configuration.capabilities)
        if capabilities is configuration.capabilities:
            return configuration
        return TargetConfiguration(
            capabilities=capabilities,
            policy=configuration.policy,
            normalizer_overrides=configuration.normalizer_overrides,
        )

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

    def is_response_format_json(self, message_piece: MessagePiece) -> bool:
        """
        Check if the response format is JSON and ensure the target supports it.

        Args:
            message_piece: A MessagePiece object with a `prompt_metadata` dictionary that may
                include a "response_format" key.

        Returns:
            bool: True if the response format is JSON, False otherwise.

        Raises:
            ValueError: If "json" response format is requested but unsupported.
        """
        config = self._get_json_response_config(message_piece=message_piece)
        return config.enabled

    def _get_json_response_config(self, *, message_piece: MessagePiece) -> JsonResponseConfig:
        """
        Get the JSON response configuration from the message piece metadata.

        Args:
            message_piece: A MessagePiece object with a `prompt_metadata` dictionary that may
                include JSON response configuration.

        Returns:
            JsonResponseConfig: The JSON response configuration.

        Raises:
            ValueError: If JSON response format is requested but unsupported.
        """
        config = JsonResponseConfig.from_metadata(metadata=message_piece.prompt_metadata)

        if config.enabled and not self.capabilities.supports_json_output:
            target_name = self.get_identifier().class_name
            raise ValueError(f"This target {target_name} does not support JSON response format.")

        return config
