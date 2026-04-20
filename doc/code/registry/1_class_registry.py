# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
#   kernelspec:
#     display_name: pyrit (3.13.5)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Listing Available Classes
#
# Use `get_names()` to see what's available, or `list_metadata()` for detailed information.

# %%
from pyrit.registry import ScenarioRegistry

registry = ScenarioRegistry.get_registry_singleton()

# Get all registered names
names = registry.get_names()
print(f"Available scenarios: {names[:5]}...")  # Show first 5

# Get detailed metadata
metadata = registry.list_metadata()
for item in metadata[:2]:  # Show first 2
    print(f"\n{item.name}:")
    print(f"  Class: {item.class_name}")
    print(f"  Description: {item.description[:80]}...")

# %% [markdown]
# ## Getting a Class
#
# Use `get_class()` to retrieve a class by name. This returns the class itself, not an instance.

# %%
# Get a scenario class

scenario_class = registry.get_class("garak.encoding")

print(f"Got class: {scenario_class}")
print(f"Class name: {scenario_class.__name__}")

# %% [markdown]
# ## Creating Instances
#
# Once you have a class, instantiate it with your parameters. You can also use `create_instance()` as a shortcut.

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore
target = OpenAIChatTarget()

# Option 1: Get class then instantiate
encoding_class = registry.get_class("garak.encoding")
scenario = encoding_class()  # type: ignore

# Pass dataset configuration to initialize_async
await scenario.initialize_async(objective_target=target)  # type: ignore

# Option 2: Use create_instance() shortcut
# scenario = registry.create_instance("encoding", objective_target=my_target, ...)

print("Scenarios can be instantiated with your target and parameters")

# %% [markdown]
# ## Checking Registration
#
# Registries support standard Python container operations.

# %%
# Check if a name is registered
print(f"'garak.encoding' registered: {'garak.encoding' in registry}")
print(f"'nonexistent' registered: {'nonexistent' in registry}")

# Get count of registered classes
print(f"Total scenarios: {len(registry)}")

# Iterate over names
for name in list(registry)[:3]:
    print(f"  - {name}")

# %% [markdown]
# ## Using different registries
#
# There can be multiple registries. Below is doing a similar thing with the `InitializerRegistry`.

# %%
from pyrit.registry import InitializerRegistry

initializer_registry = InitializerRegistry.get_registry_singleton()

# Get all registered names
initializer_names = initializer_registry.get_names()
print(f"Available initializers: {initializer_names[:5]}...")  # Show first 5

# Get detailed metadata
for init_item in initializer_registry.list_metadata()[:2]:  # Show first 2
    print(f"\n{init_item.name}:")
    print(f"  Class: {init_item.class_name}")
    print(f"  Description: {init_item.description[:80]}...")
