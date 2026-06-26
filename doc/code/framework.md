# Framework

Learn how to use PyRIT's components to build red teaming workflows.

:::::{grid} 1 1 2 3
:gutter: 3

::::{card} 📦 Datasets
:link: ./datasets/0_dataset
Load, create, and manage seed datasets for red teaming campaigns.
::::

::::{card} ⚔️ Attacks & Executors
:link: ./executor/0_executor
Run single-turn and multi-turn attacks — Crescendo, TAP, Skeleton Key, and more.
::::

::::{card} 🔌 Targets
:link: ./targets/0_prompt_targets
Connect to OpenAI, Azure, Anthropic, HuggingFace, HTTP endpoints, and custom targets.
::::

::::{card} 🔄 Converters
:link: ./converters/0_converters
Transform prompts with text, audio, image, and video converters.
::::

::::{card} 📊 Scoring
:link: ./scoring/0_scoring
Evaluate AI responses with true/false, Likert, classification, and custom scorers.
::::

::::{card} 💾 Memory
:link: ./memory/0_memory
Track conversations, scores, and attack results with SQLite or Azure SQL.
::::

::::{card} ⚙️ Setup & Configuration
:link: ./setup/0_setup
Initialize PyRIT, configure defaults, and manage resiliency settings.
::::

::::{card} 📋 Scenarios
:link: ./scenarios/0_scenarios
Run standardized evaluation scenarios at scale across harm categories.
::::

::::{card} 🗂️ Registry
:link: ./registry/0_registry
Register and discover targets, scorers, and converters via class and instance registries.
::::

::::{card} 🖨️ Output
:link: ./output/0_output
Render attack results, scenario results, conversations, and scores to terminal, files, or Jupyter.
::::

:::::

---

The sections above link to detailed guides for each component. The architecture below explains how the pieces fit together — it's primarily aimed at contributors.

# Architecture

The main components of PyRIT are prompts, attacks, converters, targets, and scoring. The best way to contribute to PyRIT is by contributing to one of these components.

![alt text](../../assets/architecture_components.png)

