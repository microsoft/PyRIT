# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Prefix for all token-usage keys stored in a MessagePiece's ``prompt_metadata``.
_METADATA_PREFIX = "token_usage_"

#: Attribute names a provider ``usage`` object may use for the input/output counts. Chat
#: Completions (OpenAI/LiteLLM) reports ``prompt_tokens``/``completion_tokens``; the Responses API
#: (and Anthropic/Gemini) reports ``input_tokens``/``output_tokens``. ``from_provider_usage``
#: accepts either, but PyRIT normalizes everything to the ``input``/``output`` vocabulary.
_PROVIDER_INPUT_NAMES = ("input_tokens", "prompt_tokens")
_PROVIDER_OUTPUT_NAMES = ("output_tokens", "completion_tokens")

#: Metadata key suffixes that map to first-class ``TokenUsage`` fields. Every other integer
#: ``token_usage_*`` key round-trips through ``extra``. ``cost`` is not listed because it is a
#: currency amount stored as a string and is filtered out by the int guard regardless.
_CORE_SUFFIXES = frozenset({"input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"})


def _first_int(source: Any, *names: str) -> int | None:
    """
    Return the first attribute among ``names`` on ``source`` that is an int, else None.

    Booleans are rejected even though ``bool`` is a subclass of ``int``.

    Args:
        source (Any): The object to read attributes from.
        names (str): Candidate attribute names, tried in order.

    Returns:
        int | None: The first integer value found, or None.
    """
    for name in names:
        value = getattr(source, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


@dataclass(frozen=True)
class TokenUsage:
    """
    Provider-neutral token accounting for a single model call.

    Field names use the ``input``/``output`` vocabulary (aligned with the OpenAI Responses API,
    Anthropic, and Gemini) rather than the Chat Completions ``prompt``/``completion`` terms;
    :meth:`from_provider_usage` accepts either provider shape. The object is persisted onto a
    :class:`~pyrit.models.MessagePiece`'s ``prompt_metadata`` via :meth:`to_metadata` using matching
    ``token_usage_input_tokens`` / ``token_usage_output_tokens`` key names (one consistent
    vocabulary end to end). ``reasoning_tokens`` and ``cached_tokens`` are the two widely-available
    sub-breakdowns promoted to fields; any other provider-specific counts (audio, predicted-output,
    cache-write) ride along in ``extra``.

    Neither cost nor the responding model name is modeled here: cost is a currency amount (tracked
    separately under ``token_usage_cost``) and the model identity is already recorded on the
    target's identifier. Both would be a category error inside a token-count value object.

    Only fields the provider actually reports are populated; absent values stay None (and are
    omitted from :meth:`to_metadata`) rather than being coerced to a misleading zero.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    extra: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_provider_usage(cls, usage: Any) -> TokenUsage:
        """
        Build a :class:`TokenUsage` from a provider ``usage`` object (OpenAI/LiteLLM/Anthropic shape).

        Reads top-level counts (mapping ``prompt``->``input`` and ``completion``->``output``, and
        accepting Responses-API ``input``/``output`` names directly) and the nested
        ``*_tokens_details`` breakdowns. ``total_tokens`` is derived from input + output when the
        provider omits it. Unmodeled detail counts (audio, predicted-output, cache-write) are
        collected into ``extra`` with disambiguated names.

        Args:
            usage (Any): The provider usage object.

        Returns:
            TokenUsage: The parsed token usage.
        """
        input_tokens = _first_int(usage, *_PROVIDER_INPUT_NAMES)
        output_tokens = _first_int(usage, *_PROVIDER_OUTPUT_NAMES)
        total_tokens = _first_int(usage, "total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        input_details = getattr(usage, "prompt_tokens_details", None) or getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "completion_tokens_details", None) or getattr(
            usage, "output_tokens_details", None
        )

        cached_tokens = _first_int(input_details, "cached_tokens") if input_details is not None else None
        reasoning_tokens = _first_int(output_details, "reasoning_tokens") if output_details is not None else None

        extra: dict[str, int] = {}
        if input_details is not None:
            _put(extra, "input_audio_tokens", _first_int(input_details, "audio_tokens"))
            _put(extra, "cache_write_tokens", _first_int(input_details, "cache_write_tokens", "cache_creation_tokens"))
        if output_details is not None:
            _put(extra, "output_audio_tokens", _first_int(output_details, "audio_tokens"))
            _put(extra, "accepted_prediction_tokens", _first_int(output_details, "accepted_prediction_tokens"))
            _put(extra, "rejected_prediction_tokens", _first_int(output_details, "rejected_prediction_tokens"))

        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
            extra=extra,
        )

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> TokenUsage | None:
        """
        Reconstruct a :class:`TokenUsage` from a :class:`~pyrit.models.MessagePiece`'s ``prompt_metadata``.

        Reads the ``token_usage_input_tokens`` / ``token_usage_output_tokens`` keys written by
        :meth:`to_metadata`. Non-core integer ``token_usage_*`` keys are collected into ``extra``;
        the string ``token_usage_cost`` key is ignored (cost is tracked separately).

        Args:
            metadata (dict[str, Any]): The prompt metadata to read.

        Returns:
            TokenUsage | None: The reconstructed usage, or None if no token-usage keys exist.
        """
        stripped = {
            key[len(_METADATA_PREFIX) :]: value for key, value in metadata.items() if key.startswith(_METADATA_PREFIX)
        }
        if not stripped:
            return None

        def _pick(suffix: str) -> int | None:
            value = stripped.get(suffix)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        extra = {
            key: value
            for key, value in stripped.items()
            if key not in _CORE_SUFFIXES and isinstance(value, int) and not isinstance(value, bool)
        }
        return cls(
            input_tokens=_pick("input_tokens"),
            output_tokens=_pick("output_tokens"),
            total_tokens=_pick("total_tokens"),
            reasoning_tokens=_pick("reasoning_tokens"),
            cached_tokens=_pick("cached_tokens"),
            extra=extra,
        )

    def to_metadata(self) -> dict[str, int]:
        """
        Serialize to flat ``token_usage_*`` metadata keys, omitting fields that are None.

        Uses the ``input``/``output`` vocabulary for the key names to match the field names (one
        consistent naming end to end). ``extra`` counts are written verbatim under the
        ``token_usage_`` prefix.

        Returns:
            dict[str, int]: The metadata fragment to merge into ``prompt_metadata``.
        """
        out: dict[str, int] = {}
        if self.input_tokens is not None:
            out[_METADATA_PREFIX + "input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            out[_METADATA_PREFIX + "output_tokens"] = self.output_tokens
        if self.total_tokens is not None:
            out[_METADATA_PREFIX + "total_tokens"] = self.total_tokens
        if self.reasoning_tokens is not None:
            out[_METADATA_PREFIX + "reasoning_tokens"] = self.reasoning_tokens
        if self.cached_tokens is not None:
            out[_METADATA_PREFIX + "cached_tokens"] = self.cached_tokens
        for name, value in self.extra.items():
            out[_METADATA_PREFIX + name] = value
        return out


def _put(target: dict[str, int], name: str, value: int | None) -> None:
    """
    Insert ``name``->``value`` into ``target`` only when ``value`` is not None.

    Args:
        target (dict[str, int]): The destination mapping.
        name (str): The key to set.
        value (int | None): The value to store, ignored when None.
    """
    if value is not None:
        target[name] = value
