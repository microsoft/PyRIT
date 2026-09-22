# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the SeedSimulatedConversation class."""

import json
import uuid

import pytest

from pyrit.models.seeds import (
    SeedPrompt,
    SeedSimulatedConversation,
)


class TestSeedSimulatedConversationInit:
    """Tests for SeedSimulatedConversation initialization."""

    def test_init_with_all_parameters(self, tmp_path):
        """Test initialization with all parameters."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")
        sim_path = tmp_path / "simulated.yaml"
        sim_path.write_text("value: test\ndata_type: text\nparameters:\n  - objective\n  - num_turns")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
            simulated_target_system_prompt_path=sim_path,
        )

        assert conv.num_turns == 5
        assert conv.adversarial_chat_system_prompt.value == "test"
        assert conv.simulated_target_system_prompt.value == "test"
        assert conv.data_type == "text"
        assert isinstance(conv.id, uuid.UUID)

    def test_init_with_minimal_parameters(self, tmp_path):
        """Test initialization with only required parameters."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.num_turns == 3  # default
        assert conv.adversarial_chat_system_prompt.value == "test"
        # The simulated target defaults to the compliant prompt
        assert conv.simulated_target_system_prompt.name == "simulated_target_compliant"

    def test_init_default_num_turns(self, tmp_path):
        """Test that default num_turns is 3."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.num_turns == 3

    def test_init_invalid_num_turns_zero_raises_error(self, tmp_path):
        """Test that num_turns=0 raises ValueError."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path=adv_path,
                num_turns=0,
            )

    def test_init_invalid_num_turns_negative_raises_error(self, tmp_path):
        """Test that negative num_turns raises ValueError."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path=adv_path,
                num_turns=-1,
            )

    def test_init_sets_data_type_to_text(self, tmp_path):
        """Test that data_type is always set to 'text'."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.data_type == "text"

    def test_init_generates_json_value(self, tmp_path):
        """Test that value is set to a JSON serialization of config."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
        )

        value = json.loads(conv.value)
        assert value["num_turns"] == 5
        assert value["adversarial_chat_system_prompt"]["value"] == "test"
        assert "pyrit_version" in value

    def test_init_value_is_deterministic(self, tmp_path):
        """Test that the same config produces the same value."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv1 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        conv2 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )

        assert conv1.value == conv2.value

    def test_init_default_sequence_is_zero(self, tmp_path):
        """Test that default sequence is 0."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.sequence == 0

    def test_init_custom_sequence(self, tmp_path):
        """Test that sequence can be set to a custom value."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            sequence=5,
        )

        assert conv.sequence == 5

    def test_init_default_next_message_system_prompt_is_none(self, tmp_path):
        """Test that the next message system prompt defaults to None."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.next_message_system_prompt is None

    def test_init_next_message_system_prompt_set(self, tmp_path):
        """Test that the next message system prompt can be set."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")
        next_msg_path = tmp_path / "next_message.yaml"
        next_msg_path.write_text("value: test\ndata_type: text\nparameters:\n  - objective\n  - conversation_context")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            next_message_system_prompt_path=next_msg_path,
        )

        assert conv.next_message_system_prompt.value == "test"


