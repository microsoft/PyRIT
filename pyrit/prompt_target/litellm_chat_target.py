# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import itertools
import logging
import os
from collections.abc import Awaitable, Callable, MutableSequence
from typing import Any, NoReturn, cast

from pyrit.auth import ensure_async_token_provider
from pyrit.exceptions import (
    EmptyResponseException,
    PyritException,
    RateLimitException,
    get_retry_max_num_attempts,
    handle_bad_request_exception,
)
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    PromptDataType,
)
from pyrit.prompt_target.common.chat_completions_message_builder import (
    build_multimodal_chat_messages_async,
    build_response_format,
    build_text_chat_messages,
    is_text_only_conversation,
)
from pyrit.prompt_target.common.chat_completions_response_parser import (
    build_content_filter_message,
    build_response_pieces_async,
    capture_token_usage,
    extract_partial_content,
    is_content_filter_response,
    validate_chat_completion_response,
)
from pyrit.prompt_target.common.json_response_config import _JsonResponseConfig
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import (
    TargetCapabilities,
    get_known_capabilities,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import (
    limit_requests_per_minute,
    validate_temperature,
    validate_top_p,
)
from pyrit.prompt_target.openai.openai_chat_audio_config import OpenAIChatAudioConfig

logger = logging.getLogger(__name__)

# Conservative capability profile used when litellm metadata can't tell us more (unknown
# model, or litellm not importable at construction). Deliberately text-only with no JSON so
# we never advertise a capability we can't honor.
_TEXT_INPUT: frozenset[frozenset[PromptDataType]] = cast(
    "frozenset[frozenset[PromptDataType]]",
    frozenset({frozenset({"text"})}),
)


def _build_input_modalities(*, image: bool, audio: bool) -> frozenset[frozenset[PromptDataType]]:
    """
    Build the set of supported input-modality combinations from capability flags.

    Always includes text. Enumerates every non-empty combination of the present modalities
    (text, image, audio) so callers can advertise mixed-modality messages.

    Args:
        image (bool): Whether image input is supported.
        audio (bool): Whether audio input is supported.

    Returns:
        frozenset[frozenset[PromptDataType]]: The supported input-modality combinations.
    """
    present: list[PromptDataType] = ["text"]
    if image:
        present.append("image_path")
    if audio:
        present.append("audio_path")

    combos = [
        frozenset(combo) for size in range(1, len(present) + 1) for combo in itertools.combinations(present, size)
    ]
    return frozenset(combos)


def _build_output_modalities(*, audio: bool) -> frozenset[frozenset[PromptDataType]]:
    """
    Build the set of supported output-modality combinations from capability flags.

    Args:
        audio (bool): Whether audio output is supported.

    Returns:
        frozenset[frozenset[PromptDataType]]: The supported output-modality combinations.
    """
    output: list[frozenset[PromptDataType]] = [cast("frozenset[PromptDataType]", frozenset({"text"}))]
    if audio:
        output.append(cast("frozenset[PromptDataType]", frozenset({"audio_path"})))
        output.append(cast("frozenset[PromptDataType]", frozenset({"text", "audio_path"})))
    return frozenset(output)


class LiteLLMChatTarget(PromptTarget):
    """
    Chat target that uses the LiteLLM SDK to access 100+ LLM providers.

    Unlike ``OpenAIChatTarget`` (which uses the OpenAI SDK directly), this target calls
    ``litellm.acompletion()`` so it can route to any provider LiteLLM supports (Anthropic,
    AWS Bedrock, Google Vertex, Cohere, etc.) without requiring a separate proxy server.

    LiteLLM speaks the OpenAI *Chat Completions* wire format, so this target shares its
    request-building and response-parsing logic with ``OpenAIChatTarget`` via the helpers in
    ``pyrit.prompt_target.common.chat_completions_message_builder`` and
    ``pyrit.prompt_target.common.chat_completions_response_parser``.

    LiteLLM reads provider API keys from environment variables automatically
    (e.g. ``ANTHROPIC_API_KEY``, ``AWS_ACCESS_KEY_ID``). You can also pass ``api_key``
    explicitly, or a callable/token-provider for Entra-style auth.

    Install the optional dependency with ``pip install pyrit[litellm]`` (or ``pip install
    pyrit[all]``).

    Args:
        model_name: LiteLLM model string (e.g. ``"anthropic/claude-sonnet-4-6"``,
            ``"bedrock/anthropic.claude-v2"``, ``"vertex_ai/gemini-pro"``). Falls back to the
            ``LITELLM_MODEL`` environment variable.
        api_key: Optional API key, or a callable that returns an access token (sync or async).
            When omitted, falls back to the ``LITELLM_API_KEY`` environment variable and then
            to LiteLLM's own provider-specific environment variable lookup.
        endpoint: Optional base URL override (e.g. for a self-hosted proxy or LiteLLM gateway).
            Falls back to the ``LITELLM_ENDPOINT`` environment variable.
        headers: Optional extra HTTP headers forwarded to the provider (``extra_headers``).
        temperature: Sampling temperature (0-2).
        top_p: Nucleus sampling probability (0-1).
        max_tokens: Maximum number of tokens to generate. This is the single token-limit knob:
            LiteLLM normalizes it to the parameter each model/provider expects (for example, it
            maps to ``max_completion_tokens`` for OpenAI reasoning and gpt-5 models). To send a
            provider-specific token parameter directly instead, use ``extra_body_parameters``.
        frequency_penalty: Penalize frequently generated tokens.
        presence_penalty: Penalize tokens already present in the conversation.
        seed: Best-effort deterministic sampling seed.
        n: Number of completions to generate.
        stop: Stop sequence(s).
        audio_response_config: Optional audio-output configuration (voice + format). When set,
            audio modality is enabled on the request (``modalities``/``audio``) for models that
            support it (e.g. ``gpt-4o-audio-preview``), and audio responses are saved as
            ``audio_path`` pieces alongside their transcript.
        drop_unsupported_params: When True (the default), LiteLLM silently drops any request
            parameter the resolved provider does not support instead of raising. This is a core
            LiteLLM behavior (it maps to LiteLLM's ``drop_params``) that lets a single target
            send the full OpenAI parameter set across many providers. Set to False for strict
            validation, where an unsupported parameter raises instead of being dropped.
        extra_body_parameters: Additional provider parameters merged into the request body
            (passthrough). These may also override the target's defaults (including
            ``drop_params``, e.g. pass ``{"drop_params": False}`` to force strict validation for
            a single request, or ``{"timeout": 30}`` to set a per-request timeout).
        underlying_model: The underlying model name (e.g. ``"gpt-4o"``) used for capability
            lookup and identification when the provider/model string differs from a known model.
        max_requests_per_minute: Client-side request cap.
        custom_configuration: Override the derived target configuration.
    """

    # Fallback only. The real per-instance configuration is normally derived from LiteLLM's
    # model metadata at construction time (see ``_derive_capabilities_from_litellm``).
    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_editable_history=True,
            supports_system_prompt=True,
            input_modalities=_TEXT_INPUT,
        )
    )

    def __init__(
        self,
        *,
        model_name: str | None = None,
        api_key: str | Callable[[], str | Awaitable[str]] | None = None,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        seed: int | None = None,
        n: int | None = None,
        stop: str | list[str] | None = None,
        audio_response_config: OpenAIChatAudioConfig | None = None,
        drop_unsupported_params: bool = True,
        extra_body_parameters: dict[str, Any] | None = None,
        underlying_model: str | None = None,
        max_requests_per_minute: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
    ) -> None:
        """
        Initialize a LiteLLMChatTarget.

        Raises:
            ValueError: If model_name is not provided and LITELLM_MODEL env var is not set.
        """
        resolved_model = model_name or os.environ.get("LITELLM_MODEL", "")
        if not resolved_model:
            raise ValueError("model_name is required. Pass it directly or set the LITELLM_MODEL environment variable.")

        validate_temperature(temperature)
        validate_top_p(top_p)

        super().__init__(
            model_name=resolved_model,
            underlying_model=underlying_model,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration,
        )

        # Resolve api_key: explicit value/callable > LITELLM_API_KEY env var > None (LiteLLM
        # then reads provider-specific env vars itself). ``ensure_async_token_provider`` wraps a
        # sync token provider so we can uniformly await it at request time.
        if api_key is None:
            api_key = os.environ.get("LITELLM_API_KEY")
        self._api_key = ensure_async_token_provider(api_key)

        self._endpoint = endpoint or os.environ.get("LITELLM_ENDPOINT")
        self._headers = headers
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._frequency_penalty = frequency_penalty
        self._presence_penalty = presence_penalty
        self._seed = seed
        self._n = n
        self._stop = stop
        self._audio_response_config = audio_response_config
        self._drop_unsupported_params = drop_unsupported_params

        # Merge audio-output config into the passthrough body (modalities + audio params), so it
        # rides the same passthrough OpenAIChatTarget uses.
        if audio_response_config:
            audio_params = audio_response_config.to_extra_body_parameters()
            extra_body_parameters = {**audio_params, **extra_body_parameters} if extra_body_parameters else audio_params

        self._extra_body_parameters = extra_body_parameters

        # Delegate transient/rate-limit retry to LiteLLM (provider-aware: honors ``Retry-After``
        # and per-provider rate-limit semantics), rather than stacking PyRIT's
        # ``pyrit_target_retry`` (which would double-retry). Derive the count from PyRIT's global
        # ``RETRY_MAX_NUM_ATTEMPTS`` convention; ``num_retries`` is a retry count so it is
        # attempts minus one. Per-request timeout is left to LiteLLM's own default; advanced
        # callers can override it via ``extra_body_parameters={"timeout": ...}``.
        self._num_retries = max(get_retry_max_num_attempts() - 1, 0)

        # Capability precedence: custom_configuration > known underlying_model profile >
        # LiteLLM-derived default > conservative fallback. The base __init__ already applied the
        # first two; only derive from LiteLLM metadata when neither was supplied.
        if custom_configuration is None:
            known = get_known_capabilities(underlying_model) if underlying_model else None
            if known is None:
                derived = self._derive_capabilities_from_litellm(resolved_model)
                if derived is not None:
                    self._configuration = TargetConfiguration(capabilities=derived)

    @staticmethod
    def _import_litellm() -> Any:
        try:
            import litellm
        except ImportError as e:
            raise ImportError(
                "The litellm package is required for LiteLLMChatTarget. Install it with `pip install pyrit[litellm]`."
            ) from e
        return litellm

    def _derive_capabilities_from_litellm(self, model: str) -> TargetCapabilities | None:
        """
        Best-effort capability derivation from LiteLLM's model metadata.

        Uses LiteLLM's own model-capability helpers (``supports_vision``,
        ``supports_response_schema``, ``get_supported_openai_params``) rather than reinventing a
        per-provider capability table. Returns None if LiteLLM is unavailable so the caller falls
        back to ``_DEFAULT_CONFIGURATION``.

        Args:
            model (str): The LiteLLM model string to inspect.

        Returns:
            TargetCapabilities | None: The derived capabilities, or None if LiteLLM is
            unavailable.
        """
        try:
            litellm = self._import_litellm()
        except ImportError:
            return None

        def _supports(attr: str) -> bool:
            fn = getattr(litellm, attr, None)
            if fn is None:
                return False
            try:
                return bool(fn(model))
            except Exception:
                return False

        supports_vision = _supports("supports_vision")
        supports_json_schema = _supports("supports_response_schema")
        supports_audio_input = _supports("supports_audio_input")
        supports_audio_output = _supports("supports_audio_output")

        try:
            supported_params = litellm.get_supported_openai_params(model=model) or []
        except Exception:
            supported_params = []
        supports_json_output = supports_json_schema or ("response_format" in supported_params)

        return TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_editable_history=True,
            supports_system_prompt=True,
            supports_json_output=supports_json_output,
            supports_json_schema=supports_json_schema,
            input_modalities=_build_input_modalities(image=supports_vision, audio=supports_audio_input),
            output_modalities=_build_output_modalities(audio=supports_audio_output),
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with LiteLLM-specific behavioral parameters.

        The API key is intentionally excluded.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "endpoint": self._endpoint,
                "temperature": self._temperature,
                "top_p": self._top_p,
                "max_tokens": self._max_tokens,
                "frequency_penalty": self._frequency_penalty,
                "presence_penalty": self._presence_penalty,
                "seed": self._seed,
                "n": self._n,
                "stop": self._stop,
            },
        )

    def is_json_response_supported(self) -> bool:
        """
        Whether this target honors a JSON ``response_format`` request.

        Returns:
            bool: True if the target advertises JSON output support.
        """
        return self.capabilities.supports_json_output

    # Not decorated with ``pyrit_target_retry``: LiteLLM owns transient/rate-limit retry via
    # ``num_retries`` (see ``__init__``). Stacking both would multiply the retry count.
    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        litellm = self._import_litellm()

        message = normalized_conversation[-1]
        request_piece: MessagePiece = message.message_pieces[0]

        logger.info(f"Sending prompt to LiteLLM target ({self._model_name}): {message}")

        json_config = self._get_json_response_config(message_piece=request_piece)
        messages = await self._build_chat_messages_async(normalized_conversation)
        api_key = await self._resolve_api_key_async()
        body = self._construct_request_body(messages=messages, json_config=json_config, api_key=api_key)

        try:
            response = await litellm.acompletion(**body)
        except Exception as exc:
            return self._handle_litellm_exception(exc=exc, request=request_piece)

        # Content filtering is red-team critical: surface it as an error Message (not an
        # exception) so attacks can continue and blocked-content scorers can still score.
        if is_content_filter_response(response):
            logger.warning("Output content filtered by content policy.")
            return [
                build_content_filter_message(
                    response=response,
                    request=request_piece,
                    partial_content=extract_partial_content(response),
                )
            ]

        validate_chat_completion_response(response=response)
        return [await self._construct_message_from_response_async(response=response, request=request_piece)]

    async def _resolve_api_key_async(self) -> str | None:
        """
        Resolve the api_key to a concrete string, awaiting async token providers.

        Returns:
            str | None: The resolved API key, or None when no key is configured.
        """
        api_key = self._api_key
        if api_key is None or isinstance(api_key, str):
            return api_key

        result = api_key()
        if isinstance(result, Awaitable):
            result = await result
        return result

    async def _build_chat_messages_async(self, conversation: MutableSequence[Message]) -> list[dict[str, Any]]:
        # Text-only conversations use the simpler {"role", "content": str} form, which the widest
        # set of OpenAI-"compatible" providers accept.
        if is_text_only_conversation(conversation):
            return build_text_chat_messages(conversation)

        prefer_transcript_for_history = bool(
            self._audio_response_config and self._audio_response_config.prefer_transcript_for_history
        )
        return await build_multimodal_chat_messages_async(
            conversation, prefer_transcript_for_history=prefer_transcript_for_history
        )

    def _construct_request_body(
        self,
        *,
        messages: list[dict[str, Any]],
        json_config: _JsonResponseConfig,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            # Drop provider-unsupported params (LiteLLM's ``drop_params``). As a cross-provider
            # target we send the full OpenAI param set, but providers support different subsets;
            # with this enabled LiteLLM drops what a provider does not accept instead of raising.
            # Controlled by the ``drop_unsupported_params`` constructor arg; advanced callers can
            # still override per request via ``extra_body_parameters={"drop_params": ...}``.
            "drop_params": self._drop_unsupported_params,
            "api_key": api_key,
            "api_base": self._endpoint,
            "extra_headers": self._headers,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
            "frequency_penalty": self._frequency_penalty,
            "presence_penalty": self._presence_penalty,
            "seed": self._seed,
            "n": self._n,
            "stop": self._stop,
            "num_retries": self._num_retries,
            "response_format": build_response_format(json_config=json_config),
        }

        # Passthrough for arbitrary provider params (may override defaults above, e.g. drop_params).
        if self._extra_body_parameters:
            body.update(self._extra_body_parameters)

        return {k: v for k, v in body.items() if v is not None}

    async def _construct_message_from_response_async(self, *, response: Any, request: MessagePiece) -> Message:
        audio_format = self._audio_response_config.audio_format if self._audio_response_config else "wav"
        pieces = await build_response_pieces_async(response=response, request=request, audio_format=audio_format)
        if not pieces:
            raise EmptyResponseException(message="Failed to extract any response content from LiteLLM.")
        capture_token_usage(pieces=pieces, response=response)
        self._capture_response_cost(pieces=pieces, response=response)
        return Message(message_pieces=pieces)

    def _capture_response_cost(self, *, pieces: list[MessagePiece], response: Any) -> None:
        """
        Record LiteLLM's computed per-call dollar cost into the first piece's metadata.

        LiteLLM attaches the spend for a completion at ``response._hidden_params["response_cost"]``
        and, failing that, can recompute it via ``litellm.completion_cost``. Cost is provider- and
        model-aware and is unique to LiteLLM (the raw OpenAI SDK response carries no cost), so this
        mirrors ``capture_token_usage`` and writes ``token_usage_cost`` alongside the token counts.
        The value is stored as a string to honor the ``prompt_metadata`` value contract, and any
        failure is swallowed so cost accounting never breaks the response path.

        Args:
            pieces (list[MessagePiece]): The constructed response pieces.
            response (Any): The LiteLLM completion response object.
        """
        if not pieces:
            return
        cost = self._extract_response_cost(response=response)
        if cost is None:
            return
        pieces[0].prompt_metadata["token_usage_cost"] = str(cost)

    @staticmethod
    def _extract_response_cost(*, response: Any) -> float | None:
        """
        Pull the per-call cost from a LiteLLM response, or None when it cannot be determined.

        Prefers LiteLLM's authoritative post-call ``_hidden_params["response_cost"]`` (which may be
        ``0.0`` for free/local models) and falls back to recomputing via ``litellm.completion_cost``.

        Args:
            response (Any): The LiteLLM completion response object.

        Returns:
            float | None: The cost in dollars, or None if unavailable.
        """
        hidden_params = getattr(response, "_hidden_params", None)
        if isinstance(hidden_params, dict) and hidden_params.get("response_cost") is not None:
            try:
                return float(hidden_params["response_cost"])
            except (TypeError, ValueError):
                return None
        try:
            litellm = LiteLLMChatTarget._import_litellm()
            cost = litellm.completion_cost(completion_response=response)
            return float(cost) if cost else None
        except Exception:
            return None

    def _handle_litellm_exception(self, *, exc: Exception, request: MessagePiece) -> list[Message]:
        """
        Translate a LiteLLM exception into either a blocked-content error Message or a PyRIT
        exception. LiteLLM re-exports the OpenAI SDK exception classes, so we match on those
        types (``isinstance``) rather than fragile string/qualname comparisons.

        Args:
            exc (Exception): The exception raised by ``litellm.acompletion``.
            request (MessagePiece): The originating request piece.

        Returns:
            list[Message]: A single error Message when the failure is a content-policy block.

        Raises:
            RateLimitException: For rate-limit and transient provider errors.
            PyritException: For authentication and all other errors.
        """
        litellm = self._import_litellm()
        exceptions = litellm.exceptions

        # Content policy violations are surfaced as an error Message (not raised) so attacks
        # continue and blocked-content scorers can score the refusal.
        if self._is_content_policy_error(exc=exc, exceptions=exceptions):
            status_code = getattr(exc, "status_code", 400) or 400
            return [
                handle_bad_request_exception(
                    response_text=str(exc),
                    request=request,
                    error_code=status_code,
                    is_content_filter=True,
                )
            ]

        return self._raise_translated_exception(exc=exc, exceptions=exceptions)

    @staticmethod
    def _is_content_policy_error(*, exc: Exception, exceptions: Any) -> bool:
        content_policy_error = getattr(exceptions, "ContentPolicyViolationError", None)
        if content_policy_error is not None and isinstance(exc, content_policy_error):
            return True
        text = str(exc).lower()
        return "content_filter" in text or "content policy" in text or "content_policy" in text

    @staticmethod
    def _raise_translated_exception(*, exc: Exception, exceptions: Any) -> NoReturn:
        rate_limit_error = getattr(exceptions, "RateLimitError", ())
        transient_errors = tuple(
            err
            for err in (
                getattr(exceptions, "APIConnectionError", None),
                getattr(exceptions, "Timeout", None),
                getattr(exceptions, "InternalServerError", None),
                getattr(exceptions, "ServiceUnavailableError", None),
            )
            if err is not None
        )
        authentication_error = getattr(exceptions, "AuthenticationError", ())

        if isinstance(exc, rate_limit_error):
            raise RateLimitException(status_code=429, message=f"Rate limited by provider: {exc}") from exc
        if transient_errors and isinstance(exc, transient_errors):
            status_code = getattr(exc, "status_code", 503) or 503
            raise RateLimitException(status_code=status_code, message=f"Transient provider error: {exc}") from exc
        if isinstance(exc, authentication_error):
            raise PyritException(message=f"Authentication failed: {exc}") from exc

        raise PyritException(message=f"LiteLLM error: {exc}") from exc
