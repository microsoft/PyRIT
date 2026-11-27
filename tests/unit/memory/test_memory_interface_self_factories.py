# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Memory read-model factories annotated ``-> Self`` build the subclass they are called on."""

from datetime import UTC, datetime
from typing import assert_type

import pytest

from pyrit.memory import ScenarioHistoryAggregate
from pyrit.memory.memory_interface import AttackConversationPage


class _DerivedScenarioHistoryAggregate(ScenarioHistoryAggregate):
    pass


class _DerivedAttackConversationPage(AttackConversationPage):
    pass


_TIMESTAMP = datetime(2026, 11, 6, 12, tzinfo=UTC)


@pytest.mark.parametrize("aggregate_cls", [ScenarioHistoryAggregate, _DerivedScenarioHistoryAggregate])
def test_scenario_history_aggregate_empty_returns_calling_class(
    aggregate_cls: type[ScenarioHistoryAggregate],
) -> None:
    aggregate = aggregate_cls.empty(scenario_result_id="run")

    assert type(aggregate) is aggregate_cls
    assert aggregate.unit_count == 0


@pytest.mark.parametrize("page_cls", [AttackConversationPage, _DerivedAttackConversationPage])
def test_attack_conversation_page_from_references_returns_calling_class(
    page_cls: type[AttackConversationPage],
) -> None:
    page = page_cls.from_references(main_conversation_id="main", pruned_conversation_ids=["p"], timestamp=_TIMESTAMP)

    assert type(page) is page_cls
    assert page.total_count == 2


def test_memory_interface_factories_preserve_subclass_types() -> None:
    aggregate = _DerivedScenarioHistoryAggregate.empty(scenario_result_id="run")
    page = _DerivedAttackConversationPage.from_references(
        main_conversation_id="main", pruned_conversation_ids=[], timestamp=_TIMESTAMP
    )

    assert_type(aggregate, _DerivedScenarioHistoryAggregate)
    assert_type(page, _DerivedAttackConversationPage)