class TestSeedSimulatedConversationFromMapping:
    """Tests for constructing SeedSimulatedConversation from a dict via ``model_validate``."""

    def test_from_dict_with_paths(self, tmp_path):
        """Test construction from a dict with path values."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        data = {
            "num_turns": 5,
            "adversarial_chat_system_prompt_path": str(adv_path),
        }
        conv = SeedSimulatedConversation.model_validate(data)

        assert conv.num_turns == 5
        assert conv.adversarial_chat_system_prompt.value == "test"

    def test_from_dict_without_simulated_target_path(self, tmp_path):
        """Test construction without simulated_target_system_prompt_path uses compliant default."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        data = {
            "num_turns": 3,
            "adversarial_chat_system_prompt_path": str(adv_path),
        }
        conv = SeedSimulatedConversation.model_validate(data)

        # The simulated target defaults to the compliant prompt
        assert conv.simulated_target_system_prompt.name == "simulated_target_compliant"

    def test_from_dict_default_num_turns(self, tmp_path):
        """Test that num_turns defaults to 3 when not specified."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        data = {
            "adversarial_chat_system_prompt_path": str(adv_path),
        }
        conv = SeedSimulatedConversation.model_validate(data)

        assert conv.num_turns == 3

    def test_from_dict_missing_adversarial_path_raises_error(self):
        """Test that construction raises when adversarial path is missing (required field)."""
        data = {"num_turns": 3}

        with pytest.raises(ValueError, match="adversarial_chat_system_prompt"):
            SeedSimulatedConversation.model_validate(data)


class TestSeedSimulatedConversationGetIdentifier:
    """Tests for SeedSimulatedConversation.get_identifier method."""

    def test_get_identifier_returns_correct_structure(self, tmp_path):
        """Test that get_identifier returns the expected structure."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        identifier = conv.get_identifier()

        assert identifier["__type__"] == "SeedSimulatedConversation"
        assert identifier["num_turns"] == 3
        assert identifier["adversarial_chat_system_prompt"]["value"] == "test"
        assert "pyrit_version" in identifier


class TestSeedSimulatedConversationComputeHash:
    """Tests for SeedSimulatedConversation.compute_hash method."""

    def test_compute_hash_returns_sha256(self, tmp_path):
        """Test that compute_hash returns a valid SHA256 hash."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )
        hash_value = conv.compute_hash()

        # SHA256 hash is 64 hex characters
        assert len(hash_value) == 64
        assert all(c in "0123456789abcdef" for c in hash_value)

    def test_compute_hash_is_deterministic(self, tmp_path):
        """Test that the same config produces the same hash."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv1 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        conv2 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )

        assert conv1.compute_hash() == conv2.compute_hash()

    def test_compute_hash_differs_for_different_num_turns(self, tmp_path):
        """Test that different num_turns produces different hash."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv1 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        conv2 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
        )

        assert conv1.compute_hash() != conv2.compute_hash()


class TestSeedSimulatedConversationRepr:
    """Tests for SeedSimulatedConversation.__repr__ method."""

    def test_repr_shows_num_turns_and_prompt_name(self, tmp_path):
        """Test __repr__ shows key information."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("name: my_adversarial\nvalue: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
        )
        repr_str = repr(conv)

        assert "SeedSimulatedConversation" in repr_str
        assert "num_turns=5" in repr_str
        assert "my_adversarial" in repr_str

    def test_repr_omits_prompt_name_when_unnamed(self):
        """An unnamed adversarial prompt drops the fragment rather than printing a placeholder."""
        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="test", data_type="text"),
            num_turns=5,
        )
        repr_str = repr(conv)

        assert "num_turns=5" in repr_str
        assert "adversarial_prompt" not in repr_str
        assert "None" not in repr_str


class TestSeedSimulatedConversationLoadSimulatedTargetSystemPrompt:
    """Tests for SeedSimulatedConversation.load_simulated_target_system_prompt static method."""

    def test_load_simulated_target_system_prompt_renders_template(self, tmp_path):
        """Test that load_simulated_target_system_prompt renders the template."""
        sim_path = tmp_path / "simulated.yaml"
        sim_path.write_text(
            "value: 'Objective: {{ objective }} Turns: {{ num_turns }}'\n"
            "data_type: text\n"
            "parameters:\n"
            "  - objective\n"
            "  - num_turns"
        )

        result = SeedSimulatedConversation.load_simulated_target_system_prompt(
            objective="Test objective",
            num_turns=5,
            simulated_target_system_prompt_path=sim_path,
        )

        assert "Test objective" in result
        assert "5" in result

    def test_load_simulated_target_system_prompt_raises_for_missing_params(self, tmp_path):
        """Test that missing template params raise an error."""
        sim_path = tmp_path / "simulated.yaml"
        sim_path.write_text("value: 'No params'\ndata_type: text")

        with pytest.raises(ValueError, match="objective and num_turns"):
            SeedSimulatedConversation.load_simulated_target_system_prompt(
                objective="Test",
                num_turns=3,
                simulated_target_system_prompt_path=sim_path,
            )


