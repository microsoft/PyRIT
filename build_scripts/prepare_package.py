# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Script to prepare the PyRIT package for distribution.
This builds the TypeScript/React frontend and copies artifacts into the Python package structure.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def build_frontend(frontend_dir: Path) -> bool:
    """
    Build the TypeScript/React frontend using npm.

    Args:
        frontend_dir: Path to the frontend directory

    Returns:
        True if successful, False otherwise
    """
    print("=" * 60)
    print("Building TypeScript/React frontend...")
    print("=" * 60)

    # Check if npm is available
    try:
        result = subprocess.run(["npm", "--version"], capture_output=True, text=True, check=True)
        print(f"Found npm version: {result.stdout.strip()}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: npm is not installed or not in PATH")
        print("Please install Node.js 24.x and npm from https://nodejs.org/")
        return False

    # Check if package.json exists
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        print(f"ERROR: package.json not found at {package_json}")
        return False

    # Install dependencies
    print("\nInstalling frontend dependencies...")
    try:
        subprocess.run(
            ["npm", "install"],
            cwd=frontend_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print("✓ Dependencies installed")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to install dependencies:\n{e.stdout}")
        return False

    # Build the frontend
    print("\nBuilding frontend for production...")
    try:
        subprocess.run(
            ["npm", "run", "build"],
            cwd=frontend_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print("✓ Frontend built successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to build frontend:\n{e.stdout}")
        return False


def copy_frontend_to_package(frontend_dist: Path, backend_frontend: Path) -> bool:
    """
    Copy frontend dist to pyrit/backend/frontend for packaging.

    Args:
        frontend_dist: Path to frontend/dist
        backend_frontend: Path to pyrit/backend/frontend

    Returns:
        True if successful, False otherwise
    """
    print("\n" + "=" * 60)
    print("Copying frontend to Python package...")
    print("=" * 60)

    # Check if frontend dist exists
    if not frontend_dist.exists():
        print(f"ERROR: Frontend dist directory not found at {frontend_dist}")
        return False

    # Remove existing backend/frontend if it exists
    if backend_frontend.exists():
        print(f"Removing existing {backend_frontend}")
        shutil.rmtree(backend_frontend)

    # Copy frontend dist to backend/frontend
    print(f"Copying {frontend_dist} to {backend_frontend}")
    shutil.copytree(frontend_dist, backend_frontend)

    # Verify files were copied
    index_html = backend_frontend / "index.html"
    if index_html.exists():
        print("✓ Frontend successfully copied to package")
        return True
    print("ERROR: index.html not found after copy")
    return False


def main():
    """Build frontend and prepare package for distribution."""
    # Define paths
    root = Path(__file__).parent.parent
    frontend_dir = root / "frontend"
    frontend_dist = frontend_dir / "dist"
    backend_frontend = root / "pyrit" / "backend" / "frontend"

    print("PyRIT Package Preparation")
    print("=" * 60)
    print(f"Root directory: {root}")
    print(f"Frontend directory: {frontend_dir}")
    print(f"Target directory: {backend_frontend}")
    print()

    # Check if frontend directory exists
    if not frontend_dir.exists():
        print(f"ERROR: Frontend directory not found at {frontend_dir}")
        return 1

    # Build the frontend
    if not build_frontend(frontend_dir):
        print("\n❌ Failed to build frontend")
        return 1

    # Copy to package
    if not copy_frontend_to_package(frontend_dist, backend_frontend):
        print("\n❌ Failed to copy frontend to package")
        return 1

    print("\n" + "=" * 60)
    print("✅ Package preparation complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Build the Python package: python -m build")
    print("  2. Upload to PyPI: python -m twine upload dist/*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
