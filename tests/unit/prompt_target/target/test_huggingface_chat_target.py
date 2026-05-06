# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from asyncio import Task
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import (
    Message,
    MessagePiece,
)
from pyrit.prompt_target import HuggingFaceChatTarget


def is_torch_installed():
    try:
        import torch  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


# Fixture to mock get_required_value
@pytest.fixture(autouse=True)
def mock_get_required_value(request):
    if request.node.name != "test_init_with_no_token_var_raises":
        with patch(
            "pyrit.prompt_target.hugging_face.hugging_face_chat_target.default_values.get_required_value",
            return_value="dummy_token",
        ):
            yield
    else:
        # Do not apply the mock for this test
        yield


# Fixture to mock os.path.exists to prevent file system access
@pytest.fixture(autouse=True)
def mock_os_path_exists():
    with patch("os.path.exists", return_value=True):
        yield


# Mock torch.cuda.is_available to prevent CUDA-related errors during testing
@pytest.fixture(autouse=True)
def mock_torch_cuda_is_available():
    with patch("torch.cuda.is_available", return_value=False):
        yield


# Mock the AutoTokenizer and AutoModelForCausalLM to prevent actual model loading
@pytest.fixture(autouse=True)
def mock_transformers():
    with patch("transformers.AutoTokenizer.from_pretrained") as mock_tokenizer_from_pretrained:
        mock_tokenizer = MagicMock()
        mock_tokenizer.chat_template = MagicMock()
        tokenized_chat_mock = MagicMock()
        tokenized_chat_mock.to.return_value = tokenized_chat_mock

        mock_tokenizer.apply_chat_template.return_value = tokenized_chat_mock
        mock_tokenizer.decode.return_value = "Assistant's response"
        mock_tokenizer_from_pretrained.return_value = mock_tokenizer

        with patch("transformers.AutoModelForCausalLM.from_pretrained") as mock_model_from_pretrained:
            mock_model = MagicMock()
            mock_model.generate.return_value = [[101, 102, 103]]
            mock_model_from_pretrained.return_value = mock_model

            yield mock_tokenizer_from_pretrained, mock_model_from_pretrained


# Mock PretrainedConfig.from_pretrained to prevent actual configuration loading
@pytest.fixture(autouse=True)
def mock_pretrained_config():
    with patch("transformers.PretrainedConfig.from_pretrained", return_value=MagicMock()):
        yield


class AwaitableTask(AsyncMock):
    """Mock that can be awaited and acts like an asyncio.Task"""

    def __await__(self):
        # Return a completed future-like object
        async def _await():
            return None

        return _await().__await__()


@pytest.fixture(autouse=True)
def mock_create_task():
    with patch("asyncio.create_task") as mock_task:
        # Return an AwaitableTask that can be awaited
        mock_task.return_value = AwaitableTask(spec=Task)
        yield mock_task


