# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pyrit.identifiers.identifier import Identifier

# TypeVar bound to Identifier, allowing subclasses to specify their identifier type
IdentifierT = TypeVar("IdentifierT", bound=Identifier)


class LegacyIdentifiable(ABC):
    """
    Deprecated legacy interface for objects that can provide an identifier dictionary.

    This interface will eventually be replaced by Identifier dataclass.
    Classes implementing this interface should return a dict describing their identity.
    """

    @abstractmethod
    def get_identifier(self) -> dict[str, str]:
        """Return a dictionary describing this object's identity."""
        pass

    def __str__(self) -> str:
        """Return string representation of the identifier."""
        return f"{self.get_identifier()}"


class Identifiable(ABC, Generic[IdentifierT]):
    """
    Abstract base class for objects that can provide a typed identifier.

    Generic over IdentifierT, allowing subclasses to specify their exact
    identifier type for strong typing support.

    Subclasses must:
    1. Implement `_build_identifier()` to construct their specific identifier
    2. Implement `get_identifier()` to return the typed identifier (can use lazy building)
    """

    @abstractmethod
    def _build_identifier(self) -> None:
        """
        Build the identifier for this object.

        Subclasses must implement this method to construct their specific identifier type
        and store it in an instance variable (typically `_identifier`).

        This method is typically called lazily on first access via `get_identifier()`.
        """
        raise NotImplementedError("Subclasses must implement _build_identifier")

    @abstractmethod
    def get_identifier(self) -> IdentifierT:
        """
        Get the typed identifier for this object.

        Returns:
            IdentifierT: The identifier for this component.
        """
        ...
