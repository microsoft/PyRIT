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


class TestPromptChatTargetTransitionalCompat:
    """Verify PromptChatTarget still exists and extends PromptTarget.

    The SDK currently imports PromptChatTarget in 6+ production files
    (_callback_chat_target.py, _orchestrator_manager.py, _scenario_orchestrator.py,
    _execution_manager.py, strategy_utils.py, _rai_service_target.py). PyRIT is
    migrating from PromptChatTarget to PromptTarget, but during the transition
    both must exist with correct inheritance.
    """

    def test_prompt_chat_target_exists(self):
        """PromptChatTarget must remain importable during the transition."""
        from pyrit.prompt_target import PromptChatTarget

        assert PromptChatTarget is not None

    def test_prompt_chat_target_extends_prompt_target(self):
        """PromptChatTarget must be a subclass of PromptTarget."""
        from pyrit.prompt_target import PromptChatTarget

        assert issubclass(PromptChatTarget, PromptTarget)


@requires_azure_ai_evaluation
class TestCallbackChatTargetInheritance:
    """Verify _CallbackChatTarget correctly extends PromptTarget.

    NOTE: These tests intentionally import private (_-prefixed) modules from
    azure-ai-evaluation. This is correct for contract testing — we need to verify
    the actual subclass relationships that PyRIT API changes could break.

    Explicit inheritance checks are REQUIRED here because:
    1. PyRIT orchestrators and scenarios detect subclasses via issubclass() at
       runtime to determine capabilities (multi-turn, system prompt support, etc.)
    2. If the inheritance chain breaks, attacks silently fall back to single-turn
       mode or skip system prompt injection — causing false negatives.
    3. These checks catch breaking changes that import-only tests would miss.
    """

    def test_callback_chat_target_extends_prompt_target(self):
        """_CallbackChatTarget must be a subclass of pyrit.prompt_target.PromptTarget."""
        from azure.ai.evaluation.red_team._callback_chat_target import _CallbackChatTarget

        assert issubclass(_CallbackChatTarget, PromptTarget)


@requires_azure_ai_evaluation
class TestRAIScorerInheritance:
    """Verify RAIServiceScorer correctly extends TrueFalseScorer.

    Explicit inheritance check — see TestCallbackChatTargetInheritance docstring
    for why issubclass() contract tests are necessary.
    """

    def test_rai_scorer_extends_true_false_scorer(self):
        """RAIServiceScorer must be a subclass of pyrit.score.true_false.TrueFalseScorer."""
        from azure.ai.evaluation.red_team._foundry._rai_scorer import RAIServiceScorer  # private: intentional

        assert issubclass(RAIServiceScorer, TrueFalseScorer)
