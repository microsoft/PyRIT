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

Getting PyRIT running takes two steps: **install** the package, then **configure** your AI endpoints. For the path that's right for you, see the [Getting Started](getting_started/README) guide.

### Step 1: Install

PyRIT offers flexible installation options to suit different needs. Choose the path that best fits your use case.

::::{important}
**Version Compatibility:**
- **User installations** (Docker, Local) install the **latest stable release** from PyPI
- **Contributor installations** (Docker, Local) use the **latest development code** from the `main` branch
- Always match your notebooks to your PyRIT version
::::

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 User Docker Installation
:link: getting_started/install_docker
**Quick Start** ⭐

Get started immediately with a pre-configured environment:
- ✅ All dependencies included
- ✅ No Python setup needed
- ✅ JupyterLab built-in
- ✅ Works on all platforms
::::

::::{card} ☀️ User Local Installation
:link: getting_started/install_local
**Custom Setup**

Install PyRIT directly on your machine:
- ✅ Pip, uv, or conda
- ✅ Full Python environment control
- ✅ Easy integration with existing workflows
::::

::::{card} 🐋 Contributor Docker Installation
:link: getting_started/install_devcontainers
**Recommended for Contributors** ⭐

Pre-configured Docker container with VS Code:
- ✅ Consistent across all contributors
- ✅ All extensions pre-installed
- ✅ One-click setup
::::

::::{card} ☀️ Contributor Local Installation
:link: getting_started/install_local_dev
**Custom Dev Setup**

Install from source in editable mode:
- ✅ Full development control
- ✅ Use any IDE or editor
- ✅ Customize environment
::::
:::::

### Step 2: Configure

After installing, configure PyRIT with your AI endpoint credentials and initialize the framework. PyRIT reads from `~/.pyrit/` by default. For more details, see the [Configure PyRIT](getting_started/configuration) page.

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🔑 Populating Secrets
:link: getting_started/populating_secrets
**Set Up Your .env File**

Create `~/.pyrit/.env` with your provider credentials. Tabbed examples for OpenAI, Azure, Ollama, Groq, and more.
::::

::::{card} 📄 Config File (Recommended)
:link: getting_started/pyrit_conf
**Full Framework Setup** ⭐

Set up `.pyrit_conf` + `.env` for persistent config. Enables initializers that register targets, scorers, and datasets — required for `pyrit_scan` and scenarios.
::::
:::::
