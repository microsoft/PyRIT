# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Prepare and verify the portable internal deployment profile."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import TypedDict


class ProfileManifest(TypedDict):
    """Provenance and source hashes for a portable baseline."""

    profile: str
    profile_digest: str
    internal_commit: str
    container_image: str
    files: dict[str, str]


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path != directory / "deployment-profile.json"
    }


def _profile_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_provenance(*, internal_commit: str, container_image: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", internal_commit):
        raise ValueError("Internal source must be a full lowercase Git commit.")
    if not re.fullmatch(r"[a-z0-9.-]+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}", container_image):
        raise ValueError("Container image must be pinned by digest.")


def prepare_profile(
    *,
    profile_directory: Path,
    output_directory: Path,
    internal_commit: str,
    container_image: str,
) -> Path:
    """
    Validate and copy a profile without changing its checked-in configuration.

    Returns:
        Path: The portable configuration file, without environment-specific bindings.

    Raises:
        ValueError: If the profile or its publication inputs are invalid.
    """
    from pyrit.registry import InitializerRegistry
    from pyrit.setup.configuration_loader import ConfigurationLoader

    _validate_provenance(internal_commit=internal_commit, container_image=container_image)
    if profile_directory.name != "main":
        raise ValueError("Only the main deployment profile is supported.")

    config_path = profile_directory / ".pyrit_conf"
    scripts_directory = profile_directory / "internal_initializers"
    if profile_directory.is_symlink() or config_path.is_symlink() or scripts_directory.is_symlink():
        raise ValueError("Deployment profile files must not be symbolic links.")
    configuration = ConfigurationLoader.from_yaml_file(config_path)
    if not configuration.allow_custom_initializers:
        raise ValueError("Deployment profiles must enable allow_custom_initializers.")
    if configuration.custom_initializers_source is not None:
        raise ValueError("custom_initializers_source is managed by the deployment pipeline; omit it from the profile.")
    scripts = sorted(scripts_directory.iterdir())
    if not scripts:
        raise ValueError("Deployment profiles must contain at least one initializer.")
    for script in scripts:
        if (
            script.is_symlink()
            or not script.is_file()
            or script.suffix != ".py"
            or not re.fullmatch(r"[a-z][a-z0-9_]*", script.stem)
        ):
            raise ValueError("internal_initializers must contain only flat, snake_case Python initializer files.")

    registry = InitializerRegistry()
    registry.configure_custom_scripts_source(str(scripts_directory))
    registry.register_stored_initializers(strict=True)
    configuration.resolve_initializers(registry=registry)

    output_directory.mkdir(parents=True, exist_ok=False)
    output_scripts = output_directory / "internal_initializers"
    output_scripts.mkdir()
    for script in scripts:
        shutil.copyfile(script, output_scripts / script.name)
    generated_config = output_directory / ".pyrit_conf"
    shutil.copyfile(config_path, generated_config)
    files = _file_hashes(output_directory)
    manifest: ProfileManifest = {
        "internal_commit": internal_commit,
        "profile": profile_directory.name,
        "profile_digest": _profile_digest(files),
        "container_image": container_image,
        "files": files,
    }
    (output_directory / "deployment-profile.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return generated_config


def validate_bundle(*, bundle_directory: Path, container_image: str | None = None) -> ProfileManifest:
    """
    Verify portable baseline contents using only the standard library.

    Returns:
        ProfileManifest: The verified baseline manifest.

    Raises:
        ValueError: If bundle metadata, file hashes, or image provenance do not match.
    """
    manifest = json.loads((bundle_directory / "deployment-profile.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("profile") != "main":
        raise ValueError("Invalid deployment profile manifest.")
    for key in ("internal_commit", "container_image", "profile_digest"):
        if not isinstance(manifest.get(key), str):
            raise ValueError(f"Invalid deployment profile manifest field: {key}")
    _validate_provenance(internal_commit=manifest["internal_commit"], container_image=manifest["container_image"])
    if container_image is not None and manifest["container_image"] != container_image:
        raise ValueError("Profile bundle was not validated with the selected container image.")
    files = manifest.get("files")
    if not isinstance(files, dict) or ".pyrit_conf" not in files or len(files) < 2:
        raise ValueError("Profile bundle must contain configuration and initializer hashes.")
    for name, digest in files.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"\.pyrit_conf|internal_initializers/[a-z][a-z0-9_]*\.py", name)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("Invalid profile bundle file hash.")
    if any(path.is_symlink() for path in bundle_directory.rglob("*")):
        raise ValueError("Profile bundle files must not be symbolic links.")
    if files != _file_hashes(bundle_directory) or manifest["profile_digest"] != _profile_digest(files):
        raise ValueError("Profile bundle contents do not match the baseline digest.")
    return ProfileManifest(
        profile="main",
        profile_digest=manifest["profile_digest"],
        internal_commit=manifest["internal_commit"],
        container_image=manifest["container_image"],
        files=files,
    )


def main() -> None:
    """Run initializer discovery in the built image and write the baseline artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--internal-commit", required=True)
    parser.add_argument("--container-image", required=True)
    args = parser.parse_args()
    prepare_profile(**vars(args))


if __name__ == "__main__":
    main()
