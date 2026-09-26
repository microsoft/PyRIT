# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for derived modality compatibility.

Modality is not stored on a technique — it is derived per run from the seed pieces, the
request converters, and the concrete target. These tests cover that derivation in layers:
projecting a converter chain, checking the projection against a target's declared input
modalities, checking the target's output modalities against the scorer, and assembling all of
it into a verdict for one built ``AtomicAttack``.

``project_request_chain`` is extracted from
``AttackTechniqueFactory.can_append_request_converter`` so the factory's append check and
scenario-level plan-time validation share one implementation rather than drifting apart.
"""

from __future__ import annotations

import asyncio
import os
from typing import get_args

import pytest
from unit.mocks import MockPromptTarget, get_image_message_piece, get_mock_target, store_message
from unit.modality_profiles import (
    IMAGE_EDIT_INPUT_MODALITIES,
    TEXT_ONLY_MODALITIES,
    VISION_INPUT_MODALITIES,
)

from pyrit.converter import (
    AudioEchoConverter,
    Base64Converter,
    Converter,
    ConverterResult,
    ImageCompressionConverter,
    QRCodeConverter,
)
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    PromptSendingAttack,
    SequentialAttack,
    SequentialChildAttack,
)
from pyrit.models import (
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    Message,
    MessagePiece,
    PromptDataType,
    SeedObjective,
    SeedPrompt,
)
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.modality_validation import (
    ModalityPolicy,
    ModalityValidationError,
    ModalityVerdict,
    project_request_chain,
    project_request_combinations,
    scorer_accepts,
    target_accepts,
    validate_atomic_attack,
)
from pyrit.score import (
    MessageScorable,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


def _configs(*converters) -> list[ConverterConfiguration]:
    """One configuration per converter, mirroring ``ConverterConfiguration.from_converters``."""
    return ConverterConfiguration.from_converters(converters=list(converters))


class _OfflineAudioToTextConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("audio_path",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "audio_path") -> ConverterResult:
        if not self.input_supported(input_type):
            raise ValueError(f"Unsupported input type: {input_type}")
        return ConverterResult(output_text="matched transcript", output_type="text")


class _TextOrImageConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text", "image_path")

    def __init__(self, *, output_type: PromptDataType = "text") -> None:
        super().__init__()
        self._output_type = output_type

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        if not self.input_supported(input_type):
            raise ValueError(f"Unsupported input type: {input_type}")
        return ConverterResult(output_text=prompt, output_type=self._output_type)


def _scorer_declaring(declared) -> SubStringScorer:
    """A real scorer whose validator declares ``declared``, or nothing when ``None``."""
    validator = (
        ScorerPromptValidator(supported_data_types=declared) if declared is not None else ScorerPromptValidator()
    )
    return SubStringScorer(substring="x", validator=validator)


def _text_seed_group() -> AttackSeedGroup:
    """A seed group carrying only an objective, so the attack sends the objective text."""
    return AttackSeedGroup(seeds=[SeedObjective(value="a text objective")])


def _media_seed_group(data_type="image_path", value="seed.png") -> AttackSeedGroup:
    """A seed group whose next message is a single media piece."""
    return AttackSeedGroup(
        seeds=[SeedObjective(value="a media objective"), SeedPrompt(value=value, data_type=data_type)]
    )


def _atomic(
    *,
    target,
    converters=None,
    converter_configurations=None,
    response_converter_configurations=None,
    scorer=None,
    seed_groups=None,
    seed_technique=None,
    params_type=None,
    name="atomic",
) -> AtomicAttack:
    """Build a real AtomicAttack around a PromptSendingAttack with the given wiring."""
    kwargs = {"objective_target": target}
    if converters is not None or converter_configurations is not None or response_converter_configurations is not None:
        kwargs["attack_converter_config"] = AttackConverterConfig(
            request_converters=(
                converter_configurations
                if converter_configurations is not None
                else ConverterConfiguration.from_converters(converters=list(converters or []))
            ),
            response_converters=response_converter_configurations or [],
        )
    if scorer is not None:
        kwargs["attack_scoring_config"] = AttackScoringConfig(objective_scorer=scorer)
    if params_type is not None:
        kwargs["params_type"] = params_type
    attack = PromptSendingAttack(**kwargs)
    return AtomicAttack(
        atomic_attack_name=name,
        attack_technique=AttackTechnique(attack=attack, seed_technique=seed_technique),
        seed_groups=seed_groups or [_text_seed_group()],
    )


# ---------------------------------------------------------------------------
# Straight-line chains
# ---------------------------------------------------------------------------
def test_project_request_chain_no_converters_returns_start_types():
    """An empty chain passes the start types through untouched."""
    projected, reason = project_request_chain(start_types=["text"], request_converters=[])
    assert projected == {"text"}
    assert reason is None


def test_project_request_chain_text_converter_preserves_text():
    """A text-to-text converter leaves the projected type as text."""
    projected, reason = project_request_chain(start_types=["text"], request_converters=_configs(Base64Converter()))
    assert projected == {"text"}
    assert reason is None


def test_project_request_chain_image_converter_yields_image_path():
    """A text-to-image converter shifts the projected type to image_path."""
    projected, reason = project_request_chain(start_types=["text"], request_converters=_configs(QRCodeConverter()))
    assert projected == {"image_path"}
    assert reason is None


def test_project_request_chain_audio_converter_preserves_audio():
    """An audio-to-audio converter accepts an audio start and keeps it."""
    projected, reason = project_request_chain(
        start_types=["audio_path"], request_converters=_configs(AudioEchoConverter())
    )
    assert projected == {"audio_path"}
    assert reason is None


def test_project_request_chain_two_converters_chain():
    """Consecutive converters compose: text becomes an image, which the next converter accepts."""
    projected, reason = project_request_chain(
        start_types=["text"],
        request_converters=_configs(QRCodeConverter(), ImageCompressionConverter()),
    )
    assert projected == {"image_path"}
    assert reason is None


def test_project_request_chain_image_converter_accepts_image_start():
    """A converter declaring image_path input accepts an image start with no bridge needed."""
    projected, reason = project_request_chain(
        start_types=["image_path"], request_converters=_configs(ImageCompressionConverter())
    )
    assert projected == {"image_path"}
    assert reason is None


# ---------------------------------------------------------------------------
# Broken chains name the offending converter
# ---------------------------------------------------------------------------
def test_project_request_chain_unsupported_input_names_converter():
    """A converter that cannot accept the current type breaks the chain and is named in the reason."""
    projected, reason = project_request_chain(
        start_types=["image_path"], request_converters=_configs(Base64Converter())
    )
    assert projected == set()
    assert reason is not None
    assert "Base64Converter" in reason
    assert "image_path" in reason


def test_project_request_chain_reports_first_failure_only():
    """The first converter that cannot accept the current type explains the failure."""
    _, reason = project_request_chain(
        start_types=["text"],
        request_converters=_configs(QRCodeConverter(), Base64Converter()),
    )
    assert reason is not None
    assert "Base64Converter" in reason
    assert "QRCodeConverter" not in reason


def test_project_request_chain_empty_start_returns_empty():
    """No start types means nothing can reach the target."""
    projected, reason = project_request_chain(start_types=[], request_converters=_configs(Base64Converter()))
    assert projected == set()
    assert reason is None


def test_project_request_chain_multi_type_start_fails_when_one_branch_breaks():
    """A converter that applies to a type it cannot accept breaks the whole chain."""
    projected, reason = project_request_chain(
        start_types=["text", "image_path"],
        request_converters=_configs(Base64Converter()),
    )
    assert projected == set()
    assert reason is not None
    assert "Base64Converter" in reason


# ---------------------------------------------------------------------------
# Conditional application
# ---------------------------------------------------------------------------
def test_project_request_chain_conditional_not_applying_preserves_type():
    """When ``prompt_data_types_to_apply`` excludes the current type the piece passes through unchanged."""
    config = ConverterConfiguration(converters=[QRCodeConverter()], prompt_data_types_to_apply=["image_path"])
    projected, reason = project_request_chain(start_types=["text"], request_converters=[config])
    assert projected == {"text"}
    assert reason is None


def test_project_request_chain_conditional_applying_converts():
    """When ``prompt_data_types_to_apply`` includes the current type the converter runs."""
    config = ConverterConfiguration(converters=[QRCodeConverter()], prompt_data_types_to_apply=["text"])
    projected, reason = project_request_chain(start_types=["text"], request_converters=[config])
    assert projected == {"image_path"}
    assert reason is None


@pytest.mark.parametrize(
    ("start_types", "indexes", "expected"),
    [
        (["text"], [0], {"image_path"}),
        (["text", "text"], [0], {"image_path", "text"}),
        (["text", "text"], [0, 1], {"image_path"}),
        (["text", "text"], [1], {"image_path", "text"}),
        (["text", "text"], [2], {"text"}),
    ],
)
def test_project_request_chain_indexes_to_apply_tracks_pieces(start_types, indexes, expected):
    """Only the pieces not selected by an indexed converter retain their old type."""
    config = ConverterConfiguration(converters=[QRCodeConverter()], indexes_to_apply=indexes)
    projected, reason = project_request_chain(start_types=start_types, request_converters=[config])
    assert projected == expected
    assert reason is None


def test_project_request_chain_unknown_indexes_keeps_possible_branches():
    """A factory without a concrete message must allow for unselected pieces."""
    config = ConverterConfiguration(converters=[QRCodeConverter()], indexes_to_apply=[0])
    projected, reason = project_request_chain(
        start_types=["text"], request_converters=[config], piece_indexes_known=False
    )
    assert projected == {"text", "image_path"}
    assert reason is None


def test_project_request_chain_indexed_conversion_preserves_position_across_configurations():
    """Later indexed configurations select the same message piece after an earlier conversion."""
    configs = [
        ConverterConfiguration(converters=[QRCodeConverter()], indexes_to_apply=[0]),
        ConverterConfiguration(converters=[ImageCompressionConverter()], indexes_to_apply=[0]),
    ]
    projected, reason = project_request_chain(start_types=["text", "text"], request_converters=configs)
    assert projected == {"image_path", "text"}
    assert reason is None


def test_project_request_chain_multi_type_start_projects_each_independently():
    """Each start type is projected on its own; a conditional converter touches only what it applies to."""
    config = ConverterConfiguration(converters=[QRCodeConverter()], prompt_data_types_to_apply=["text"])
    projected, reason = project_request_chain(
        start_types=["text", "audio_path"],
        request_converters=[config],
    )
    assert projected == {"image_path", "audio_path"}
    assert reason is None


def test_project_request_combinations_preserves_alternative_outputs():
    combinations, reason = project_request_combinations(
        start_types=["text"], request_converters=_configs(_TextOrImageConverter())
    )
    assert combinations == {frozenset({"text"}), frozenset({"image_path"})}
    assert reason is None


def test_project_request_combinations_preserves_piece_positions_and_indexes():
    configuration = ConverterConfiguration(converters=[_TextOrImageConverter()], indexes_to_apply=[0])
    combinations, reason = project_request_combinations(
        start_types=["text", "text"], request_converters=[configuration]
    )
    assert combinations == {frozenset({"text"}), frozenset({"text", "image_path"})}
    assert reason is None


def test_project_request_combinations_branches_through_subsequent_converter():
    combinations, reason = project_request_combinations(
        start_types=["text"],
        request_converters=_configs(_TextOrImageConverter(), ImageCompressionConverter()),
    )
    assert combinations == {frozenset({"image_path"})}
    assert reason is not None
    assert "ImageCompressionConverter" in reason


# ---------------------------------------------------------------------------
# Target acceptance (request chain terminus)
# ---------------------------------------------------------------------------
def test_target_accepts_exact_combo_is_compatible():
    """A projection matching an advertised combo exactly is compatible."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES)
    assert target_accepts(target=target, request_types={"text"}) is ModalityVerdict.COMPATIBLE


