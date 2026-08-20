# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.common.mime_type import get_mime_type


@pytest.mark.parametrize(
    ("file_path", "expected_mime_type"),
    [
        ("photo.WEBP", "image/webp"),
        ("data.csv", "text/csv"),
        ("document.xml", "application/xml"),
        ("audio.flac", "audio/flac"),
        ("video.avi", "video/x-msvideo"),
        ("document.rtf", "application/rtf"),
        ("archive.zip", "application/zip"),
    ],
)
def test_get_mime_type_explicit_mapping_takes_precedence(file_path: str, expected_mime_type: str):
    with patch("pyrit.common.mime_type.guess_type") as mock_guess_type:
        result = get_mime_type(file_path)

    assert result == expected_mime_type
    mock_guess_type.assert_not_called()


def test_get_mime_type_uses_non_strict_fallback():
    with patch("pyrit.common.mime_type.guess_type", return_value=("application/example", None)) as mock_guess_type:
        result = get_mime_type("file.example")

    assert result == "application/example"
    mock_guess_type.assert_called_once_with("file.example", strict=False)


def test_get_mime_type_returns_none_when_unknown():
    with patch("pyrit.common.mime_type.guess_type", return_value=(None, None)):
        result = get_mime_type("README")

    assert result is None
