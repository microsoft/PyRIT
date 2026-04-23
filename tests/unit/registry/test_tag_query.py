# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass

import pytest

from pyrit.registry.tag_query import TagQuery

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeSpec:
    name: str
    tags: list[str]


# ---------------------------------------------------------------------------
# Leaf matching
# ---------------------------------------------------------------------------


class TestTagQueryLeafMatching:
    def test_empty_query_matches_everything(self) -> None:
        q = TagQuery()
        assert q.matches(set()) is True
        assert q.matches({"a", "b"}) is True

    def test_include_all_requires_all_tags(self) -> None:
        q = TagQuery(include_all=frozenset({"a", "b"}))
        assert q.matches({"a", "b", "c"}) is True
        assert q.matches({"a"}) is False
        assert q.matches(set()) is False

    def test_include_any_requires_at_least_one(self) -> None:
        q = TagQuery(include_any=frozenset({"x", "y"}))
        assert q.matches({"x"}) is True
        assert q.matches({"y", "z"}) is True
        assert q.matches({"z"}) is False

    def test_exclude_rejects_matching_tags(self) -> None:
        q = TagQuery(exclude_tags=frozenset({"deprecated"}))
        assert q.matches({"core", "stable"}) is True
        assert q.matches({"core", "deprecated"}) is False

    def test_combined_leaf_fields(self) -> None:
        q = TagQuery(
            include_all=frozenset({"core"}),
            include_any=frozenset({"single_turn", "multi_turn"}),
            exclude_tags=frozenset({"deprecated"}),
        )
        assert q.matches({"core", "single_turn"}) is True
        assert q.matches({"core", "multi_turn", "extra"}) is True
        assert q.matches({"core"}) is False  # missing include_any
        assert q.matches({"single_turn"}) is False  # missing include_all
        assert q.matches({"core", "single_turn", "deprecated"}) is False  # excluded


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


class TestTagQueryOperators:
    def test_and_both_must_match(self) -> None:
        q = TagQuery(include_all=frozenset({"a"})) & TagQuery(include_any=frozenset({"b", "c"}))
        assert q.matches({"a", "b"}) is True
        assert q.matches({"a", "c"}) is True
        assert q.matches({"a"}) is False  # fails include_any
        assert q.matches({"b"}) is False  # fails include_all

    def test_or_either_can_match(self) -> None:
        q = TagQuery(include_all=frozenset({"a", "b"})) | TagQuery(include_all=frozenset({"c"}))
        assert q.matches({"a", "b"}) is True
        assert q.matches({"c"}) is True
        assert q.matches({"a"}) is False

    def test_complex_nesting(self) -> None:
        # (A OR B) AND (C OR D) AND NOT deprecated
        q = (
            TagQuery(include_any=frozenset({"a", "b"}))
            & TagQuery(include_any=frozenset({"c", "d"}))
            & TagQuery.none_of("deprecated")
        )
        assert q.matches({"a", "c"}) is True
        assert q.matches({"b", "d"}) is True
        assert q.matches({"a", "c", "deprecated"}) is False
        assert q.matches({"a"}) is False  # missing c or d

    def test_chained_or(self) -> None:
        q = (
            TagQuery(include_all=frozenset({"a"}))
            | TagQuery(include_all=frozenset({"b"}))
            | TagQuery(include_all=frozenset({"c"}))
        )
        assert q.matches({"a"}) is True
        assert q.matches({"b"}) is True
        assert q.matches({"c"}) is True
        assert q.matches({"d"}) is False


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class TestTagQueryFilter:
    def test_filter_returns_matching_items(self) -> None:
        items = [
            _FakeSpec(name="x", tags=["core", "single_turn"]),
            _FakeSpec(name="y", tags=["core", "multi_turn"]),
            _FakeSpec(name="z", tags=["experimental"]),
        ]
        q = TagQuery(include_all=frozenset({"core"}))
        result = q.filter(items)
        assert [i.name for i in result] == ["x", "y"]

    def test_filter_preserves_order(self) -> None:
        items = [
            _FakeSpec(name="c", tags=["t"]),
            _FakeSpec(name="a", tags=["t"]),
            _FakeSpec(name="b", tags=["t"]),
        ]
        q = TagQuery(include_any=frozenset({"t"}))
        assert [i.name for i in q.filter(items)] == ["c", "a", "b"]

    def test_filter_empty_query_returns_all(self) -> None:
        items = [_FakeSpec(name="x", tags=["a"]), _FakeSpec(name="y", tags=["b"])]
        assert len(TagQuery().filter(items)) == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestTagQueryValidation:
    """Tests for __post_init__ validation."""

    def test_invalid_op_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid TagQuery op"):
            TagQuery(_op="xor", _children=(TagQuery(), TagQuery()))

    def test_and_requires_at_least_two_children(self) -> None:
        with pytest.raises(ValueError, match="'and' TagQuery must have at least 2 children"):
            TagQuery(_op="and", _children=(TagQuery(),))

    def test_or_requires_at_least_two_children(self) -> None:
        with pytest.raises(ValueError, match="'or' TagQuery must have at least 2 children"):
            TagQuery(_op="or", _children=(TagQuery(),))

    def test_leaf_rejects_children(self) -> None:
        with pytest.raises(ValueError, match="Leaf TagQuery must not have children"):
            TagQuery(_op="", _children=(TagQuery(),))

    def test_valid_composite_accepted(self) -> None:
        # Should not raise
        TagQuery(_op="and", _children=(TagQuery(), TagQuery()))
        TagQuery(_op="or", _children=(TagQuery(), TagQuery()))


