# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from pyrit.models.messages.message import Message


class RequestGenerationConfig(BaseModel):
    """Request-local generation options, independent of the target's configured defaults."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    METADATA_KEY: ClassVar[str] = "request_generation_config"
    LEGACY_METADATA_KEY: ClassVar[str] = "request_temperature"
    temperature: float | None = None

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> RequestGenerationConfig:
        """
        Read generation options, including the draft's legacy temperature key.

        Returns:
            RequestGenerationConfig: Missing or null temperature means no override.

        Raises:
            ValueError: Options are invalid or explicit legacy and current temperatures conflict.
        """
        metadata = metadata or {}
        value = metadata.get(cls.METADATA_KEY)
        config = cls() if value is None else cls.model_validate(value)
        legacy = cls(temperature=metadata.get(cls.LEGACY_METADATA_KEY))
        if legacy.temperature is not None:
            if config.temperature is not None and config.temperature != legacy.temperature:
                raise ValueError("Conflicting request temperatures in legacy and current generation metadata.")
            config = config.model_copy(update={"temperature": legacy.temperature})
        return config

    @classmethod
    def from_message(cls, message: Message) -> RequestGenerationConfig:
        """
        Read all current pieces.

        Returns:
            RequestGenerationConfig: The common non-null override, if present.

        Raises:
            ValueError: The pieces specify conflicting temperatures.
        """
        temperatures = {
            config.temperature
            for piece in message.message_pieces
            if (config := cls.from_metadata(metadata=piece.prompt_metadata)).temperature is not None
        }
        if len(temperatures) > 1:
            raise ValueError("Conflicting request temperatures across current message pieces.")
        return cls(temperature=next(iter(temperatures), None))

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
        """Remove current and legacy request options without touching unrelated metadata."""
        metadata.pop(cls.METADATA_KEY, None)
        metadata.pop(cls.LEGACY_METADATA_KEY, None)


class TemperatureResolution(BaseModel):
    """The requested temperature, submitted parameter, and origin of that parameter."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    requested: float | None
    resolved: float | None
    source: Literal["request", "target_default", "provider_default"]


class RequestGenerationInfo(BaseModel):
    """Framework-authored generation evidence for one logical send, not a provider success claim."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    METADATA_KEY: ClassVar[str] = "_pyrit_generation"
    LEGACY_METADATA_KEY: ClassVar[str] = "_pyrit_temperature"
    temperature: TemperatureResolution
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
        Read current or legacy evidence, rejecting contradictory records.

        Returns:
            RequestGenerationInfo | None: Evidence, or None for unrecorded messages.

        Raises:
            ValueError: Evidence is malformed or the current and legacy records differ.
        """
        metadata = metadata or {}
        value = metadata.get(cls.METADATA_KEY)
        info = cls._from_record(value) if value is not None else None
        legacy_value = metadata.get(cls.LEGACY_METADATA_KEY)
        legacy = cls._from_legacy_record(legacy_value) if legacy_value is not None else None
        if info is not None and legacy is not None and info != legacy:
            raise ValueError("Conflicting legacy and current generation evidence.")
        return info if info is not None else legacy

    @classmethod
    def clear_metadata(cls, metadata: dict[str, Any]) -> None:
        """Remove current and legacy evidence before a new send or response association."""
        metadata.pop(cls.METADATA_KEY, None)
        metadata.pop(cls.LEGACY_METADATA_KEY, None)

    @classmethod
    def _from_record(cls, value: Any) -> RequestGenerationInfo:
        # JSON represents tuples as arrays; validate all other fields strictly.
        if isinstance(value, dict) and isinstance(value.get("request_piece_ids"), list):
            value = {**value, "request_piece_ids": tuple(value["request_piece_ids"])}
        return cls.model_validate(value)

    @classmethod
    def _from_legacy_record(cls, value: Any) -> RequestGenerationInfo:
        fields = {"requested_temperature": "requested", "resolved_temperature": "resolved", "source": "source"}
        if not isinstance(value, dict) or set(value) - fields.keys() - {"request_piece_ids"}:
            raise ValueError("Invalid legacy temperature evidence.")
        return cls._from_record(
            {
                "temperature": {name: value[key] for key, name in fields.items() if key in value},
                "request_piece_ids": value.get("request_piece_ids", ()),
            }
        )
