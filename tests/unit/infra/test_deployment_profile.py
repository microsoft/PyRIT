# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exercise deployment profile preparation without contacting Azure."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from build_scripts.prepare_deployment_profile import prepare_profile, validate_bundle
from build_scripts.publish_deployment_profile import publish_profile

pytestmark = pytest.mark.usefixtures("patch_central_database")

INTERNAL_COMMIT = "a" * 40
IMAGE = f"copyritacr.azurecr.io/pyrit@sha256:{'b' * 64}"
CONTAINER = "https://profiles.blob.core.windows.net/runtime"
CONFIG = (
    "# Source: PyRIT-internal / pyrit_internal/deployment/main/.pyrit_conf\n"
    "initializers:\n"
    "  - target\n"
    "  - technique\n"
    "  - deployment_smoke_test\n"
    "allow_custom_initializers: true\n"
)
SCRIPT = (
    "from pyrit.setup.initializers import PyRITInitializer\n\n"
    "class DeploymentSmokeTestInitializer(PyRITInitializer):\n"
    "    async def initialize_async(self) -> None:\n"
    "        pass\n"
)


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    directory = tmp_path / "main"
    scripts = directory / "internal_initializers"
    scripts.mkdir(parents=True)
    (directory / ".pyrit_conf").write_text(CONFIG, encoding="utf-8")
    (scripts / "deployment_smoke_test.py").write_text(SCRIPT, encoding="utf-8")
    return directory


def _prepare(*, profile: Path, output: Path, commit: str = INTERNAL_COMMIT, image: str = IMAGE) -> Path:
    return prepare_profile(
        profile_directory=profile,
        output_directory=output,
        internal_commit=commit,
        container_image=image,
    )


def test_profile_preserves_portable_source_bytes_and_records_provenance(profile: Path, tmp_path: Path) -> None:
    output = tmp_path / "published"
    config_bytes = CONFIG.replace("\n", "\r\n").encode()
    (profile / ".pyrit_conf").write_bytes(config_bytes)
    generated = _prepare(profile=profile, output=output)
    assert generated.read_bytes() == config_bytes
    assert "custom_initializers_source" not in yaml.safe_load(generated.read_text(encoding="utf-8"))
    assert (output / "internal_initializers" / "deployment_smoke_test.py").read_text(encoding="utf-8") == SCRIPT
    manifest = json.loads((output / "deployment-profile.json").read_text(encoding="utf-8"))
    assert manifest["internal_commit"] == INTERNAL_COMMIT
    assert manifest["profile"] == "main"
    assert manifest["container_image"] == IMAGE
    assert manifest["files"] == {
        ".pyrit_conf": hashlib.sha256(generated.read_bytes()).hexdigest(),
        "internal_initializers/deployment_smoke_test.py": hashlib.sha256(
            (profile / "internal_initializers" / "deployment_smoke_test.py").read_bytes()
        ).hexdigest(),
    }
    assert validate_bundle(bundle_directory=output, container_image=IMAGE) == manifest


