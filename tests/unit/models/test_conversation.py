# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models import ComponentIdentifier, Conversation


def test_init_requires_conversation_id():
    with pytest.raises(ValidationError):
        Conversation()  # type: ignore[call-arg]


def test_init_defaults_target_identifier_to_none():
    conversation = Conversation(conversation_id="conv-1")
    assert conversation.conversation_id == "conv-1"
    assert conversation.target_identifier is None


def test_init_forbids_extra_fields():
    with pytest.raises(ValidationError):
        Conversation(conversation_id="conv-1", unexpected="value")  # type: ignore[call-arg]


def test_init_accepts_component_identifier():
    identifier = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target")
    conversation = Conversation(conversation_id="conv-1", target_identifier=identifier)
    assert conversation.target_identifier == identifier


def test_target_identifier_accepts_flat_dict():
    identifier = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target")
    conversation = Conversation(conversation_id="conv-1", target_identifier=identifier.model_dump())
    assert isinstance(conversation.target_identifier, ComponentIdentifier)
    assert conversation.target_identifier.class_name == "OpenAIChatTarget"


def test_model_dump_serializes_target_identifier_to_flat_dict():
    identifier = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target")
    conversation = Conversation(conversation_id="conv-1", target_identifier=identifier)

    dumped = conversation.model_dump()

    assert dumped["conversation_id"] == "conv-1"
    assert dumped["target_identifier"]["class_name"] == "OpenAIChatTarget"
    assert dumped["target_identifier"]["class_module"] == "pyrit.prompt_target"


def test_model_dump_with_no_target_identifier():
    conversation = Conversation(conversation_id="conv-1")
    assert conversation.model_dump()["target_identifier"] is None


def test_round_trips_through_model_validate():
    identifier = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target")
    conversation = Conversation(conversation_id="conv-1", target_identifier=identifier)

    restored = Conversation.model_validate(conversation.model_dump())

    assert restored.conversation_id == "conv-1"
    assert restored.target_identifier == identifier
