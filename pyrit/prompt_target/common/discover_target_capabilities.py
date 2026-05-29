# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Runtime capability and modality discovery for prompt targets.

This module exposes two complementary probes:

* ``_discover_capability_flags_async`` discovers the boolean capability flags
  defined on ``TargetCapabilities`` (e.g. ``supports_system_prompt``,
  ``supports_multi_message_pieces``). For each capability that has a probe
  defined, a minimal request is sent to the target. If the request succeeds,
    the capability is included in the returned set. Capabilities without a
    registered probe fall back to the target's declared native support from
    ``target.capabilities``.
* ``_discover_input_modalities_async`` discovers which input modality
  combinations a target actually supports by sending a minimal test request
  for each combination declared in ``TargetCapabilities.input_modalities``.

.. note::
   Output modality probing is intentionally not provided. Unlike inputs,
   output modality is largely a property of the endpoint type (chat models
   return text, image models return images, TTS endpoints return audio)
   rather than something the caller controls per request, and there is no
   PyRIT-level ``response_format=image`` style hint to assert against.
   Eliciting non-text output reliably depends on prompt phrasing, costs
   real compute per probe, and is prone to false negatives from safety
   filters. Trust ``target.capabilities.output_modalities`` as declared.

.. warning::
    These probes only verify that a request was *accepted*. They do not prove
    that the endpoint enforced the feature, and the JSON probes are only
    meaningful for targets that translate ``prompt_metadata`` JSON hints into
    provider request fields. Treat the results as an upper bound on support and
    validate response content separately when that distinction matters.
