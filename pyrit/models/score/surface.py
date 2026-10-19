# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend-neutral evidence about what a surface such as a file system holds."""

from __future__ import annotations

from enum import Enum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class SurfaceMatch(str, Enum):
    """How a surface locator selects files."""

    EXACT = "exact"
    GLOB = "glob"


class SurfaceEntry(BaseModel):
    """
    One location a source read, with a bounded copy of its content.

    ``size_bytes`` is the whole file's size. ``sha256`` covers the whole content and is
    ``None`` when the source stopped reading before the end, for example on a read budget,
    so a digest is never claimed for bytes that were not hashed. ``content`` retains at most
    the source's configured limit and is ``None`` when the bytes are not UTF-8 text, so a
    criterion that needs the text can tell "absent" from "not retained".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
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
            ValueError: If truncation is claimed without retained text, or text from an
                incomplete read is presented as the whole content.
        """
        if self.content_truncated and self.content is None:
            raise ValueError("A truncated surface entry must retain the text it kept.")
        if self.sha256 is None and self.content is not None and not self.content_truncated:
            raise ValueError("Text from an incomplete read must be marked truncated.")
        return self


class SurfaceCoverage(BaseModel):
    """Whether every location the scorable names was enumerated and read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    complete: bool = False
    reasons: tuple[str, ...] = ()


class SurfaceBaseline(BaseModel):
    """
    What the named locations held when an attempt started.

    A baseline lets a later snapshot show differences in observed content, not who wrote it
    or whether writes occurred between snapshots. Entries keep bounded evidence so a criterion
    can compare what was there before with what is there now. A location the baseline did not
    cover cannot establish that a later location is new.

    ``concurrent`` is set when another attempt observed the same locations while this one ran.
    The comparison cannot associate a change with this attempt's window.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    captured_at: AwareDatetime
    entries: tuple[SurfaceEntry, ...] = ()
    coverage: SurfaceCoverage = Field(default_factory=SurfaceCoverage)
    concurrent: bool = False

    @model_validator(mode="after")
    def _validate_entries(self) -> SurfaceBaseline:
        """
        Keep baseline entries unique.

        Returns:
            SurfaceBaseline: The validated baseline.

        Raises:
            ValueError: If an entry repeats.
        """
        uris = [entry.uri for entry in self.entries]
        if len(set(uris)) != len(uris):
            raise ValueError("Baseline entries must name each location once.")
        return self

    def entry_for(self, uri: str) -> SurfaceEntry | None:
        """
        Return the baseline entry for one location.

        Args:
            uri (str): The location.

        Returns:
            SurfaceEntry | None: The entry, or None when the baseline holds none.
        """
        return next((entry for entry in self.entries if entry.uri == uri), None)
