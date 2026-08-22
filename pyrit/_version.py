# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""PyRIT package version."""

# Keep the version in this dependency-free module so transitive imports can
# resolve ``pyrit.__version__`` without creating an import cycle.
# Remove the development suffix when releasing and keep this value in sync with pyproject.toml.
__version__ = "1.1.0.dev0"
