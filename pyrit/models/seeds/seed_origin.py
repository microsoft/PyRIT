# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import StrEnum


class SeedOrigin(StrEnum):
    """How a seed entered PyRIT, independent of upstream authorship."""

    LOCAL = "local"
    REMOTE = "remote"
    GENERATED = "generated"
    USER = "user"
    UNKNOWN = "unknown"
