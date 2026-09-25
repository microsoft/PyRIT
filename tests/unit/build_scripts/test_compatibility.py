# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Provenance validation and Git-free distribution regression tests."""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest

from build_scripts import build_backend, prepare_package
from build_scripts.stamp_compatibility import read_stamp, seal_frontend, stamp_source, verify_distribution
from pyrit._compatibility import get_compatibility_id, is_valid_compatibility_id

ROOT = Path(__file__).resolve().parents[3]
COMMIT = "a" * 40


@pytest.fixture
def source(tmp_path, monkeypatch):
    package = tmp_path / "pyrit"
    package.mkdir()
    (package / "_version.py").write_text('__version__ = "1.2.0.dev0"\n')
    monkeypatch.delenv("PYRIT_SOURCE_COMMIT", raising=False)
    monkeypatch.delenv("PYRIT_SOURCE_DIRTY", raising=False)
    return tmp_path


def _stamp(source, monkeypatch, *, dirty="false"):
    monkeypatch.setenv("PYRIT_SOURCE_COMMIT", COMMIT)
    monkeypatch.setenv("PYRIT_SOURCE_DIRTY", dirty)
    return stamp_source(source, development=dirty == "true")


def _frontend(source, stamp):
    frontend = source / "pyrit/backend/frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text('<script src="app.js"></script>')
    (frontend / "app.js").write_text(f'const identity = "{stamp["compatibility_id"]}";')
    (frontend / "compatibility.json").write_text(json.dumps({"compatibility_id": stamp["compatibility_id"]}))
    seal_frontend(source, stamp)
    return frontend


@pytest.mark.parametrize("value", [None, "", "1.2.0", f"1.2.0+g{COMMIT[:8]}", f"1.2.0+g{COMMIT}\n", 3])
def test_invalid_identity(value):
    assert not is_valid_compatibility_id(value)


def test_full_commit_identity():
    assert is_valid_compatibility_id(f"1.2.0.dev0+g{COMMIT}")


@pytest.mark.parametrize("version", ["1.2.0custom", "1.2.0RC1", "1" * 211 + ".2.0"])
def test_build_and_runtime_reject_the_same_invalid_identity(*, source: Path, version: str) -> None:
    (source / "pyrit/_version.py").write_text(f"__version__ = {version!r}\n")
    stamp = {"version": version, "commit": COMMIT, "dirty": False, "compatibility_id": f"{version}+g{COMMIT}"}
    stamp_path = source / "pyrit/_compatibility.json"
    original = json.dumps(stamp)
    stamp_path.write_text(original)
    with (
        patch("pyrit._compatibility.__file__", str(source / "pyrit/_compatibility.py")),
        patch("pyrit._version.__version__", version),
        patch.dict(os.environ, {"PYRIT_SOURCE_COMMIT": COMMIT, "PYRIT_SOURCE_DIRTY": "false"}),
    ):
        with pytest.raises(ValueError, match="provenance"):
            get_compatibility_id()
        with pytest.raises(ValueError, match="provenance"):
            read_stamp(source)
        with pytest.raises(ValueError, match="provenance"):
            stamp_source(source)
    assert stamp_path.read_text() == original


def test_build_validator_loads_without_importing_pyrit() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import runpy, sys; "
                "helper = runpy.run_path(sys.argv[1]); "
                f"assert helper['is_valid_compatibility_id']('1.2.0.dev0+g{COMMIT}'); "
                "assert 'pyrit' not in sys.modules"
            ),
            str(ROOT / "build_scripts/stamp_compatibility.py"),
        ],
        check=True,
        capture_output=True,
    )
    assert not result.stdout


@pytest.mark.parametrize("writer", ["source", "frontend"])
@pytest.mark.parametrize("replace_fails", [False, True])
def test_stamp_writes_are_atomic(*, source: Path, writer: str, replace_fails: bool) -> None:
    with patch.dict(os.environ, {"PYRIT_SOURCE_COMMIT": COMMIT, "PYRIT_SOURCE_DIRTY": "false"}):
        stamp = stamp_source(source)
        frontend = _frontend(source, stamp)
        stamp_path = source / "pyrit/_compatibility.json"
        original = stamp_path.read_text(encoding="utf-8")
        original_paths = set(source.rglob("*"))
        original_replace = Path.replace

        def observe_replace(temporary_path: Path, destination: Path) -> Path:
            assert destination == stamp_path
            assert temporary_path.parent == stamp_path.parent
            assert temporary_path != stamp_path
            assert temporary_path.stat().st_mode & 0o444 == 0o444
            assert stamp_path.read_text(encoding="utf-8") == original
            assert (
                json.loads(temporary_path.read_text(encoding="utf-8"))["compatibility_id"] == stamp["compatibility_id"]
            )
            if replace_fails:
                raise OSError("replacement failed")
            return original_replace(temporary_path, destination)

        with (
            patch.object(Path, "replace", autospec=True, side_effect=observe_replace) as replace,
            pytest.raises(OSError, match="replacement failed") if replace_fails else nullcontext(),
        ):
            if writer == "source":
                stamp_source(source)
            else:
                (frontend / "app.js").write_text("updated bundle")
                seal_frontend(source, stamp)

    replace.assert_called_once()
    assert set(source.rglob("*")) == original_paths
    assert read_stamp(source)["compatibility_id"] == stamp["compatibility_id"]
    assert (stamp_path.read_text(encoding="utf-8") == original) is replace_fails


