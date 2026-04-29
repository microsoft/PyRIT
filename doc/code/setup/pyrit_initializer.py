# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
# ---

# %% [markdown]
# # PyRIT Initializers
#
# You can configure PyRIT using:
# 1. **Built-in initializers** - SimpleInitializer, AIRTInitializer
# 2. **External scripts** - Custom PyRITInitializer classes for project-specific needs
#
# ## Execution Order
#
# When `initialize_pyrit_async` is called:
# 1. Environment files are loaded (`.env`, `.env.local`)
# 2. Memory database is configured
# 3. All initializers are sorted by `execution_order` and executed
#
# ## Creating an Initializer

# %% [markdown]
# The following is a minimal `PyRITInitializer` class. It doesn't need much! In this case, it sets the default value for temperature for all OpenAIChatTargets to .9.

# %%
from pyrit.common.apply_defaults import set_default_value
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer


class CustomInitializer(PyRITInitializer):
    @property
    def name(self) -> str:
        return "Custom Configuration"

    @property
    def execution_order(self) -> int:
        return 2  # Lower numbers run first (default is 1)

    async def initialize_async(self) -> None:
        set_default_value(class_type=OpenAIChatTarget, parameter_name="temperature", value=0.9)

    @property
    def description(self) -> str:
        return "Sets custom temperature for OpenAI targets"


CustomInitializer()

# %% [markdown]
# ## Built-in Initializers
#
# PyRIT includes a few built-in initializers that set more intelligent defaults!
#
# - **SimpleInitializer**: Requires only OPENAI_CHAT_ENDPOINT, OPENAI_CHAT_MODEL, and OPENAI_CHAT_KEY
# - **AIRTInitializer**: Our best guess at defaults, but requires full Azure OpenAI configuration
#
# These are easy to include.

# %%
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import SimpleInitializer

# Using built-in initializer
await initialize_pyrit_async(  # type: ignore
    memory_db_type="InMemory", initializers=[SimpleInitializer()]
)

# %% [markdown]
# ## External Scripts
#
# External scripts allow custom configurations without modifying PyRIT. For example, you can write your own library, include them, and never have to check out pyrit in editable mode. Here are some use cases:
# - Custom targets for security assessments
# - Project-specific defaults
# - Organization-specific defaults
#
# As an example, say you are building a product, and want to set all your `adversarial_chat` in one place. You can using this!
#
# Like the built-in initializers, external scripts have the same format and must contain PyRITInitializer classes. In fact, using something like SimpleInitializer() as a template for your own is not a bad place to start.

# %%
import os
import shutil
import tempfile

from pyrit.setup import initialize_pyrit_async

temp_dir = tempfile.mkdtemp()
script_path = os.path.join(temp_dir, "custom_init.py")

# This is the simple custom initializer from the "Creating an Initializer" section of this notebook
script_content = """
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer
from pyrit.common.apply_defaults import set_default_value
from pyrit.prompt_target import OpenAIChatTarget

class CustomInitializer(PyRITInitializer):
    @property
    def name(self) -> str:
        return "Custom Configuration"

    @property
    def execution_order(self) -> int:
        return 2  # Lower numbers run first (default is 1)

    async def initialize_async(self) -> None:
        set_default_value(class_type=OpenAIChatTarget, parameter_name="temperature", value=0.9)

    @property
    def description(self) -> str:
        return "Sets custom temperature for OpenAI targets"

"""

with open(script_path, "w") as f:
    f.write(script_content)

print(f"Created: {script_path}")

await initialize_pyrit_async(  # type: ignore
    memory_db_type="InMemory", initialization_scripts=[temp_dir + "/custom_init.py"]
)

if os.path.exists(temp_dir):
    shutil.rmtree(temp_dir)

# %% [markdown]
# The initialization_scripts argument ultimately uses `pathlib.Path`, so the scripts are loaded relative to the current working directory (where you're executing the script from, not where PyRIT library is). To avoid ambiguity, it is usually better to use full paths if possible.

# %% [markdown]
# ## More information:
# - [Configuration notebook](1_configuration.ipynb) shows practical examples with custom targets
# - [Default Values notebook](default_values.md) explains how defaults work
#
