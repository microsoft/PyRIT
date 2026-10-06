# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Offline coverage for automatic and explicit PyPI release selection."""

import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from build_scripts import select_pypi_version


def _metadata(version: str = "1.10.0") -> dict[str, Any]:
    filename = f"pyrit-{version}-py3-none-any.whl"
    return {
        "info": {"name": "pyrit", "version": version, "yanked": False},
        "urls": [
            {
                "filename": filename,
                "packagetype": "bdist_wheel",
                "url": f"https://files.pythonhosted.org/packages/example/{filename}",
                "yanked": False,
            }
        ],
    }


def _resolve(*, metadata: object, override: str = "") -> str:
    response = io.BytesIO(json.dumps(metadata).encode())
    with patch.object(select_pypi_version, "urlopen", return_value=response) as lookup:
        version = select_pypi_version.resolve_pypi_version(override)
    lookup.assert_called_once()
    expected = f"https://pypi.org/pypi/pyrit/{override}/json" if override else "https://pypi.org/pypi/pyrit/json"
    assert lookup.call_args.args == (expected,)
    assert lookup.call_args.kwargs == {"timeout": 30}
    return version


@pytest.mark.parametrize("version", ["1.10.0", "1.10.0.post1", "2.0.0"])
def test_latest_uses_pypi_ordering_not_lexical_order_or_upload_time(version: str) -> None:
    metadata = _metadata(version)
    metadata["releases"] = {"1.9.0": [], "99.0.0rc1": [], "99.0.0": [{"yanked": True}]}
    assert _resolve(metadata=metadata) == version


@pytest.mark.parametrize("version", ["1.2.0a1", "1.2.0b1", "1.2.0rc1", "1.2.0.dev1", "1.2.0.post1.dev1"])
def test_latest_rejects_prerelease_fallback_when_no_stable_release_is_available(version: str) -> None:
    with pytest.raises(ValueError, match="stable, non-yanked"):
        _resolve(metadata=_metadata(version))


@pytest.mark.parametrize("version", ["1.0.0", "1.2.0rc1", "1.2.0.dev0", "1.2.0.post1"])
def test_explicit_override_validates_that_exact_published_release(version: str) -> None:
    assert _resolve(metadata=_metadata(version), override=version) == version


@pytest.mark.parametrize(
    "override", ["latest", "v1.2.0", "1.2.*", ">=1.2.0", "  ", "1.2.0\nversion=evil", "1.2.0 --index-url=x"]
)
def test_invalid_override_fails_before_lookup(override: str) -> None:
    with patch.object(select_pypi_version, "urlopen") as lookup:
        with pytest.raises(ValueError, match="exact release"):
            select_pypi_version.resolve_pypi_version(override)
    lookup.assert_not_called()


def test_override_does_not_accept_a_different_release() -> None:
    with pytest.raises(ValueError, match="Requested PyPI release 1.0.0, but PyPI returned 1.10.0"):
        _resolve(metadata=_metadata(), override="1.0.0")


@pytest.mark.parametrize("override", ["", "1.10.0"])
@pytest.mark.parametrize("yanked", [True, None, "false"])
def test_yanked_or_malformed_release_fails_without_fallback(*, override: str, yanked: object) -> None:
    metadata = _metadata()
    metadata["info"]["yanked"] = yanked
    with pytest.raises(ValueError, match="yanked or has invalid yank metadata"):
        _resolve(metadata=metadata, override=override)


@pytest.mark.parametrize("metadata", [None, [], {}, {"info": None}, {"info": {"name": "another-package"}}])
def test_invalid_project_metadata_fails(metadata: object) -> None:
    with pytest.raises(ValueError, match="Invalid PyPI project metadata"):
        _resolve(metadata=metadata)


@pytest.mark.parametrize("version", [None, 123, "", "not-a-version", "1.2.0\nversion=evil"])
def test_invalid_metadata_version_fails(version: object) -> None:
    metadata = _metadata()
    metadata["info"]["version"] = version
    with pytest.raises(ValueError, match="exact release"):
        _resolve(metadata=metadata)


@pytest.mark.parametrize("files", [None, {}, [None], [{}], [{"yanked": "false"}]])
def test_invalid_distribution_metadata_fails(files: object) -> None:
    metadata = _metadata()
    metadata["urls"] = files
    with pytest.raises(ValueError, match="Invalid distribution metadata"):
        _resolve(metadata=metadata)