@pytest.mark.parametrize("value", [None, 1, True, []])
def test_package_version_must_be_a_string(source, monkeypatch, value):
    (source / "pyrit/_version.py").write_text(f"__version__ = {value!r}\n")
    with pytest.raises(ValueError, match="version must be a string"):
        _stamp(source, monkeypatch)


def test_missing_provenance_fails(source):
    with pytest.raises(ValueError, match="provenance"):
        stamp_source(source)


def test_dirty_publication_rejected(source, monkeypatch):
    with pytest.warns(UserWarning, match="Local edits"):
        dirty_stamp = _stamp(source, monkeypatch, dirty="true")
    with pytest.raises(ValueError, match="dirty"):
        stamp_source(source)
    clean_stamp = _stamp(source, monkeypatch)
    assert dirty_stamp["compatibility_id"] == clean_stamp["compatibility_id"]


def test_full_commit_and_dirty_attestation_required(source, monkeypatch):
    monkeypatch.setenv("PYRIT_SOURCE_COMMIT", COMMIT)
    with pytest.raises(ValueError, match="PYRIT_SOURCE_DIRTY"):
        stamp_source(source)
    monkeypatch.setenv("PYRIT_SOURCE_DIRTY", "false")
    monkeypatch.setenv("PYRIT_SOURCE_COMMIT", COMMIT[:8])
    with pytest.raises(ValueError, match="full lowercase"):
        stamp_source(source)


def test_git_checkout_is_authoritative(source, monkeypatch):
    (source / ".git").mkdir()
    monkeypatch.setattr(
        "build_scripts.stamp_compatibility._git", lambda root, *args: COMMIT if args[0] == "rev-parse" else ""
    )
    monkeypatch.setenv("PYRIT_SOURCE_COMMIT", "b" * 40)
    with pytest.raises(ValueError, match="HEAD"):
        stamp_source(source)


def test_git_free_stamp_and_frontend_integrity(source, monkeypatch):
    stamp = _stamp(source, monkeypatch)
    frontend = _frontend(source, stamp)
    monkeypatch.delenv("PYRIT_SOURCE_COMMIT")
    assert stamp_source(source)["compatibility_id"] == stamp["compatibility_id"]
    verify_distribution(source)
    (frontend / "app.js").write_text("stale frontend")
    with pytest.raises(ValueError, match="differs"):
        verify_distribution(source)


def test_different_bundle_identity_rejected(source, monkeypatch):
    stamp = _stamp(source, monkeypatch)
    frontend = _frontend(source, stamp)
    (frontend / "compatibility.json").write_text(json.dumps({"compatibility_id": f"1.2.0.dev0+g{'b' * 40}"}))
    with pytest.raises(ValueError, match="differ"):
        seal_frontend(source, stamp)


@pytest.mark.parametrize("payload", ["null", "[]", "{}", "not json"])
def test_packaged_stamp_malformed(source, monkeypatch, payload):
    (source / "pyrit/_compatibility.json").write_text(payload)
    monkeypatch.setattr("pyrit._compatibility.__file__", str(source / "pyrit/_compatibility.py"))
    with pytest.raises(ValueError, match="provenance"):
        get_compatibility_id()
    with pytest.raises(ValueError, match="provenance"):
        read_stamp(source)


def test_installed_identity_never_reads_git(source, monkeypatch):
    stamp = _stamp(source, monkeypatch)
    monkeypatch.setattr("pyrit._version.__version__", stamp["version"])
    monkeypatch.setattr("pyrit._compatibility.__file__", str(source / "pyrit/_compatibility.py"))
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: pytest.fail("Runtime must not invoke Git"))
    assert get_compatibility_id() == stamp["compatibility_id"]


def test_hooks_prepare_all_artifacts(monkeypatch):
    calls = []
    monkeypatch.setattr(build_backend, "_prepare", lambda: calls.append("prepare"))
    monkeypatch.setattr(build_backend.build_meta, "build_wheel", lambda *args: "wheel")
    monkeypatch.setattr(build_backend.build_meta, "build_sdist", lambda *args: "sdist")
    assert build_backend.build_wheel("dist") == "wheel"
    assert build_backend.build_sdist("dist") == "sdist"
    assert calls == ["prepare", "prepare"]


def test_source_hooks_cannot_reuse_assets_when_frontend_deleted(source, monkeypatch):
    stamp = _stamp(source, monkeypatch)
    _frontend(source, stamp)
    (source / ".git").mkdir()
    monkeypatch.setattr(build_backend, "ROOT", source)
    monkeypatch.setattr("build_scripts.prepare_package.main", lambda: 1)
    with pytest.raises(RuntimeError, match="preparation failed"):
        build_backend._prepare()


