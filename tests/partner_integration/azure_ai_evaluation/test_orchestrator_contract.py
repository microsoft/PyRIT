# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for PyRIT orchestrator imports used by azure-ai-evaluation.

The azure-ai-evaluation _orchestrator_manager.py imports orchestrators with a
try/except fallback pattern because orchestrators may be removed or restructured
across PyRIT versions. These tests validate both the import paths and the graceful
fallback behavior that the SDK depends on.

Imports tested:
- pyrit.orchestrator.single_turn.prompt_sending_orchestrator.PromptSendingOrchestrator
- pyrit.orchestrator.multi_turn.red_teaming_orchestrator.RedTeamingOrchestrator
- pyrit.orchestrator.multi_turn.crescendo_orchestrator.CrescendoOrchestrator
- pyrit.orchestrator.Orchestrator (base class)
"""


class TestOrchestratorImportPattern:
    """Validate that orchestrator imports follow the SDK's try/except pattern.

    The SDK wraps these imports in try/except ImportError, so the contract is:
    either the imports succeed OR they fail gracefully (ImportError). They must
    never raise a different exception type.
    """

    def test_prompt_sending_orchestrator_import_graceful(self):
        """PromptSendingOrchestrator import must succeed or raise ImportError."""
        try:
            from pyrit.orchestrator.single_turn.prompt_sending_orchestrator import (
                PromptSendingOrchestrator,
            )

            assert PromptSendingOrchestrator is not None
        except ImportError:
            pass  # Acceptable — SDK handles this gracefully

    def test_red_teaming_orchestrator_import_graceful(self):
        """RedTeamingOrchestrator import must succeed or raise ImportError."""
        try:
            from pyrit.orchestrator.multi_turn.red_teaming_orchestrator import (
                RedTeamingOrchestrator,
            )

            assert RedTeamingOrchestrator is not None
        except ImportError:
            pass  # Acceptable — SDK handles this gracefully

    def test_crescendo_orchestrator_import_graceful(self):
        """CrescendoOrchestrator import must succeed or raise ImportError."""
        try:
            from pyrit.orchestrator.multi_turn.crescendo_orchestrator import (
                CrescendoOrchestrator,
            )

            assert CrescendoOrchestrator is not None
        except ImportError:
            pass  # Acceptable — SDK handles this gracefully

    def test_orchestrator_base_import_graceful(self):
        """Orchestrator base class import must succeed or raise ImportError."""
        try:
            from pyrit.orchestrator import Orchestrator

            assert Orchestrator is not None
        except ImportError:
            pass  # Acceptable — SDK handles this gracefully
