# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetCapabilities:
    """
    Describes the capabilities of a PromptTarget so that attacks
    and other components can adapt their behavior accordingly.

    Each target class defines default capabilities via the _DEFAULT_CAPABILITIES
    class attribute. Users can override individual capabilities per instance
    through constructor parameters, which is useful for targets whose
    capabilities depend on deployment configuration (e.g., Playwright, HTTP).
    """

    # Whether the target natively supports multi-turn conversations
    # (i.e., it accepts and uses conversation history or maintains state
    # across turns via external mechanisms like WebSocket connections).
    supports_multi_turn: bool = False
