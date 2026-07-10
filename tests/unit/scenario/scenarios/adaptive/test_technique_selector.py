# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.scenario.scenarios.adaptive.selectors import (
    EpsilonGreedyTechniqueSelector,
    TechniqueSelector,
)


class TestTechniqueSelectorProtocol:
    def test_implements_protocol(self):
        selector = EpsilonGreedyTechniqueSelector()
        assert isinstance(selector, TechniqueSelector)
