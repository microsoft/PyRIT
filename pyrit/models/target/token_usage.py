# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Prefix for all token-usage keys stored in a MessagePiece's ``prompt_metadata``.
_METADATA_PREFIX = "token_usage_"

#: Metadata key suffixes that map to first-class ``TokenUsage`` fields. Every other integer
#: ``token_usage_*`` key round-trips through ``extra``. ``cost`` is not listed because it is a
#: currency amount stored as a string and is filtered out by the int guard regardless.
_CORE_SUFFIXES = frozenset({"input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"})


@dataclass(frozen=True)
class TokenUsage:
    """
    Provider-neutral token accounting for a single model call.

    Field names use the ``input``/``output`` vocabulary (aligned with the OpenAI Responses API,
    Anthropic, and Gemini) rather than the Chat Completions ``prompt``/``completion`` terms. The
    object is persisted onto a ``MessagePiece``'s ``prompt_metadata`` via ``to_metadata`` using
    matching ``token_usage_input_tokens`` / ``token_usage_output_tokens`` key names (one consistent
    vocabulary end to end). ``reasoning_tokens`` and ``cached_tokens`` are the two widely-available
    sub-breakdowns promoted to fields; any other provider-specific counts (audio, predicted-output,
    cache-write) ride along in ``extra``.

    This is a pure value object: it holds counts and (de)serializes them to metadata. Turning a
    provider ``usage`` payload into a ``TokenUsage`` is the responsibility of the target/parser that
    knows which wire format it received (for example, the Chat Completions parser in
    ``pyrit.prompt_target.common.chat_completions_response_parser``).

    Neither cost nor the responding model name is modeled here: cost is a currency amount (tracked
    separately under ``token_usage_cost``) and the model identity is already recorded on the
    target's identifier. Both would be a category error inside a token-count value object.

    Only fields the provider actually reports are populated; absent values stay None (and are
    omitted from ``to_metadata``) rather than being coerced to a misleading zero.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    extra: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> TokenUsage | None:
        """
        Reconstruct a ``TokenUsage`` from a ``MessagePiece``'s ``prompt_metadata``.

        Reads the ``token_usage_input_tokens`` / ``token_usage_output_tokens`` keys written by
        ``to_metadata``. Non-core integer ``token_usage_*`` keys are collected into ``extra``;
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
