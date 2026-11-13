# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The verdicts a Nemotron Safety Guard score records, and their metadata keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyrit.models.score.score import Score


class InvalidNemotronSafetyGuardVerdictsError(ValueError):
    """A Nemotron Safety Guard verdict is not ``safe`` or ``unsafe``, or a category name is unusable."""


_VERDICTS = ("safe", "unsafe")


@dataclass(frozen=True)
class NemotronSafetyGuardVerdicts:
    """
    The verdicts NVIDIA's Nemotron Safety Guard gives in one judgment.

    This type owns the ``nemotron_safety_guard_user_safety``, ``nemotron_safety_guard_response_safety``
    and ``nemotron_safety_guard_safety_categories`` metadata keys and their readers and writers. A
    read treats missing, legacy or invalid data as unset.

    Attributes:
        user_safety (str | None): ``safe`` or ``unsafe`` for the user prompt, or None when the model
            left it out.
        response_safety (str | None): ``safe`` or ``unsafe`` for the response, or None when the
            model left it out.
        categories (tuple[str, ...]): The Aegis 2.0 category names the model gave, in its order.
    """

    USER_SAFETY_KEY: ClassVar[str] = "nemotron_safety_guard_user_safety"
    RESPONSE_SAFETY_KEY: ClassVar[str] = "nemotron_safety_guard_response_safety"
    CATEGORIES_KEY: ClassVar[str] = "nemotron_safety_guard_safety_categories"

    user_safety: str | None = None
    response_safety: str | None = None
    categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """
        Validate the verdicts.

        Raises:
            InvalidNemotronSafetyGuardVerdictsError: If neither verdict is given, a verdict is not
                ``safe`` or ``unsafe``, or a category name is empty or holds a comma.
        """
        if self.user_safety is None and self.response_safety is None:
            raise InvalidNemotronSafetyGuardVerdictsError("Nemotron Safety Guard verdicts need at least one verdict.")
        for name, value in (("user_safety", self.user_safety), ("response_safety", self.response_safety)):
            if value is not None and value not in _VERDICTS:
                raise InvalidNemotronSafetyGuardVerdictsError(
                    f"Nemotron Safety Guard {name} must be 'safe' or 'unsafe', got {value!r}."
                )
        for category in self.categories:
            if not isinstance(category, str) or not category.strip() or "," in category:
                raise InvalidNemotronSafetyGuardVerdictsError(
                    f"Nemotron Safety Guard category names must be non-empty text without commas, got {category!r}."
                )

    @classmethod
    def read(cls, score: Score | None) -> NemotronSafetyGuardVerdicts | None:
        """
        Read the verdicts a Nemotron Safety Guard score recorded.

        Args:
            score (Score | None): The score to read.

        Returns:
            NemotronSafetyGuardVerdicts | None: The verdicts, or None when the score has no usable ones.
        """
        return cls.from_metadata(metadata=score.score_metadata if score is not None else None)

    @classmethod
    def from_metadata(cls, *, metadata: Mapping[str, str | int | float] | None) -> NemotronSafetyGuardVerdicts | None:
        """
        Read the verdicts from score metadata.

        Args:
            metadata (Mapping[str, str | int | float] | None): The score metadata.

        Returns:
            NemotronSafetyGuardVerdicts | None: The verdicts, or None when both verdicts are missing or
                any stored value is invalid.
        """
        if not metadata:
            return None
        user_safety = metadata.get(cls.USER_SAFETY_KEY)
        response_safety = metadata.get(cls.RESPONSE_SAFETY_KEY)
        categories = metadata.get(cls.CATEGORIES_KEY, "")
        if user_safety is not None and not isinstance(user_safety, str):
            return None
        if response_safety is not None and not isinstance(response_safety, str):
            return None
        if not isinstance(categories, str):
            return None
        try:
            return cls(
                user_safety=user_safety,
                response_safety=response_safety,
                categories=tuple(name for name in categories.split(",") if name),
            )
        except InvalidNemotronSafetyGuardVerdictsError:
            return None

    def to_metadata(self) -> dict[str, str]:
        """
        Return the metadata fragment carrying these verdicts.

        A verdict the model left out and an empty category list are omitted.

        Returns:
            dict[str, str]: The verdicts under their metadata keys.
        """
        metadata = {}
        if self.user_safety is not None:
            metadata[self.USER_SAFETY_KEY] = self.user_safety
        if self.response_safety is not None:
            metadata[self.RESPONSE_SAFETY_KEY] = self.response_safety
        if self.categories:
            metadata[self.CATEGORIES_KEY] = ",".join(self.categories)
        return metadata