As much as possible, each component is a pluggable brick of functionality. Prompts from one attack can be used in another. An attack for one scenario can use multiple targets. And sometimes you completely skip components (e.g. almost every component can be a NoOp also, you can have a NoOp converter that doesn't convert, or a NoOp target that just prints the prompts).

If you are contributing to PyRIT, that work will most likely land in one of the core components buckets and be as self-contained as possible. It isn't always this clean, but when an attack scenario doesn't quite fit (and that's okay!) it's good to brainstorm with the maintainers about how we can modify our architecture. Also, if our **Framework Plans** would be helpful, please open issues!

# Core Components

## [Datasets](./datasets/0_dataset)

**Responsibility**: Create a single place to manage prompts

- New Datasets can be added in the dataset module.
- Datasets should never be retrieved from DatasetProviders; DatasetProviders should load into memory, and then components retireve from memory
- Most components should always work with seeds passed directly in (except scenarios which may package them from memory). Never use DatasetProfiders, file paths, etc. Either pass the seed as an argument or retrieve from memory.

**Framework Plans**:

- There is some churn here. We haven't managed these much at scale, and we may have to redefine how it works.
- We want more investment in managing datasets and loading them more intelligently
- We need to more consistently pass seeds or use memory

**Contributing (difficulty easy)**: Are there more prompts and jailbreak templates you can add that include scenarios you're testing for? It is easy to add new dataset providers.

## [Attacks](./executor/0_executor)

**Responsibility**: Manage conversations between objective targets and adversarial targets; using datasets, scorers, and converters to achieve an objective.

- Any branching decision (e.g. the next thing(s) to do is based on a previous result) should be an attack.
- Attacks should always make use of other component's responsibilities. An attack should alwways branch based on a scorer and NOT a direct response. (e.g. was this prompt blocked? is a scorer responsibility, not an attack responsibility)
- Attacks should use scoring and target capabilities implicitly. Attacks should support multi-modal.
- Compound attacks are possible, combining different attacks in different ways.

**Rough Framework Plans**:

- We need to move some older attacks that don't belong here. Many (FlipAttack) should just be attack techniques
- There are potential ways we could combine different algorithms. Are Crescendo and TAP ultimately the same?
- We need to support target capabilities more implicitly
- Other executors, like benchmarks, need better end-to-end support; potentially including an `ExpectedResult` seed and associated scorers.
- More flexible compound attacks should continue to be added

**Contributing (difficulty high)**: The best way to contribute is likely opening issues if you run into limitations.

## [Attack Technique]

**Responsibility**: An attack technique packages an executor, converters, datasets, and strategies into a single attack. The goal is that any attack (something trying to achieve an objective) can be defined as an attack technique.

**Rough Framework Plans**:

- Managing these better, so scenarios can more easily select or build the attack techniques to use

**Contributing (difficulty easy)**: Simply add the attack technique to one of the initializers.

## [Scenarios](./scenarios/0_scenarios)

**Responsibility**: This is the avenue to "run PyRIT against something". What does that look like?

- A scenario takes user input and uses it to package datasets with attack techniques
- A scenario orchestrates resiliency and parallelism from a high level
- No result should depend on previous results (that is an attack's job)

**Rough Framework Plans**:

- Scenarios are new enough that we are still discovering patterns and limitations. So they will regularly be refactored

**Contributing (difficulty medium)**: Is there a scanner that does something PyRIT doesn't? Add it as a scenario. But because we're changing how things are done rapidly, it is not as well-defined as other areas.

## [Converters](./converters/0_converters)

**Responsibility**: Converters are a component that converts prompts to something else. They can be stacked and combined. They can be as varied as translating a text prompt into a Word document, rephrasing a prompt, or adding a text overlay to an image.

**Rough Framework Plans**:

- We want to refactor our converter pipeline, so there are currently some things that should be converters that we may want to postpone (e.g. partial converting). This is supported but could be much more dynamic.

**Contributing (difficulty low)**: The existing pattern is well-defined. Are there ways prompts can be converted that would be useful for an attack?

## [Target](./targets/0_prompt_targets.md)

**Responsibility**: A Prompt Target can be thought of as "the thing we're sending the prompt to". Many other components use it, including scorers, attacks, and converters.

- This is often an LLM, but it doesn't have to be. For Cross-Domain Prompt Injection Attacks, the Prompt Target might be a Storage Account that a later Prompt Target has a reference to. Message and conversation should be generic enough to handle this extra data.
- Prompt Target capabilities should be used to see if a target is compatible with the capabilities that the other components want to use.
- Targets should use message_normalizer along with PromptCapabilities to transorm `Messages` into formats that target supports.
- Because targets are so varied, it is reasonable to return multiple tool calls, or none at all.
- One attack can have many Prompt Targets (and in fact, converters and Scoring Engine can also use Prompt Targets to convert/score the prompt).

**Rough Framework Plans**:

- Better agent support may require extra pieces attached to a Message
- Better surface support may require expanding the return types

**Contributing (difficulty low)**: 

- The pattern is well-defined.
- Are there models you want to use at any stage or for different attacks? But also, can your model just be one of the existing targets?

## [Scoring](./scoring/0_scoring.ipynb)

**Responsibility**: The scoring engine is a component that gives feedback to the attack on what happened with the prompt. This could be as simple as "Was this prompt blocked?" or "Was our objective achieved?"

- Any decision an attack makes should be based on a scorer result

**Contributing (difficulty low)**:  

- The pattern is well-defined.
- You can evaluate how accurate probabalistic scorers are and likely make them more accurate.
- Is there data you want to use to make decisions or analyze?

**Framework Plans**:

- Scorers will be refactored to be more generic, so they can determine more general results (does a file exist? Was a tool called?)

# Core library

The below talks about responsibilities of several modules in the PyRIT library

## [Registry](./registry/0_registry)

**Responsibility**: The registry is used to build and store the core components. 

- If you are creating a component with user input (e.g. via config, REST, or automatically) it should always use the registry
- If you are storing an instance of a component, it should always use the registry

## [Models]

**Responsibility**: pyrit.models is a lightweight module where core types are defined. These should always be used where possible to prevent drift.

- If you are creating a class that has a lot of overlap with another class, or using a dict to serialize across boundaries, consider if you can use/move pyrit.models
- Models includes `identifiers` which are descriptions of the core components. And along with the registry, can often recreate those components.
- Models includes types passed around between components, and should be prefered in REST
- models should never include any dependencies outside of pyrit.common (which shouldn't depend on anything)

## Output

**Responsibility**: The Output module is responsible for writing different components in different formats to different places.

## [Memory](./memory/0_memory.md)

One important thing to remember about this architecture is its swappable nature. Prompts and targets and converters and attacks and scorers should all be swappable. But sometimes one of these components needs additional information. If the target is an LLM, we need a way to look up previous messages sent to that session so we can properly construct the new message. If the target is a blob store, we need to know the URL to use for a future attack.

## Framework Component Documentation

**Responsibility** Show how the framework is used in a concise way

- Notebooks that contain code should be notebooks that can execute
- Notebooks should execute quickly
