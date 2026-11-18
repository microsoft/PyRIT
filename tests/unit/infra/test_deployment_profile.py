# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exercise private CI selection, real initializer discovery, and the per-start working copy."""

import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from infra.pipelines import deployment_profile, resolve_profile_run
from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.infra.test_code_deployment import BASH, REPO_ROOT

COMMIT = "a" * 40
PROJECT = "11111111-1111-1111-1111-111111111111"
CONFIG = b"# Baseline\r\nallow_custom_initializers: true\r\ninitializers: [target, deployment_smoke_test]\r\n"
SCRIPT = (
    "from pyrit.setup.initializers import PyRITInitializer\n\n"
    "class DeploymentSmokeTestInitializer(PyRITInitializer):\n"
    "    async def initialize_async(self) -> None:\n"
    "        pass\n"
)


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    directory = tmp_path / "baseline"
    scripts = directory / "internal_initializers"
    scripts.mkdir(parents=True)
    (directory / ".pyrit_conf").write_bytes(CONFIG)
    (scripts / "deployment_smoke_test.py").write_text(SCRIPT, encoding="utf-8")
    (directory / "source-commit.txt").write_bytes((COMMIT + "\n").encode())
    return directory


@pytest.fixture
def build() -> dict[str, Any]:
    return {
        "id": 123,
        "project": {"id": PROJECT},
        "definition": {"id": 13},
        "reason": "individualCI",
        "status": "completed",
        "result": "succeeded",
        "sourceBranch": "refs/heads/main",
        "sourceVersion": COMMIT,
    }


def _resolve(*, selector: str, payload: object) -> tuple[tuple[int, str], str]:
    with patch.object(resolve_profile_run, "HTTPSConnection", autospec=True) as connect:
        connection = connect.return_value
        response = connection.getresponse.return_value
        response.status = 200
        response.read.side_effect = io.BytesIO(json.dumps(payload).encode()).read
        result = resolve_profile_run.resolve_run(
            collection="https://dev.azure.com/team/",
            project=PROJECT,
            definition="13",
            selector=selector,
            token="dummy",
        )
        request = connection.request.call_args
        assert connect.call_args.args == ("dev.azure.com",)
        assert request.args[0] == "GET" and request.kwargs["headers"] == {"Authorization": "Bearer dummy"}
        url = request.args[1]
        assert isinstance(url, str)
        return result, url


@pytest.mark.parametrize("selector", ["latest", "123"])
def test_pins_exact_successful_main_build(build: dict[str, Any], selector: str) -> None:
    result, url = _resolve(selector=selector, payload={"value": [build]} if selector == "latest" else build)
    assert result == (123, COMMIT)
    path = f"/team/{PROJECT}/_apis/build/builds"
    if selector == "latest":
        assert urlsplit(url).path == path
        assert parse_qs(urlsplit(url).query) == {
            "api-version": ["7.1"],
            "branchName": ["refs/heads/main"],
            "definitions": ["13"],
            "statusFilter": ["completed"],
            "resultFilter": ["succeeded"],
            "queryOrder": ["finishTimeDescending"],
            "$top": ["1"],
        }
    else:
        assert urlsplit(url).path == path + "/123"


@pytest.mark.parametrize(
    "changes",
    [
        {"sourceBranch": "refs/pull/42/merge"},
        {"reason": "pullRequest"},
        {"project": {"id": "other"}},
        {"definition": {"id": 14}},
        {"result": "partiallySucceeded"},
        {"result": "failed"},
        {"status": "inProgress"},
        {"sourceVersion": "bad-sha"},
        {"id": 124},
    ],
)
def test_rejects_untrusted_explicit_run(build: dict[str, Any], changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="expected definition"):
        _resolve(selector="123", payload={**build, **changes})


@pytest.mark.parametrize("selector", ["0", "-1", "", "1?api-version=7.1"])
def test_invalid_selector_never_contacts_ado(selector: str) -> None:
    with pytest.raises(ValueError, match="positive decimal"):
        _resolve(selector=selector, payload={})


def test_no_successful_run_does_not_fall_back() -> None:
    with pytest.raises(ValueError, match="No successful"):
        _resolve(selector="latest", payload={"value": []})


@pytest.mark.parametrize(
    "extra", ["bundle.zip", ".dockerignore", "internal_initializers/notes", "internal_initializers/X.py"]
)
def test_rejects_extra_files_before_build(profile: Path, extra: str) -> None:
    (profile / extra).touch()
    with pytest.raises(ValueError, match="artifact layout|flat"):
        deployment_profile.validate_artifact(directory=profile, commit=COMMIT)