def test_target_accepts_advertised_multi_type_combo_is_compatible():
    """A text-plus-image request matches a target that advertises that exact combination."""
    target = get_mock_target(input_modalities=VISION_INPUT_MODALITIES)
    assert target_accepts(target=target, request_types={"text", "image_path"}) is ModalityVerdict.COMPATIBLE


def test_target_accepts_no_matching_combo_is_incompatible():
    """The canonical failure: an image reaches a text-only target."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES)
    assert target_accepts(target=target, request_types={"image_path"}) is ModalityVerdict.INCOMPATIBLE


def test_target_accepts_types_split_across_combos_is_incompatible():
    """Types advertised only separately do not satisfy one request that carries both."""
    target = get_mock_target(input_modalities=[{"text"}, {"image_path"}])
    assert target_accepts(target=target, request_types={"text", "image_path"}) is ModalityVerdict.INCOMPATIBLE


def test_target_accepts_lone_media_needs_its_own_advertised_combo():
    """
    A lone image is acceptable only if the target advertises ``{image_path}`` by itself.

    ``{text, image_path}`` means "text with an image", not "an image alone". A video-generation
    target declares exactly that shape and genuinely cannot take a bare reference image; a
    vision chat model that can take one declares ``{image_path}`` too, and is accepted.
    """
    text_with_image_only = get_mock_target(input_modalities=[{"text"}, {"text", "image_path"}])
    assert target_accepts(target=text_with_image_only, request_types={"image_path"}) is ModalityVerdict.INCOMPATIBLE
    vision = get_mock_target(input_modalities=VISION_INPUT_MODALITIES)
    assert target_accepts(target=vision, request_types={"image_path"}) is ModalityVerdict.COMPATIBLE


def test_target_accepts_edit_only_target_with_media_seed_is_compatible():
    """
    An image-edit target advertises only ``{text, image_path}``.

    A seed supplying both adversarial text and a media piece satisfies it, so plan-time
    validation must not reject this shape — it is how edit flows legitimately run.
    """
    target = get_mock_target(input_modalities=IMAGE_EDIT_INPUT_MODALITIES)
    assert target_accepts(target=target, request_types={"text", "image_path"}) is ModalityVerdict.COMPATIBLE


def test_target_accepts_edit_only_target_with_text_only_seed_is_incompatible():
    """The same target rejects a text-only request, matching what the router raises at run time."""
    target = get_mock_target(input_modalities=IMAGE_EDIT_INPUT_MODALITIES)
    assert target_accepts(target=target, request_types={"text"}) is ModalityVerdict.INCOMPATIBLE


def test_target_accepts_empty_projection_is_incompatible():
    """Nothing reaching the target cannot be compatible."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES)
    assert target_accepts(target=target, request_types=set()) is ModalityVerdict.INCOMPATIBLE


