# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Stable identity carried by Adaptive selector arms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class AdaptiveTechniqueIdentifier:
    """
    Join registered factory identity with cross-scenario behavioral identity.

    The factory hash keeps separately registered configured techniques as
    distinct selector arms. The technique eval hash links each arm to normal
    scenario results persisted from the same ``AttackTechnique`` behavior.
    """

    factory_hash: str
    technique_eval_hash: str

    _PREFIX: ClassVar[str] = "adaptive-v1"
    _SEPARATOR: ClassVar[str] = ":"

    def serialize(self) -> str:
        """
        Serialize the identifier for selector keys and persisted labels.

        Returns:
            str: Versioned identifier containing both canonical hashes.

        Raises:
            ValueError: If either hash is empty or contains the field separator.
        """
        if self._SEPARATOR in self.factory_hash or self._SEPARATOR in self.technique_eval_hash:
            raise ValueError("Adaptive technique identity hashes cannot contain ':'")
        if not self.factory_hash or not self.technique_eval_hash:
            raise ValueError("Adaptive technique identity hashes cannot be empty")
        return self._SEPARATOR.join((self._PREFIX, self.factory_hash, self.technique_eval_hash))

    @classmethod
    def parse(cls, value: str) -> AdaptiveTechniqueIdentifier | None:
        """
        Parse a serialized Adaptive identifier.

        Unknown selector identifiers remain valid for custom selectors and
        legacy tests, so malformed or unversioned values return ``None``.

        Returns:
            AdaptiveTechniqueIdentifier | None: Parsed identity when recognized.
        """
        parts = value.split(cls._SEPARATOR)
        if len(parts) != 3 or parts[0] != cls._PREFIX or not parts[1] or not parts[2]:
            return None
        return cls(factory_hash=parts[1], technique_eval_hash=parts[2])


def get_history_eval_hash(*, technique_identifier: str) -> str:
    """
    Return the normal-scenario eval hash associated with a selector arm.

    Unversioned identifiers fall back to themselves for backward compatibility
    with custom selectors and callers that already pass eval hashes.

    Returns:
        str: Behavioral eval hash used for historical result lookup.
    """
    parsed = AdaptiveTechniqueIdentifier.parse(technique_identifier)
    return parsed.technique_eval_hash if parsed is not None else technique_identifier
