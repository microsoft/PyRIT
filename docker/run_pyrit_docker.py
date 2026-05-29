# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Run PyRIT Docker container in Jupyter or GUI mode.

Usage:
    python run_pyrit_docker.py jupyter
    python run_pyrit_docker.py gui
    python run_pyrit_docker.py gui --tag abc1234def5678
"""

import argparse
import subprocess
import sys
from pathlib import Path


def check_image_exists(tag):
    """Check if the Docker image exists."""
    result = subprocess.run(["docker", "images", "-q", f"pyrit:{tag}"], capture_output=True, text=True)
    return len(result.stdout.strip()) > 0


def run_container(mode, tag="latest"):
    """Run the PyRIT container in the specified mode."""
    root_dir = Path(__file__).parent.parent
    pyrit_config_dir = Path.home() / ".pyrit"
    env_file = pyrit_config_dir / ".env"
    env_local_file = pyrit_config_dir / ".env.local"
    pyrit_conf_file = pyrit_config_dir / ".pyrit_conf"

    print("🐳 PyRIT Docker Runner")
    print("=" * 60)

    # Check for .env file
    if not env_file.exists():
        print("❌ ERROR: .env file not found!")
        print(f"   Expected location: {env_file}")
        print("   Please create a .env file with your API keys.")
        print("   See: https://github.com/microsoft/PyRIT/blob/main/doc/setup/setup.md")
        sys.exit(1)

    # Check if image exists
    if not check_image_exists(tag):
        print(f"❌ ERROR: Docker image 'pyrit:{tag}' not found!")
        print()
        print("Please build the image first:")
        print("   python docker/build_pyrit_docker.py --source local")
        print("   python docker/build_pyrit_docker.py --source pypi --version X.Y.Z")
        sys.exit(1)

    # Determine port based on mode
    if mode == "jupyter":
        port = "8888"
        url = "http://localhost:8888"
        container_name = "pyrit-jupyter"
    elif mode == "gui":
        port = "8000"
        url = "http://localhost:8000"
        container_name = "pyrit-gui"
    else:
        print(f"ERROR: Invalid mode '{mode}'. Must be 'jupyter' or 'gui'")
        sys.exit(1)

    print(f"🚀 Starting PyRIT in {mode.upper()} mode")
    print(f"   Image: pyrit:{tag}")
    print(f"   Port: {port}")
    print()

    # Build docker run command
    # Mount env files to ~/.pyrit/ where PyRIT expects them
    # Use -it for interactive mode (needed for az login device code prompt)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-it",
        "--name",
        container_name,
        "-p",
        f"127.0.0.1:{port}:{port}",
        "-e",
        f"PYRIT_MODE={mode}",
        "-v",
        f"{env_file}:/home/vscode/.pyrit/.env:ro",
    ]

    # Add .env.local if it exists
    if env_local_file.exists():
        print(f"   Found .env.local - including it")
        cmd.extend(["-v", f"{env_local_file}:/home/vscode/.pyrit/.env.local:ro"])

    # Add .pyrit_conf if it exists (sets operator, operation, initializers)
    if pyrit_conf_file.exists():
        print(f"   Found .pyrit_conf - including it")
        cmd.extend(["-v", f"{pyrit_conf_file}:/home/vscode/.pyrit/.pyrit_conf:ro"])

    # Mount Azure CLI config so 'az login' tokens persist across container restarts.
    # ~/.azure is created by the Azure CLI when you run 'az login' on the host.
    # Mounting it lets the container reuse existing auth tokens without re-login.
    azure_dir = Path.home() / ".azure"
    if azure_dir.exists():
        print(f"   Found .azure/ - mounting for Azure CLI auth")
        cmd.extend(["-v", f"{azure_dir}:/home/vscode/.azure"])

    cmd.append(f"pyrit:{tag}")

    print()
    print("=" * 60)
    print("🌐 Open in your browser:")
    print()
    print(f"   {url}")
    print()
    print("=" * 60)
    print()
    print("Press Ctrl+C to stop")
    print()

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping PyRIT...")
        print("✅ Stopped")


def main():
    parser = argparse.ArgumentParser(
        description="Run PyRIT Docker container",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run in Jupyter mode
  python docker/run_pyrit_docker.py jupyter

  # Run in GUI mode
  python docker/run_pyrit_docker.py gui

  # Run with specific image tag
  python docker/run_pyrit_docker.py gui --tag abc1234def5678
        """,
    )

    parser.add_argument("mode", choices=["jupyter", "gui"], help="Mode to run: 'jupyter' or 'gui'")

    parser.add_argument("--tag", default="latest", help="Docker image tag to use (default: latest)")

    args = parser.parse_args()

    run_container(args.mode, args.tag)


if __name__ == "__main__":
    main()
