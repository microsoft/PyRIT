# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
# ---

# %% [markdown]
# # Barge-In Attack (Streaming Audio)
#
# `BargeInAttack` streams user audio to a `RealtimeTarget` and uses server-side voice-activity
# detection (VAD) to detect turn boundaries. When the user speaks while the assistant is still
# responding, server VAD cancels the in-flight response (barge-in). Interrupted turns are
# persisted with `prompt_metadata["interrupted"] = True`.
#
# Audio converters are applied per turn after VAD commits. The raw audio drives interruption
# timing while the model responds to the converted version.
#
# > **Note:** Memory must be initialized via `initialize_pyrit_async`. See the
# > [Memory Configuration Guide](../../memory/0_memory.md).

# %% [markdown]
# ## Setup
#
# `BargeInAttack` requires a `RealtimeTarget` with `server_vad=True` (or a `ServerVadConfig`
# for custom tuning).

# %%
import asyncio
import wave
from pathlib import Path

from pyrit.executor.attack import (
    AttackConverterConfig,
    BargeInAttack,
    BargeInAttackContext,
    ConsoleAttackResultPrinter,
)
from pyrit.executor.attack.core import AttackParameters
from pyrit.memory import CentralMemory
from pyrit.prompt_converter import AudioFrequencyConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.prompt_target import RealtimeTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# %% [markdown]
# ## Shared setup
#
# Both sections use a pre-recorded 24 kHz mono PCM16 question about photosynthesis. The
# format matches what the OpenAI Realtime API expects. Any async generator yielding 24 kHz
# PCM16 bytes works as a chunk source (live mic, TTS, etc.).

# %%
CHUNK_MS = 100
CHUNK_SIZE = CHUNK_MS * 48  # PCM16 @ 24 kHz mono = 48 bytes per millisecond.
SILENCE_CHUNK = b"\x00" * CHUNK_SIZE
audio_path = Path("../../../../assets/photosynthesis_question.wav").resolve()


def _load_pcm(path: Path) -> bytes:
    """Read a WAV at 24 kHz / mono / PCM16 into raw PCM bytes."""
    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == 24000 and wav.getnchannels() == 1 and wav.getsampwidth() == 2
        return wav.readframes(wav.getnframes())


async def _yield_chunks(pcm: bytes, real_time: bool = True):
    """Yield PCM in 100ms slices, optionally pacing at real-time."""
    for offset in range(0, len(pcm), CHUNK_SIZE):
        yield pcm[offset : offset + CHUNK_SIZE]
        if real_time:
            await asyncio.sleep(CHUNK_MS / 1000)


question_pcm_24k = _load_pcm(audio_path)
print(f"Loaded question: {len(question_pcm_24k) / 48 / 1000:.2f}s @ 24 kHz")

converters = PromptConverterConfiguration.from_converters(converters=[AudioFrequencyConverter(shift_value=200)])


# %% [markdown]
# ## Section 1: Single-turn streaming with a converter
#
# Streams one user statement, applies a frequency-shift converter after VAD commits the turn,
# and gets the model's response. Exercises the full pipeline (chunk push, convert-on-commit,
# item swap, response trigger, memory persistence) without barge-in.


# %%
async def single_turn_source():
    async for chunk in _yield_chunks(question_pcm_24k):
        yield chunk
    # Trailing silence helps server VAD recognize end-of-turn.
    for _ in range(25):  # 2.5s trailing silence, above the 1.5s VAD threshold
        yield SILENCE_CHUNK
        await asyncio.sleep(CHUNK_MS / 1000)


target = RealtimeTarget()
attack = BargeInAttack(
    objective_target=target,
    attack_converter_config=AttackConverterConfig(request_converters=converters),
)

context = BargeInAttackContext(
    params=AttackParameters(objective="Observe a single converted user turn end-to-end"),
    audio_chunks=single_turn_source(),
)

