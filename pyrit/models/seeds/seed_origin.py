# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum


class SeedOrigin(str, Enum):
    """How a seed entered PyRIT, independent of upstream authorship."""

    LOCAL = "local"
    REMOTE = "remote"
    GENERATED = "generated"
    USER = "user"
    UNKNOWN = "unknown"
