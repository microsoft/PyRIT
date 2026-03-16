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

+++ { "kind": "justified" }

## Installation Guide

PyRIT offers flexible installation options to suit different needs. Choose the path that best fits your use case.

::::{important}
**Version Compatibility:**
- **User installations** (Docker, Pip/Conda) install the **latest stable release** from PyPI
- **Contributor installations** (DevContainers, Local Development) use the **latest development code** from the `main` branch
- Always match your notebooks to your PyRIT version
::::

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 Docker Installation
:link: setup/1b_install_docker
**For Users - Quick Start** ⭐

Get started immediately with a pre-configured environment:
- ✅ All dependencies included
- ✅ No Python setup needed
- ✅ JupyterLab built-in
- ✅ Works on all platforms
::::

::::{card} ☀️ Local Pip/uv Installation
:link: setup/1a_install_uv
**For Users - Custom Setup**

Install PyRIT directly on your machine:
- ✅ Full Python environment control
- ✅ Lighter weight installation
- ✅ Easy integration with existing workflows
::::

::::{card} 🐋 DevContainers in VS Code
:link: contributing/1b_install_devcontainers
**For Contributors** ⭐

Standardized development environment:
- ✅ Pre-configured VS Code setup
- ✅ Consistent across all contributors
- ✅ All extensions pre-installed
::::

::::{card} ☀️ Local uv Development
:link: contributing/1a_install_uv
**For Contributors - Custom Dev Setup**

Install from source in editable mode:
- ✅ Full development control
- ✅ Use any IDE or editor
- ✅ Customize environment
::::
:::::
