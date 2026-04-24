# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the /api/media endpoint.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app


@pytest.fixture()
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture()
def _mock_memory(tmp_path: Path):
    """Mock CentralMemory with results_path pointing to tmp_path."""
    mock_mem = MagicMock()
    mock_mem.results_path = str(tmp_path)
    # Create allowed subdirectories
    (tmp_path / "prompt-memory-entries").mkdir()
    (tmp_path / "seed-prompt-entries").mkdir()
    with patch("pyrit.backend.routes.media.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = mock_mem
        yield tmp_path


@pytest.mark.usefixtures("_mock_memory")
class TestServeMedia:
    """Tests for the /api/media endpoint."""

    def test_serves_existing_file(self, client: TestClient, _mock_memory: Path) -> None:
        """Valid file under allowed subdirectory is served with correct MIME type."""
        results_dir = _mock_memory
        file_path = results_dir / "prompt-memory-entries" / "test_image.png"
        file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"\x89PNG\r\n\x1a\n"

    def test_rejects_path_outside_results_directory(self, client: TestClient, _mock_memory: Path) -> None:
        """Paths outside the results directory are rejected with 403."""
        # Create a file outside the allowed directory
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"secret")
            outside_path = tmp.name

        try:
            response = client.get("/api/media", params={"path": outside_path})
            assert response.status_code == 403
        finally:
            os.unlink(outside_path)

    def test_rejects_path_traversal(self, client: TestClient, _mock_memory: Path) -> None:
        """Path traversal attempts are rejected with 403."""
        traversal_path = str(_mock_memory / ".." / ".." / "etc" / "passwd")
        response = client.get("/api/media", params={"path": traversal_path})
        assert response.status_code == 403

    def test_returns_404_for_nonexistent_file(self, client: TestClient, _mock_memory: Path) -> None:
        """Non-existent files under allowed subdirectory return 404."""
        file_path = _mock_memory / "prompt-memory-entries" / "nonexistent.png"
        response = client.get("/api/media", params={"path": str(file_path)})
        assert response.status_code == 404

    def test_serves_file_in_subdirectory(self, client: TestClient, _mock_memory: Path) -> None:
        """Files in subdirectories of allowed media dirs are served."""
        sub_dir = _mock_memory / "prompt-memory-entries" / "images"
        sub_dir.mkdir(parents=True)
        file_path = sub_dir / "photo.jpg"
        file_path.write_bytes(b"\xff\xd8\xff\xe0")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_serves_file_from_seed_prompt_entries(self, client: TestClient, _mock_memory: Path) -> None:
        """Files in seed-prompt-entries are also served."""
        file_path = _mock_memory / "seed-prompt-entries" / "seed_image.png"
        file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 200

    def test_rejects_unknown_extension(self, client: TestClient, _mock_memory: Path) -> None:
        """Files with unknown extensions are rejected by the allowlist."""
        file_path = _mock_memory / "prompt-memory-entries" / "data.xyz123"
        file_path.write_bytes(b"binary data")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 403

    def test_rejects_file_in_results_root(self, client: TestClient, _mock_memory: Path) -> None:
        """Files directly in results_path (not in allowed subdir) are rejected."""
        file_path = _mock_memory / "pyrit.db"
        file_path.write_bytes(b"SQLite format 3")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 403

    def test_rejects_database_file_in_allowed_subdir(self, client: TestClient, _mock_memory: Path) -> None:
        """Database files are not in the extension allowlist."""
        file_path = _mock_memory / "prompt-memory-entries" / "leaked.db"
        file_path.write_bytes(b"SQLite format 3")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 403

    def test_rejects_yaml_file(self, client: TestClient, _mock_memory: Path) -> None:
        """YAML files are not in the extension allowlist."""
        file_path = _mock_memory / "prompt-memory-entries" / "config.yaml"
        file_path.write_bytes(b"key: value")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 403

    def test_rejects_disallowed_subdirectory(self, client: TestClient, _mock_memory: Path) -> None:
        """Files in non-allowed subdirectories are rejected."""
        other_dir = _mock_memory / "other-stuff"
        other_dir.mkdir()
        file_path = other_dir / "image.png"
        file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 403


class TestServeMediaErrors:
    """Tests for /api/media error cases without mock memory."""

    def test_returns_500_when_memory_not_initialized(self, client: TestClient) -> None:
        """Returns 500 when CentralMemory is not initialized."""
        with patch("pyrit.backend.routes.media.CentralMemory") as mock_cm:
            mock_cm.get_memory_instance.side_effect = ValueError("not initialized")

            response = client.get("/api/media", params={"path": "/some/file.png"})

            assert response.status_code == 500

    def test_returns_500_when_results_path_is_none(self, client: TestClient) -> None:
        """Returns 500 when memory.results_path is None."""
        mock_mem = MagicMock()
        mock_mem.results_path = None
        with patch("pyrit.backend.routes.media.CentralMemory") as mock_cm:
            mock_cm.get_memory_instance.return_value = mock_mem

            response = client.get("/api/media", params={"path": "/some/file.png"})

            assert response.status_code == 500
            detail = response.json()["detail"].lower()
            assert "results_path" in detail or "results path" in detail
