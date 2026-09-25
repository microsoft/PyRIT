# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""PEP 517 hooks enforcing coordinated wheel/sdist provenance."""

from collections.abc import Mapping

from setuptools import build_meta

from build_scripts.stamp_compatibility import ROOT, stamp_source, verify_distribution

get_requires_for_build_wheel = build_meta.get_requires_for_build_wheel
get_requires_for_build_sdist = build_meta.get_requires_for_build_sdist
get_requires_for_build_editable = build_meta.get_requires_for_build_editable
prepare_metadata_for_build_wheel = build_meta.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = build_meta.prepare_metadata_for_build_editable


def _prepare() -> None:
    if (ROOT / ".git").exists() or (ROOT / "frontend/package.json").is_file():
        from build_scripts.prepare_package import main

        if main() != 0:
            raise RuntimeError("Coordinated package preparation failed")
    else:
        verify_distribution()


def build_wheel(
    wheel_directory: str,
    config_settings: Mapping[str, str | list[str] | None] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a wheel only after validating its coordinated assets."""
    _prepare()
    return build_meta.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(
    sdist_directory: str,
    config_settings: Mapping[str, str | list[str] | None] | None = None,
) -> str:
    """Build an sdist containing prepared assets usable without Git or Node."""
    _prepare()
    return build_meta.build_sdist(sdist_directory, config_settings)


def build_editable(
    wheel_directory: str,
    config_settings: Mapping[str, str | list[str] | None] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Stamp developer installs without requiring a frontend build."""
    stamp_source(development=True)
    return build_meta.build_editable(wheel_directory, config_settings, metadata_directory)
