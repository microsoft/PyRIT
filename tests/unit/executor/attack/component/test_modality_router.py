# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for :class:`_ModalityFeedbackRouter`."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.component.modality_router import _ModalityFeedbackRouter
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pyrit.models.literals import PromptDataType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_target(*, input_modalities: Iterable[Iterable[str]]) -> MagicMock:
    """Build a minimal mocked PromptTarget exposing ``configuration.capabilities``."""
    capabilities = TargetCapabilities(
        input_modalities=frozenset(frozenset(combo) for combo in input_modalities),
    )
    configuration = TargetConfiguration(capabilities=capabilities)
    target = MagicMock()
    target.configuration = configuration
    return target


def _make_response(*, data_type: PromptDataType, value: str = "some-value") -> Message:
    """Wrap a single assistant piece of the given data type in a Message."""
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=value,
                original_value_data_type=data_type,
            )
        ]
    )


# ---------------------------------------------------------------------------
# validate_first_turn_seed
# ---------------------------------------------------------------------------
class TestValidateFirstTurnSeed:
    def test_text_only_target_no_seed_required(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}]),
        )

        router.validate_first_turn_seed(next_message=None)

    def test_multimodal_target_with_text_allowed_no_seed_required(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
        )

        router.validate_first_turn_seed(next_message=None)

    def test_edit_only_target_without_next_message_raises(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )

        with pytest.raises(ValueError, match="seed"):
            router.validate_first_turn_seed(next_message=None)

    def test_edit_only_target_with_text_only_next_message_raises(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )
        next_message = Message.from_prompt(prompt="just text", role="user")

        with pytest.raises(ValueError, match="seed"):
            router.validate_first_turn_seed(next_message=next_message)

    def test_edit_only_target_with_media_seed_passes(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )
        shared_conv = "edit-only-conv"
        next_message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="",
                    original_value_data_type="text",
                    conversation_id=shared_conv,
                    prompt_metadata={"adversarial_placeholder": True},
                ),
                MessagePiece(
                    role="user",
                    original_value="/path/to/seed.png",
                    original_value_data_type="image_path",
                    conversation_id=shared_conv,
                ),
            ]
        )

        router.validate_first_turn_seed(next_message=next_message)


# ---------------------------------------------------------------------------
# build_adversarial_input_message
# ---------------------------------------------------------------------------
class TestBuildAdversarialInputMessage:
    @pytest.mark.parametrize("media_type", ["image_path", "audio_path", "video_path"])
    def test_text_only_adversarial_falls_back_to_text(self, media_type):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", media_type}]),
        )
        last_response = _make_response(data_type=media_type, value="/tmp/output.bin")

        msg = router.build_adversarial_input_message(
            text="please refine",
            last_response=last_response,
        )

        assert len(msg.message_pieces) == 1
        assert msg.get_value() == "please refine"
        assert msg.message_pieces[0].original_value_data_type == "text"

    @pytest.mark.parametrize("media_type", ["image_path", "audio_path", "video_path"])
    def test_multimodal_adversarial_attaches_media(self, media_type):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}, {"text", media_type}]),
            objective_target=_build_target(input_modalities=[{"text"}]),
        )
        last_response = _make_response(data_type=media_type, value="/tmp/output.bin")

        msg = router.build_adversarial_input_message(
            text="please refine",
            last_response=last_response,
        )

        assert len(msg.message_pieces) == 2
        assert msg.message_pieces[0].original_value_data_type == "text"
        assert msg.message_pieces[0].original_value == "please refine"
        assert msg.message_pieces[1].original_value_data_type == media_type
        assert msg.message_pieces[1].original_value == "/tmp/output.bin"
        # All pieces share a single conversation_id so Message validation passes.
        assert msg.message_pieces[0].conversation_id == msg.message_pieces[1].conversation_id

    def test_no_last_response_returns_text_only(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
            objective_target=_build_target(input_modalities=[{"text"}]),
        )

        msg = router.build_adversarial_input_message(
            text="initial prompt",
            last_response=None,
        )

        assert len(msg.message_pieces) == 1
        assert msg.get_value() == "initial prompt"

    def test_seed_media_is_forwarded_to_adversarial_when_supported(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
        )
        seed_message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="",
                    original_value_data_type="text",
                    conversation_id="seed-forwarding-conv",
                    prompt_metadata={"adversarial_placeholder": True},
                ),
                MessagePiece(
                    role="user",
                    original_value="/tmp/seed.png",
                    original_value_data_type="image_path",
                    conversation_id="seed-forwarding-conv",
                ),
            ]
        )

        msg = router.build_adversarial_input_message(
            text="seed-aware prompt",
            last_response=None,
            seed_message=seed_message,
        )

        assert len(msg.message_pieces) == 2
        assert msg.message_pieces[0].original_value_data_type == "text"
        assert msg.message_pieces[0].original_value == "seed-aware prompt"
        assert msg.message_pieces[1].original_value_data_type == "image_path"
        assert msg.message_pieces[1].original_value == "/tmp/seed.png"

    def test_response_media_type_not_supported_falls_back(self):
        # Adversarial accepts {text, image_path} but the response is audio_path.
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", "audio_path"}]),
        )
        last_response = _make_response(data_type="audio_path", value="/tmp/out.wav")

        msg = router.build_adversarial_input_message(
            text="rationale",
            last_response=last_response,
        )

        assert len(msg.message_pieces) == 1
        assert msg.get_value() == "rationale"


