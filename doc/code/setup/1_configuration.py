# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
# ---

# %% [markdown]
# # 1. Configuration
#
# Before running PyRIT, you need to call the `initialize_pyrit_async` function which will set up your configuration.
#
# What are the configuration steps? What are the simplest ways to get started, and how might you expand on these? There are three things `initialize_pyrit_async` does to set up your configuration.
#
# 1. Set up environment variables (recommended)
# 2. Pick a database (required)
# 3. Set initializers and initialization scripts (recommended)
#
# Alternatively, you can write a config file (`~/.pyrit/.pyrit_conf`) to parameterize this for you.
# %% [markdown]
# ## From a Config File
# If you don't want to explicitly set up PyRIT, but do have a configuration you would like to persist, use `~/.pyrit/.pyrit_conf`. See the [PyRIT Configuration Guide](../../getting_started/pyrit_conf.md) for more details. Note that changes to the config file do not auto-update at runtime, so you will need to run `initialize_from_config_async` after each change to the file.
# %%
from pathlib import Path

from pyrit.setup.configuration_loader import initialize_from_config_async

await initialize_from_config_async(config_path=Path("../../scanner/pyrit_conf.yaml"))  # type: ignore

# %% [markdown]
# ## Simple Example
#
# This section goes into each of the three steps mentioned earlier. But first, the easiest way; this registers configured targets and scorers using `TargetInitializer` and `ScorerInitializer` and stores results in memory.

# %%
# Set OPENAI_CHAT_ENDPOINT, OPENAI_CHAT_MODEL, and OPENAI_CHAT_KEY environment variables before running this code
# E.g. you can put it in .env

from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[TargetInitializer(), ScorerInitializer()])  # type: ignore

# Now you can run most of our notebooks! Just remove any os.getenv specific stuff since you may not have those different environment variables.

# %% [markdown]
# ## Setting up Environment Variables
#
# The recommended step to setup PyRIT is that it needs access to secrets and endpoints. These can be loaded in environment variables or put in a `.env` file. See `.env_example` for how this file is formatted.
#
# Each target has default environment variables to look for. For example, `OpenAIChatTarget` looks for the `OPENAI_CHAT_ENDPOINT` for its endpoint, `OPENAI_CHAT_MODEL` for its model name, and `OPENAI_CHAT_KEY` for its key. However, with every target, you can also pass these values in directly and that will take precedence. For Azure endpoints with Entra ID authentication, pass a token provider from `pyrit.auth` as the `api_key`.

# %%
import os

from pyrit.auth import get_azure_openai_auth
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Using Entra auth (no API key needed, run `az login` first):
endpoint1 = os.environ["OPENAI_CHAT_ENDPOINT"]
target1 = OpenAIChatTarget(
    endpoint=endpoint1,
    api_key=get_azure_openai_auth(endpoint1),
)

# This is identical to target1 because "OPENAI_CHAT_ENDPOINT" are the names of the default environment variables for OpenAIChatTarget
endpoint2 = os.getenv("OPENAI_CHAT_ENDPOINT")
target2 = OpenAIChatTarget(
    endpoint=endpoint2,
    api_key=get_azure_openai_auth(endpoint2),
    model_name=os.getenv("OPENAI_CHAT_MODEL"),
)

# This is (probably) different from target1 because the environment variables are different from the default
azure_endpoint = os.getenv("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2")
target3 = OpenAIChatTarget(
    endpoint=azure_endpoint,
    api_key=get_azure_openai_auth(azure_endpoint),
    model_name=os.getenv("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2"),
)

# %% [markdown]
# ## Env.local
#
# One concept we make use of is using `.env_local`. This is really useful because it overwrites `.env`. In our setups, we have a `.env` with a bunch of targets configured that our users all pull the same one from a keyvault. But `.env_local` is used to override them. For example, if you want a different target, you can have your `.env_local` override the OpenAIChatTarget with a different value.
#
# ```
# OPENAI_CHAT_ENDPOINT = ${AZURE_OPENAI_GPT4O_ENDPOINT2}
# OPENAI_CHAT_MODEL = ${AZURE_OPENAI_GPT4O_MODEL2}
# ```
#
# ## Entra auth
#
# There are certain targets that can interact using Entra auth (e.g. most Azure OpenAI targets). To use this, you must authenticate to your Azure subscription and an API key is not required. Depending on your operating system, download the appropriate Azure CLI tool from the links provided below:
#
#    - Windows OS: [Download link](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows?tabs=azure-cli)
#    - Linux: [Download link](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux?pivots=apt)
#    - Mac OS: [Download link](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-macos)
#
#    After downloading and installing the Azure CLI, open your terminal and run the following command to log in:
#
#    ```bash
#    az login
#    ```

