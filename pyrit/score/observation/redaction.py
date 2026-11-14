# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Withhold sensitive evidence text when a source writes an observation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Literal, get_args

from pyrit.models import ComponentIdentifier

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from pyrit.models import SurfaceEntry

#: Retained text fields a policy can withhold: surface file content.
RedactableField = Literal["content"]


class RedactionPolicy:
    """
    Decide, when a source writes evidence, which retained text it must withhold.

    A policy withholds a whole field, never part of it, so withheld text cannot leave a
    misleading fragment behind. The field is stored empty with a redaction flag, and matchers
    treat it as unknown rather than absent: a criterion that needs it is undetermined. Sizes,
    digests and modification times are kept.

    ``fields`` names fields that are always withheld. ``patterns`` are regular expressions;
    a field is withheld when any of them matches its text. Text a source could only retain in
    part cannot be checked in full, so a policy with patterns withholds it too. The identifier
    records a digest of the patterns, not the patterns themselves.
    """

    _VERSION = 1

    def __init__(self, *, patterns: Sequence[str] = (), fields: Collection[RedactableField] = ()) -> None:
        """
        Initialize a policy from the fields to withhold and the patterns that trigger withholding.

        Args:
            patterns (Sequence[str]): Regular expressions; a field whose text matches any is withheld.
            fields (Collection[RedactableField]): Fields that are always withheld.

        Raises:
            ValueError: If the policy withholds nothing, names an unknown field, or a pattern is invalid.
        """
        unknown = set(fields) - set(get_args(RedactableField))
        if unknown:
            raise ValueError(f"Unknown redactable fields: {sorted(unknown)}.")
        if not patterns and not fields:
            raise ValueError("A redaction policy needs at least one pattern or field.")
        try:
            self._patterns = tuple(re.compile(pattern) for pattern in patterns)
        except re.error as error:
            raise ValueError(f"Invalid redaction pattern: {error}.") from error
        self._fields = frozenset(fields)
        self._patterns_sha256 = hashlib.sha256(json.dumps(list(patterns)).encode("utf-8")).hexdigest()

    def get_identifier(self) -> ComponentIdentifier:
        """Return the policy version, withheld fields, and a digest of the patterns."""
        return ComponentIdentifier.of(
            self,
            params={
                "redaction_version": self._VERSION,
                "fields": sorted(self._fields),
                "patterns_sha256": self._patterns_sha256 if self._patterns else None,
            },
        )

    def withholds(self, *, field: RedactableField, text: str, complete: bool) -> bool:
        """
        Decide whether one retained field must be withheld.

        Args:
            field (RedactableField): The field the text belongs to.
            text (str): The text the source would retain.
            complete (bool): Whether ``text`` is the field's whole value.

        Returns:
            bool: True when the field must be withheld.
        """
        if field in self._fields:
            return True
        if not self._patterns:
            return False
        return not complete or any(pattern.search(text) for pattern in self._patterns)


def _redact_surface_entry(*, entry: SurfaceEntry, policy: RedactionPolicy | None) -> SurfaceEntry:
    """
    Withhold an entry's retained text when the policy requires it.

    Returns:
        SurfaceEntry: The entry as it is written.
    """
    if policy is None or entry.content is None:
        return entry
    if not policy.withholds(field="content", text=entry.content, complete=not entry.content_truncated):
        return entry
    return entry.model_copy(update={"content": None, "content_truncated": False, "content_redacted": True})
