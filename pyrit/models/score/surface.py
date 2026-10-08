# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend-neutral evidence about what a surface such as a file system holds."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class SurfaceEntry(BaseModel):
    """
    One location a source read, with a bounded copy of its content.

    The digest and size always cover the whole content. ``content`` retains at most the
    source's configured limit and is ``None`` when the bytes are not UTF-8 text, so a
    criterion that needs the text can tell "absent" from "not retained".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    modified_at: AwareDatetime
    content: str | None = None
    content_truncated: bool = False

    @model_validator(mode="after")
    def _validate_retained_content(self) -> SurfaceEntry:
        """
        Keep the truncation marker consistent with what was retained.

        Returns:
            SurfaceEntry: The validated entry.

        Raises:
            ValueError: If truncation is claimed without retained text.
        """
        if self.content_truncated and self.content is None:
            raise ValueError("A truncated surface entry must retain the text it kept.")
        return self


class SurfaceCoverage(BaseModel):
    """Whether every location the scorable names was enumerated and read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    complete: bool = False
    reasons: tuple[str, ...] = ()
