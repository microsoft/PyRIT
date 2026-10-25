# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Provider-neutral request policy for target-hosted web search."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Mapping


class WebSearchMode(str, Enum):
    """Whether a request preserves, disables, or requires target-hosted search."""

    DEFAULT = "default"
    DISABLED = "disabled"
    REQUIRED = "required"


class WebSearchConfig(BaseModel):
    """A request policy; targets own provider options and search-result evidence."""

    METADATA_KEY: ClassVar[str] = "web_search_config"
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: WebSearchMode = WebSearchMode.DEFAULT

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize the policy to message-piece prompt metadata.

        Returns:
            dict[str, Any]: Empty for default behavior, otherwise the explicit policy.
        """
        if self.mode == WebSearchMode.DEFAULT:
            return {}
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, *, metadata: Mapping[str, Any] | None) -> WebSearchConfig:
        """
        Read a policy without changing the metadata.

        Args:
            metadata (Mapping[str, Any] | None): Message-piece prompt metadata.

        Returns:
            WebSearchConfig: The explicit policy, or default behavior when absent.
        """
        if metadata is None or cls.METADATA_KEY not in metadata:
            return cls()
        return cls.model_validate(metadata[cls.METADATA_KEY])