class TestTagQueryFilterWithSetTags:
    """Test filter() with items whose tags are set[str] rather than list[str]."""

    @dataclass
    class _SetTagSpec:
        name: str
        tags: set[str]

    def test_filter_works_with_set_tags(self) -> None:
        items = [
            self._SetTagSpec(name="a", tags={"core", "single_turn"}),
            self._SetTagSpec(name="b", tags={"experimental"}),
        ]
        q = TagQuery(include_all=frozenset({"core"}))
        result = q.filter(items)
        assert [i.name for i in result] == ["a"]


class TestTagQueryClassmethods:
    def test_all_creates_include_all(self) -> None:
        q = TagQuery.all("a", "b")
        assert q.matches({"a", "b", "c"}) is True
        assert q.matches({"a"}) is False

    def test_any_of_creates_include_any(self) -> None:
        q = TagQuery.any_of("x", "y")
        assert q.matches({"x"}) is True
        assert q.matches({"z"}) is False

    def test_none_of_creates_exclude(self) -> None:
        q = TagQuery.none_of("deprecated")
        assert q.matches({"core"}) is True
        assert q.matches({"deprecated"}) is False


class TestTagQuerySetAcceptance:
    def test_constructor_accepts_plain_sets(self) -> None:
        q = TagQuery(include_all={"a", "b"})
        assert q.matches({"a", "b"}) is True
        assert isinstance(q.include_all, frozenset)

    def test_constructor_accepts_plain_set_for_exclude(self) -> None:
        q = TagQuery(exclude_tags={"deprecated"})
        assert q.matches({"deprecated"}) is False
        assert isinstance(q.exclude_tags, frozenset)


class TestTagQueryEdgeCases:
    def test_frozen_dataclass_is_hashable(self) -> None:
        q = TagQuery(include_all=frozenset({"a"}))
        assert hash(q) is not None
        assert {q}  # can be added to a set

    def test_matches_accepts_frozenset(self) -> None:
        q = TagQuery(include_all=frozenset({"a"}))
        assert q.matches(frozenset({"a", "b"})) is True

    @pytest.mark.parametrize(
        "tags",
        [set(), frozenset()],
        ids=["empty_set", "empty_frozenset"],
    )
    def test_empty_tags_only_match_empty_query(self, tags: set[str] | frozenset[str]) -> None:
        assert TagQuery().matches(tags) is True
        assert TagQuery(include_all=frozenset({"a"})).matches(tags) is False
