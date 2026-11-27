# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""API rules for saved registry entries that the registry library leaves to the API."""


class SavedEntryRemovalError(RuntimeError):
    """A saved entry can't be removed through the API yet, since it comes back from its saved file at every start."""
