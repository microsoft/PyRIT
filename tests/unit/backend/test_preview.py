# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for ``pyrit.backend.mappers.format_last_message_preview``."""

import pytest

from pyrit.backend.mappers import format_last_message_preview
from pyrit.models import ConversationStats


class TestFormatLastMessagePreview:
    def test_text_short_value_passes_through(self) -> None:
        result = format_last_message_preview(value="hello", data_type="text", max_len=100)
        assert result == "hello"

    def test_text_long_value_is_truncated_with_ellipsis(self) -> None:
        long_text = "x" * 200
        result = format_last_message_preview(value=long_text, data_type="text", max_len=100)
        assert result is not None
        assert len(result) == 103
        assert result.endswith("...")
        assert result.startswith("x" * 100)

    def test_text_none_value_returns_none(self) -> None:
        assert format_last_message_preview(value=None, data_type="text", max_len=100) is None

    def test_text_empty_value_returns_none(self) -> None:
        assert format_last_message_preview(value="", data_type="text", max_len=100) is None

    def test_unknown_data_type_treated_as_text(self) -> None:
        result = format_last_message_preview(value="hello", data_type=None, max_len=100)
        assert result == "hello"

    def test_default_max_len_matches_conversation_stats_contract(self) -> None:
        # The formatter's default truncation length should track the model
        # constant so callers don't have to plumb it through manually.
        long_text = "y" * (ConversationStats.PREVIEW_MAX_LEN + 50)
        result = format_last_message_preview(value=long_text, data_type="text")
        assert result is not None
        assert len(result) == ConversationStats.PREVIEW_MAX_LEN + 3
        assert result.endswith("...")

    @pytest.mark.parametrize(
        ("data_type", "label"),
        [
            ("image_path", "Image"),
            ("audio_path", "Audio"),
            ("video_path", "Video"),
            ("binary_path", "File"),
        ],
    )
    def test_media_windows_absolute_path_renders_basename_only(self, data_type: str, label: str) -> None:
        path = r"C:\Users\someone\git\PyRIT\dbdata\prompt-memory-entries\audio\1780010098266691.mp3"
        result = format_last_message_preview(value=path, data_type=data_type, max_len=100)
        assert result == f"[{label}: 1780010098266691.mp3]"
        assert "C:\\" not in (result or "")
        assert "Users" not in (result or "")

    def test_media_posix_absolute_path_renders_basename_only(self) -> None:
        path = "/home/someone/PyRIT/dbdata/prompt-memory-entries/images/abcdef.png"
        result = format_last_message_preview(value=path, data_type="image_path", max_len=100)
        assert result == "[Image: abcdef.png]"
        assert "/home/" not in (result or "")

    def test_media_relative_path_renders_basename(self) -> None:
        result = format_last_message_preview(value="audio/foo.mp3", data_type="audio_path", max_len=100)
        assert result == "[Audio: foo.mp3]"

    def test_media_azure_blob_url_strips_query_and_keeps_filename(self) -> None:
        url = "https://acct.blob.core.windows.net/container/folder/file.png?sv=2024-01-01&sig=secrettoken"
        result = format_last_message_preview(value=url, data_type="image_path", max_len=100)
        assert result == "[Image: file.png]"
        assert "sig=" not in (result or "")
        assert "blob.core.windows.net" not in (result or "")

    def test_media_empty_value_falls_back_to_label_only(self) -> None:
        result = format_last_message_preview(value="", data_type="image_path", max_len=100)
        assert result == "[Image]"

    def test_media_none_value_falls_back_to_label_only(self) -> None:
        result = format_last_message_preview(value=None, data_type="audio_path", max_len=100)
        assert result == "[Audio]"

    def test_media_data_uri_falls_back_to_label_only(self) -> None:
        # Defensive: data URIs aren't expected as stored converted_value for
        # media-path types, but if one shows up we should not try to derive a
        # nonsensical basename from it.
        result = format_last_message_preview(
            value="data:image/png;base64,iVBORw0KGgo=", data_type="image_path", max_len=100
        )
        assert result == "[Image]"

    def test_media_long_path_basename_not_truncated(self) -> None:
        # Even with a 100-char text limit, the basename label should not be
        # truncated. Memory layer fetches up to PREVIEW_FETCH_MAX_LEN chars so
        # the basename survives even very deep paths.
        deep = "C:\\very\\deep\\nested\\directory\\structure\\that\\is\\quite\\long\\file_name_that_is_also_long.png"
        result = format_last_message_preview(value=deep, data_type="image_path", max_len=20)
        assert result == "[Image: file_name_that_is_also_long.png]"

    def test_preview_fetch_max_len_contract_is_generous(self) -> None:
        # Sanity check on the model-side constant: must be large enough to fit
        # realistic filesystem paths and signed blob URLs in a single fetch.
        assert ConversationStats.PREVIEW_FETCH_MAX_LEN >= 512
