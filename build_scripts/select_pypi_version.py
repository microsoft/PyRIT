# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Select a published PyRIT release without installing project dependencies."""

import argparse
import json
import re
from urllib.request import urlopen


def _validate_version(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?", value) is None
    ):
        raise ValueError("PyRIT version must be an exact release, such as X.Y.Z or X.Y.Zrc1")
    return value


def _validate_distributions(*, files: object, version: str) -> None:
    if not isinstance(files, list) or any(
        not isinstance(file, dict) or not isinstance(file.get("yanked"), bool) for file in files
    ):
        raise ValueError(f"Invalid distribution metadata for PyPI release {version}")
    if not any(
        file["yanked"] is False
        and isinstance(file.get("filename"), str)
        and (
            (
                file.get("packagetype") == "bdist_wheel"
                and file["filename"].startswith(f"pyrit-{version}-")
                and file["filename"].endswith(".whl")
            )
            or (
                file.get("packagetype") == "sdist"
                and file["filename"] in {f"pyrit-{version}.tar.gz", f"pyrit-{version}.zip"}
            )
        )
        and isinstance(file.get("url"), str)
        and file["url"].startswith("https://files.pythonhosted.org/")
        and file["url"].endswith(f"/{file['filename']}")
        for file in files
    ):
        raise ValueError(f"PyPI release {version} has no non-yanked published wheel or sdist")


def resolve_pypi_version(version: str = "") -> str:
    """Resolve PyPI's latest stable release, or validate an explicit published version.

    Args:
        version: Exact manual override. An empty string selects the latest stable release.

    Returns:
        str: The exact published version to install and test.

    Raises:
        OSError: If the PyPI lookup fails.
        ValueError: If the version, metadata, or published distributions are invalid.
    """
    if version:
        _validate_version(version)
    url = f"https://pypi.org/pypi/pyrit/{version}/json" if version else "https://pypi.org/pypi/pyrit/json"
    with urlopen(url) as response:
        metadata = json.load(response)
    info = metadata.get("info") if isinstance(metadata, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get("name"), str) or info["name"].lower() != "pyrit":
        raise ValueError("Invalid PyPI project metadata: expected PyRIT release information")
    selected = _validate_version(info.get("version"))
    if version and selected != version:
        raise ValueError(f"Requested PyPI release {version}, but PyPI returned {selected}")
    # The project endpoint orders non-yanked, stable releases using PyPI's version ordering.
    if not version and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.post[0-9]+)?", selected) is None:
        raise ValueError("PyPI did not return a stable, non-yanked PyRIT release")
    if info.get("yanked") is not False:
        raise ValueError(f"PyPI release {selected} is yanked or has invalid yank metadata")
    _validate_distributions(files=metadata.get("urls"), version=selected)
    return selected


def main() -> None:
    """Print only the validated version, or fail without supplying a fallback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", default="", help="Exact published override; defaults to PyPI's latest stable release"
    )
    args = parser.parse_args()
    try:
        version = resolve_pypi_version(args.version)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"::error::PyPI release selection failed: {exc}\n")
    print(version)


if __name__ == "__main__":
    main()