result = await attack.execute_with_context_async(context=context)  # type: ignore
print(f"executed_turns: {result.executed_turns}")
await ConsoleAttackResultPrinter(width=200).write_async(result=result)  # type: ignore
await target.cleanup_target_async()  # type: ignore

# %% [markdown]
# ## Section 2: Barge-in (interrupting the assistant mid-response)
#
# Plays the question twice with timing arranged so turn 2's speech arrives during turn 1's
# response. Server VAD detects the new speech, cancels turn 1's response, and resolves it
# with `interrupted=True`.

# %% [markdown]
# ### Reading the barge-in output
#
# After running the next cell, if barge-in fired successfully:
# - `executed_turns: 2` (two VAD-detected user turns)
# - First assistant turn shows `[INTERRUPTED]` with a truncated transcript
# - Second assistant turn completes normally
#
# If you don't see `[INTERRUPTED]`, decrease `TURN1_RESPONSE_WAIT_S` so turn 2's audio
# arrives earlier in turn 1's response window.

# %%
TURN1_RESPONSE_WAIT_S = 0.2  # how long to let the model start speaking before barging in


async def barge_in_source():
    # Turn 1: speak the question, then 1.5s of silence so VAD commits.
    async for chunk in _yield_chunks(question_pcm_24k):
        yield chunk
    for _ in range(25):  # 2.5s trailing silence
        yield SILENCE_CHUNK
        await asyncio.sleep(CHUNK_MS / 1000)

    # Let the model get partway into its response before we interrupt.
    for _ in range(int(TURN1_RESPONSE_WAIT_S * 10)):
        yield SILENCE_CHUNK
        await asyncio.sleep(CHUNK_MS / 1000)

    # Turn 2: speak the question again. VAD's speech_started fires while turn 1's response
    # is still streaming → server cancels + truncates turn 1.
    async for chunk in _yield_chunks(question_pcm_24k):
        yield chunk
    for _ in range(25):  # 2.5s trailing silence
        yield SILENCE_CHUNK
        await asyncio.sleep(CHUNK_MS / 1000)


target2 = RealtimeTarget()
attack2 = BargeInAttack(
    objective_target=target2,
    attack_converter_config=AttackConverterConfig(request_converters=converters),
)

barge_in_context = BargeInAttackContext(
    params=AttackParameters(objective="Demonstrate barge-in by interrupting a benign answer"),
    audio_chunks=barge_in_source(),
)

barge_in_result = await attack2.execute_with_context_async(context=barge_in_context)  # type: ignore
print(f"executed_turns: {barge_in_result.executed_turns}")

# Inspect memory to verify the barge-in landed in metadata.
memory = CentralMemory.get_memory_instance()
turns = memory.get_conversation(conversation_id=barge_in_result.conversation_id)
print(f"\nPersisted pieces ({len(turns)} messages):")
for message in turns:
    for piece in message.message_pieces:
        interrupted = piece.prompt_metadata.get("interrupted")
        marker = " [INTERRUPTED]" if interrupted else ""
        val = piece.converted_value
        if piece.converted_value_data_type == "audio_path":
            val = Path(val).name
        value_preview = (val[:80] + "...") if len(val) > 80 else val
        print(f"  {piece._role} {piece.converted_value_data_type}{marker}: {value_preview}")

await ConsoleAttackResultPrinter(width=200).write_async(result=barge_in_result)  # type: ignore
await target2.cleanup_target_async()  # type: ignore

# %% [markdown]
# ## Alternate chunk sources
#
# The chunk source is the main strategy hook:
#
# - **Pre-recorded WAV** (this notebook): most common starting point
# - **TTS converter**: generate audio from text prompts dynamically
# - **Live microphone**: use `sounddevice` or similar; yield what the mic produces
#
# For feedback-driven attacks — for example, scoring each assistant turn and choosing
# to barge in with follow-up audio only when the response shows incomplete refusal —
# subclass `BargeInAttack` and override `_perform_async` to interleave turn observation
# with chunk generation.