@pytest.mark.parametrize(
    "changes",
    [
        {"yanked": True},
        {"packagetype": "unknown"},
        {"url": ""},
        {"url": "http://files.pythonhosted.org/packages/pyrit-1.10.0-py3-none-any.whl"},
        {"filename": "another-package-1.10.0.whl"},
        {"filename": "pyrit-1.10.01-py3-none-any.whl"},
        {"filename": "pyrit-1.10.0.post1-py3-none-any.whl"},
    ],
)
def test_release_requires_a_non_yanked_published_distribution(changes: dict[str, object]) -> None:
    metadata = _metadata()
    metadata["urls"][0].update(changes)
    with pytest.raises(ValueError, match="no non-yanked published wheel or sdist"):
        _resolve(metadata=metadata)


def test_empty_release_fails_without_selecting_an_older_release() -> None:
    metadata = _metadata()
    metadata["urls"] = []
    metadata["releases"] = {"1.0.0": _metadata("1.0.0")["urls"]}
    with pytest.raises(ValueError, match="1.10.0 has no non-yanked published"):
        _resolve(metadata=metadata)


def test_non_yanked_sdist_is_a_published_distribution() -> None:
    metadata = _metadata()
    metadata["urls"][0].update(
        {
            "packagetype": "sdist",
            "filename": "pyrit-1.10.0.tar.gz",
            "url": "https://files.pythonhosted.org/packages/example/pyrit-1.10.0.tar.gz",
        }
    )
    assert _resolve(metadata=metadata) == "1.10.0"


@pytest.mark.parametrize("override", ["", "1.10.0"])
@pytest.mark.parametrize(
    "error",
    [URLError("unavailable"), HTTPError("https://pypi.org", 404, "Not Found", None, None), TimeoutError("timed out")],
)
def test_lookup_errors_propagate_without_fallback(*, override: str, error: OSError) -> None:
    with patch.object(select_pypi_version, "urlopen", side_effect=error) as lookup:
        with pytest.raises(OSError):
            select_pypi_version.resolve_pypi_version(override)
    lookup.assert_called_once()


@pytest.mark.parametrize("body", [b"not JSON", b'{"info":'])
def test_invalid_json_is_not_a_success(body: bytes) -> None:
    with patch.object(select_pypi_version, "urlopen", return_value=io.BytesIO(body)):
        with pytest.raises(ValueError):
            select_pypi_version.resolve_pypi_version()


@pytest.mark.parametrize("override", ["", "1.10.0"])
def test_cli_prints_only_the_resolved_version(*, capsys: pytest.CaptureFixture[str], override: str) -> None:
    with (
        patch.object(sys, "argv", ["select_pypi_version.py", "--version", override]),
        patch.object(select_pypi_version, "urlopen", return_value=io.BytesIO(json.dumps(_metadata()).encode())),
    ):
        select_pypi_version.main()
    output = capsys.readouterr()
    assert output.out == "1.10.0\n"
    assert output.err == ""


@pytest.mark.parametrize("error", [URLError("unavailable"), ValueError("invalid metadata"), TimeoutError("timed out")])
def test_cli_reports_errors_without_outputting_a_version(
    *, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    with (
        patch.object(sys, "argv", ["select_pypi_version.py"]),
        patch.object(select_pypi_version, "resolve_pypi_version", side_effect=error),
        pytest.raises(SystemExit) as stopped,
    ):
        select_pypi_version.main()
    output = capsys.readouterr()
    assert stopped.value.code == 1
    assert output.out == ""
    assert "::error::PyPI release selection failed:" in output.err


@pytest.mark.parametrize("override", ["", "1.10.0"])
def test_response_read_timeout_fails_without_outputting_a_version(
    *, capsys: pytest.CaptureFixture[str], override: str
) -> None:
    response = MagicMock(spec=io.BytesIO)
    response.__enter__.return_value = response
    response.read.side_effect = TimeoutError("PyPI response read timed out")
    with (
        patch.object(sys, "argv", ["select_pypi_version.py", "--version", override]),
        patch.object(select_pypi_version, "urlopen", return_value=response),
        pytest.raises(SystemExit) as stopped,
    ):
        select_pypi_version.main()
    output = capsys.readouterr()
    assert stopped.value.code == 1
    assert output.out == ""
    assert "::error::PyPI release selection failed: PyPI response read timed out" in output.err
    response.read.assert_called_once()
    response.__exit__.assert_called_once()
