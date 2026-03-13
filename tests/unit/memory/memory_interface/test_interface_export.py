# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyrit.common.path import DB_DATA_PATH
from pyrit.memory import MemoryExporter, MemoryInterface
from pyrit.models import MessagePiece


def test_export_conversation_by_attack_id_file_created(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    attack1_id = sample_conversations[0].attack_identifier.hash

    # Default path in export_conversations()
    file_name = f"{attack1_id}.json"
    file_path = Path(DB_DATA_PATH, file_name)

    sqlite_instance.exporter = MemoryExporter()

    with patch("pyrit.memory.sqlite_memory.SQLiteMemory.get_message_pieces") as mock_get:
        mock_get.return_value = sample_conversations
        sqlite_instance.export_conversations(attack_id=attack1_id, file_path=file_path)

        # Verify file was created
        assert file_path.exists()


def test_export_all_conversations_file_created(sqlite_instance: MemoryInterface):
    sqlite_instance.exporter = MemoryExporter()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as temp_file:
        with (
            patch("pyrit.memory.sqlite_memory.SQLiteMemory.get_message_pieces") as mock_get_pieces,
            patch("pyrit.memory.sqlite_memory.SQLiteMemory.get_prompt_scores") as mock_get_scores,
        ):
            file_path = Path(temp_file.name)

            # Create mock with serializable data

            mock_get_pieces.return_value = [
                MagicMock(
                    original_prompt_id="1234",
                    converted_value="sample piece",
                    to_dict=lambda: {"message_piece_id": "1234", "conversation": ["sample piece"]},
                )
            ]
            mock_get_scores.return_value = [
                MagicMock(
                    message_piece_id="1234",
                    score_value=10,
                    to_dict=lambda: {"message_piece_id": "1234", "score_value": 10},
                )
            ]

            result_path = sqlite_instance.export_conversations(file_path=file_path)

            assert result_path == file_path
            assert file_path.exists()


def test_export_all_conversations_with_scores_correct_data(sqlite_instance: MemoryInterface):
    sqlite_instance.exporter = MemoryExporter()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as temp_file:
        file_path = Path(temp_file.name)
        temp_file.close()  # Close the file to allow Windows to open it for writing

    try:
        with (
            patch.object(sqlite_instance, "get_message_pieces") as mock_get_pieces,
            patch.object(sqlite_instance, "get_prompt_scores") as mock_get_scores,
        ):
            # Create a mock piece
            mock_piece = MagicMock()
            mock_piece.id = "piece_id_1234"
            mock_piece.original_prompt_id = "1234"
            mock_piece.converted_value = "sample piece"
            mock_piece.to_dict.return_value = {
                "id": "piece_id_1234",
                "original_prompt_id": "1234",
                "converted_value": "sample piece",
            }

            # Create a mock score
            mock_score = MagicMock()
            mock_score.message_piece_id = "piece_id_1234"
            mock_score.score_value = 10
            mock_score.to_dict.return_value = {"message_piece_id": "piece_id_1234", "score_value": 10}

            mock_get_pieces.return_value = [mock_piece]
            mock_get_scores.return_value = [mock_score]

            result_path = sqlite_instance.export_conversations(file_path=file_path)

            # Verify the file was created and contains correct data
            assert result_path == file_path
            assert file_path.exists()

            # Read and verify the exported JSON content
            import json

            with open(file_path) as f:
                exported_data = json.load(f)

            assert len(exported_data) == 1
            assert exported_data[0]["id"] == "piece_id_1234"
            assert exported_data[0]["original_prompt_id"] == "1234"
            assert exported_data[0]["converted_value"] == "sample piece"
            assert len(exported_data[0]["scores"]) == 1
            assert exported_data[0]["scores"][0]["score_value"] == 10
    finally:
        # Clean up the temp file
        if file_path.exists():
            os.remove(file_path)


def test_export_all_conversations_with_scores_empty_data(sqlite_instance: MemoryInterface):
    sqlite_instance.exporter = MemoryExporter()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as temp_file:
        file_path = Path(temp_file.name)
        temp_file.close()  # Close the file to allow Windows to open it for writing

    try:
        with (
            patch("pyrit.memory.sqlite_memory.SQLiteMemory.get_message_pieces") as mock_get_pieces,
            patch("pyrit.memory.sqlite_memory.SQLiteMemory.get_prompt_scores") as mock_get_scores,
        ):
            mock_get_pieces.return_value = []
            mock_get_scores.return_value = []

            result_path = sqlite_instance.export_conversations(file_path=file_path)

            # Verify the file was created and is empty JSON array
            assert result_path == file_path
            assert file_path.exists()

            # Read and verify the exported JSON content is empty
            import json

            with open(file_path) as f:
                exported_data = json.load(f)

            assert exported_data == []
    finally:
        # Clean up the temp file
        if file_path.exists():
            os.remove(file_path)