def test_target_accepts_unknown_capabilities_is_unknown():
    """A mock target with no real capabilities is indeterminate, never a failure."""
    assert target_accepts(target=get_mock_target(), request_types={"image_path"}) is ModalityVerdict.UNKNOWN


@pytest.mark.parametrize("media_type", ["image_path", "audio_path", "video_path"])
def test_target_accepts_media_with_text_combos(media_type):
    """Every media type pairs with text the same way."""
    target = get_mock_target(input_modalities=[{"text"}, {"text", media_type}])
    assert target_accepts(target=target, request_types={"text", media_type}) is ModalityVerdict.COMPATIBLE


# ---------------------------------------------------------------------------
# Scorer acceptance (response chain)
# ---------------------------------------------------------------------------
def test_scorer_accepts_declared_superset_is_compatible():
    """A scorer declaring everything the target emits is compatible."""
    target = get_mock_target(output_modalities=[{"text"}])
    verdict, reason = scorer_accepts(scorer=_scorer_declaring(["text", "image_path"]), target=target)
    assert verdict is ModalityVerdict.COMPATIBLE
    assert reason is None


def test_scorer_accepts_missing_emitted_type_is_incompatible():
    """A text-only scorer cannot read an image the target may emit."""
    target = get_mock_target(output_modalities=[{"image_path"}])
    verdict, reason = scorer_accepts(scorer=_scorer_declaring(["text"]), target=target)
    assert verdict is ModalityVerdict.INCOMPATIBLE
    assert reason is not None
    assert "image_path" in reason