class TestSeedSimulatedConversationCanonicalPrompts:
    """Tests for the canonical SeedPrompt fields and the deprecated path inputs."""

    def test_init_with_canonical_prompts(self):
        """Canonical SeedPrompt inputs populate the fields without touching disk."""
        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial", parameters=["objective"]),
            simulated_target_system_prompt=SeedPrompt(value="target", parameters=["objective", "num_turns"]),
            next_message_system_prompt=SeedPrompt(value="next", parameters=["objective", "conversation_context"]),
            num_turns=2,
        )

        assert conv.adversarial_chat_system_prompt.value == "adversarial"
        assert conv.simulated_target_system_prompt.value == "target"
        assert conv.next_message_system_prompt is not None
        assert conv.next_message_system_prompt.value == "next"

    def test_path_inputs_are_not_model_fields(self):
        """The deprecated path inputs never become fields, so they cannot reach persistence."""
        for field_name in (
            "adversarial_chat_system_prompt_path",
            "simulated_target_system_prompt_path",
            "next_message_system_prompt_path",
        ):
            assert field_name not in SeedSimulatedConversation.model_fields

    def test_deprecated_path_input_warns_and_resolves(self, tmp_path):
        """A deprecated path input warns and is resolved into the canonical prompt."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: adversarial\ndata_type: text")

        with pytest.warns(DeprecationWarning, match="adversarial_chat_system_prompt_path"):
            conv = SeedSimulatedConversation(adversarial_chat_system_prompt_path=adv_path)

        assert conv.adversarial_chat_system_prompt.value == "adversarial"
        assert not hasattr(conv, "adversarial_chat_system_prompt_path")

    def test_prompt_and_path_together_raises(self, tmp_path):
        """Supplying both a canonical prompt and its deprecated path is ambiguous."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: adversarial\ndata_type: text")

        with pytest.raises(
            ValueError, match="Set only one of SeedSimulatedConversation.adversarial_chat_system_prompt"
        ):
            SeedSimulatedConversation(
                adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
                adversarial_chat_system_prompt_path=adv_path,
            )

    def test_missing_deprecated_path_raises(self, tmp_path):
        """A deprecated path that does not exist fails loudly rather than silently."""
        with pytest.raises(FileNotFoundError):
            SeedSimulatedConversation(adversarial_chat_system_prompt_path=tmp_path / "missing.yaml")

    def test_deprecated_simulated_target_path_requires_parameters(self, tmp_path):
        """Loading a simulated target from a path keeps the declared-parameter contract."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: adversarial\ndata_type: text")
        sim_path = tmp_path / "simulated.yaml"
        sim_path.write_text("value: no params\ndata_type: text")

        with pytest.raises(ValueError, match="objective and num_turns"):
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path=adv_path,
                simulated_target_system_prompt_path=sim_path,
            )

    def test_explicit_none_simulated_target_falls_back_to_compliant(self):
        """An explicit None simulated target (as memory reconstruction sends) uses the default."""
        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
            simulated_target_system_prompt=None,
        )

        assert conv.simulated_target_system_prompt.name == "simulated_target_compliant"


class TestSeedSimulatedConversationIdentity:
    """Tests for the content-based value and hash."""

    def test_value_carries_prompt_text_not_paths(self):
        """The serialized value holds the prompt text so a technique is inspectable."""
        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial text", parameters=["objective"])
        )
        value = json.loads(conv.value)

        assert value["adversarial_chat_system_prompt"]["value"] == "adversarial text"
        assert value["adversarial_chat_system_prompt"]["parameters"] == ["objective"]
        assert not any(key.endswith("_path") for key in value)

    def test_editing_one_word_changes_identity(self):
        """Changing a word in a system prompt produces a different configuration identity."""
        original = SeedSimulatedConversation(adversarial_chat_system_prompt=SeedPrompt(value="be a screenwriter"))
        edited = SeedSimulatedConversation(adversarial_chat_system_prompt=SeedPrompt(value="be a novelist"))

        assert original.value != edited.value
        assert original.compute_hash() != edited.compute_hash()

    def test_identical_content_from_different_files_matches(self, tmp_path):
        """Two copies of the same prompt text share an identity even from different files."""
        first = tmp_path / "first.yaml"
        first.write_text("value: same text\ndata_type: text")
        second = tmp_path / "second.yaml"
        second.write_text("value: same text\ndata_type: text")

        conv1 = SeedSimulatedConversation(adversarial_chat_system_prompt_path=first)
        conv2 = SeedSimulatedConversation(adversarial_chat_system_prompt_path=second)

        assert conv1.value == conv2.value

    def test_response_json_schema_changes_identity(self):
        """Two prompts with identical text but different schemas are not interchangeable."""
        plain = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
            next_message_system_prompt=SeedPrompt(value="next"),
        )
        with_schema = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
            next_message_system_prompt=SeedPrompt(value="next", response_json_schema_name="adversarial_chat"),
        )

        assert plain.value != with_schema.value

    @pytest.mark.parametrize("mode", ["python", "json"])
    def test_value_is_stable_across_round_trip(self, mode):
        """Dumping and revalidating recomputes the same value."""
        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial", parameters=["objective"]),
            next_message_system_prompt=SeedPrompt(value="next", response_json_schema_name="adversarial_chat"),
            num_turns=2,
        )

        assert SeedSimulatedConversation.model_validate(conv.model_dump(mode=mode)).value == conv.value

    def test_sequence_range_accounts_for_next_message(self):
        """The next message adds one sequence slot after the generated turns."""
        without = SeedSimulatedConversation(adversarial_chat_system_prompt=SeedPrompt(value="adversarial"), num_turns=2)
        with_next = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
            next_message_system_prompt=SeedPrompt(value="next"),
            num_turns=2,
        )

        assert list(without.sequence_range) == [0, 1, 2, 3]
        assert list(with_next.sequence_range) == [0, 1, 2, 3, 4]


class TestSeedSimulatedConversationTemplatePreparation:
    """Tests that a prompt is prepared exactly once, so deferred template syntax survives."""

    DEFERRED_TEMPLATE = "{% raw %}{% if num_turns == 1 %}one turn{% else %}many turns{% endif %}{% endraw %}"

    @pytest.fixture
    def deferred_prompt_path(self, tmp_path):
        path = tmp_path / "deferred.yaml"
        path.write_text(
            f"data_type: text\nparameters:\n  - objective\n  - num_turns\nvalue: '{self.DEFERRED_TEMPLATE}'\n"
        )
        return path

    def test_canonical_prompt_is_not_prepared_again(self, deferred_prompt_path):
        """A prompt handed in already prepared keeps its deferred template syntax."""
        prompt = SeedPrompt.from_yaml_file(deferred_prompt_path)
        original = prompt.value

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
            simulated_target_system_prompt=prompt,
            num_turns=1,
        )

        assert prompt.value == original, "the caller's prompt must not be mutated"
        assert conv.simulated_target_system_prompt.render_template_value(objective="o", num_turns=1) == "one turn"

    def test_deprecated_path_prompt_is_not_prepared_again(self, deferred_prompt_path):
        """Loading through the deprecated path input also prepares the template only once."""
        with pytest.warns(DeprecationWarning):
            conv = SeedSimulatedConversation(
                adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
                simulated_target_system_prompt_path=deferred_prompt_path,
                num_turns=1,
            )

        assert conv.simulated_target_system_prompt.render_template_value(objective="o", num_turns=1) == "one turn"

    def test_prompt_rebuilt_from_value_is_not_prepared_again(self, deferred_prompt_path):
        """A prompt rebuilt from the serialized configuration keeps its deferred template syntax."""
        with pytest.warns(DeprecationWarning):
            conv = SeedSimulatedConversation(
                adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
                simulated_target_system_prompt_path=deferred_prompt_path,
                num_turns=1,
            )

        rebuilt = SeedPrompt(**json.loads(conv.value)["simulated_target_system_prompt"])

        assert rebuilt.render_template_value(objective="o", num_turns=1) == "one turn"
