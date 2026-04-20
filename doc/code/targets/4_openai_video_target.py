# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.2
# ---

# %% [markdown]
# # 4. OpenAI Video Target
#
# `OpenAIVideoTarget` supports three modes:
# - **Text-to-video**: Generate a video from a text prompt.
# - **Remix**: Create a variation of an existing video (using `video_id` from a prior generation).
# - **Text+Image-to-video**: Use an image as the first frame of the generated video.
#
# Note that the video scorer requires `opencv`, which is not a default PyRIT dependency. You need to install it manually or using `pip install pyrit[opencv]`.

# %% [markdown]
# ## Text-to-Video
#
# This example shows the simplest mode: generating video from text prompts, with scoring.

# %%
from pyrit.executor.attack import (
    AttackExecutor,
    AttackScoringConfig,
    ConsoleAttackResultPrinter,
    PromptSendingAttack,
)
from pyrit.prompt_target import OpenAIChatTarget, OpenAIVideoTarget
from pyrit.score import (
    AudioTrueFalseScorer,
    AzureContentFilterScorer,
    SelfAskTrueFalseScorer,
    TrueFalseQuestion,
    VideoFloatScaleScorer,
    VideoTrueFalseScorer,
)
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

video_target = OpenAIVideoTarget()

# %% [markdown]
# ## Generating and scoring a video:
#
# Using the video target you can send prompts to generate a video. The video scorer can evaluate the video content itself. Note this section is simply scoring the **video** not the audio.

# %%
objectives = [
    "Video of a raccoon pirate eating flan at a cafe in Spain",
    "Video of a raccoon pirate eating a croissant at a cafe in France",
]

objective_scorer = SelfAskTrueFalseScorer(
    chat_target=OpenAIChatTarget(),
    true_false_question=TrueFalseQuestion(true_description="A raccoon dressed as a pirate is actively eating a pastry"),
)

video_scorer = VideoTrueFalseScorer(
    image_capable_scorer=objective_scorer,
    num_sampled_frames=10,
)

attack = PromptSendingAttack(
    objective_target=video_target,
    attack_scoring_config=AttackScoringConfig(
        objective_scorer=video_scorer,
        auxiliary_scorers=[VideoFloatScaleScorer(image_capable_scorer=AzureContentFilterScorer())],
    ),
)

results = await AttackExecutor().execute_attack_async(  # type: ignore
    attack=attack,
    objectives=objectives,
)

for result in results:
    await ConsoleAttackResultPrinter().print_result_async(result=result, include_auxiliary_scores=True)  # type: ignore

# %% [markdown]
# ## Scoring video and audio **together**:
#
# The audio scorer can be added in in order to evaluate both the video content and the audio present in the generated video.
#
# *Note*: the current audio scoring will use transcription, so if the audio is not able to be transcribed this will return False

# %%
# Scorer for audio content (transcript) - checks what is HEARD/SAID in the video
objectives = [
    "Video of a raccoon pirate eating a croissant at a cafe in France who says 'Bonjour!, my name is Roakey and this is the best croissant ever!' Ensure the video contains all the audio.",
    "Video of a raccoon pirate eating a croissant at a cafe in Spain who says 'Hola a todos, my name is Roakey and I am in Spain!' Ensure the video contains all the audio.",
]

# Visual scorer - checks what is SEEN in the video frames
visual_scorer = SelfAskTrueFalseScorer(
    chat_target=OpenAIChatTarget(),
    true_false_question=TrueFalseQuestion(true_description="A raccoon dressed as a pirate is actively eating a pastry"),
)

# Audio transcript scorer - checks what is SAID in the video
audio_text_scorer = SelfAskTrueFalseScorer(
    chat_target=OpenAIChatTarget(),
    true_false_question=TrueFalseQuestion(
        true_description="Someone introduces themselves and expresses enjoyment of a croissant"
    ),
)

# BOTH the audio and visual scorers must be true for the overall score to be true (AND aggregation)
audio_and_video_scorer = VideoTrueFalseScorer(
    image_capable_scorer=visual_scorer,
    num_sampled_frames=3,
    audio_scorer=AudioTrueFalseScorer(text_capable_scorer=audio_text_scorer),
)

attack = PromptSendingAttack(
    objective_target=video_target,
    attack_scoring_config=AttackScoringConfig(
        objective_scorer=audio_and_video_scorer,
    ),
)

results = await AttackExecutor().execute_attack_async(  # type: ignore
    attack=attack,
    objectives=objectives,
)

for result in results:
    await ConsoleAttackResultPrinter().print_result_async(result=result, include_auxiliary_scores=True)  # type: ignore

# Capture video_id from the first result for use in the remix section below
video_id = results[0].last_response.prompt_metadata["video_id"]
print(f"Video ID for remix: {video_id}")

# %% [markdown]
# ## Remix (Video Variation)
#
# Remix creates a variation of an existing video. After any successful generation, the response
# includes a `video_id` in `prompt_metadata`. Pass this back via `prompt_metadata={"video_id": "<id>"}` to remix.

# %%
from pyrit.models import Message, MessagePiece

# Remix using the video_id captured from the text-to-video section above
remix_piece = MessagePiece(
    role="user",
    original_value="Make it a watercolor painting style",
    prompt_metadata={"video_id": video_id},
)
remix_result = await video_target.send_prompt_async(message=Message([remix_piece]))  # type: ignore
print(f"Remixed video: {remix_result[0].message_pieces[0].converted_value}")

# %% [markdown]
# ## Text+Image-to-Video
#
# Use an image as the first frame of the generated video. The input image dimensions must match
# the video resolution (e.g. 1280x720). Pass both a text piece and an `image_path` piece in the same message.

# %%
import uuid

# Create a simple test image matching the video resolution (1280x720)
from PIL import Image

from pyrit.common.path import HOME_PATH

sample_image = HOME_PATH / "assets" / "pyrit_architecture.png"
resized = Image.open(sample_image).resize((1280, 720)).convert("RGB")

import tempfile

tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)  # noqa: SIM115
resized.save(tmp, format="JPEG")
tmp.close()
image_path = tmp.name

# Send text + image to the video target
i2v_target = OpenAIVideoTarget()
conversation_id = str(uuid.uuid4())

text_piece = MessagePiece(
    role="user",
    original_value="Animate this image with gentle camera motion",
    conversation_id=conversation_id,
)
image_piece = MessagePiece(
    role="user",
    original_value=image_path,
    converted_value_data_type="image_path",
    conversation_id=conversation_id,
)
result = await i2v_target.send_prompt_async(message=Message([text_piece, image_piece]))  # type: ignore
print(f"Text+Image-to-video result: {result[0].message_pieces[0].converted_value}")
