# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Validate the private artifact and start the team image with a disposable copy."""

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


def validate_artifact(*, directory: Path, commit: str) -> None:
    """Allow only the contracted regular files into the private Docker context."""
    config = directory / ".pyrit_conf"
    marker = directory / "source-commit.txt"
    scripts = directory / "internal_initializers"
    if (
        directory.is_symlink()
        or set(directory.iterdir()) != {config, marker, scripts}
        or not all(path.is_file() and not path.is_symlink() for path in (config, marker))
        or not scripts.is_dir()
        or scripts.is_symlink()
    ):
        raise ValueError("Unexpected deployment-profile artifact layout.")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or marker.read_bytes() not in {
        commit.encode() + ending for ending in (b"", b"\n", b"\r\n")
    }:
        raise ValueError("Artifact source-commit.txt does not match the private CI sourceVersion.")
    entries = list(scripts.iterdir())
    if not entries or not all(
        path.is_file() and not path.is_symlink() and re.fullmatch(r"[a-z][a-z0-9_]*\.py", path.name) for path in entries
    ):
        raise ValueError("Expected one or more flat, snake_case Python initializer files.")


def working_config(baseline: Path) -> Path:
    """Copy the pristine files into a fresh private directory and bind initializer edits to it."""
    runtime = Path(tempfile.mkdtemp(prefix="copyrit-"))
    scripts = runtime / "internal_initializers"
    scripts.mkdir()
    for source in (baseline / scripts.name).iterdir():
        shutil.copyfile(source, scripts / source.name)
    config = runtime / ".pyrit_conf"
    shutil.copyfile(baseline / config.name, config)
    with config.open("ab") as stream:
        stream.write(f"\ncustom_initializers_source: {json.dumps(str(scripts))}\n".encode())
    return config


def validate_profile(baseline: Path) -> None:
    """Exercise the rendered config and real initializer discovery in the final image."""
    import yaml

    from pyrit.registry import InitializerRegistry
    from pyrit.setup.configuration_loader import ConfigurationLoader

    settings = yaml.safe_load((baseline / ".pyrit_conf").read_bytes())
    if not isinstance(settings, dict):
        raise ValueError("Expected a deployment profile configuration mapping.")
    for key in ("memory_db_type", "env_akv_ref", "custom_initializers_source"):
        if key in settings:
            raise ValueError(f"Omit {key}; it is supplied by the deployment.")
    config = working_config(baseline)
    configuration = ConfigurationLoader.from_yaml_file(config)
    if not configuration.allow_custom_initializers or configuration.custom_initializers_source != str(
        config.parent / "internal_initializers"
    ):
        raise ValueError("Profile must enable custom initializers from the local working copy.")
    registry = InitializerRegistry()
    builtins = set(registry.get_class_names())
    registry.configure_custom_scripts_source(configuration.custom_initializers_source)
    registry.register_stored_initializers(strict=True)
    expected = {script.stem for script in (baseline / "internal_initializers").iterdir()}
    if set(registry.get_class_names()) - builtins != expected:
        raise ValueError("Not every bundled initializer was registered.")
    configuration.resolve_initializers(registry=registry)


if __name__ == "__main__":
    baseline = Path(__file__).with_name("baseline")
    if sys.argv[1] == "check-artifact":
        validate_artifact(directory=Path(sys.argv[2]), commit=sys.argv[3])
    elif sys.argv[1] == "check-image":
        validate_profile(baseline)
    elif sys.argv[1] == "start":
        os.environ["PYRIT_CONFIG_FILE"] = str(working_config(baseline))
        os.execv("/app/start.sh", ["/app/start.sh", *sys.argv[2:]])
    else:
        raise SystemExit("Unknown deployment profile operation.")
