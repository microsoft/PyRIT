# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
# ---
# %% [markdown]
# # GitHub Copilot Target
#
# `GitHubCopilotTarget` sends text prompts through the GitHub Copilot SDK. This is
# separate from the [WebSocket Copilot target](./10_3_websocket_copilot_target.ipynb),
# which connects to Microsoft Copilot services.
#
# The target keeps one native Copilot session for each PyRIT conversation. Use the
# same `conversation_id` for each turn to continue that session.

# %% [markdown]
# ## Install and authenticate
#
# Install PyRIT with the optional GitHub Copilot SDK dependency:
#
# ```bash
# pip install "pyrit[github-copilot]"
# ```
#
# This example uses `gpt-5-mini`; choose a model available to your GitHub Copilot
# account.
#
# `GitHubCopilotTarget` resolves authentication in this order: an explicit
# `github_token`, the `GITHUB_TOKEN` environment variable, then the SDK's normal
# login discovery. PyRIT initialization loads configured environment files. See
# [installation](../../getting_started/install.md),
# [secrets](../../getting_started/populating_secrets.md), and the
# [initializer notebook](../setup/pyrit_initializer.ipynb) for setup details.

# %% [markdown]
# ## Continue a native text conversation
#
# `PromptNormalizer` assigns the shared conversation ID and persists both turns
# and their responses. The Copilot SDK retains its native history between sends.
#
# The target disables SDK tools and does not expose editable or imported native
# history. Workflows that rewrite or branch earlier turns need a target with
# editable history. See [target capabilities](./6_1_target_capabilities.ipynb).
#
# An empty reply is saved as an empty response, and the conversation continues.
# Any other failed turn ends its native conversation. Later sends with that
# conversation ID fail, so use a new conversation ID to continue.

# %%
from uuid import uuid4

from pyrit.models import MessagePiece
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import GitHubCopilotTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

target = GitHubCopilotTarget(model_name="gpt-5-mini")
normalizer = PromptNormalizer()
conversation_id = str(uuid4())

try:
    first_response = await normalizer.send_prompt_async(
        message=MessagePiece(
            role="user",
            original_value="Remember the harmless code word ORCHID.",
        ).to_message(),
        target=target,
        conversation_id=conversation_id,
    )
    second_response = await normalizer.send_prompt_async(
        message=MessagePiece(
            role="user",
            original_value="What code word did I ask you to remember?",
        ).to_message(),
        target=target,
        conversation_id=conversation_id,
    )

    print(f"First response: {first_response.get_piece().converted_value}")
    print(f"Second response: {second_response.get_piece().converted_value}")
finally:
    await target.cleanup_target_async()

# %% [markdown]
# ## Judge the saved response
#
# `SelfAskRefusalScorer` asks Copilot whether `second_response` is a refusal:
# `True` means refusal and `False` means no refusal. It judges refusal, not whether
# the response recalled ORCHID. The judge uses a separate Copilot conversation; the
# target from the earlier example has already been cleaned up. For other true/false
# scorers, see [True/False Scorers](../scoring/1_true_false_scorers.ipynb).

# %%
from pyrit.models import MessageScorable, ScoringExpectation
from pyrit.prompt_target import GitHubCopilotTarget
from pyrit.score import SelfAskRefusalScorer

judge_target = GitHubCopilotTarget(model_name="gpt-5-mini")
refusal_scorer = SelfAskRefusalScorer(chat_target=judge_target)
try:
    [refusal_score] = await refusal_scorer.score_async(
        scorable=MessageScorable.from_message(second_response),
        expectation=ScoringExpectation(objective="Recall the harmless code word from the earlier turn."),
    )
    print(f"Refusal: {refusal_score.get_value()}")
    print(f"Rationale: {refusal_score.score_rationale}")
finally:
    await judge_target.cleanup_target_async()

# %% [markdown]
# ## Known shutdown-notification issue
#
# Depending on the SDK/runtime version, shutdown-notification parsing may report
# `ValueError: 'destroy' is not a valid ShutdownType`. This notification-parsing issue
# is not evidence of a model-response failure and does not confirm successful or failed
# session deletion.
