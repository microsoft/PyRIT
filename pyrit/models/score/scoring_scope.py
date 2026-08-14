# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class ScoringScope:
    """
    Bounds a scorable that names a location rather than a stored record.

    A surface URI or a trace id names a place, not a thing, so it cannot say on its
    own which run produced what is there now. A scope narrows that question with a
    time window and correlation labels.

    Labels are the extension point for frameworks built on PyRIT that control how
    evidence is emitted and can therefore supply stronger correlation keys. PyRIT
    never interprets them.
    """

    window: tuple[datetime, datetime] | None = None
    labels: dict[str, str] = field(default_factory=dict)