def test_scorer_accepts_composite_with_disjoint_child_modalities_is_unknown(patch_central_database):
    """Do not promise compatibility for a compound scorer pending post-refactor review."""
    target = get_mock_target(output_modalities=[{"text"}])
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.OR,
        scorers=[_scorer_declaring(["text"]), _scorer_declaring(["image_path"])],
    )
    verdict, reason = scorer_accepts(scorer=scorer, target=target)
    assert verdict is ModalityVerdict.UNKNOWN
    assert reason is None


async def test_scorer_accepts_mixed_response_with_selective_text_scorer(patch_central_database):
    """The text piece can be scored without reading the audio piece."""
    scorer = SubStringScorer(substring="matched")
    response = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value="matched text"),
            MessagePiece(role="assistant", original_value="audio.wav", original_value_data_type="audio_path"),
        ]
    )
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))
    assert len(scores) == 1
    assert scores[0].get_value() is True

    target = get_mock_target(output_modalities=[{"text", "audio_path"}])
    assert scorer_accepts(scorer=scorer, target=target)[0] is ModalityVerdict.COMPATIBLE


def test_scorer_accepts_mixed_response_with_strict_text_scorer():
    """A strict scorer cannot process an unsupported audio piece in the same response."""
    scorer = SubStringScorer(
        substring="matched",
        validator=ScorerPromptValidator(supported_data_types=["text"], enforce_all_pieces_valid=True),
    )
    target = get_mock_target(output_modalities=[{"text", "audio_path"}])
    assert scorer_accepts(scorer=scorer, target=target)[0] is ModalityVerdict.INCOMPATIBLE


@pytest.mark.parametrize("inverted", [False, True])
async def test_scorer_accepts_mixed_response_when_empty_score_raises(patch_central_database, inverted: bool):
    """Raising on wholly unreadable evidence does not prevent selective mixed-piece scoring."""
    text_scorer = SubStringScorer(
        substring="matched",
        validator=ScorerPromptValidator(supported_data_types=["text"], raise_on_no_valid_pieces=True),
    )
    scorer = TrueFalseInverterScorer(scorer=text_scorer) if inverted else text_scorer
    response = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value="matched text"),
            MessagePiece(role="assistant", original_value="audio.wav", original_value_data_type="audio_path"),
        ]
    )
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))
    assert len(scores) == 1
    assert scores[0].get_value() is not inverted

    target = get_mock_target(output_modalities=[{"text", "audio_path"}])
    assert scorer_accepts(scorer=scorer, target=target)[0] is ModalityVerdict.COMPATIBLE


@pytest.mark.parametrize("inverted", [False, True])
async def test_scorer_accepts_audio_only_when_empty_score_raises(patch_central_database, inverted: bool):
    """The same scorer cannot read an audio-only response and must still raise."""
    text_scorer = SubStringScorer(
        substring="matched",
        validator=ScorerPromptValidator(supported_data_types=["text"], raise_on_no_valid_pieces=True),
    )
    scorer = TrueFalseInverterScorer(scorer=text_scorer) if inverted else text_scorer
    target = get_mock_target(output_modalities=[{"audio_path"}])
    assert scorer_accepts(scorer=scorer, target=target)[0] is ModalityVerdict.INCOMPATIBLE
    response = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value="audio.wav", original_value_data_type="audio_path")
        ]
    )
    expected_error = RuntimeError if inverted else ValueError
    with pytest.raises(expected_error, match="There are no valid pieces to score"):
        await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))