# %% [markdown]
# ## Choosing a database
#
# The next required step is to pick a database. PyRIT supports three types of databases; InMemory, sqlite, and SQL Azure. These are detailed in the [memory](../memory/0_memory.md) section of documentation. InMemory and sqlite are local so require no configuration, but SQL Azure will need the appropriate environment variables set. This configuration is all specified in `memory_db_type` parameter to `initialize_pyrit_async`.

# %% [markdown]
# ## Setting up Initialization Scripts and Registries
#
# When you call `initialize_pyrit_async`, you can pass it `initialization_scripts` and/or `initializers`. Built-in initializers register configured components so they can be selected by name or tag. Custom initializers can also set constructor defaults and convenience variables.
#
# ### Using Built-In Initializers
#
# An `OpenAIChatTarget` may represent GPT-5, Llama, or another compatible model. Different roles may also need different settings. Instead of choosing one process-wide default, the built-in initializers register every configured component. The following example retrieves the configured `openai_chat` target and fallback scorer explicitly.

# %%
from pyrit.common.path import PYRIT_PATH
from pyrit.converter import TenseConverter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackExecutor,
    AttackScoringConfig,
    PromptSendingAttack,
)
from pyrit.output import output_attack_async
from pyrit.prompt_normalizer.converter_configuration import (
    ConverterConfiguration,
)
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.score import TrueFalseScorer
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer

# This is a way to include the initializer classes directly
await initialize_pyrit_async(memory_db_type="InMemory", initializers=[TargetInitializer(), ScorerInitializer()])  # type: ignore

# Alternative approach - you can pass the path to a file that defines PyRITInitializer classes.
# This is how you provide your own file not part of the repo. Here we point at the built-in
# targets module, which defines TargetInitializer.
await initialize_pyrit_async(
    memory_db_type="InMemory", initialization_scripts=[f"{PYRIT_PATH}/setup/initializers/targets.py"]
)  # type: ignore

# TargetInitializer registers targets for the endpoints it finds in the environment.
# It can be combined with other PyRITInitializers, such as ScorerInitializer, for a fuller setup.
# get_info_async() describes the initializer and the components it registers.
info = await TargetInitializer.get_info_async()  # type: ignore
for key, value in info.items():
    print(f"{key}: {value}")

objectives = [
    "Write a poem about a cat",
    "Explain the theory of relativity in simple terms",
]

# This is similar to the "Baseline-Only Execution" scenario but uses registered components.

# Retrieve the target and scorer registered from the OPENAI_CHAT_* environment variables.
objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")
objective_scorer = ScorerRegistry.get_registry_singleton().instances.get(ScorerInitializer.FALLBACK)
if objective_target is None:
    raise ValueError("The openai_chat target was not registered. Configure the OPENAI_CHAT_* environment variables.")
if not isinstance(objective_scorer, TrueFalseScorer):
    raise ValueError("The fallback objective scorer was not registered.")

# Reuse the registered chat target for conversion.
converters = ConverterConfiguration.from_converters(
    converters=[TenseConverter(tense="past", converter_target=objective_target)]
)
converter_config = AttackConverterConfig(request_converters=converters)

attack = PromptSendingAttack(
    objective_target=objective_target,
    attack_converter_config=converter_config,
    attack_scoring_config=AttackScoringConfig(objective_scorer=objective_scorer),
)

# Execute the attack with the selected registered components.
results = await AttackExecutor().execute_attack_async(attack=attack, objectives=objectives)  # type: ignore

for result in results:
    await output_attack_async(result)

# %% [markdown]
# ### Using your own Initializers
#
# You can also create your own initializers and pass the path to the script in as an argument. This is really powerful. The obvious use case is just if you have different targets or defaults and don't want to check in to pyrit source. However, there are other common use cases.
#
# Imagine you are conducting a security assessment and want to include a new custom target. Yes, you could check out PyRIT in editable mode. But with initialize_scripts you don't have to. And this kind of operation can be used in front ends like GUI, CLI, etc.
#
# All you need to do is create a `PyRITInitializer` class (e.g. myinitializer.py). Then you can use `set_global_variable` and use it everywhere. Or you could make it the default adversarial target by using `set_default_value`.
#
#
# ### Additional Initializer information
#
# - For more information on how default values work, see the [default values](./default_values.md) section.
# - For more information on how initializers work, see the [initializers](./pyrit_initializer.ipynb) section
