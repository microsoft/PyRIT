# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The verdict a CyberSecEval MITRE score records, and its metadata key."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyrit.models.score.score import Score


class InvalidCyberSecEvalMitreVerdictError(ValueError):
    """A CyberSecEval MITRE verdict is not one CyberSecEval reports."""


@dataclass(frozen=True)
class CyberSecEvalMitreVerdict:
    """
    How CyberSecEval's MITRE judging classifies a response.

    The score itself is True for ``malicious`` only; this keeps the full verdict, which CyberSecEval
    reports as separate refusal, malicious and benign counts. ``else`` is a judge answer that names none
    of the three, which CyberSecEval counts in no column.

    This type owns the ``cyberseceval_mitre_verdict`` metadata key and its reader and writer. A read
    treats a missing, legacy or invalid value as unset.

    Attributes:
        verdict (str): ``refusal``, ``malicious``, ``benign`` or ``else``.
    """

    KEY: ClassVar[str] = "cyberseceval_mitre_verdict"
    VERDICTS: ClassVar[tuple[str, ...]] = ("refusal", "malicious", "benign", "else")

    verdict: str

    def __post_init__(self) -> None:
        """
        Validate the verdict.

        Raises:
            InvalidCyberSecEvalMitreVerdictError: If the verdict is not one CyberSecEval reports.
        """
        if self.verdict not in self.VERDICTS:
            raise InvalidCyberSecEvalMitreVerdictError(
                f"CyberSecEval MITRE verdict must be one of {', '.join(self.VERDICTS)}, got {self.verdict!r}."
            )

    @classmethod
    def read(cls, score: Score | None) -> CyberSecEvalMitreVerdict | None:
        """
        Read the verdict a CyberSecEval MITRE score recorded.

        Args:
            score (Score | None): The score to read.

        Returns:
            CyberSecEvalMitreVerdict | None: The verdict, or None when the score has no usable one.
        """
        return cls.from_metadata(metadata=score.score_metadata if score is not None else None)

    @classmethod
    def from_metadata(cls, *, metadata: Mapping[str, str | int | float] | None) -> CyberSecEvalMitreVerdict | None:
        """
        Read the verdict from score metadata.

        Args:
            metadata (Mapping[str, str | int | float] | None): The score metadata.

        Returns:
            CyberSecEvalMitreVerdict | None: The verdict, or None when it is missing or invalid.
        """
        if not metadata:
            return None
        verdict = metadata.get(cls.KEY)
        if not isinstance(verdict, str) or verdict not in cls.VERDICTS:
            return None
        return cls(verdict=verdict)

    def to_metadata(self) -> dict[str, str]:
        """
        Return the metadata fragment carrying this verdict.

        Returns:
            dict[str, str]: The verdict under its metadata key.
        """
        return {self.KEY: self.verdict}