def test_rejects_nested_or_linked_initializers(profile: Path) -> None:
    entry = profile / "internal_initializers" / "nested.py"
    entry.mkdir()
    with pytest.raises(ValueError, match="flat"):
        deployment_profile.validate_artifact(directory=profile, commit=COMMIT)
    entry.rmdir()
    try:
        entry.symlink_to(profile / ".pyrit_conf")
    except OSError:
        pytest.skip("Creating symlinks requires elevated Windows permissions")
    with pytest.raises(ValueError, match="flat"):
        deployment_profile.validate_artifact(directory=profile, commit=COMMIT)


@pytest.mark.parametrize("marker", [b"main", b"a" * 39, b"b" * 40, b"A" * 40, b"a" * 40 + b"\nother\n"])
def test_requires_exact_authoritative_commit(profile: Path, marker: bytes) -> None:
    (profile / "source-commit.txt").write_bytes(marker)
    with pytest.raises(ValueError, match="sourceVersion"):
        deployment_profile.validate_artifact(directory=profile, commit=COMMIT)


@pytest.mark.usefixtures("patch_central_database")
def test_strict_discovery_including_unused_scripts(profile: Path, tmp_path: Path) -> None:
    deployment_profile.validate_artifact(directory=profile, commit=COMMIT)
    with patch.object(tempfile, "tempdir", str(tmp_path)):
        deployment_profile.validate_profile(profile)
        unused = profile / "internal_initializers" / "unused.py"
        unused.write_text("import missing_private_dependency\n")
        with pytest.raises(ValueError, match="Failed to load initializer script"):
            deployment_profile.validate_profile(profile)
        unused.write_bytes(b"\xff")
        with pytest.raises(ValueError, match="Not every bundled"):
            deployment_profile.validate_profile(profile)


@pytest.mark.parametrize(
    "config",
    [
        CONFIG + b"custom_initializers_source: ./elsewhere\n",
        CONFIG + b"custom_initializers_source: null\n",
        CONFIG.replace(b"true", b"false"),
    ],
)
def test_requires_pipeline_owned_initializer_source(profile: Path, tmp_path: Path, config: bytes) -> None:
    (profile / ".pyrit_conf").write_bytes(config)
    with patch.object(tempfile, "tempdir", str(tmp_path)), pytest.raises(ValueError, match="custom[ _]initializer"):
        deployment_profile.validate_profile(profile)


def test_gui_and_migration_starts_never_promote_live_edits(profile: Path, tmp_path: Path) -> None:
    wrapper = tmp_path / "deployment_profile.py"
    shutil.copyfile(deployment_profile.__file__, wrapper)
    script = Path("internal_initializers/deployment_smoke_test.py")

    def start(mode: str, *args: str) -> Path:
        with (
            patch.dict(os.environ, {"PYRIT_CONFIG_FILE": "https://legacy.invalid/conf", "PYRIT_MODE": mode}),
            patch.object(tempfile, "tempdir", str(tmp_path)),
            patch.object(sys, "argv", [str(wrapper), "start", *args]),
            patch.object(os, "execv") as launch,
        ):
            runpy.run_path(str(wrapper), run_name="__main__")
            assert os.environ["PYRIT_MODE"] == mode
            assert launch.call_args.args == ("/app/start.sh", ["/app/start.sh", *args])
            config = Path(os.environ["PYRIT_CONFIG_FILE"])
            assert config.read_bytes().startswith(CONFIG)
            assert (
                Path(yaml.safe_load(config.read_bytes())["custom_initializers_source"]) == config.parent / script.parent
            )
            return config

    gui = start("gui")
    gui.write_text("operation: live_edit")
    (gui.parent / script).write_text("edited")
    (gui.parent / script.parent / "added.py").touch()
    fresh = start("migrate", "--check-only")
    assert gui.parent != fresh.parent and gui.read_text() == "operation: live_edit"
    assert (fresh.parent / script).read_text() == SCRIPT
    assert not (fresh.parent / script.parent / "added.py").exists()
    assert (profile / ".pyrit_conf").read_bytes() == CONFIG and (profile / script).read_text() == SCRIPT


