# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.score.scorer_evaluation.metrics_type import MetricsType, RegistryUpdateBehavior


def test_metrics_type_harm_value():
    assert MetricsType.HARM.value == "harm"


def test_metrics_type_objective_value():
    assert MetricsType.OBJECTIVE.value == "objective"


def test_metrics_type_members():
    members = list(MetricsType)
    assert len(members) == 2
    assert MetricsType.HARM in members
    assert MetricsType.OBJECTIVE in members


def test_metrics_type_from_value():
    assert MetricsType("harm") is MetricsType.HARM
    assert MetricsType("objective") is MetricsType.OBJECTIVE


def test_registry_update_behavior_skip_if_exists():
    assert RegistryUpdateBehavior.SKIP_IF_EXISTS.value == "skip_if_exists"


def test_registry_update_behavior_always_update():
    assert RegistryUpdateBehavior.ALWAYS_UPDATE.value == "always_update"


def test_registry_update_behavior_never_update():
    assert RegistryUpdateBehavior.NEVER_UPDATE.value == "never_update"


def test_registry_update_behavior_members():
    members = list(RegistryUpdateBehavior)
    assert len(members) == 3


def test_registry_update_behavior_from_value():
    assert RegistryUpdateBehavior("skip_if_exists") is RegistryUpdateBehavior.SKIP_IF_EXISTS
    assert RegistryUpdateBehavior("always_update") is RegistryUpdateBehavior.ALWAYS_UPDATE
    assert RegistryUpdateBehavior("never_update") is RegistryUpdateBehavior.NEVER_UPDATE