# ---------------------------------------------------------------------------
# fill_adversarial_placeholders
# ---------------------------------------------------------------------------
class TestFillAdversarialPlaceholders:
    @staticmethod
    def _make_placeholder_message() -> Message:
        shared_conv = "edit-fill-conv"
        return Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="",
                    original_value_data_type="text",
                    conversation_id=shared_conv,
                    prompt_metadata={"adversarial_placeholder": True},
                ),
                MessagePiece(
                    role="user",
                    original_value="/path/to/base.png",
                    original_value_data_type="image_path",
                    conversation_id=shared_conv,
                ),
            ]
        )

    def test_replaces_placeholder_only(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )
        message = self._make_placeholder_message()

        filled = router.fill_adversarial_placeholders(
            message=message,
            adversarial_text="please edit this image",
        )

        assert len(filled.message_pieces) == 2
        assert filled.message_pieces[0].original_value == "please edit this image"
        assert filled.message_pieces[0].original_value_data_type == "text"
        assert filled.message_pieces[0].is_adversarial_placeholder() is False
        assert filled.message_pieces[1].original_value == "/path/to/base.png"
        assert filled.message_pieces[1].original_value_data_type == "image_path"

    def test_does_not_mutate_input_message(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )
        message = self._make_placeholder_message()

        router.fill_adversarial_placeholders(
            message=message,
            adversarial_text="adv text",
        )

        # Original input message still has the placeholder.
        assert message.message_pieces[0].is_adversarial_placeholder() is True
        assert message.message_pieces[0].original_value == ""

    def test_filled_pieces_share_conversation_id(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )
        message = self._make_placeholder_message()

        filled = router.fill_adversarial_placeholders(
            message=message,
            adversarial_text="adv text",
        )

        ids = {piece.conversation_id for piece in filled.message_pieces}
        assert len(ids) == 1


# ---------------------------------------------------------------------------
# build_objective_input_message
# ---------------------------------------------------------------------------
class TestBuildObjectiveInputMessage:
    def test_turn_zero_text_only_target(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
        )
        last_response = _make_response(data_type="image_path", value="/tmp/prev.png")

        msg = router.build_objective_input_message(
            text="adversarial prompt",
            last_response=last_response,
            turn_index=0,
        )

        assert len(msg.message_pieces) == 1
        assert msg.get_value() == "adversarial prompt"
        assert not router.has_forwardable_objective_media(message=last_response, turn_index=0)

    def test_turn_zero_edit_only_target_raises_defensive(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )

        with pytest.raises(ValueError, match="text-only"):
            router.build_objective_input_message(
                text="adversarial prompt",
                last_response=None,
                turn_index=0,
            )

    def test_turn_n_attaches_prev_media_when_supported(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
        )
        last_response = _make_response(data_type="image_path", value="/tmp/prev.png")

        msg = router.build_objective_input_message(
            text="refine this further",
            last_response=last_response,
            turn_index=1,
        )

        assert len(msg.message_pieces) == 2
        assert msg.message_pieces[0].original_value == "refine this further"
        assert msg.message_pieces[1].original_value_data_type == "image_path"
        assert msg.message_pieces[1].original_value == "/tmp/prev.png"

    def test_turn_n_text_only_target_drops_media(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}]),
        )
        last_response = _make_response(data_type="image_path", value="/tmp/prev.png")

        msg = router.build_objective_input_message(
            text="refine this further",
            last_response=last_response,
            turn_index=1,
        )

        assert len(msg.message_pieces) == 1
        assert msg.get_value() == "refine this further"

    def test_turn_n_no_prev_response_returns_text_only(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
        )

        msg = router.build_objective_input_message(
            text="continue",
            last_response=None,
            turn_index=2,
        )

        assert len(msg.message_pieces) == 1
        assert msg.get_value() == "continue"

    @pytest.mark.parametrize("media_type", ["image_path", "audio_path", "video_path"])
    def test_turn_n_generic_media_types(self, media_type):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", media_type}]),
        )
        last_response = _make_response(data_type=media_type, value=f"/tmp/prev.{media_type}")

        msg = router.build_objective_input_message(
            text="continue",
            last_response=last_response,
            turn_index=1,
        )

        assert len(msg.message_pieces) == 2
        assert msg.message_pieces[1].original_value_data_type == media_type


# ---------------------------------------------------------------------------
# properties / construction
# ---------------------------------------------------------------------------
class TestProperties:
    def test_objective_target_requires_media_on_first_turn_when_text_excluded(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text", "image_path"}]),
        )

        assert router.objective_target_requires_media_on_first_turn is True

    def test_objective_does_not_require_media_when_text_allowed(self):
        router = _ModalityFeedbackRouter(
            adversarial_chat=_build_target(input_modalities=[{"text"}]),
            objective_target=_build_target(input_modalities=[{"text"}, {"text", "image_path"}]),
        )

        assert router.objective_target_requires_media_on_first_turn is False