def test_scorer_accepts_alternative_output_when_empty_score_raises(patch_central_database):
    scorer = SubStringScorer(
        substring="matched",
        validator=ScorerPromptValidator(supported_data_types=["text"], raise_on_no_valid_pieces=True),
    )
    target = get_mock_target(output_modalities=[{"text", "audio_path"}, {"audio_path"}])
    assert scorer_accepts(scorer=scorer, target=target)[0] is ModalityVerdict.UNKNOWN


def test_scorer_accepts_audio_only_response_with_text_scorer():
    target = get_mock_target(output_modalities=[{"audio_path"}])
    assert scorer_accepts(scorer=_scorer_declaring(["text"]), target=target)[0] is ModalityVerdict.INCOMPATIBLE


def test_scorer_accepts_alternative_text_or_audio_response_is_unknown():
    """One output may be scored and another may not; neither outcome is guaranteed."""
    target = get_mock_target(output_modalities=[{"text"}, {"audio_path"}])
    assert scorer_accepts(scorer=_scorer_declaring(["text"]), target=target)[0] is ModalityVerdict.UNKNOWN


async def test_scorer_accepts_converted_audio_response(patch_central_database):
    """Runtime converts the returned audio piece to text before objective scoring."""
    scorer = SubStringScorer(substring="matched")
    target = get_mock_target(output_modalities=[{"audio_path"}])
    configurations = _configs(_OfflineAudioToTextConverter())
    response = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value="audio.wav", original_value_data_type="audio_path")
        ]
    )
    await PromptNormalizer().convert_values_async(converter_configurations=configurations, message=response)
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))
    assert [piece.converted_value_data_type for piece in response.message_pieces] == ["text"]
    assert len(scores) == 1
    assert scores[0].get_value() is True

    atomic = _atomic(target=target, scorer=scorer, response_converter_configurations=configurations)
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.COMPATIBLE


def test_scorer_accepts_conditional_response_conversion(patch_central_database):
    """Only matching output types are converted, leaving any text piece readable."""
    target = get_mock_target(output_modalities=[{"text", "audio_path"}])
    configuration = ConverterConfiguration(
        converters=[_OfflineAudioToTextConverter()], prompt_data_types_to_apply=["audio_path"]
    )
    scorer = SubStringScorer(
        substring="matched",
        validator=ScorerPromptValidator(supported_data_types=["text"], enforce_all_pieces_valid=True),
    )
    atomic = _atomic(target=target, scorer=scorer, response_converter_configurations=[configuration])
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.COMPATIBLE


def test_scorer_accepts_indexed_response_conversion_is_unknown(patch_central_database):
    """Without a response piece count, index 0 may or may not cover every audio piece."""
    target = get_mock_target(output_modalities=[{"audio_path"}])
    configuration = ConverterConfiguration(converters=[_OfflineAudioToTextConverter()], indexes_to_apply=[0])
    scorer = SubStringScorer(substring="matched")
    atomic = _atomic(target=target, scorer=scorer, response_converter_configurations=[configuration])
    verdict, _ = scorer_accepts(scorer=scorer, target=target, response_converters=[configuration])
    assert verdict is ModalityVerdict.UNKNOWN
    assert validate_atomic_attack(atomic_attack=atomic).verdict is not ModalityVerdict.INCOMPATIBLE


def test_scorer_accepts_alternative_response_converter_outputs(patch_central_database):
    target = get_mock_target(output_modalities=[{"text"}])
    scorer = SubStringScorer(
        substring="matched",
        validator=ScorerPromptValidator(supported_data_types=["text"], enforce_all_pieces_valid=True),
    )
    verdict, reason = scorer_accepts(
        scorer=scorer, target=target, response_converters=_configs(_TextOrImageConverter())
    )
    assert verdict is ModalityVerdict.UNKNOWN
    assert reason is None


def test_scorer_accepts_none_scorer_is_unknown():
    """No scorer means nothing to check."""
    target = get_mock_target(output_modalities=[{"image_path"}])
    assert scorer_accepts(scorer=None, target=target)[0] is ModalityVerdict.UNKNOWN


def test_scorer_accepts_undeclared_scorer_is_unknown():
    """An undeclared scorer is indeterminate rather than assumed compatible."""
    target = get_mock_target(output_modalities=[{"image_path"}])
    assert scorer_accepts(scorer=_scorer_declaring(None), target=target)[0] is ModalityVerdict.UNKNOWN


