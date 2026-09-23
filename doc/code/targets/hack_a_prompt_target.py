# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.5
# ---

# %% [markdown]
# # HackAPrompt Target
#
# [HackAPrompt](https://www.hackaprompt.com) is a prompt injection competition. Each challenge gives you a model
# to break and an intent to reach, and a panel of judges run by the platform decides whether a session reached
# it. `HackAPromptTarget` sends prompts to one challenge and can ask that judge panel for its verdict, so the
# challenges can be attacked with PyRIT attacks and scorers instead of the website's chat box.
#
# Only the first cell below runs without credentials. The two that talk to the platform need a signed-in
# HackAPrompt session, so their output is not checked in and this notebook is skipped when the documentation is
# regenerated.

# %% [markdown]
# ## Authentication
#
# HackAPrompt has no API keys. Requests are authenticated with the cookies of a signed-in browser session, and a
# challenge session is identified by a session id that the website creates when you open a challenge. Both values
# are copied out of the browser once and expire when the browser session does.
#
# 1. Sign in at [hackaprompt.com](https://www.hackaprompt.com) and open the challenge you want to attack.
# 2. Open the browser's developer tools (`F12`) and switch to the **Network** tab.
# 3. Send any prompt in the challenge's chat box and select the resulting `chat` request.
# 4. The request **payload** holds `session_id`, `challenge_slug` and `competition_slug`.
# 5. The request **headers** hold the `Cookie` header. Copy the whole header value; it holds the
#    `sb-<project>-auth-token.*` cookies that identify you.
#
# Put the two secrets in your `.env` file, where the target picks them up:
#
# ```
# HACK_A_PROMPT_SESSION_ID="a4d3e38c-..."
# HACK_A_PROMPT_COOKIE="sb-<project>-auth-token.0=...; sb-<project>-auth-token.1=..."
# ```
#
# They can also be passed to the constructor as `session_id` and `cookie` — useful when you refresh them from a
# browser in the same script. Neither ends up in the target identifier that PyRIT writes to memory.

# %% [markdown]
# ## Picking a challenge
#
# `HackAPromptChallenge` lists the text challenges of the CBRNE practice track, the live twin of the CBRNE
# competition track that closed on June 19th. Picking a member fills in both slugs the API needs, and carries
# the rest of what the platform publishes about the challenge: the intents the judges grade against, the models
# the prompt is run against, and whether the challenge takes a single prompt only.

# %%
from pyrit.prompt_target import HackAPromptChallenge

for challenge in HackAPromptChallenge:
    turns = "one prompt" if challenge.one_shot else "conversation"
    models = ", ".join(challenge.models) or "not published"
    print(f"{challenge.name:<33} {challenge.challenge_slug:<35} {turns:<12} {models}")

# %% [markdown]
# ## Sending a prompt
#
# One target drives one HackAPrompt challenge session: the platform keeps the transcript on its side, keyed by
# the session id, and the judges grade all of it together. Build a new target — or assign a fresh `session_id`
# copied from a freshly opened challenge page — for every attack run that should be graded on its own.
#
# A challenge the platform does not flag as one-shot is a conversation, so multi-turn attacks such as
# `CrescendoAttack` work against it; a one-shot challenge rejects a second turn.

# %%
from pyrit.executor.attack import PromptSendingAttack
from pyrit.output import output_attack_async
from pyrit.prompt_target import HackAPromptTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

challenge = HackAPromptChallenge.BACTERIAL_BASICS
target = HackAPromptTarget(challenge=challenge)

print(f"Attacking {challenge.title} at {target.challenge_url}")

attack = PromptSendingAttack(objective_target=target)
result = await attack.execute_async(objective=challenge.intents[0])  # type: ignore
await output_attack_async(result)  # type: ignore

# %% [markdown]
# ## Asking the judges
#
# The attack above runs without a scorer, so PyRIT reports its outcome as undetermined. On HackAPrompt it is the
# platform's own judge panel that decides: `check_challenge_async` submits the session to it and returns its
# verdict unchanged, one entry per judge plus the points the session earned. The judges grade the whole session,
# so call it after the attack has run.

# %%
judgement = await target.check_challenge_async()  # type: ignore

for judge in judgement.get("judgePanel", []):
    print(f"{judge['name']} passed={judge['passed']}: {judge['judge_response']}")

print(f"Points earned: {judgement.get('pointsEarned', 0)}")

# %% [markdown]
# ## Other challenges
#
# HackAPrompt has far more challenges than the CBRNE practice track, and new ones appear with every competition.
# Any of them can be attacked by passing its slug together with the slug of the competition it belongs to — both
# are in the payload of the `chat` request, and in the address of the challenge page. This is also how the
# challenges of the closed CBRNE competition track are reached.
#
# ```python
# target = HackAPromptTarget(challenge_slug="jokebot_goes_to_therapy", competition_slug="dougdoug")
# ```
