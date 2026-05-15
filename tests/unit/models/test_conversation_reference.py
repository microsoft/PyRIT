# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models.conversation_reference import ConversationReference, ConversationType


def test_conversation_type_values():
    assert ConversationType.ADVERSARIAL.value == "adversarial"
    assert ConversationType.PRUNED.value == "pruned"
    assert ConversationType.SCORE.value == "score"
    assert ConversationType.CONVERTER.value == "converter"


def test_conversation_reference_init():
    ref = ConversationReference(conversation_id="abc-123", conversation_type=ConversationType.ADVERSARIAL)
    assert ref.conversation_id == "abc-123"
    assert ref.conversation_type == ConversationType.ADVERSARIAL
    assert ref.description is None


def test_conversation_reference_with_description():
    ref = ConversationReference(
        conversation_id="abc-123",
        conversation_type=ConversationType.PRUNED,
        description="pruned branch",
    )
    assert ref.description == "pruned branch"


def test_conversation_reference_is_frozen():
    ref = ConversationReference(conversation_id="abc", conversation_type=ConversationType.SCORE)
    with pytest.raises(AttributeError):
        ref.conversation_id = "new_id"


def test_conversation_reference_hash():
    ref = ConversationReference(conversation_id="abc", conversation_type=ConversationType.ADVERSARIAL)
    assert hash(ref) == hash("abc")


def test_conversation_reference_eq_same_id():
    ref1 = ConversationReference(conversation_id="abc", conversation_type=ConversationType.ADVERSARIAL)
    ref2 = ConversationReference(
        conversation_id="abc",
        conversation_type=ConversationType.PRUNED,
        description="different",
    )
    assert ref1 == ref2


def test_conversation_reference_eq_different_id():
    ref1 = ConversationReference(conversation_id="abc", conversation_type=ConversationType.ADVERSARIAL)
    ref2 = ConversationReference(conversation_id="xyz", conversation_type=ConversationType.ADVERSARIAL)
    assert ref1 != ref2


def test_conversation_reference_eq_non_reference():
    ref = ConversationReference(conversation_id="abc", conversation_type=ConversationType.ADVERSARIAL)
    assert ref != "abc"
    assert ref != 42
    assert ref != None  # noqa: E711


def test_conversation_reference_usable_in_set():
    ref1 = ConversationReference(conversation_id="abc", conversation_type=ConversationType.ADVERSARIAL)
    ref2 = ConversationReference(conversation_id="abc", conversation_type=ConversationType.PRUNED)
    ref3 = ConversationReference(conversation_id="xyz", conversation_type=ConversationType.SCORE)
    s = {ref1, ref2, ref3}
    assert len(s) == 2


def test_conversation_reference_usable_as_dict_key():
    ref = ConversationReference(conversation_id="abc", conversation_type=ConversationType.CONVERTER)
    d = {ref: "value"}
    lookup_ref = ConversationReference(conversation_id="abc", conversation_type=ConversationType.ADVERSARIAL)
    assert d[lookup_ref] == "value"