def test_scorer_accepts_unknown_target_outputs_is_unknown():
    """A target with no real capabilities makes the response chain indeterminate."""
    assert scorer_accepts(scorer=_scorer_declaring(["text"]), target=get_mock_target())[0] is ModalityVerdict.UNKNOWN


# ---------------------------------------------------------------------------
# Policy and error surface
# ---------------------------------------------------------------------------
def test_modality_policy_mirrors_scorer_override_policy_values():
    """The policy reuses the SKIP/WARN/RAISE vocabulary already established by ScorerOverridePolicy."""
    assert {policy.value for policy in ModalityPolicy} == {"skip", "warn", "raise"}


def test_modality_validation_error_is_a_value_error():
    """Subclassing ValueError keeps existing ``except ValueError`` handlers working."""
    assert issubclass(ModalityValidationError, ValueError)


# ---------------------------------------------------------------------------
# validate_atomic_attack — one verdict per built attack
# ---------------------------------------------------------------------------
def test_validate_atomic_attack_text_chain_is_compatible(patch_central_database):
    """A text seed with no converters reaching a text target is compatible."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, scorer=_scorer_declaring(["text"])))
    assert report.verdict is ModalityVerdict.COMPATIBLE
    assert report.reasons == ()


def test_validate_atomic_attack_image_converter_into_text_target_is_incompatible(patch_central_database):
    """The canonical case: a converter emits an image and the target is text-only."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, converters=[QRCodeConverter()]))
    assert report.verdict is ModalityVerdict.INCOMPATIBLE
    assert any("image_path" in reason for reason in report.reasons)


def test_validate_atomic_attack_image_converter_into_vision_target_is_compatible(patch_central_database):
    """The same chain against a capable target is compatible."""
    target = get_mock_target(input_modalities=VISION_INPUT_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, converters=[QRCodeConverter()]))
    assert report.verdict is ModalityVerdict.COMPATIBLE


