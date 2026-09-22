# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""PyRIT package version."""

# Keep this module dependency-free to avoid circular imports. Submodules such
# as component identifiers and memory models reference ``pyrit.__version__``
# and can be imported transitively while the package is still initializing.
# Remove the development suffix when releasing and keep this value in sync with pyproject.toml.
__version__ = "1.2.0.dev0"
