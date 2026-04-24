# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from enum import Enum


class MetricsType(Enum):
    """
    Enum representing the type of metrics when evaluating scorers on human-labeled datasets.
    """

    HARM = "harm"
    OBJECTIVE = "objective"


class RegistryUpdateBehavior(Enum):
    """
    Enum representing how the evaluation registry should be updated.

    Members:
        SKIP_IF_EXISTS: Only run evaluation and update registry if no matching entry exists.
            This is the default production behavior for efficiency.
        ALWAYS_UPDATE: Always run evaluation and overwrite any existing registry entry.
            Use when you want to force re-evaluation with updated scorer configuration.
        NEVER_UPDATE: Always run evaluation but never write to the registry.
            Use for debugging and testing without affecting stored results.
    """

    SKIP_IF_EXISTS = "skip_if_exists"
    ALWAYS_UPDATE = "always_update"
    NEVER_UPDATE = "never_update"