@pytest.fixture(autouse=True)
def mock_download_specific_files():
    with patch(
        "pyrit.prompt_target.hugging_face.hugging_face_chat_target.download_specific_files", new_callable=AsyncMock
    ) as mock:
        yield mock


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_init_with_no_token_var_raises(monkeypatch, patch_central_database):
    # Ensure the environment variable is unset
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)

    with pytest.raises(ValueError) as excinfo:
        HuggingFaceChatTarget(model_id="test_model", use_cuda=False, hf_access_token=None)

    assert "Environment variable HUGGINGFACE_TOKEN is required" in str(excinfo.value)


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_hf_initialization(patch_central_database, mock_download_specific_files):
    # Test the initialization without loading the actual models
    hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
    assert hf_chat.model_id == "test_model"
    assert not hf_chat.use_cuda
    assert hf_chat.device == "cpu"

    await hf_chat.load_model_and_tokenizer()
    assert hf_chat.model is not None
    assert hf_chat.tokenizer is not None
    mock_download_specific_files.assert_awaited_once()


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_is_model_id_valid_true():
    # Simulate valid model ID
    hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
    # Await the background task to prevent warnings
    await hf_chat.load_model_and_tokenizer_task
    assert hf_chat.is_model_id_valid()


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_is_model_id_valid_false():
    # Simulate invalid model ID by causing an exception
    with patch("transformers.PretrainedConfig.from_pretrained", side_effect=Exception("Invalid model")):
        hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
        # Await the background task to prevent warnings
        await hf_chat.load_model_and_tokenizer_task
        assert not hf_chat.is_model_id_valid()


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_load_model_and_tokenizer():
    hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
    await hf_chat.load_model_and_tokenizer()
    assert hf_chat.model is not None
    assert hf_chat.tokenizer is not None


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async():
    hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
    await hf_chat.load_model_and_tokenizer()

    message_piece = MessagePiece(
        role="user",
        original_value="Hello, how are you?",
        converted_value="Hello, how are you?",
        converted_value_data_type="text",
    )
    message = Message(message_pieces=[message_piece])

    # Use await to handle the asynchronous call
    response = await hf_chat.send_prompt_async(message=message)  # type: ignore[arg-type]

    # Access the response text via message_pieces
    assert len(response) == 1
    assert response[0].message_pieces[0].original_value == "Assistant's response"


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
@pytest.mark.usefixtures("patch_central_database")
async def test_missing_chat_template_error():
    hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
    await hf_chat.load_model_and_tokenizer()
    hf_chat.tokenizer.chat_template = None

    message_piece = MessagePiece(
        role="user",
        original_value="Hello, how are you?",
        converted_value="Hello, how are you?",
        converted_value_data_type="text",
    )
    message = Message(message_pieces=[message_piece])

    with pytest.raises(ValueError) as excinfo:
        # Use await to handle the asynchronous call
        await hf_chat.send_prompt_async(message=message)

    assert "Tokenizer does not have a chat template" in str(excinfo.value)


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_invalid_prompt_request_validation():
    hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
    # Await the background task to prevent warnings
    await hf_chat.load_model_and_tokenizer_task

    # Create an invalid message with multiple message pieces
    message_piece1 = MessagePiece(
        role="user",
        original_value="First piece",
        converted_value="First piece",
        converted_value_data_type="text",
        conversation_id="123",
    )
    message_piece2 = MessagePiece(
        role="user",
        original_value="Second piece",
        converted_value="Second piece",
        converted_value_data_type="text",
        conversation_id="123",
    )
    message = Message(message_pieces=[message_piece1, message_piece2])

    with pytest.raises(ValueError) as excinfo:
        hf_chat._validate_request(normalized_conversation=[message])

    assert "This target only supports a single message piece." in str(excinfo.value)


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_load_with_missing_files():
    hf_chat = HuggingFaceChatTarget(model_id="test_model", use_cuda=False, necessary_files=["file1", "file2"])
    await hf_chat.load_model_and_tokenizer()

    assert hf_chat.model is not None
    assert hf_chat.tokenizer is not None


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_enable_disable_cache():
    # Test enabling cache
    HuggingFaceChatTarget.enable_cache()
    assert HuggingFaceChatTarget._cache_enabled

    # Test disabling cache
    HuggingFaceChatTarget.disable_cache()
    assert not HuggingFaceChatTarget._cache_enabled
    assert HuggingFaceChatTarget._cached_model is None
    assert HuggingFaceChatTarget._cached_tokenizer is None
    assert HuggingFaceChatTarget._cached_model_id is None


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_load_model_with_model_path():
    """Test loading a model from a local directory (`model_path`)."""
    model_path = "./mock_local_model_path"
    hf_chat = HuggingFaceChatTarget(model_path=model_path, use_cuda=False, trust_remote_code=False)
    await hf_chat.load_model_and_tokenizer()
    assert hf_chat.model is not None
    assert hf_chat.tokenizer is not None


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_load_model_with_trust_remote_code():
    """Test loading a remote model requiring `trust_remote_code=True`."""
    model_id = "mock_remote_model"
    hf_chat = HuggingFaceChatTarget(model_id=model_id, use_cuda=False, trust_remote_code=True)
    await hf_chat.load_model_and_tokenizer()
    assert hf_chat.model is not None
    assert hf_chat.tokenizer is not None


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_init_with_both_model_id_and_model_path_raises():
    """Ensure providing both `model_id` and `model_path` raises an error."""
    with pytest.raises(ValueError) as excinfo:
        HuggingFaceChatTarget(model_id="test_model", model_path="./mock_local_model_path", use_cuda=False)
    assert "Provide only one of `model_id` or `model_path`, not both." in str(excinfo.value)


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_load_model_without_model_id_or_path():
    """Ensure initializing without `model_id` or `model_path` raises an error."""
    with pytest.raises(ValueError) as excinfo:
        HuggingFaceChatTarget(use_cuda=False)
    assert "Either `model_id` or `model_path` must be provided." in str(excinfo.value)


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_optional_kwargs_args_passed_when_loading_model(mock_transformers):
    """Test loading a model from a local directory (`model_path`) with optional keyword arguments."""
    mock_tokenizer_from_pretrained, mock_model_from_pretrained = mock_transformers
    hf_chat = HuggingFaceChatTarget(
        model_path="./mock_local_model_path",
        use_cuda=False,
        device_map="auto",
        torch_dtype="float16",
        attn_implementation="flash_attention_2",
    )
    await hf_chat.load_model_and_tokenizer()
    # Assert that from_pretrained was called with expected kwargs
    assert mock_model_from_pretrained.called
    call_args = mock_model_from_pretrained.call_args[1]  # Get the kwargs of the most recent call
    assert call_args.get("device_map") == "auto"
    assert call_args.get("torch_dtype") == "float16"
    assert call_args.get("attn_implementation") == "flash_attention_2"


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
async def test_hugging_face_chat_sets_endpoint_and_rate_limit(patch_central_database):
    target = HuggingFaceChatTarget(
        model_id="test_model",
        use_cuda=False,
        max_requests_per_minute=30,
    )
    # Await the background task to prevent warnings
    await target.load_model_and_tokenizer_task
    identifier = target.get_identifier()
    # HuggingFaceChatTarget doesn't set an endpoint (it's local), so it should be empty
    assert not identifier.params.get("endpoint")
    assert target._max_requests_per_minute == 30


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_identifier_includes_generation_params():
    """New generation params (top_k, do_sample, repetition_penalty, random_seed) appear in the identifier."""
    target = HuggingFaceChatTarget(
        model_id="test_model",
        use_cuda=False,
        top_k=40,
        do_sample=True,
        repetition_penalty=1.2,
        random_seed=42,
        temperature=0.7,
    )
    identifier = target.get_identifier()
    assert identifier.params["top_k"] == 40
    assert identifier.params["do_sample"] is True
    assert identifier.params["repetition_penalty"] == 1.2
    assert identifier.params["random_seed"] == 42
    assert identifier.params["temperature"] == 0.7


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_identifier_excludes_none_generation_params():
    """None-valued generation params are excluded from the identifier (backward compatibility)."""
    target = HuggingFaceChatTarget(
        model_id="test_model",
        use_cuda=False,
    )
    identifier = target.get_identifier()
    assert "top_k" not in identifier.params
    assert "do_sample" not in identifier.params
    assert "repetition_penalty" not in identifier.params
    assert "random_seed" not in identifier.params


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_generate_passes_new_params():
    """Verify top_k, do_sample, repetition_penalty are forwarded to model.generate()."""
    target = HuggingFaceChatTarget(
        model_id="test_model",
        use_cuda=False,
        top_k=40,
        do_sample=True,
        repetition_penalty=1.2,
    )
    await target.load_model_and_tokenizer()

    message_piece = MessagePiece(
        role="user",
        original_value="test prompt",
        converted_value="test prompt",
        converted_value_data_type="text",
    )
    message = Message(message_pieces=[message_piece])
    await target.send_prompt_async(message=message)

    call_kwargs = target.model.generate.call_args[1]
    assert call_kwargs["top_k"] == 40
    assert call_kwargs["do_sample"] is True
    assert call_kwargs["repetition_penalty"] == 1.2


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_generate_omits_none_params():
    """When optional params are None, they should not be passed to model.generate()."""
    target = HuggingFaceChatTarget(
        model_id="test_model",
        use_cuda=False,
    )
    await target.load_model_and_tokenizer()

    message_piece = MessagePiece(
        role="user",
        original_value="test prompt",
        converted_value="test prompt",
        converted_value_data_type="text",
    )
    message = Message(message_pieces=[message_piece])
    await target.send_prompt_async(message=message)

    call_kwargs = target.model.generate.call_args[1]
    assert "top_k" not in call_kwargs
    assert "do_sample" not in call_kwargs
    assert "repetition_penalty" not in call_kwargs


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_random_seed_calls_manual_seed_at_init():
    """When random_seed is set, torch.manual_seed is called during construction."""
    with patch("torch.manual_seed") as mock_manual_seed:
        HuggingFaceChatTarget(
            model_id="test_model",
            use_cuda=False,
            random_seed=42,
        )
        mock_manual_seed.assert_called_once_with(42)


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_no_random_seed_does_not_call_manual_seed():
    """When random_seed is None, torch.manual_seed is not called."""
    with patch("torch.manual_seed") as mock_manual_seed:
        HuggingFaceChatTarget(
            model_id="test_model",
            use_cuda=False,
        )
        mock_manual_seed.assert_not_called()


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_set_random_seed_reseeds_rng():
    """Calling set_random_seed updates the seed and immediately re-seeds the RNG."""
    target = HuggingFaceChatTarget(
        model_id="test_model",
        use_cuda=False,
    )
    with patch("torch.manual_seed") as mock_manual_seed:
        target.set_random_seed(99)
        mock_manual_seed.assert_called_once_with(99)
    assert target._random_seed == 99


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_sampling_params_without_do_sample_warns():
    """Setting temperature != 1.0 without do_sample=True emits a warning."""
    with pytest.warns(UserWarning, match="do_sample is not True"):
        HuggingFaceChatTarget(
            model_id="test_model",
            use_cuda=False,
            temperature=0.7,
        )


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_sampling_params_with_do_sample_no_warning():
    """Setting temperature != 1.0 with do_sample=True does not warn."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        HuggingFaceChatTarget(
            model_id="test_model",
            use_cuda=False,
            temperature=0.7,
            do_sample=True,
        )


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
def test_default_params_no_warning():
    """Default parameters (temperature=1.0, top_p=1.0) do not trigger warning."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        HuggingFaceChatTarget(
            model_id="test_model",
            use_cuda=False,
        )


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_full_conversation_sent_to_chat_template():
    """Verify system and user messages from the full conversation are sent to the chat template."""
    target = HuggingFaceChatTarget(model_id="test_model", use_cuda=False)
    await target.load_model_and_tokenizer()

    system_piece = MessagePiece(
        role="system",
        original_value="You are a helpful assistant.",
        converted_value="You are a helpful assistant.",
        converted_value_data_type="text",
        conversation_id="conv1",
        sequence=0,
    )
    user_piece = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        converted_value_data_type="text",
        conversation_id="conv1",
        sequence=1,
    )
    system_msg = Message(message_pieces=[system_piece])
    user_msg = Message(message_pieces=[user_piece])

    with patch.object(target, "_apply_chat_template", wraps=target._apply_chat_template) as mock_template:
        await target._send_prompt_to_target_async(normalized_conversation=[system_msg, user_msg])

        call_args = mock_template.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert call_args[1] == {"role": "user", "content": "Hello"}


@pytest.mark.skipif(not is_torch_installed(), reason="torch is not installed")
@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_effective_generation_config_in_metadata():
    """Verify effective generation config is stored in response prompt_metadata."""
    target = HuggingFaceChatTarget(
        model_id="test_model",
        use_cuda=False,
        top_k=40,
        do_sample=True,
        random_seed=42,
    )
    await target.load_model_and_tokenizer()

    # Mock generation_config on the model
    mock_gen_config = MagicMock()
    mock_gen_config.to_dict.return_value = {"eos_token_id": 2, "bos_token_id": 1}
    target.model.generation_config = mock_gen_config

    message_piece = MessagePiece(
        role="user",
        original_value="test",
        converted_value="test",
        converted_value_data_type="text",
    )
    message = Message(message_pieces=[message_piece])

    response = await target.send_prompt_async(message=message)
    metadata = response[0].message_pieces[0].prompt_metadata
    effective_config = json.loads(metadata["effective_generation_config"])

    assert effective_config["top_k"] == 40
    assert effective_config["do_sample"] is True
    assert effective_config["random_seed"] == 42
    assert effective_config["temperature"] == 1.0
    # Model defaults should also be present
    assert effective_config["eos_token_id"] == 2