@pytest.mark.parametrize("output_type", ["text", "image_path"])
async def test_validate_atomic_attack_alternative_converter_matches_runtime(
    patch_central_database, output_type: PromptDataType
):
    """Each run produces one supported message type, not a synthetic mixed request."""
    target = get_mock_target(input_modalities=[{"text"}, {"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    converter = _TextOrImageConverter(output_type=output_type)
    atomic = _atomic(target=target, converters=[converter])
    response = Message.from_prompt(prompt="request", role="user")
    await PromptNormalizer().convert_values_async(converter_configurations=_configs(converter), message=response)
    actual = {piece.converted_value_data_type for piece in response.message_pieces}
    assert actual == {output_type}
    assert target_accepts(target=target, request_types=actual) is ModalityVerdict.COMPATIBLE
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.verdict is ModalityVerdict.COMPATIBLE
    assert report.projected_request_types == frozenset({"text", "image_path"})


def test_validate_atomic_attack_some_alternatives_are_incompatible(patch_central_database):
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, converters=[_TextOrImageConverter()]))
    assert report.verdict is ModalityVerdict.UNKNOWN


def test_validate_atomic_attack_no_alternatives_are_compatible(patch_central_database):
    target = get_mock_target(input_modalities=[{"audio_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, converters=[_TextOrImageConverter()]))
    assert report.verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_alternative_converter_does_not_invent_mixed_message(patch_central_database):
    target = get_mock_target(input_modalities=[{"text", "image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, converters=[_TextOrImageConverter()]))
    assert report.verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_many_alternatives_are_unknown(patch_central_database):
    """Bounded projection does not turn an explosion of possibilities into a false rejection."""
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=TEXT_ONLY_MODALITIES)
    group = AttackSeedGroup(seeds=[SeedObjective(value="objective"), *(SeedPrompt(value=str(i)) for i in range(9))])
    atomic = _atomic(target=target, converters=[_TextOrImageConverter()], seed_groups=[group])
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.UNKNOWN


def test_validate_atomic_attack_fully_selected_index_reaches_image_only_target(patch_central_database):
    """A one-piece text request converted at index zero no longer contains text."""
    target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    config = ConverterConfiguration(converters=[QRCodeConverter()], indexes_to_apply=[0])
    atomic = _atomic(target=target, converter_configurations=[config])
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset({"image_path"})
    assert report.verdict is ModalityVerdict.COMPATIBLE


def test_validate_atomic_attack_partially_selected_index_retains_text(patch_central_database):
    """Two text pieces with only the first converted form a text-and-image message."""
    target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    config = ConverterConfiguration(converters=[QRCodeConverter()], indexes_to_apply=[0])
    seed_group = AttackSeedGroup(
        seeds=[
            SeedObjective(value="objective"),
            SeedPrompt(value="first", data_type="text"),
            SeedPrompt(value="second", data_type="text"),
        ]
    )
    atomic = _atomic(target=target, converter_configurations=[config], seed_groups=[seed_group])
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset({"text", "image_path"})
    assert report.verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_converter_break_names_the_converter(patch_central_database):
    """A media seed hitting a text-only converter reports the converter, not the target."""
    target = get_mock_target(input_modalities=VISION_INPUT_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    atomic = _atomic(target=target, converters=[Base64Converter()], seed_groups=[_media_seed_group()])
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.verdict is ModalityVerdict.INCOMPATIBLE
    assert any("Base64Converter" in reason for reason in report.reasons)


def test_validate_atomic_attack_media_seed_reaches_capable_target(patch_central_database):
    """An image seed with no converters is compatible with a target that accepts images."""
    target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    atomic = _atomic(target=target, seed_groups=[_media_seed_group()])
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.COMPATIBLE


@pytest.mark.parametrize("override_type", get_args(PromptDataType))
def test_validate_atomic_attack_uses_constructor_message_override_for_every_type(
    patch_central_database, override_type: PromptDataType
):
    """The effective message type, not the unused seed's type, reaches the target."""
    seed_type: PromptDataType = "text" if override_type == "image_path" else "image_path"
    target = get_mock_target(input_modalities=[{override_type}], output_modalities=TEXT_ONLY_MODALITIES)
    override = Message(
        message_pieces=[MessagePiece(role="user", original_value="override", original_value_data_type=override_type)]
    )
    atomic = AtomicAttack(
        atomic_attack_name="override",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=[_media_seed_group(data_type=seed_type)],
        next_message=override,
    )

    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset({override_type})
    assert report.verdict is ModalityVerdict.COMPATIBLE


@pytest.mark.parametrize("override", [None, Message.from_prompt(prompt="override", role="user")])
def test_validate_atomic_attack_none_or_text_override_replaces_media_seed(
    patch_central_database, override: Message | None
):
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=TEXT_ONLY_MODALITIES)
    atomic = AtomicAttack(
        atomic_attack_name="override",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=[_media_seed_group()],
        next_message=override,
    )
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset({"text"})
    assert report.verdict is ModalityVerdict.COMPATIBLE


def test_validate_atomic_attack_without_override_uses_media_seed(patch_central_database):
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=TEXT_ONLY_MODALITIES)
    atomic = _atomic(target=target, seed_groups=[_media_seed_group()])
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_incompatible_override_does_not_use_compatible_seed(patch_central_database):
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=TEXT_ONLY_MODALITIES)
    override = Message(
        message_pieces=[MessagePiece(role="user", original_value="image.png", original_value_data_type="image_path")]
    )
    atomic = AtomicAttack(
        atomic_attack_name="image_override",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=[_text_seed_group()],
        next_message=override,
    )
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset({"image_path"})
    assert report.verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_unmodelable_override_is_unknown(patch_central_database):
    """The validator must not reject an unused seed when it cannot model its replacement."""
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=TEXT_ONLY_MODALITIES)
    atomic = AtomicAttack(
        atomic_attack_name="opaque_override",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=[_media_seed_group()],
        next_message=object(),
    )
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset()
    assert report.verdict is ModalityVerdict.UNKNOWN


@pytest.mark.parametrize(
    ("override", "expected_sent"),
    [
        (Message.from_prompt(prompt="actual text request", role="user"), "actual text request"),
        (None, "a media objective"),
    ],
)
async def test_validate_atomic_attack_override_matches_actual_text_request(
    patch_central_database, override: Message | None, expected_sent: str
):
    """The target sees only the override; the original image path need not exist."""
    target = MockPromptTarget()
    target.apply_capabilities(
        capabilities=TargetCapabilities(
            input_modalities=frozenset({frozenset({"text"})}),
            output_modalities=frozenset({frozenset({"text"})}),
        )
    )
    atomic = AtomicAttack(
        atomic_attack_name="override",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=[_media_seed_group(value="nonexistent-seed.png")],
        next_message=override,
    )
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset({"text"})
    assert report.verdict is ModalityVerdict.COMPATIBLE

    result = await atomic.run_async()
    assert len(result.completed_results) == 1
    assert not result.incomplete_objectives
    assert target.prompt_sent == [expected_sent]


def test_validate_atomic_attack_projects_request_converters_after_override(patch_central_database):
    """A converter runs on the override pieces, never on the unused media seed."""
    target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    attack = PromptSendingAttack(
        objective_target=target,
        attack_converter_config=AttackConverterConfig(request_converters=_configs(QRCodeConverter())),
    )
    atomic = AtomicAttack(
        atomic_attack_name="converted_override",
        attack_technique=AttackTechnique(attack=attack),
        seed_groups=[_media_seed_group(data_type="audio_path")],
        next_message=Message.from_prompt(prompt="text to convert", role="user"),
    )
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.projected_request_types == frozenset({"image_path"})
    assert report.verdict is ModalityVerdict.COMPATIBLE


def test_validate_atomic_attack_uses_converted_override_piece_type(patch_central_database):
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=TEXT_ONLY_MODALITIES)
    override = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="original.png",
                original_value_data_type="image_path",
                converted_value="converted text",
                converted_value_data_type="text",
            )
        ]
    )
    atomic = AtomicAttack(
        atomic_attack_name="converted_override",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=[_media_seed_group()],
        next_message=override,
    )
    assert validate_atomic_attack(atomic_attack=atomic).projected_request_types == frozenset({"text"})


