# Executor

## Overview

The `pyrit/executor` module provides a flexible framework for executing various operations in PyRIT. This document explains the core components and how they are utilized across different executor categories.

## Core Components (`pyrit/executor/core`)

The core executor module contains the foundational classes and interfaces that all executor categories inherit from:

- **Strategy** (`strategy.py`): Abstract base class for strategies with enforced lifecycle management.
- **StrategyContext** (`strategy.py`): The abstract base class that manages strategy context (all data needed to successfully execute the strategy).
- **StrategyConverterConfig** (`config.py`): Configuration for prompt converters used in strategies.
- **StrategyResult** (`pyrit/models/strategy_result.py`): Base class for all strategy results.

```{mermaid}
flowchart LR
    A(["Strategy"])
    A --consumes--> B(["Strategy Context"])
    A --takes in as parameters within __init__--> D(["Strategy Configurations (e.g. Converters)"])
    A --produces--> C(["Strategy Result <br>"])
```

To execute, one generally follows this pattern:
1. Create an **strategy context** containing state information
2. Initialize a **strategy** (with optional **configurations** for converters etc.)
3. _Execute_ the attack strategy with the created context
4. Receive and process the **strategy result**

Each attack implements a lifecycle with distinct phases (all abstract methods), and the `Strategy` class provides a non-abstract `execute_async()` method that enforces this lifecycle:
* `_validate_context`: Validate context
* `_setup_async`: Initialize state
* `_perform_async`: Execute the core  logic
* `_teardown_async`: Clean up resources

This implementation enforces a consistent execution flow across all strategies by:
1. Guaranteeing that setup is always performed before the attack begins
2. Ensuring the attack logic is only executed if setup succeeds
3. Guaranteeing teardown is always executed, even if errors occur, through the use of a finally block
4. Providing centralized error handling and logging

## Executor Categories

All of these categories follow the flow of control described above.

### Attack (`pyrit/executor/attack`)

Attacks implement various adversarial testing strategies to send prompts to a target endpoint, evaluate the responses, and report on the success of the attack.

- **Single-Turn Attacks**: Single-turn attacks typically send prompts to a target endpoint to try to achieve a specific objective within a single turn. These attack strategies evaluate the target response using optional scorers to determine if the objective has been met.
- **Multi-Turn Attacks**: Multi-turn attacks introduce an iterative attack process where an adversarial chat model generates prompts to send to a target system, attempting to achieve a specified objective over multiple turns. This strategy also evaluates the response using a scorer to determine if the objective has been met. These attacks continue iterating until the objective is met or a maximum numbers of turns is attempted. These types of attacks tend to work better than single-turn attacks in eliciting harm if a target endpoint keeps track of conversation history.

Read more about the Attack architecture [here](../executor/attack/0_attack.md)

### Prompt Generator (`pyrit/executor/promptgen`)

Prompt generators create various types of prompts using different strategies. Some examples are:

- **Fuzzer Generator**: Generates diverse jailbreak prompts by systematically exploring and generating prompt templates using the Monte Carlo Tree Search to balance exploration of new templates with exploitation of promising ones.
- **Anecdoctor Generator**: Generates misinformation content by using few-shot examples directly or by extracting a knowledge graph from examples, then using it.

Read more about Prompt Generators [here](../executor/promptgen/0_promptgen.md)

### Workflow (`pyrit/executor/workflow`)

Workflows orchestrate complex multi-step operations. Examples include:

- **XPIA Workflow**: This workflow orchestrates an cross prompt-injection attack (XPIA), where one might hide a prompt injection within a website or PDF and ask a target system to evaluate the contents to trigger the prompt injection.

Read more about Workflows [here](../executor/workflow/0_workflow.md)


### Benchmark (`pyrit/executor/benchmark`)

Benchmarks evaluate model performance and safety based off of specific criteria. Examples include:

- **Question Answering Benchmark**: This benchmark strategy evaluates target models by sending multiple choice questions as prompts and seeing how accurately the model answers those questions. The responses are evaluated for benchmark reporting.

Read more about Benchmarks [here](../executor/benchmark/0_benchmark.md)