def test_publish_seeds_then_indexes_and_reuses_without_writing(
    profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = tmp_path / "bundle"
    _prepare(profile=profile, output=bundle)
    operations: list[str] = []
    blobs: dict[str, bytes] = {}

    def run_az(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        def arg(flag: str) -> str:
            return args[args.index(flag) + 1]

        assert kwargs["check"] is True and arg("--auth-mode") == "login"
        operation = args[3]
        operations.append(operation)
        name = arg("--name") if "--name" in args else ""
        output = ""
        if operation == "exists":
            output = str(name in blobs).lower()
        elif operation == "upload-batch":
            assert arg("--overwrite") == "false"
            source = Path(arg("--source"))
            blobs.update(
                {
                    f"{arg('--destination-path')}/{p.relative_to(source).as_posix()}": p.read_bytes()
                    for p in source.rglob("*")
                    if p.is_file()
                }
            )
        elif operation == "upload":
            assert arg("--overwrite") == "false" and name not in blobs
            blobs[name] = Path(arg("--file")).read_bytes()
        elif operation == "download":
            Path(arg("--file")).write_bytes(blobs[name])
        elif operation == "show":
            assert name in blobs and arg("--query") == "properties.etag"
            output = "0x1234"
        else:
            pytest.fail(f"Unexpected storage operation: {operation}")
        return subprocess.CompletedProcess(args, 0, stdout=output)

    with patch("build_scripts.publish_deployment_profile.subprocess.run", side_effect=run_az):
        publish_profile(bundle=bundle, container_uri=CONTAINER, image=IMAGE, slot="test", build_id="42")
        assert operations == ["exists", "upload-batch", "upload", "show"]
        index_name = next(name for name in blobs if name.startswith("profiles/main/"))
        config_uri = json.loads(blobs[index_name])["config_file_uri"]
        config = config_uri.removeprefix(CONTAINER + "/")
        assert blobs[config].startswith((bundle / ".pyrit_conf").read_bytes())
        assert (
            yaml.safe_load(blobs[config])["custom_initializers_source"]
            == config_uri.rsplit("/", 1)[0] + "/internal_initializers"
        )
        assert capsys.readouterr().out.splitlines()[-1].endswith(config_uri)
        blobs[config] = b"# GUI edit"
        operations.clear()
        publish_profile(bundle=bundle, container_uri=CONTAINER, image=IMAGE, slot="test", build_id="43")
        assert operations == ["exists", "download", "show"] and blobs[config] == b"# GUI edit"
        assert capsys.readouterr().out.splitlines()[-1].endswith(config_uri)


@pytest.mark.parametrize(
    "failure", ["exists", "upload-batch", "upload", "download", "cross-container", "show", "bad-etag"]
)
def test_failed_publication_never_exports_a_config_uri(
    profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    bundle = tmp_path / "bundle"
    _prepare(profile=profile, output=bundle)
    digest = validate_bundle(bundle_directory=bundle)["profile_digest"]
    reuse = failure in {"download", "cross-container", "show", "bad-etag"}
    operations: list[str] = []

    def run_az(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        operation = args[3]
        operations.append(operation)
        if operation == failure:
            raise subprocess.CalledProcessError(1, args)
        if operation == "download":
            container = CONTAINER.replace("profiles.", "other.") if failure == "cross-container" else CONTAINER
            index = {
                "schema_version": 1,
                "profile": "main",
                "profile_digest": digest,
                "config_file_uri": f"{container}/releases/1/{'d' * 32}/main/.pyrit_conf",
            }
            Path(args[args.index("--file") + 1]).write_text(json.dumps(index), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout=str(reuse).lower() if operation == "exists" else "")

    with (
        patch("build_scripts.publish_deployment_profile.subprocess.run", side_effect=run_az),
        pytest.raises((subprocess.CalledProcessError, ValueError, RuntimeError)),
    ):
        publish_profile(bundle=bundle, container_uri=CONTAINER, image=IMAGE, slot="test", build_id="42")
    assert "profileConfigFileUri" not in capsys.readouterr().out
    if reuse:
        assert "upload" not in operations and "upload-batch" not in operations
    if failure == "upload-batch":
        assert "upload" not in operations


def test_digest_ignores_internal_commit_image_and_unrelated_files(profile: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _prepare(profile=profile, output=first)
    (profile / "README.md").write_text("Unrelated profile documentation", encoding="utf-8")
    _prepare(
        profile=profile,
        output=second,
        commit="d" * 40,
        image=f"copyritacr.azurecr.io/pyrit@sha256:{'e' * 64}",
    )
    assert (
        validate_bundle(bundle_directory=first)["profile_digest"]
        == validate_bundle(bundle_directory=second)["profile_digest"]
    )


@pytest.mark.parametrize("file", [".pyrit_conf", "internal_initializers/deployment_smoke_test.py"])
def test_digest_changes_when_checked_in_profile_content_changes(profile: Path, tmp_path: Path, file: str) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _prepare(profile=profile, output=first)
    path = profile / file
    path.write_text(path.read_text(encoding="utf-8") + "\n# Changed source\n", encoding="utf-8")
    _prepare(profile=profile, output=second)
    assert (
        validate_bundle(bundle_directory=first)["profile_digest"]
        != validate_bundle(bundle_directory=second)["profile_digest"]
    )


def test_bundle_rejects_tampering_and_wrong_application_image(profile: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _prepare(profile=profile, output=bundle)
    with pytest.raises(ValueError, match="selected container image"):
        validate_bundle(bundle_directory=bundle, container_image=IMAGE.replace("b", "c"))
    (bundle / ".pyrit_conf").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline digest"):
        validate_bundle(bundle_directory=bundle)


def test_profile_rejects_a_moving_source_ref(profile: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="full lowercase Git commit"):
        _prepare(profile=profile, output=tmp_path / "published", commit="main")


@pytest.mark.parametrize(
    ("addition", "message"),
    [
        (
            "allow_custom_initializers: true\ncustom_initializers_source: ./elsewhere\n",
            "managed by the deployment pipeline",
        ),
        ("allow_custom_initializers: false\n", "enable allow_custom_initializers"),
    ],
)
def test_profile_rejects_incompatible_source_settings(
    profile: Path, tmp_path: Path, addition: str, message: str
) -> None:
    (profile / ".pyrit_conf").write_text(
        CONFIG.replace("allow_custom_initializers: true\n", "") + addition, encoding="utf-8"
    )
    with pytest.raises(ValueError, match=message):
        _prepare(profile=profile, output=tmp_path / "published")


@pytest.mark.parametrize("name", ["helpers", "invalid-name.py"])
def test_profile_rejects_files_the_runtime_cannot_load(profile: Path, tmp_path: Path, name: str) -> None:
    (profile / "internal_initializers" / name).write_text(SCRIPT, encoding="utf-8")
    with pytest.raises(ValueError, match="flat, snake_case"):
        _prepare(profile=profile, output=tmp_path / "published")


def test_profile_rejects_missing_initializer_dependencies_before_publication(profile: Path, tmp_path: Path) -> None:
    (profile / "internal_initializers" / "deployment_smoke_test.py").write_text(
        "import missing_private_deployment_dependency\n" + SCRIPT, encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Failed to load initializer script"):
        _prepare(profile=profile, output=tmp_path / "published")
    assert not (tmp_path / "published").exists()


def test_profile_does_not_replace_an_existing_release(profile: Path, tmp_path: Path) -> None:
    output = tmp_path / "published"
    _prepare(profile=profile, output=output)
    with pytest.raises(FileExistsError):
        _prepare(profile=profile, output=output)
