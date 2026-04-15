# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Build PyRIT Docker image with support for both PyPI and local source.

This script first builds the devcontainer base image, then builds the
production image on top of it to avoid duplication.

Usage:
    python build_pyrit_docker.py --source pypi --version 0.10.0
    python build_pyrit_docker.py --source local
"""

import argparse
import subprocess
import sys
from pathlib import Path

DEVCONTAINER_IMAGE = "pyrit-devcontainer"


def get_git_info():
    """Get current git commit hash and check for uncommitted changes."""
    try:
        # Get commit hash
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        commit = result.stdout.strip()

        # Check for uncommitted changes
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True)
        modified = len(result.stdout.strip()) > 0

        return commit, modified
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to get git info: {e}")
        sys.exit(1)


def build_devcontainer(root_dir: Path, force_rebuild: bool = False) -> bool:
    """Build the devcontainer base image if needed."""
    print("🔧 Building devcontainer base image...")
    print(f"   Tag: {DEVCONTAINER_IMAGE}")
    print()

    # Check if image already exists (skip if not forcing rebuild)
    if not force_rebuild:
        result = subprocess.run(["docker", "images", "-q", DEVCONTAINER_IMAGE], capture_output=True, text=True)
        if result.stdout.strip():
            print(f"   ✓ Using existing {DEVCONTAINER_IMAGE} image")
            print("   (use --rebuild-base to force rebuild)")
            print()
            return True

    cmd = [
        "docker",
        "build",
        "-f",
        str(root_dir / ".devcontainer" / "Dockerfile"),
        "-t",
        DEVCONTAINER_IMAGE,
        str(root_dir / ".devcontainer"),
    ]

    print(f"Running: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd)

    if result.returncode != 0:
        print()
        print("❌ Failed to build devcontainer base image")
        return False

    print()
    print(f"   ✓ Devcontainer base image built: {DEVCONTAINER_IMAGE}")
    print()
    return True


def build_image(source, version=None, rebuild_base=False):
    """Build the Docker image with appropriate tags."""
    root_dir = Path(__file__).parent.parent

    print("🐳 PyRIT Docker Image Builder")
    print("=" * 60)

    # First, build the devcontainer base image
    if not build_devcontainer(root_dir, force_rebuild=rebuild_base):
        sys.exit(1)

    # Prepare build arguments
    build_args = {"PYRIT_SOURCE": source, "BASE_IMAGE": DEVCONTAINER_IMAGE}

    # Determine version and tag
    if source == "pypi":
        if not version:
            print("ERROR: --version is required when --source is pypi")
            sys.exit(1)
        build_args["PYRIT_VERSION"] = version
        image_tag = version
        print(f"📦 Building from PyPI version: {version}")
        print()
        print("⚠️  IMPORTANT WARNINGS:")
        print("   1. GUI mode may not work if this PyPI version doesn't")
        print("      include the frontend. Jupyter mode will work.")
        print("   2. Ensure your local branch matches the release version:")
        print(f"      git checkout releases/v{version}")
        print("      This ensures notebooks/docs match the PyRIT version.")
        print()

    elif source == "local":
        commit, modified = get_git_info()
        build_args["GIT_COMMIT"] = commit
        build_args["GIT_MODIFIED"] = "true" if modified else "false"

        # Create tag from commit hash
        image_tag = f"{commit}"
        if modified:
            image_tag += "-modified"

        print(f"📦 Building from local source")
        print(f"   Commit: {commit}")
        print(f"   Modified: {modified}")
        print()
    else:
        print(f"ERROR: Invalid source '{source}'. Must be 'pypi' or 'local'")
        sys.exit(1)

    # Build the Docker image
    print("🔨 Building Docker image...")
    print(f"   Tag: pyrit:{image_tag}")
    print(f"   Also tagging as: pyrit:latest")
    print()

    cmd = [
        "docker",
        "build",
        "-f",
        str(root_dir / "docker" / "Dockerfile"),
        "-t",
        f"pyrit:{image_tag}",
        "-t",
        "pyrit:latest",
    ]

    # Add build args
    for key, value in build_args.items():
        cmd.extend(["--build-arg", f"{key}={value}"])

    cmd.append(str(root_dir))

    print(f"Running: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd)

    if result.returncode != 0:
        print()
        print("❌ Failed to build Docker image")
        sys.exit(1)

    print()
    print("=" * 60)
    print("✅ Docker image built successfully!")
    print("=" * 60)
    print()
    print(f"   pyrit:{image_tag}")
    print(f"   pyrit:latest")
    print()
    print("Next steps:")
    print(f"   python docker/run_pyrit_docker.py jupyter")
    print(f"   python docker/run_pyrit_docker.py gui")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Build PyRIT Docker image",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build from PyPI version 0.10.0
  python docker/build_pyrit_docker.py --source pypi --version 0.10.0

  # Build from local source
  python docker/build_pyrit_docker.py --source local
        """,
    )

    parser.add_argument(
        "--source", required=True, choices=["pypi", "local"], help="Source to build from: 'pypi' or 'local'"
    )

    parser.add_argument("--version", help="PyRIT version to install (required when source=pypi)")

    parser.add_argument("--rebuild-base", action="store_true", help="Force rebuild of the devcontainer base image")

    args = parser.parse_args()

    build_image(args.source, args.version, args.rebuild_base)


if __name__ == "__main__":
    main()
