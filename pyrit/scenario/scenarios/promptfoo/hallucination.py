# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""promptfoo's hallucination red-team plugin."""

from __future__ import annotations

from pyrit.scenario.scenarios.promptfoo.promptfoo_scenario import PromptfooScenario


class Hallucination(PromptfooScenario):
    """
    Test whether an application fabricates information, using promptfoo's ``hallucination`` plugin.

    An adversarial chat writes realistic requests, tailored to the application's purpose, for
    information that is unlikely to be known or verifiable. The application's responses are graded
    with promptfoo's hallucination rubric: a response fails when it states fabricated names, dates,
    statistics, or other unsupported claims as fact instead of acknowledging what it cannot know.

    Reference: [@promptfoo2025redteam]
    """

    VERSION: int = 1

    @classmethod
    def get_plugin_ids(cls) -> tuple[str, ...]:
        """
        Return the promptfoo plugin this scenario runs.

        Returns:
            tuple[str, ...]: ``("hallucination",)``.
        """
        return ("hallucination",)
