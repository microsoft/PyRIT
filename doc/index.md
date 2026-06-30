---
title: PyRIT — Python Risk Identification Tool
site:
  hide_title_block: true
  hide_toc: true
  hide_outline: true
---

+++ { "kind": "split-image" }

PyRIT

## Python Risk Identification Tool

Automated and human-led AI red teaming — a flexible, extensible framework for assessing the security and safety of generative AI systems at scale.

![](banner.png)

+++ { "kind": "justified" }

What PyRIT Offers

## Key Capabilities

:::::{grid} 1 2 3 3

::::{card}
🎯 **Automated Red Teaming**

Run multi-turn attack strategies like Crescendo, TAP, and Skeleton Key against AI systems with minimal setup. Single-turn and multi-turn attacks supported out of the box.
::::

::::{card}
📦 **Scenario Framework**

Run standardized evaluation scenarios at large scale — covering content harms, psychosocial risks, data leakage, and more. Compose strategies and datasets for repeatable, comprehensive assessments across hundreds of objectives.
::::

::::{card}
🖥️ **CoPyRIT**

A graphical user interface for human-led red teaming. Interact with AI systems directly, track findings, and collaborate with your team — all from a modern web UI.
::::

```{image} roakey_peek.png
:alt: Roakey peeking in
:class: roakey-peek roakey-peek-left
```

::::{card}
🔌 **Any Target**

Test OpenAI, Azure, Anthropic, Google, HuggingFace, custom HTTP endpoints or WebSockets, web app targets with Playwright, or build your own with a simple interface.
::::

::::{card}
💾 **Built-in Memory**

Track all conversations, scores, and attack results with SQLite or Azure SQL. Export, analyze, and share results with your team.
::::

::::{card}
📊 **Flexible Scoring**

Evaluate AI responses with true/false, Likert scale, classification, and custom scorers — powered by LLMs, Azure AI Content Safety, or your own logic.
::::

:::::

---

## Getting Started
1. Install PyRIT and verify installation.\
For more details and alternative installation methods, see the [Install PyRIT](getting_started/install) page
```bash
# note: for local installation, python version 3.13 is recommended: https://www.python.org/downloads/latest/python3.13
pip install pyrit
python -c "import pyrit; print(f'PyRIT version installed: {pyrit.__version__}')"
```

2. Create and populate endpoint and startup configuration files in `~/.pyrit/.env` and `~/.pyrit/.pyrit_conf` with minimal content below.\
For more details, see the [Configure PyRIT](getting_started/configuration) page.

:::::{grid} 1 1 2 2

::::{card} 🔑 ~/.pyrit/.env
```bash
# example OPENAI_CHAT_ENDPOINT values:
# "https://api.openai.com/v1"
# "https://<project>.cognitiveservices.azure.com/openai/v1/"
# "https://<project>.services.ai.azure.com/openai/v1"
OPENAI_CHAT_ENDPOINT="<open-ai-chat-endpoint>"
OPENAI_CHAT_KEY="<your-api-key>"
OPENAI_CHAT_MODEL="<model-name>"
```
::::

::::{card} 📄 ~/.pyrit/.pyrit_conf
```yaml
memory_db_type: in_memory

initializers:
  - name: target
    args:
      tags:
        - default
        - scorer
  - name: scorer
```
::::

:::::

```{image} roakey_tail.png
:alt: Roakey's tail peeking in
:class: roakey-peek roakey-peek-tail
```

3. Use PyRIT in any mode that best fits your use case: Scanner, GUI, or Framework.

::::{tab-set}

:::{tab-item}🔍 Scanner
Run security assessments from the command line with `pyrit_scan` or the interactive `pyrit_shell`. Execute built-in scenarios against your AI targets.

```bash
pyrit_scan airt.scam --target openai_chat
```

![scanner-demo](scanner-demo.png)

Use `pyrit_scan --help` to learn more about what else `pyrit_scan` can do.
For more details, see the [Scanner](scanner/0_scanner) page.
:::

:::{tab-item}🖥️ GUI
Use CoPyRIT's graphical interface for interactive red teaming. Chat with AI systems, track findings, and collaborate with your team.

Start the local web app and give it a try:

```bash
pyrit_backend # serves webapp on http://localhost:8000/
```
![copyrit-demo](copyrit-demo.png)

For more details, see the [GUI](gui/0_gui) page.
:::

:::{tab-item}🧩 Framework
Dive into PyRIT's modular components — targets, converters, scorers, memory, and more. Create custom attacks and extend the framework.

```python
from pyrit.executor.attack import ConsoleAttackResultPrinter, PromptSendingAttack
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)

target = OpenAIChatTarget()
attack = PromptSendingAttack(objective_target=target)
result = await attack.execute_async(objective="What model exactly are you? be concise.")

printer = ConsoleAttackResultPrinter()
await printer.print_conversation_async(result=result)
```

![framework-demo](framework-demo.png)
:::

For more details, see the [Framework](code/framework) page.
::::
