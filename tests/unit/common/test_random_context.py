# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.random_context import (
    RandomContext,
    configure_random_seed,
    get_random_generator,
    random_execution,
)


def teardown_function() -> None:
    """Restore unseeded behavior after each test."""
    configure_random_seed(seed=None)


def test_random_context_derives_stable_named_children() -> None:
    root = RandomContext(seed=42)

    assert root.child("selection").derived_seed(stream="values") == root.child("selection").derived_seed(
        stream="values"
    )
    assert root.child("selection").derived_seed(stream="values") != root.child("marks").derived_seed(stream="values")


def test_sibling_stream_consumption_does_not_change_other_stream() -> None:
    configure_random_seed(seed=42)

    with random_execution(namespace="converter"):
        selection = get_random_generator(stream="word-selection")
        for _ in range(100):
            selection.random()
        marks_after_many_selection_draws = [get_random_generator(stream="combining-marks").random() for _ in range(5)]

    with random_execution(namespace="converter"):
        get_random_generator(stream="word-selection").random()
        marks_after_one_selection_draw = [get_random_generator(stream="combining-marks").random() for _ in range(5)]

    assert marks_after_many_selection_draws == marks_after_one_selection_draw


def test_explicit_child_seed_overrides_parent_context() -> None:
    configure_random_seed(seed=42)

    with random_execution(namespace="converter", seed=1):
        first = [get_random_generator(namespace="selection", stream="words", seed=7).random() for _ in range(5)]

    with random_execution(namespace="converter", seed=99):
        second = [get_random_generator(namespace="selection", stream="words", seed=7).random() for _ in range(5)]

    assert first == second


def test_same_type_component_instances_do_not_share_mutable_streams() -> None:
    configure_random_seed(seed=42)
    first_owner = object()
    second_owner = object()

    with random_execution(namespace="converter"):
        first = get_random_generator(namespace="selection", stream="words", owner=first_owner)
        first.random()
        second_owner_first_draw = get_random_generator(
            namespace="selection",
            stream="words",
            owner=second_owner,
        ).random()

    with random_execution(namespace="converter"):
        expected_first_draw = get_random_generator(
            namespace="selection",
            stream="words",
            owner=second_owner,
        ).random()

    assert second_owner_first_draw == expected_first_draw


def test_operation_key_diversifies_deterministic_stream() -> None:
    configure_random_seed(seed=42)

    with random_execution(namespace="converter", operation_key="first"):
        first = get_random_generator(stream="values").random()
    with random_execution(namespace="converter", operation_key="second"):
        second = get_random_generator(stream="values").random()

    assert first != second


def test_explicit_child_seed_preserves_inherited_operation_path() -> None:
    configure_random_seed(seed=42)

    with random_execution(namespace="converter", operation_key="first"):
        first = get_random_generator(namespace="selection", stream="words", seed=7).random()
    with random_execution(namespace="converter", operation_key="second"):
        second = get_random_generator(namespace="selection", stream="words", seed=7).random()

    assert first != second
