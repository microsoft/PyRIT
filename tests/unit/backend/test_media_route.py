# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the /api/media endpoint.
"""

import base64
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.main import app
from pyrit.backend.models import MessagePieceRequest
from pyrit.backend.services.media_persistence import persist_message_pieces_async
from pyrit.backend.services.staged_media import staged_media_store


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path: Path):
    with patch.object(staged_media_store, "_directory", tmp_path / "uploads"):
        yield


@pytest.fixture()
def client(compatibility_headers: dict[str, str]) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app, headers=compatibility_headers)


@pytest.fixture()
def _mock_memory(tmp_path: Path):
    """Mock CentralMemory with results_path pointing to tmp_path."""
    mock_mem = MagicMock()
    mock_mem.results_path = str(tmp_path)
    # Create allowed subdirectories
    (tmp_path / "prompt-memory-entries").mkdir()
    (tmp_path / "seed-prompt-entries").mkdir()
    with patch("pyrit.backend.services.media_persistence.CentralMemory") as mock_cm:
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
        assert response.headers["x-content-type-options"] == "nosniff"
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
            assert "outside the allowed results directory" in response.json()["detail"]
        finally:
            os.unlink(outside_path)

    def test_rejects_path_traversal(self, client: TestClient, _mock_memory: Path) -> None:
        """Path traversal attempts are rejected with 403."""
        traversal_path = str(_mock_memory / ".." / ".." / "etc" / "passwd")
        response = client.get("/api/media", params={"path": traversal_path})
        assert response.status_code == 403
        assert "outside the allowed results directory" in response.json()["detail"]

    def test_rejects_symlink_pointing_outside_results(self, client: TestClient, _mock_memory: Path) -> None:
        """A symlink under an allowed subdirectory that points outside the results dir is rejected.

        ``Path.resolve()`` resolves symlinks, so a symlink that targets a file outside the
        allowed root must be rejected just like a plain path traversal attempt.
        """
        # Create a file outside the allowed directory
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"\x89PNG\r\n\x1a\n")
            outside_target = tmp.name

        try:
            symlink_path = _mock_memory / "prompt-memory-entries" / "evil_symlink.png"
            try:
                os.symlink(outside_target, symlink_path)
            except (OSError, NotImplementedError) as exc:
                # Symlink creation may fail on Windows without admin / developer mode.
                pytest.skip(f"Cannot create symlink in this environment: {exc}")

            response = client.get("/api/media", params={"path": str(symlink_path)})
            assert response.status_code == 403
            # Confirm the rejection reason is the symlink-escape check specifically,
            # not one of the other 403 paths (subdirectory / extension).
            assert "outside the allowed results directory" in response.json()["detail"]
        finally:
            os.unlink(outside_target)

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

    @pytest.mark.parametrize(
        ("file_name", "content"),
        [
            ("program.exe", b"MZ"),
            ("data.xyz123", b"binary data"),
            ("config.yaml", b"key: value"),
            ("leaked.db", b"SQLite format 3"),
            ("active.html", b"<script>alert(1)</script>"),
            ("active.svg", b"<svg></svg>"),
        ],
    )
    def test_non_inline_type_downloads_as_opaque_attachment(
        self,
        client: TestClient,
        _mock_memory: Path,
        file_name: str,
        content: bytes,
    ) -> None:
        """Any stored type can download, but only allowlisted media renders inline."""
        file_path = _mock_memory / "prompt-memory-entries" / file_name
        file_path.write_bytes(content)

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert file_name in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_rejects_file_in_results_root(self, client: TestClient, _mock_memory: Path) -> None:
        """Files directly in results_path (not in allowed subdir) are rejected."""
        file_path = _mock_memory / "pyrit.db"
        file_path.write_bytes(b"SQLite format 3")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 403

    def test_serves_documents_as_attachments(self, client: TestClient, _mock_memory: Path) -> None:
        """Documents download as opaque bytes instead of rendering in the application origin."""
        file_path = _mock_memory / "prompt-memory-entries" / "document.pdf"
        file_path.write_bytes(b"%PDF-1.4\n")

        response = client.get("/api/media", params={"path": str(file_path)})

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"

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
        with patch("pyrit.backend.services.media_persistence.CentralMemory") as mock_cm:
            mock_cm.get_memory_instance.side_effect = ValueError("not initialized")

            response = client.get("/api/media", params={"path": "/some/file.png"})

            assert response.status_code == 500

    def test_returns_500_when_results_path_is_none(self, client: TestClient) -> None:
        """Returns 500 when memory.results_path is None."""
        mock_mem = MagicMock()
        mock_mem.results_path = None
        with patch("pyrit.backend.services.media_persistence.CentralMemory") as mock_cm:
            mock_cm.get_memory_instance.return_value = mock_mem

            response = client.get("/api/media", params={"path": "/some/file.png"})

            assert response.status_code == 500
            detail = response.json()["detail"].lower()
            assert "results_path" in detail or "results path" in detail


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("content", [b"attachment bytes", b""])
@pytest.mark.parametrize(
    ("data_type", "mime_type"),
    [
        ("image_path", "image/png"),
        ("audio_path", "audio/wav"),
        ("video_path", "video/mp4"),
        ("binary_path", "application/octet-stream"),
    ],
)
async def test_upload_stages_readable_bytes_reused_by_messages_async(
    client: TestClient, data_type: str, mime_type: str, content: bytes
) -> None:
    response = client.post(
        "/api/media/uploads",
        json={"data_type": data_type, "content": base64.b64encode(content).decode(), "mime_type": mime_type},
    )
    assert response.status_code == 201
    value = response.json()["value"]
    expires_at = response.json()["expires_at"]
    assert time.time() < expires_at <= time.time() + staged_media_store.RETENTION_SECONDS
    assert value.startswith(staged_media_store.PREFIX)
    assert await staged_media_store.read_async(value) == content
    assert client.get("/api/media", params={"path": value}).status_code == 403
    piece = MessagePieceRequest.model_validate(
        {"data_type": data_type, "original_value": value, "mime_type": mime_type}
    )
    created_paths: list[str] = []
    await persist_message_pieces_async(pieces=[piece], persisted_paths=created_paths)
    assert piece.original_value != value
    assert Path(piece.original_value).read_bytes() == content
    assert created_paths == [piece.original_value]
    assert client.get("/api/media", params={"path": piece.original_value}).content == content
    created_paths.clear()
    await persist_message_pieces_async(pieces=[piece], persisted_paths=created_paths)
    assert created_paths == []


def test_upload_reports_shared_staging_quota_without_losing_existing_uploads(client: TestClient) -> None:
    upload = {"data_type": "binary_path", "content": "YQ=="}
    with patch.object(staged_media_store, "MAX_STAGED_BYTES", 1):
        first = client.post("/api/media/uploads", json=upload)
        assert first.status_code == 201
        rejected = client.post("/api/media/uploads", json={**upload, "content": "Yg=="})
        assert rejected.status_code == 507
        assert "staging is full" in rejected.json()["detail"]
        renewed = client.post("/api/media/uploads", json=upload)
        assert renewed.status_code == 201
        assert renewed.json()["value"] == first.json()["value"]


@pytest.mark.usefixtures("patch_central_database")
async def test_upload_save_and_expiry_preserve_saved_media_and_retry_identity_async(
    client: TestClient, tmp_path: Path
) -> None:
    upload = {"data_type": "binary_path", "content": "bWVkaWE=", "mime_type": "application/octet-stream"}
    staged = client.post("/api/media/uploads", json=upload).json()["value"]
    request = {
        "save_id": str(uuid4()),
        "destination": "new_attack",
        "operator": "owner",
        "objective": "Staged upload",
        "messages": [{"role": "user", "pieces": [{"data_type": "binary_path", "original_value": staged}]}],
    }

    saved = client.post("/api/attacks/save-conversation", json=request)

    assert saved.status_code == 200, saved.text
    stored = Path(saved.json()["messages"]["messages"][0]["message_pieces"][0]["original_value"])
    assert stored.read_bytes() == b"media"
    result_files = set(stored.parent.iterdir())
    staged_path = tmp_path / "uploads" / f"{staged.removeprefix(staged_media_store.PREFIX)}.upload"
    expired_time = time.time() - staged_media_store.RETENTION_SECONDS
    os.utime(staged_path, (expired_time, expired_time))
    await staged_media_store.cleanup_expired_async()
    assert not staged_path.exists()
    assert stored.read_bytes() == b"media"
    assert client.post("/api/media/uploads", json=upload).json()["value"] == staged

    retried = client.post("/api/attacks/save-conversation", json=request)

    assert retried.status_code == 200, retried.text
    assert retried.json()["attack"]["attack_result_id"] == saved.json()["attack"]["attack_result_id"]
    assert set(stored.parent.iterdir()) == result_files


@pytest.mark.parametrize("content", ["not base64!", "/etc/passwd", "https://example.test/image.png", "%%%%"])
def test_upload_rejects_non_base64_content(client: TestClient, content: str) -> None:
    response = client.post("/api/media/uploads", json={"data_type": "image_path", "content": content})
    assert response.status_code == 400
    assert response.json()["detail"] == "File content must be valid base64."


@pytest.mark.parametrize(
    "body",
    [{"data_type": "text", "content": "YQ=="}, {"data_type": "image_path"}],
)
def test_upload_rejects_invalid_request(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/api/media/uploads", json=body).status_code == 422


def test_upload_requires_authentication_in_entra_mode(client: TestClient) -> None:
    with patch.object(app.state, "authentication_policy", AuthenticationPolicy(mode="entra")):
        response = client.post("/api/media/uploads", json={"data_type": "image_path", "content": "YQ=="})
    assert response.status_code == 401