async def test_sequential_media_child_is_not_rejected_by_invented_text_request(patch_central_database):
    """A real sequential child owns and sends its image seed to its image-only target."""
    child_target = MockPromptTarget()
    child_target.apply_capabilities(
        capabilities=TargetCapabilities(
            input_modalities=frozenset({frozenset({"image_path"})}),
            output_modalities=frozenset({frozenset({"text"})}),
        )
    )
    nominal_target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    media_piece = await asyncio.to_thread(get_image_message_piece)
    try:
        seed_group = _media_seed_group(value=media_piece.original_value)
        child = SequentialChildAttack(
            strategy=PromptSendingAttack(objective_target=child_target),
            seed_group=seed_group,
        )
        wrapper = SequentialAttack(objective_target=nominal_target, child_attacks=[child])
        atomic = AtomicAttack(
            atomic_attack_name="sequential_image",
            attack_technique=AttackTechnique(attack=wrapper),
            seed_groups=[seed_group],
        )

        result = await wrapper.execute_async(objective=seed_group.objective.value)
        assert len(result.child_attack_results) == 1
        assert child_target.prompt_sent == [media_piece.original_value]
        report = validate_atomic_attack(atomic_attack=atomic)
        assert report.verdict is ModalityVerdict.UNKNOWN
        assert report.projected_request_types == frozenset()
    finally:
        await asyncio.to_thread(os.remove, media_piece.original_value)


def test_validate_atomic_attack_scorer_cannot_read_target_output(patch_central_database):
    """An incompatibility on the response chain fails the attack just as the request chain does."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=[{"image_path"}])
    atomic = _atomic(target=target, scorer=_scorer_declaring(["text"]))
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.verdict is ModalityVerdict.INCOMPATIBLE
    assert any("SubStringScorer" in reason for reason in report.reasons)


def test_validate_atomic_attack_unknown_target_is_unknown(patch_central_database):
    """A plain mock target yields UNKNOWN so existing scenario tests are never blocked."""
    report = validate_atomic_attack(atomic_attack=_atomic(target=get_mock_target()))
    assert report.verdict is ModalityVerdict.UNKNOWN


def test_validate_atomic_attack_without_scoring_config_still_checks_request_chain(patch_central_database):
    """A missing scoring config leaves the response chain unknown but does not mask a request failure."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, converters=[QRCodeConverter()]))
    assert report.verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_report_carries_attack_name(patch_central_database):
    """The report identifies which atomic attack it describes, for the policy's log and error."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES)
    report = validate_atomic_attack(atomic_attack=_atomic(target=target, name="qr_image_hate"))
    assert report.atomic_attack_name == "qr_image_hate"


def test_validate_atomic_attack_incompatible_if_any_seed_group_fails(patch_central_database):
    """Seed groups are projected independently; one failure condemns the attack."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    atomic = _atomic(target=target, seed_groups=[_text_seed_group(), _media_seed_group()])
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_seed_groups_are_not_unioned(patch_central_database):
    """
    Two seed groups of different types are separate requests, not one multimodal request.

    A target accepting text alone or an image alone must accept both groups; unioning their
    types would wrongly demand a single combo containing both.
    """
    target = get_mock_target(input_modalities=[{"text"}, {"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    atomic = _atomic(target=target, seed_groups=[_text_seed_group(), _media_seed_group()])
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.COMPATIBLE


def test_validate_atomic_attack_merges_technique_seed_group(patch_central_database):
    """
    A technique seed group merges into the seed group, so its pieces join the projection.

    This is the mechanism behind template techniques such as the four-panel jailbreak: the
    merged request carries the template text alongside the seed's own media.
    """
    target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    technique = AttackTechniqueSeedGroup(
        seeds=[SeedPrompt(value="a template", data_type="text", is_general_technique=True)]
    )
    atomic = _atomic(target=target, seed_groups=[_media_seed_group()], seed_technique=technique)
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.verdict is ModalityVerdict.INCOMPATIBLE


def test_validate_atomic_attack_attack_excluding_next_message_starts_from_text(patch_central_database):
    """
    Attacks whose params type excludes ``next_message`` build turn 0 from the objective text.

    Their media seeds never reach the target on the first turn, so the projection starts at text.
    """
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    atomic = _atomic(
        target=target,
        seed_groups=[_media_seed_group()],
        params_type=AttackParameters.excluding("next_message"),
    )
    assert validate_atomic_attack(atomic_attack=atomic).verdict is ModalityVerdict.COMPATIBLE
