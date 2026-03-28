# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

__name__ = "pyrit"
# Remove dev suffix when releasing and keep in sync with pyproject.toml
# NOTE: __version__ must be set before imports below to avoid circular import issues.
# Submodules (e.g., component_identifier, memory_models) reference pyrit.__version__
# and get imported transitively during the .common import chain.
__version__ = "0.12.1.dev0"

from .common import turn_off_transformers_warning  # noqa: F401
from .show_versions import show_versions  # noqa: F401
