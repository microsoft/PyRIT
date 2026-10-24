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

    METADATA_KEY: ClassVar[str] = "request_temperature"
    temperature: float | None = None

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> RequestGenerationConfig:
        """
        Read the canonical request temperature.

        Returns:
            RequestGenerationConfig: Missing or null means no override.
        """
        return cls(temperature=(metadata or {}).get(cls.METADATA_KEY))

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
        Serialize the request option.

        Returns:
            dict[str, Any]: Metadata fragment, empty for an unset override.
        """
        return {} if self.temperature is None else {self.METADATA_KEY: self.temperature}


class TemperatureRequestInfo(BaseModel):
    """Framework-authored evidence of the temperature parameter submitted to a provider."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)

    METADATA_KEY: ClassVar[str] = "_pyrit_temperature"
    requested_temperature: float | None
    resolved_temperature: float | None
    source: Literal["request", "target_default", "provider_default"]
    request_piece_ids: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize evidence separately from caller-authored request options.

        Returns:
            dict[str, Any]: JSON-compatible evidence metadata.
        """
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any] | None) -> TemperatureRequestInfo | None:
        """
        Read recorded evidence.

        Returns:
            TemperatureRequestInfo | None: Evidence, or None for unrecorded messages.
        """
        value = (metadata or {}).get(cls.METADATA_KEY)
        if value is None:
            return None
        # JSON represents tuples as arrays; validate all other fields strictly.
        if isinstance(value, dict) and isinstance(value.get("request_piece_ids"), list):
            value = {**value, "request_piece_ids": tuple(value["request_piece_ids"])}
        return cls.model_validate(value)
