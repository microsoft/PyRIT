<p align="center"><img src="./doc/roakey.png" width="150"></p>

# PyRIT: AI red teaming for models and applications

[![Code contributors](https://img.shields.io/github/contributors/microsoft/PyRIT?label=code%20contributors)](https://github.com/microsoft/PyRIT/graphs/contributors)
[![PRs merged](https://img.shields.io/github/issues-search?query=repo%3Amicrosoft%2FPyRIT%20is%3Apr%20is%3Amerged&label=PRs%20merged)](https://github.com/microsoft/PyRIT/pulls?q=is%3Apr+is%3Amerged)
[![Issues closed](https://img.shields.io/github/issues-closed/microsoft/PyRIT?label=issues%20closed)](https://github.com/microsoft/PyRIT/issues?q=is%3Aissue+is%3Aclosed)

PyRIT (Python Risk Identification Tool for generative AI) is an open-source toolkit
for finding security and safety weaknesses in generative AI systems through automated
scans, adaptive multi-turn attacks, and human-led testing.

Built by the Microsoft AI Red Team and an open-source community of contributors,
PyRIT brings a command-line scanner, a graphical interface, and an extensible
Python framework together in one toolkit.

You do not need to be a Microsoft employee or an experienced red teamer to get involved.
Join our [Discord community](https://discord.gg/9fMpq3tc8u) to ask questions, exchange
techniques, share what you are building, and connect with maintainers and other users.

Contributions include code, documentation, examples, bug reports, testing, and feedback
from real-world use. Read the
[contributor guide](https://microsoft.github.io/PyRIT/latest/contributing/readme/) and
explore [issues labeled "help wanted"](https://github.com/microsoft/PyRIT/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22).

**[Get started](https://microsoft.github.io/PyRIT/) |
[Join Discord](https://discord.gg/9fMpq3tc8u) |
[Read the whitepaper](https://commandline.microsoft.com/wp-content/uploads/2026/08/PyRIT_Whitepaper_2026.pdf)**

## Explore PyRIT

- **[Scanner](https://microsoft.github.io/PyRIT/latest/scanner/scanner/):**
  Run repeatable assessments from the command line.
- **[CoPyRIT GUI](https://microsoft.github.io/PyRIT/latest/gui/gui/):**
  Explore AI systems interactively and review results in your browser.
- **[Python framework](https://microsoft.github.io/PyRIT/latest/code/framework/):**
  Compose and extend components to build custom red teaming workflows.

Visit our [website](https://microsoft.github.io/PyRIT/) for installation, configuration,
demos, and documentation. Use the version picker to match the docs to your installation;
the guides above follow the latest development version.

## Models and systems

Connect to **OpenAI, Anthropic, Azure OpenAI, Microsoft Foundry, Azure Machine Learning,
Google Gemini, Google Vertex AI, AWS Bedrock, Hugging Face, Ollama, Groq, OpenRouter,
Cohere, and GitHub Copilot**.

Use OpenAI-compatible APIs or built-in targets, or install the optional
[LiteLLM adapter](https://github.com/microsoft/PyRIT/blob/main/pyrit/prompt_target/litellm_chat_target.py)
for additional provider integrations. See the
[provider configuration guide](https://microsoft.github.io/PyRIT/latest/getting-started/populating-secrets/)
and [LiteLLM provider directory](https://docs.litellm.ai/docs/providers) for setup and more providers.
Available features depend on the target and model.

Test complete applications and other endpoints through
[HTTP](https://microsoft.github.io/PyRIT/latest/code/targets/http-target/),
[WebSockets](https://microsoft.github.io/PyRIT/latest/code/targets/websocket-target/),
[browser automation with Playwright](https://microsoft.github.io/PyRIT/latest/code/targets/playwright-target/),
or [custom targets](https://microsoft.github.io/PyRIT/latest/code/targets/custom-targets/).

## Research and citation

Read [PyRIT: Democratizing AI Red Teaming Through Open-Source Tooling](https://commandline.microsoft.com/wp-content/uploads/2026/08/PyRIT_Whitepaper_2026.pdf)
for the design principles and real-world experience behind the toolkit.
If you use PyRIT in your research, please cite the paper using
[`CITATION.cff`](./CITATION.cff).

## Security

To report vulnerabilities in PyRIT privately through the Microsoft Security Response
Center, follow our [security policy](https://github.com/microsoft/PyRIT/security/policy).

## Trademarks

This project may contain trademarks or logos for projects, products, or services.
Authorized use of Microsoft trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must
not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's
policies.
