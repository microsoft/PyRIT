# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Import smoke tests for azure-ai-evaluation red team module integration.

These tests verify that the azure-ai-evaluation red team module can be imported
and that its PyRIT subclasses correctly extend PyRIT base classes.

Tests are SKIPPED if azure-ai-evaluation[redteam] is not installed.
"""

import pytest

from pyrit.prompt_target import PromptTarget
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


def _azure_ai_evaluation_available() -> bool:
    """Check if azure-ai-evaluation[redteam] is installed."""
    try:
        from azure.ai.evaluation.red_team import RedTeam  # noqa: F401

        return True
    except ImportError:
        return False


requires_azure_ai_evaluation = pytest.mark.skipif(
    not _azure_ai_evaluation_available(),
    reason="azure-ai-evaluation[redteam] is not installed",
)


@requires_azure_ai_evaluation
class TestRedTeamModuleImports:
    """Verify azure-ai-evaluation red_team module imports succeed with current PyRIT."""

    def test_redteam_public_api_imports(self):
        """Verify all public classes from azure.ai.evaluation.red_team are importable."""
        from azure.ai.evaluation.red_team import (
            AttackStrategy,
            RedTeam,
            RedTeamResult,
            RiskCategory,
            SupportedLanguages,
        )

        assert RedTeam is not None
        assert AttackStrategy is not None
        assert RiskCategory is not None
        assert RedTeamResult is not None
        assert SupportedLanguages is not None


@requires_azure_ai_evaluation
class TestCallbackChatTargetInheritance:
    """Verify _CallbackChatTarget correctly extends PromptTarget.

    NOTE: These tests intentionally import private (_-prefixed) modules from
    azure-ai-evaluation. This is correct for contract testing — we need to verify
    the actual subclass relationships that PyRIT API changes could break.
    Explicit inheritance checks are needed because azure-ai-evaluation subclasses
    are detected via issubclass() checks in PyRIT orchestrators and scenarios.
    """

    def test_callback_chat_target_extends_prompt_target(self):
        """_CallbackChatTarget must be a subclass of pyrit.prompt_target.PromptTarget."""
        from azure.ai.evaluation.red_team._callback_chat_target import _CallbackChatTarget

        assert issubclass(_CallbackChatTarget, PromptTarget)


@requires_azure_ai_evaluation
class TestRAIScorerInheritance:
    """Verify RAIServiceScorer correctly extends TrueFalseScorer."""

    def test_rai_scorer_extends_true_false_scorer(self):
        """RAIServiceScorer must be a subclass of pyrit.score.true_false.TrueFalseScorer."""
        from azure.ai.evaluation.red_team._foundry._rai_scorer import RAIServiceScorer  # private: intentional

        assert issubclass(RAIServiceScorer, TrueFalseScorer)