@pytest.mark.skipif(BASH is None, reason="Native Bash is required")
@pytest.mark.parametrize(
    ("version", "use_profile", "failure"),
    [
        ("main", False, ""),
        ("main", True, ""),
        ("1.2.3", True, ""),
        ("1.2.3", False, ""),
        ("main", False, "dirty"),
        ("1.2.3", True, "check-image"),
        ("1.2.3", True, "anonymous"),
        ("1.2.3", True, "unknown-access"),
        ("1.2.3", False, "unstamped"),
    ],
)
def test_build_pins_only_the_validated_image(
    *, tmp_path: Path, profile: Path, version: str, use_profile: bool, failure: str
) -> None:
    # Use the real build script and compatibility reader against a synthetic installed package.
    # Docker, PyPI, ACR, and Git are offline stand-ins; no project credentials are used.
    assert BASH is not None
    package_version = "1.2.3"
    code_commit = COMMIT if version == "main" else "b" * 40
    identity = f"{package_version}+g{code_commit}"
    package = tmp_path / "pyrit"
    frontend = package / "backend/frontend"
    frontend.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "pyrit/_compatibility.py", package / "_compatibility.py")
    (package / "_version.py").write_text(f"__version__ = {package_version!r}\n")
    (package / "__init__.py").write_text("from ._version import __version__\n")
    stamp = {"version": package_version, "commit": code_commit, "compatibility_id": identity, "dirty": False}
    (package / "_compatibility.json").write_text(json.dumps(stamp))
    (frontend / "index.html").touch()
    if failure != "unstamped":
        (frontend / "compatibility.json").write_text(json.dumps({"compatibility_id": identity}))
    commands = tmp_path / "commands"
    script = """
git() {
  case "$1" in
    rev-parse) echo "$PYRIT_SOURCE_VERSION";;
    status) if [[ "$FAIL" == dirty ]]; then echo ' M changed'; fi;;
    *) return 98;;
  esac
}
az() {
  echo "az $*" >>"$COMMANDS"
  case "$1 $2" in
    'acr show') case "$FAIL" in anonymous) echo true;; unknown-access) echo null;; *) echo false;; esac;;
    'acr login') return 0;;
    *) return 98;;
  esac
}
python3() {
  echo "python3 $*" >>"$COMMANDS"
  if [[ "$1" != build_scripts/select_pypi_version.py ]]; then "$TEST_PYTHON" "$@"; fi
}
docker() {
  echo "docker $*" >>"$COMMANDS"
  case "$1" in
    build|tag) return 0;;
    run) if [[ "$*" == *check-image ]]; then echo PRIVATE-CONTENTS >&2; [[ "$FAIL" != check-image ]];
         else (cd "$TEST_INSTALL" && "$TEST_PYTHON" - "$PYRIT_VERSION" "$PYRIT_SOURCE_VERSION"); fi;;
    push) echo "tag: digest: sha256:$DIGEST size: 345";;
    *) return 98;;
  esac
}
source infra/pipelines/build_image.sh
"""
    environment = {
        "PYRIT_VERSION": version,
        "PYRIT_ACR_NAME": "copyritacr",
        "PYRIT_ACR_LOGIN_SERVER": "copyritacr.azurecr.io",
        "PYRIT_IMAGE_NAME": "pyrit",
        "PYRIT_SOURCE_VERSION": COMMIT,
        "USE_PROFILE": str(use_profile),
        "PROFILE_DIRECTORY": profile.as_posix(),
        "PROFILE_RUN_ID": "123",
        "PROFILE_COMMIT": COMMIT,
        "BUILD_BUILDID": "45",
        "SYSTEM_JOBATTEMPT": "2",
        "DIGEST": "c" * 64,
        "TEST_INSTALL": tmp_path.as_posix(),
        "TEST_PYTHON": Path(sys.executable).as_posix(),
        "COMMANDS": commands.as_posix(),
        "FAIL": failure,
    }
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-s"],
        input=script,
        env={**os.environ, "BASH_ENV": "", **environment},
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=TEST_WATCHDOG_SECONDS,
        check=False,
    )
    trace = commands.read_text() if commands.exists() else ""
    assert "PRIVATE-CONTENTS" not in result.stdout + result.stderr
    if failure:
        assert result.returncode != 0 and "docker push" not in trace and "immutableImage;" not in result.stdout
        return
    assert result.returncode == 0, result.stdout + result.stderr
    base_build = next(line for line in trace.splitlines() if "-f docker/Dockerfile" in line)
    assert "PYRIT_EXTRAS=all" in base_build
    if version == "main":
        assert f"PYRIT_SOURCE=local --build-arg GIT_COMMIT={COMMIT} --build-arg GIT_MODIFIED=false" in base_build
        assert "select_pypi_version" not in trace
    else:
        assert "PYRIT_SOURCE=pypi --build-arg PYRIT_VERSION=1.2.3" in base_build and "GIT_COMMIT" not in base_build
        assert "select_pypi_version.py --version 1.2.3" in trace
    repository = "copyritacr.azurecr.io/pyrit" + ("-internal" if use_profile else "")
    tag = COMMIT if version == "main" else "v1.2.3"
    assert [line for line in trace.splitlines() if line.startswith("docker push")] == [
        f"docker push {repository}:{tag}-45-2"
    ]
    assert f"immutableImage;isOutput=true]{repository}@sha256:{'c' * 64}" in result.stdout
    assert f"compatibility={identity}" in result.stdout
    assert ("az acr show" in trace) is use_profile
    assert ("check-artifact" in trace and "check-image" in trace) is use_profile