"""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace

from pyrit.common.path import DATASETS_PATH
from pyrit.models import Message, MessagePiece, PromptDataType
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityName,
    TargetCapabilities,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

logger = logging.getLogger(__name__)

# Per-call timeout (seconds) applied to every discovery request. Override per-call via
# the ``per_probe_timeout_s`` parameter on the public functions.
DEFAULT_PROBE_TIMEOUT_SECONDS: float = 30.0
DEFAULT_PROBE_RETRY_BACKOFF_SECONDS: float = 0.1
MAX_PROBE_RETRY_BACKOFF_SECONDS: float = 1.0

# Exceptions that are deterministic on the probe payload and will not become
# valid on a retry (malformed Message, type errors, missing attributes, etc.).
# These fail the probe immediately rather than wasting backoff time.
_NON_RETRYABLE_PROBE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    AttributeError,
)

# Marker stamped onto every MessagePiece this module writes to memory. Consumers
# that aggregate or display memory rows can filter probe-written rows by checking
# ``piece.prompt_metadata.get("capability_probe") == "1"``. Memory does not yet
# expose a delete-by-conversation-id API, so tagging is the cleanup mechanism.
PROBE_METADATA_KEY: str = "capability_probe"
PROBE_METADATA_VALUE: str = "1"

_CapabilityProbe = Callable[[PromptTarget, float, int], Awaitable[bool]]


def _json_enforcing_target_types() -> tuple[type[PromptTarget], ...]:
    """
    Return the tuple of target classes that translate ``prompt_metadata`` JSON
    hints (``response_format``, ``json_schema``) into native provider request
    fields. Used to suppress the "JSON probe is upper-bound only" debug log for
    these targets and their subclasses.

    Imports are lazy to avoid a circular dependency at module load time and to
    keep this concern entirely within the discovery module. Class objects (not
    strings) are returned so renames are caught by import errors rather than
    silently flipping the log behavior.

    Returns:
        tuple[type[PromptTarget], ...]: The target classes that enforce JSON hints.
    """
    from pyrit.prompt_target.openai.openai_chat_target import OpenAIChatTarget
    from pyrit.prompt_target.openai.openai_response_target import OpenAIResponseTarget

    return (OpenAIChatTarget, OpenAIResponseTarget)


# Every text probe sends a text-only payload. Permissive overrides therefore
# always include this combination so that ``_validate_request``'s per-piece
# data-type check does not reject text probes against text-less targets.
_TEXT_MODALITY: frozenset[frozenset[PromptDataType]] = frozenset({frozenset({"text"})})

# Packaged fallback assets for non-text modality discovery.
_TARGET_CAPABILITIES_DATASET_PATH = DATASETS_PATH / "prompt_target" / "target_capabilities"


@contextmanager
def _permissive_configuration(
    *,
    target: PromptTarget,
    extra_input_modalities: Iterable[frozenset[PromptDataType]] | None = None,
) -> Iterator[None]:
    """
    Temporarily replace ``target``'s configuration with one that declares every
    boolean capability as natively supported.

    This bypasses ``PromptTarget._validate_request``, which would otherwise
    short-circuit probes for capabilities the target declares as unsupported
    before any API call is made. The original configuration is restored on exit.

    Args:
        target (PromptTarget): The target whose configuration is temporarily replaced.
        extra_input_modalities (Iterable[frozenset[PromptDataType]] | None):
            Additional modality combinations to include in ``input_modalities``
            during the override. Used by modality probes so that
            ``_validate_request``'s per-piece data-type check does not reject
            combinations the caller asked us to test but the target does not
            yet declare. Defaults to None.

    Yields:
        None: Control returns to the ``with`` block while the permissive
        configuration is in effect.
    """
    original = target.configuration
    merged_modalities = original.capabilities.input_modalities | _TEXT_MODALITY
    if extra_input_modalities is not None:
        merged_modalities = frozenset(merged_modalities | frozenset(extra_input_modalities))
    permissive_caps = replace(
        original.capabilities,
        supports_multi_turn=True,
        supports_multi_message_pieces=True,
        supports_json_schema=True,
        supports_json_output=True,
        supports_editable_history=True,
        supports_system_prompt=True,
        input_modalities=merged_modalities,
    )
    # Rebuild a fresh configuration from the instance's native capabilities so
    # probes bypass preflight validation without inheriting ADAPT policy or
    # custom normalizer overrides from the target's runtime configuration.
    probe_configuration = TargetConfiguration(capabilities=permissive_caps)
    target._configuration = probe_configuration
    try:
        yield
    finally:
        target._configuration = original


def _new_conversation_id() -> str:
    """
    Generate a unique conversation id for a single capability probe.

    Returns:
        str: A conversation id of the form ``"capability-probe-<uuid>"``.
    """
    return f"capability-probe-{uuid.uuid4()}"


def _probe_metadata(extra: dict[str, str | int] | None = None) -> dict[str, str | int]:
    """Return a fresh ``prompt_metadata`` dict tagged as a capability probe."""
    metadata: dict[str, str | int] = {PROBE_METADATA_KEY: PROBE_METADATA_VALUE}
    if extra:
        metadata.update(extra)
    return metadata


def _user_text_piece(*, value: str, conversation_id: str) -> MessagePiece:
    """
    Build a single user-role text ``MessagePiece`` for use in a probe.

    The piece's ``prompt_metadata`` is tagged with ``PROBE_METADATA_KEY``
    so that consumers aggregating memory can filter out probe-written rows.

    Args:
        value (str): The text payload to send.
        conversation_id (str): The conversation id to attach to the piece.

    Returns:
        MessagePiece: A user-role text piece bound to ``conversation_id``.
    """
    return MessagePiece(
        role="user",
        original_value=value,
        original_value_data_type="text",
        conversation_id=conversation_id,
        prompt_metadata=_probe_metadata(),
    )


async def _send_and_check_async(
    *,
    target: PromptTarget,
    message: Message,
    timeout_s: float,
    retries: int = 1,
    label: str = "Capability probe",
) -> bool:
    """
    Send ``message`` and report whether the call succeeded cleanly.

    Each attempt is bounded by ``timeout_s``. Transient errors (timeouts,
    connection/OS errors) trigger up to ``retries`` retries with a short
    exponential backoff. Deterministic errors that will not become valid on
    a retry (``ValueError``, ``TypeError``, ``AttributeError`` — typically
    from message validation or programmer error in a probe payload) fail
    the probe immediately. An explicit error response from the target is
    treated as deterministic and never retried.

    Args:
        target (PromptTarget): The target to send the probe message to.
        message (Message): The probe message to send.
        timeout_s (float): Per-attempt timeout in seconds.
        retries (int): Number of additional attempts after the first failure.
            Only transient errors are retried; non-retryable errors and
            non-error responses are final. Retry attempts use exponential
            backoff starting at ``DEFAULT_PROBE_RETRY_BACKOFF_SECONDS``.
            Defaults to 1.
        label (str): Short label used in log messages. Defaults to
            ``"Capability probe"``.

    Returns:
        bool: ``True`` iff the call returned without raising and every response
        piece reported ``response_error == "none"``; ``False`` otherwise.
        Any other ``response_error`` value (``"blocked"``, ``"processing"``,
        ``"empty"``, ``"unknown"``) is treated as failure. An empty response
        list (or responses with no message pieces) is also treated as a failure.
    """
    attempts = max(1, retries + 1)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            responses = await asyncio.wait_for(target.send_prompt_async(message=message), timeout=timeout_s)
        except asyncio.TimeoutError:
            last_exc = TimeoutError(f"timed out after {timeout_s}s")
            logger.debug("%s timed out (attempt %d/%d)", label, attempt + 1, attempts)
            if attempt + 1 < attempts:
                await _sleep_before_retry_async(attempt=attempt)
            continue
        except _NON_RETRYABLE_PROBE_EXCEPTIONS as exc:
            # Deterministic on the probe payload — retrying will not help.
            logger.debug("%s failed with non-retryable error: %s", label, exc)
            return False
        except Exception as exc:
            last_exc = exc
            logger.debug("%s failed (attempt %d/%d): %s", label, attempt + 1, attempts, exc)
            if attempt + 1 < attempts:
                await _sleep_before_retry_async(attempt=attempt)
            continue

        if not responses or any(not r.message_pieces for r in responses):
            logger.debug("%s returned an empty response; treating as failure", label)
            return False
        for response in responses:
            for piece in response.message_pieces:
                if piece.response_error != "none":
                    logger.debug("%s returned error response: %s", label, piece.converted_value)
                    return False
        return True

    logger.info("%s exhausted %d attempt(s); last error: %s", label, attempts, last_exc)
    return False


def _retry_backoff_seconds(*, attempt: int) -> float:
    """Return the exponential backoff delay for a retry attempt."""
    return min(DEFAULT_PROBE_RETRY_BACKOFF_SECONDS * (2**attempt), MAX_PROBE_RETRY_BACKOFF_SECONDS)


async def _sleep_before_retry_async(*, attempt: int) -> None:
    """Sleep for the retry backoff associated with ``attempt``."""
    await asyncio.sleep(_retry_backoff_seconds(attempt=attempt))


async def _probe_system_prompt_async(target: PromptTarget, timeout_s: float, retries: int = 1) -> bool:
    """
    Probe whether ``target`` accepts a system prompt followed by a user message.

    Writes a system-role ``MessagePiece`` directly to ``target._memory``
    rather than calling ``pyrit.prompt_target.PromptChatTarget.set_system_prompt``
    (which is only defined on ``PromptChatTarget`` subclasses anyway).
    ``set_system_prompt`` can be overridden by subclasses (e.g. mocks) to do
    nothing or to perform extra work, which would mask whether the underlying
    API actually accepts a system message. A direct memory write also works
    uniformly for plain ``PromptTarget`` subclasses that have no
    ``set_system_prompt`` method, and guarantees the probe sees the same
    multi-piece, system-then-user payload the target's wire layer would see
    via the standard pipeline.

    Args:
        target (PromptTarget): The target to probe.
        timeout_s (float): Per-attempt timeout in seconds.
        retries (int): Number of additional attempts after the first failure.
            Only exceptions/timeouts are retried; an explicit error response
            is final. Defaults to 1.

    Returns:
        bool: ``True`` if the system + user request succeeded; ``False`` otherwise.
    """
    conversation_id = _new_conversation_id()
    system_piece = MessagePiece(
        role="system",
        original_value="You are a helpful assistant.",
        original_value_data_type="text",
        conversation_id=conversation_id,
        prompt_metadata=_probe_metadata(),
    )
    try:
        target._memory.add_message_to_memory(request=Message([system_piece]))
    except Exception as exc:
        logger.debug("System-prompt probe could not seed system message: %s", exc)
        return False
    user_piece = _user_text_piece(value="hi", conversation_id=conversation_id)
    return await _send_and_check_async(
        target=target,
        message=Message([user_piece]),
        timeout_s=timeout_s,
        retries=retries,
        label="System-prompt probe",
    )


async def _probe_multi_message_pieces_async(target: PromptTarget, timeout_s: float, retries: int = 1) -> bool:
    """
    Probe whether ``target`` accepts a single message containing multiple pieces.

    Args:
        target (PromptTarget): The target to probe.
        timeout_s (float): Per-attempt timeout in seconds.
        retries (int): Number of additional attempts after the first failure.
            Only exceptions/timeouts are retried; an explicit error response
            is final. Defaults to 1.

    Returns:
        bool: ``True`` if the multi-piece request succeeded; ``False`` otherwise.
    """
    conversation_id = _new_conversation_id()
    pieces = [
        _user_text_piece(value="part one", conversation_id=conversation_id),
        _user_text_piece(value="part two", conversation_id=conversation_id),
    ]
    return await _send_and_check_async(
        target=target,
        message=Message(pieces),
        timeout_s=timeout_s,
        retries=retries,
        label="Multi-message-pieces probe",
    )


async def _probe_multi_turn_async(target: PromptTarget, timeout_s: float, retries: int = 1) -> bool:
    """
    Probe whether ``target`` accepts a request that includes prior conversation history.

    ``PromptTarget.send_prompt_async`` reads conversation history from memory but
    does not write to it (persistence normally happens in the orchestrator
    layer). To exercise true multi-turn behavior, this probe:

    1. Sends an initial user message.
    2. Persists that user message and a synthetic assistant reply directly to
       the target's memory under the same ``conversation_id``.
    3. Sends a second user message; ``send_prompt_async`` then fetches the
       2-message history and the target receives a real 3-message
       multi-turn payload.

    The synthetic assistant reply's content is irrelevant — we are testing
    whether the target's API accepts a multi-turn payload, not whether the
    model recalls anything.

    Args:
        target (PromptTarget): The target to probe.
        timeout_s (float): Per-attempt timeout in seconds.
        retries (int): Number of additional attempts after the first failure.
            Only exceptions/timeouts are retried; an explicit error response
            is final. Defaults to 1.

    Returns:
        bool: ``True`` if both turns succeeded; ``False`` if either turn failed.
    """
    conversation_id = _new_conversation_id()
    first = _user_text_piece(value="My favorite color is blue.", conversation_id=conversation_id)
    if not await _send_and_check_async(
        target=target, message=Message([first]), timeout_s=timeout_s, retries=retries, label="Multi-turn probe (turn 1)"
    ):
        return False

    # Seed memory so the second send sees real prior history.
    try:
        target._memory.add_message_to_memory(request=Message([first]))
        assistant_reply = MessagePiece(
            role="assistant",
            original_value="Got it.",
            original_value_data_type="text",
            conversation_id=conversation_id,
            prompt_metadata=_probe_metadata(),
        ).to_message()
        target._memory.add_message_to_memory(request=assistant_reply)
    except Exception as exc:
        logger.debug("Multi-turn probe could not seed conversation history: %s", exc)
        return False

    second = _user_text_piece(value="What did I just tell you?", conversation_id=conversation_id)
    return await _send_and_check_async(
        target=target,
        message=Message([second]),
        timeout_s=timeout_s,
        retries=retries,
        label="Multi-turn probe (turn 2)",
    )


async def _probe_json_output_async(target: PromptTarget, timeout_s: float, retries: int = 1) -> bool:
    """
    Probe whether ``target`` accepts a request asking for JSON-mode output.

    This probe is only meaningful for targets that translate PyRIT's JSON
    metadata hints into native provider request fields.

    Args:
        target (PromptTarget): The target to probe.
        timeout_s (float): Per-attempt timeout in seconds.
        retries (int): Number of additional attempts after the first failure.
            Only exceptions/timeouts are retried; an explicit error response
            is final. Defaults to 1.

    Returns:
        bool: ``True`` if the JSON-mode request succeeded; ``False`` otherwise.
    """
    conversation_id = _new_conversation_id()
    piece = MessagePiece(
        role="user",
        original_value='Respond with a JSON object: {"ok": true}.',
        original_value_data_type="text",
        conversation_id=conversation_id,
        # This only becomes a real JSON-mode request on targets that honor
        # PyRIT's JSON metadata contract when building the provider payload.
        prompt_metadata=_probe_metadata({"response_format": "json"}),
    )
    return await _send_and_check_async(
        target=target, message=Message([piece]), timeout_s=timeout_s, retries=retries, label="JSON-output probe"
    )


async def _probe_json_schema_async(target: PromptTarget, timeout_s: float, retries: int = 1) -> bool:
    """
    Probe whether ``target`` accepts a request constrained by a JSON schema.

    This probe is only meaningful for targets that translate PyRIT's JSON
    metadata hints into native provider request fields.

    Args:
        target (PromptTarget): The target to probe.
        timeout_s (float): Per-attempt timeout in seconds.
        retries (int): Number of additional attempts after the first failure.
            Only exceptions/timeouts are retried; an explicit error response
            is final. Defaults to 1.

    Returns:
        bool: ``True`` if the schema-constrained request succeeded; ``False`` otherwise.
    """
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    conversation_id = _new_conversation_id()
    piece = MessagePiece(
        role="user",
        original_value='Respond with a JSON object matching the schema: {"ok": true}.',
        original_value_data_type="text",
        conversation_id=conversation_id,
        # As above, this probe is only strong for targets that map these
        # metadata keys to native JSON-schema request parameters.
        prompt_metadata=_probe_metadata(
            {
                "response_format": "json",
                "json_schema": json.dumps(schema),
            }
        ),
    )
    return await _send_and_check_async(
        target=target, message=Message([piece]), timeout_s=timeout_s, retries=retries, label="JSON-schema probe"
    )


# Registry of capabilities that can be queried via a live API call.
# Capabilities not present here fall back to the target's declared support.
_CAPABILITY_PROBES: dict[CapabilityName, _CapabilityProbe] = {
    CapabilityName.SYSTEM_PROMPT: _probe_system_prompt_async,
    CapabilityName.MULTI_MESSAGE_PIECES: _probe_multi_message_pieces_async,
    CapabilityName.MULTI_TURN: _probe_multi_turn_async,
    CapabilityName.JSON_OUTPUT: _probe_json_output_async,
    CapabilityName.JSON_SCHEMA: _probe_json_schema_async,
}


async def _discover_capability_flags_async(
    *,
    target: PromptTarget,
    capabilities: Iterable[CapabilityName] | None = None,
    per_probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    retries: int = 1,
) -> set[CapabilityName]:
    """
     Probe which capabilities ``target`` accepts.

     Registered capabilities are checked with live requests. Capabilities
     without a live probe fall back to declared native support.

    Args:
        target (PromptTarget): The target to probe.
        capabilities (Iterable[CapabilityName] | None): Capabilities to check.
            Defaults to every member of ``CapabilityName``.
        per_probe_timeout_s (float): Per-attempt timeout (seconds) applied to
            each probe request. Defaults to
            ``DEFAULT_PROBE_TIMEOUT_SECONDS``.
        retries (int): Number of additional attempts after the first failure
            for each probe. Only exceptions/timeouts are retried; an explicit
            error response is final. Set to ``0`` to disable retries.
            Defaults to 1.

    Returns:
        set[CapabilityName]: The capabilities confirmed to work against the target.
    """
    capabilities_to_check: list[CapabilityName] = (
        list(capabilities) if capabilities is not None else list(CapabilityName)
    )

    queried: set[CapabilityName] = set()
    json_capabilities = {CapabilityName.JSON_OUTPUT, CapabilityName.JSON_SCHEMA}
    queried_json_capabilities: set[CapabilityName] = set()
    with _permissive_configuration(target=target):
        for capability in capabilities_to_check:
            probe = _CAPABILITY_PROBES.get(capability)
            if probe is None:
                # Capabilities without a probe are handled after the permissive
                # override is removed so we can read the target's native flags.
                continue

            try:
                # "Supported" means the request was accepted. A target can
                # still ignore the feature semantics after accepting the call.
                if await probe(target, per_probe_timeout_s, retries):
                    queried.add(capability)
                    if capability in json_capabilities:
                        queried_json_capabilities.add(capability)
            except Exception as exc:
                logger.debug("Probe for %s raised: %s", capability.value, exc)

    # JSON probes only verify the target accepted the request, not that the
    # target translated the JSON metadata into provider request fields. Emit
    # a single summary line when probes succeeded against a target that does
    # not enforce JSON hints, so the result is treated as an upper bound.
    # ``isinstance`` covers user-defined subclasses of enforcing targets.
    if queried_json_capabilities and not isinstance(target, _json_enforcing_target_types()):
        logger.debug(
            "JSON capability probes %s succeeded for %s, but this target does not translate "
            "prompt_metadata JSON hints into provider request fields; treat the result as upper-bound support only.",
            sorted(c.value for c in queried_json_capabilities),
            type(target).__name__,
        )

    # Read unprobed capabilities from target.capabilities, not
    # target.configuration, so ADAPTed behavior is not reported as native
    # support.
    for capability in capabilities_to_check:
        if capability not in _CAPABILITY_PROBES and target.capabilities.includes(capability=capability):
            queried.add(capability)

    return queried


# ---------------------------------------------------------------------------
# Modality query
# ---------------------------------------------------------------------------


# Default mapping of non-text modalities to packaged probe assets. Callers can
# override via the ``test_assets`` parameter of
# ``_discover_input_modalities_async``. Modalities whose assets do not exist
# on disk are skipped (logged and excluded from the result).
DEFAULT_TEST_ASSETS: dict[PromptDataType, str] = {
    "audio_path": str(_TARGET_CAPABILITIES_DATASET_PATH / "probe_audio.wav"),
    "image_path": str(_TARGET_CAPABILITIES_DATASET_PATH / "probe_image.png"),
}


async def _discover_input_modalities_async(
    *,
    target: PromptTarget,
    test_modalities: set[frozenset[PromptDataType]] | None = None,
    test_assets: dict[PromptDataType, str] | None = None,
    per_probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    retries: int = 1,
) -> set[frozenset[PromptDataType]]:
    """
    Probe which input modality combinations ``target`` accepts.

    Each modality combination is checked with a minimal request built from the
    supplied test assets.

    Args:
        target (PromptTarget): The target to probe.
        test_modalities (set[frozenset[PromptDataType]] | None): Specific
            modality combinations to test. Defaults to the combinations
            declared in ``target.capabilities.input_modalities``.
        test_assets (dict[PromptDataType, str] | None): Mapping from
            non-text modality to a file path used as the probe payload.
            Defaults to ``DEFAULT_TEST_ASSETS``. Combinations whose
            non-text assets are missing on disk are skipped.
        per_probe_timeout_s (float): Per-attempt timeout (seconds) applied to
            each probe request. Defaults to
            ``DEFAULT_PROBE_TIMEOUT_SECONDS``.
        retries (int): Number of additional attempts after the first failure
            for each probe. Only exceptions/timeouts are retried; an explicit
            error response is final. Set to ``0`` to disable retries.
            Defaults to 1.

    Returns:
        set[frozenset[PromptDataType]]: The modality combinations confirmed
        to work against the target.
    """
    if test_modalities is None:
        declared = target.capabilities.input_modalities
        test_modalities = set(declared)
    elif not test_modalities:
        logger.info("_discover_input_modalities_async called with an empty test_modalities set; nothing to probe.")
        return set()

    assets = test_assets if test_assets is not None else DEFAULT_TEST_ASSETS

    queried: set[frozenset[PromptDataType]] = set()
    with _permissive_configuration(target=target, extra_input_modalities=test_modalities):
        for combination in test_modalities:
            try:
                message = _create_test_message(modalities=combination, test_assets=assets)
            except FileNotFoundError as exc:
                # Skip combinations we cannot construct a valid probe payload for.
                logger.info("Skipping modality %s: %s", combination, exc)
                continue
            except ValueError as exc:
                logger.info("Skipping modality %s: %s", combination, exc)
                continue

            # "Supported" means the request was accepted. A target may still
            # ignore the non-text payload after accepting it.
            if await _send_and_check_async(
                target=target,
                message=message,
                timeout_s=per_probe_timeout_s,
                retries=retries,
                label=f"Modality probe {sorted(combination)}",
            ):
                queried.add(combination)

    return queried


async def discover_target_capabilities_async(
    *,
    target: PromptTarget,
    per_probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    test_modalities: set[frozenset[PromptDataType]] | None = None,
    test_assets: dict[PromptDataType, str] | None = None,
    capabilities: Iterable[CapabilityName] | None = None,
    retries: int = 1,
    apply: bool = False,
) -> TargetCapabilities:
    """
    Probe both the boolean capability flags and the input modality combinations
    that ``target`` accepts, and return a merged best-effort
    ``TargetCapabilities``.

    Boolean capabilities with a registered probe are checked with live
    requests; capabilities without a probe fall back to the target's
    declared native support. Each input modality combination is checked
    with a minimal request built from the supplied test assets.
    "Supported" means the request was accepted — a target that silently
    ignores a feature is still reported as supporting it.

    Args:
        target (PromptTarget): The target to probe.
        per_probe_timeout_s (float): Per-attempt timeout (seconds) applied to
            each probe request.
        test_modalities (set[frozenset[PromptDataType]] | None): Specific
            modality combinations to probe. Defaults to the target's declared
            ``input_modalities``. Combinations not listed here fall back to
            the target's declared support.
        test_assets (dict[PromptDataType, str] | None): Mapping from non-text
            modality to a file path used as the probe payload. Defaults to
            ``DEFAULT_TEST_ASSETS``. Combinations whose non-text assets
            are missing on disk are skipped.
        capabilities (Iterable[CapabilityName] | None): Capabilities to probe.
            Defaults to every member of ``CapabilityName``. Capabilities
            not listed here fall back to the target's declared support.
        retries (int): Number of additional attempts after the first failure
            for each probe. Only exceptions/timeouts are retried; an explicit
            error response is final. Set to ``0`` to disable retries.
            Defaults to 1.
        apply (bool): If True, install the discovered capabilities on ``target``
            via ``PromptTarget.apply_capabilities`` before returning.
             Probe results are an upper bound (the request was accepted, not
            necessarily honored), so leave this False when you want to inspect
            or diff the result before committing to it. Defaults to False.

    Returns:
        TargetCapabilities: A merged capability view: probed where possible,
        declared where probing is unavailable or out of scope.
    """
    capabilities_to_probe = list(capabilities) if capabilities is not None else None

    queried_caps = await _discover_capability_flags_async(
        target=target,
        capabilities=capabilities_to_probe,
        per_probe_timeout_s=per_probe_timeout_s,
        retries=retries,
    )
    queried_modalities = await _discover_input_modalities_async(
        target=target,
        test_modalities=test_modalities,
        test_assets=test_assets,
        per_probe_timeout_s=per_probe_timeout_s,
        retries=retries,
    )

    declared = target.capabilities
    # If the caller narrows the capability set, leave the rest at their
    # declared values instead of silently forcing them to False.
    probed: set[CapabilityName] = (
        set(capabilities_to_probe) if capabilities_to_probe is not None else set(CapabilityName)
    )

    def _resolve(name: CapabilityName) -> bool:
        if name in probed:
            return name in queried_caps
        return bool(getattr(declared, name.value))

    resolved_multi_turn = _resolve(CapabilityName.MULTI_TURN)
    # Editable history is only meaningful if multi-turn probing/declaration
    # also resolved to True.
    resolved_editable_history = declared.supports_editable_history and resolved_multi_turn
    if test_modalities is None:
        # Mirror the boolean fallback: combinations the probe could not confirm
        # fall back to the target's declared support rather than being silently
        # dropped (e.g. on transient network failure).
        resolved_input_modalities = frozenset(queried_modalities | declared.input_modalities)
    else:
        resolved_input_modalities = frozenset(
            queried_modalities | (declared.input_modalities - frozenset(test_modalities))
        )

    resolved = TargetCapabilities(
        supports_multi_turn=resolved_multi_turn,
        supports_multi_message_pieces=_resolve(CapabilityName.MULTI_MESSAGE_PIECES),
        supports_json_schema=_resolve(CapabilityName.JSON_SCHEMA),
        supports_json_output=_resolve(CapabilityName.JSON_OUTPUT),
        supports_editable_history=resolved_editable_history,
        supports_system_prompt=_resolve(CapabilityName.SYSTEM_PROMPT),
        input_modalities=resolved_input_modalities,
        # Output modalities are still declarative because probing them would
        # require target-specific response inspection.
        output_modalities=declared.output_modalities,
    )

    if apply:
        target.apply_capabilities(capabilities=resolved)

    return resolved


def _create_test_message(
    *,
    modalities: frozenset[PromptDataType],
    test_assets: dict[PromptDataType, str],
) -> Message:
    """
    Build a minimal ``Message`` that exercises ``modalities``.

    Args:
        modalities (frozenset[PromptDataType]): The modalities to include.
        test_assets (dict[PromptDataType, str]): Mapping from non-text
            modality to a file path used for the probe.

    Returns:
        Message: A message containing one piece per modality.

    Raises:
        FileNotFoundError: If a configured asset path does not exist.
        ValueError: If a non-text modality has no configured asset.
    """
    conversation_id = f"modality-probe-{uuid.uuid4()}"
    pieces: list[MessagePiece] = []

    for modality in modalities:
        if modality == "text":
            pieces.append(
                MessagePiece(
                    role="user",
                    original_value="test",
                    original_value_data_type="text",
                    conversation_id=conversation_id,
                    prompt_metadata=_probe_metadata(),
                )
            )
            continue

        asset_path = test_assets.get(modality)
        if asset_path is None:
            raise ValueError(f"No test asset configured for modality '{modality}'.")
        if not os.path.isfile(asset_path):
            raise FileNotFoundError(f"Test asset for modality '{modality}' not found at: {asset_path}")

        pieces.append(
            MessagePiece(
                role="user",
                original_value=asset_path,
                original_value_data_type=modality,
                conversation_id=conversation_id,
                prompt_metadata=_probe_metadata(),
            )
        )

    return Message(pieces)
