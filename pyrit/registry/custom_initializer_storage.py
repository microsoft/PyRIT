# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage backends for custom initializer Python source."""

from __future__ import annotations

import logging

from pyrit.registry.file_document_storage import FileDocumentStorage

logger = logging.getLogger(__name__)


def _decode_script(content: bytes) -> str:
    """
    Decode stored script bytes with the line endings a text-mode read would produce.

    Callers materialize this source back to a file in text mode, which translates every
    newline again, so a stored CRLF would round-trip into a corrupted CRCRLF.

    Returns:
        str: Decoded script source with normalized line endings.

    Raises:
        UnicodeDecodeError: If the content is not valid UTF-8 text.
    """
    return content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


class CustomInitializerStorage(FileDocumentStorage):
    """Read and write custom initializer scripts in a directory or blob container."""

    def __init__(self, *, source: str) -> None:
        """
        Initialize storage from a local directory or Azure Blob source URI.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        super().__init__(source=source, extension=".py", source_label="Custom initializer")

    def get_script_source(self, name: str) -> str:
        """
        Get the credential-free location of a custom initializer script.

        Returns:
            str: Local file path or Azure Blob URI for the script.
        """
        return self._get_document_source(name)

    def list_scripts(self) -> dict[str, str]:
        """
        List stored Python scripts by registry name.

        A script that is not valid UTF-8 text is logged and skipped, so one unreadable
        file cannot hide every other stored initializer.

        Returns:
            dict[str, str]: Script content keyed by registry name.
        """
        scripts: dict[str, str] = {}
        for name, content in self._list_documents().items():
            try:
                scripts[name] = _decode_script(content)
            except UnicodeDecodeError:
                logger.warning(f"Skipping stored initializer '{name}': it is not valid UTF-8 text.")
        return scripts

    def save_script(self, *, name: str, content: str) -> None:
        """Persist one custom initializer script."""
        self._save_document(name=name, content=content.encode("utf-8"))

    def delete_script(self, name: str) -> None:
        """Delete one custom initializer script if it exists."""
        self._delete_document(name)
