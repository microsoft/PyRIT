# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from pyrit.models.target.target_capabilities import CapabilityName

if TYPE_CHECKING:
    from pyrit.models.messages.message import Message


class RequestGenerationConfig(BaseModel):
    """Request-local generation options, independent of the target's configured defaults."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    METADATA_KEY: ClassVar[str] = "request_generation_config"
    FIELD_CAPABILITIES: ClassVar[dict[str, CapabilityName]] = {"temperature": CapabilityName.TEMPERATURE}
    temperature: float | None = None

    @property
    def required_capabilities(self) -> frozenset[CapabilityName]:
        """The native capabilities required by non-null request options."""
        return frozenset(self.FIELD_CAPABILITIES[name] for name in self.model_dump(exclude_none=True))

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> RequestGenerationConfig:
        """
        Read generation options from the canonical metadata envelope.

        Returns:
            RequestGenerationConfig: Missing or null options mean no override.

        Raises:
            ValueError: Options are invalid.
        """
        metadata = metadata or {}
        value = metadata.get(cls.METADATA_KEY)
        return cls() if value is None else cls.model_validate(value)

    @classmethod
    def from_message(cls, message: Message) -> RequestGenerationConfig:
        """
        Read all current pieces.

        Returns:
            RequestGenerationConfig: The common non-null value for each selected option.

        Raises:
            ValueError: The pieces specify conflicting values for an option.
        """
        options: dict[str, Any] = {}
        for piece in message.message_pieces:
            config = cls.from_metadata(metadata=piece.prompt_metadata)
            for name, value in config.model_dump(exclude_none=True).items():
                if name in options and options[name] != value:
                    raise ValueError(f"Conflicting request {name} values across current message pieces.")
                options[name] = value
        return cls.model_validate(options)

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize only explicitly selected options into the canonical envelope.

        Returns:
            dict[str, Any]: Metadata fragment, empty for an unset override.
        """
        options = self.model_dump(mode="json", exclude_none=True)
        return {self.METADATA_KEY: options} if options else {}

    @classmethod
    def clear_metadata(cls, metadata: dict[str, Any]) -> None:
        """Remove request options without touching unrelated metadata."""
        metadata.pop(cls.METADATA_KEY, None)


class ParameterResolution(BaseModel):
    """The requested numeric value, submitted parameter, and origin of that parameter."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    requested: float | None
    resolved: float | None
    source: Literal["request", "target_default", "provider_default"]


class RequestGenerationInfo(BaseModel):
    """Framework-authored generation evidence for one logical send, not a provider success claim."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    METADATA_KEY: ClassVar[str] = "_pyrit_generation"
    temperature: ParameterResolution
    request_piece_ids: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize evidence separately from caller-authored request options.

        Returns:
            dict[str, Any]: JSON-compatible evidence metadata.
        """
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> RequestGenerationInfo | None:
        """
        Read framework-authored evidence from the canonical metadata envelope.

        Returns:
            RequestGenerationInfo | None: Evidence, or None for unrecorded messages.

        Raises:
            ValueError: Evidence is malformed.
        """
        metadata = metadata or {}
        value = metadata.get(cls.METADATA_KEY)
        return cls._from_record(value) if value is not None else None

    @classmethod
    def clear_metadata(cls, metadata: dict[str, Any]) -> None:
        """Remove evidence before a new send or response association."""
        metadata.pop(cls.METADATA_KEY, None)

    @classmethod
    def _from_record(cls, value: Any) -> RequestGenerationInfo:
        # JSON represents tuples as arrays; validate all other fields strictly.
        if isinstance(value, dict) and isinstance(value.get("request_piece_ids"), list):
            value = {**value, "request_piece_ids": tuple(value["request_piece_ids"])}
        return cls.model_validate(value)