def test_unattributed_source_tree_cannot_reuse_a_stamp(source, monkeypatch):
    _stamp(source, monkeypatch)
    monkeypatch.delenv("PYRIT_SOURCE_COMMIT")
    (source / "frontend").mkdir()
    (source / "frontend/package.json").write_text("{}")
    with pytest.raises(ValueError, match="explicit"):
        stamp_source(source)


def test_git_free_dirty_distribution_rejected(source, monkeypatch):
    with pytest.warns(UserWarning):
        stamp = _stamp(source, monkeypatch, dirty="true")
    _frontend(source, stamp)
    monkeypatch.delenv("PYRIT_SOURCE_COMMIT")
    with pytest.raises(ValueError, match="dirty"):
        verify_distribution(source)


def test_git_free_build_preserves_stamp_despite_environment(source, monkeypatch):
    stamp = _stamp(source, monkeypatch)
    _frontend(source, stamp)
    monkeypatch.setenv("PYRIT_SOURCE_COMMIT", "b" * 40)
    monkeypatch.setattr(build_backend, "ROOT", source)
    monkeypatch.setattr(build_backend, "verify_distribution", lambda: verify_distribution(source))
    build_backend._prepare()
    assert read_stamp(source)["compatibility_id"] == stamp["compatibility_id"]


def test_version_sources_must_match(source, monkeypatch):
    (source / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n')
    with pytest.raises(ValueError, match="versions differ"):
        _stamp(source, monkeypatch)


def test_package_preparation_embeds_and_seals_one_identity(source, monkeypatch):
    _stamp(source, monkeypatch)
    (source / "frontend").mkdir()
    (source / "frontend/package.json").write_text("{}")
    monkeypatch.setattr(prepare_package, "__file__", str(source / "build_scripts/prepare_package.py"))

    def build_frontend(frontend_dir, *, compatibility_id):
        dist = frontend_dir / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("frontend")
        (dist / "compatibility.json").write_text(json.dumps({"compatibility_id": compatibility_id}))
        (dist / "app.js").write_text(compatibility_id)
        return True

    monkeypatch.setattr(prepare_package, "build_frontend", build_frontend)
    assert prepare_package.main() == 0
    verify_distribution(source)
    assert read_stamp(source)["compatibility_id"] == f"1.2.0.dev0+g{COMMIT}"


def test_sdist_wheel_install_without_git(source, monkeypatch):
    stamp = _stamp(source, monkeypatch)
    _frontend(source, stamp)
    monkeypatch.delenv("PYRIT_SOURCE_COMMIT")
    (source / "pyrit/__init__.py").write_text("")
    shutil.copy2(ROOT / "pyrit/_compatibility.py", source / "pyrit/_compatibility.py")
    shutil.copytree(ROOT / "build_scripts", source / "build_scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (source / "pyproject.toml").write_text(
        '[project]\nname="pyrit"\nversion="1.2.0.dev0"\n'
        '[build-system]\nrequires=["setuptools", "wheel"]\nbuild-backend="build_scripts.build_backend"\n'
        'backend-path=["."]\n[tool.setuptools.packages.find]\ninclude=["pyrit", "pyrit.*"]\n'
    )
    shutil.copy2(ROOT / "MANIFEST.in", source / "MANIFEST.in")
    build_env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"PYRIT_SOURCE_COMMIT", "PYRIT_SOURCE_DIRTY", "PYRIT_COMPATIBILITY_ID"}
    }
    build_env["PATH"] = ""
    subprocess.run(
        [sys.executable, "-c", "from build_scripts.build_backend import build_sdist; build_sdist('dist')"],
        cwd=source,
        env=build_env,
        check=True,
        capture_output=True,
    )
    archive = next((source / "dist").glob("*.tar.gz"))
    unpacked = source / "unpacked"
    shutil.unpack_archive(str(archive), str(unpacked))
    extracted = next(unpacked.iterdir())
    assert {path.name for path in (extracted / "build_scripts").iterdir()} == {
        "__init__.py",
        "build_backend.py",
        "prepare_package.py",
        "stamp_compatibility.py",
    }
    subprocess.run(
        [sys.executable, "-c", "from build_scripts.build_backend import build_wheel; build_wheel('dist')"],
        cwd=extracted,
        env=build_env,
        check=True,
        capture_output=True,
    )
    wheel = next((extracted / "dist").glob("*.whl"))
    installed = source / "installed"
    with zipfile.ZipFile(wheel) as bundle:
        assert not any(name.startswith("build_scripts/") for name in bundle.namelist())
        bundle.extractall(installed)
    result = subprocess.run(
        [sys.executable, "-c", "from pyrit._compatibility import get_compatibility_id; print(get_compatibility_id())"],
        cwd=installed,
        env=build_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == stamp["compatibility_id"]
    assert stamp["compatibility_id"] in (installed / "pyrit/backend/frontend/app.js").read_text()
