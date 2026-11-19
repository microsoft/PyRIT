# Frequently Asked Questions

Short answers to questions that come up often, each with a link to the page that covers the topic in depth. If your question is not here, ask on [Discord](https://discord.gg/9fMpq3tc8u) or [open an issue](https://github.com/microsoft/PyRIT/issues).

## Getting Started

### I am new to AI red teaming. Where do I start?

Read [Learn AI Red Teaming](./red_teaming/0_red_teaming.md). It covers how to approach the work before you pick a tool, and [Learning Resources](./red_teaming/1_resources.md) lists videos, guides, and practice labs.

### What is the fastest way to try PyRIT?

Run `pip install pyrit`, set `OPENAI_CHAT_ENDPOINT`, `OPENAI_CHAT_KEY`, and `OPENAI_CHAT_MODEL` for a model you have access to, and initialize PyRIT. [Configure PyRIT](./getting_started/configuration.md) shows the three lines of Python that follow. If you would rather not set up Python, the [Docker installation](./getting_started/install_docker.md) gives you a container with JupyterLab.

### Should I use the Scanner, the GUI, or the Framework?

It depends on what you want to do:

- The [Scanner](./scanner/0_scanner.md) (`pyrit_scan`) runs built-in scenarios against a target from the command line. Use it for automated and repeatable runs.
- The [GUI](./gui/0_gui.md) (CoPyRIT) is for manual, human-led testing in the browser. It can also launch scans and review their results.
- The [Framework](./code/framework.md) is the Python library. Use it when you need a custom attack, target, converter, or scorer.

Most people use more than one. See [Mix Human-Led and Automated Red Teaming](./red_teaming/0_red_teaming.md) for why.

### Which Python versions are supported?

Python 3.11, 3.12, 3.13, and 3.14. See [User Local Installation](./getting_started/install_local.md).

### Do I need an Azure subscription?

No. PyRIT works with any OpenAI-compatible API, including models that run on your own machine. Some optional features use Azure services, such as Azure SQL as a memory backend or Azure AI Content Safety for scoring.

## Models and Providers

### Can I use PyRIT with an OpenAI API key?

Yes. Set `OPENAI_CHAT_ENDPOINT` to `https://api.openai.com/v1`, `OPENAI_CHAT_KEY` to your key, and `OPENAI_CHAT_MODEL` to the model name. See [Populating Secrets](./getting_started/populating_secrets.md).

### Can I use Anthropic, Google Gemini, Amazon Bedrock, Groq, or OpenRouter?

Yes. Each of these offers an OpenAI-compatible endpoint, and `OpenAIChatTarget` works with any of them. You set the same three environment variables and point the endpoint at the provider. [Populating Secrets](./getting_started/populating_secrets.md) has a tab with the exact values for each provider.

### Can I run everything against a local model?

Yes. [Ollama](./getting_started/populating_secrets.md) and [LM Studio](./code/targets/1_openai_chat_target.ipynb) serve local models through an OpenAI-compatible endpoint, so `OpenAIChatTarget` works with both. `HuggingFaceChatTarget` loads a Hugging Face model directly in your Python process. See [HuggingFace Chat Target](./code/targets/use_huggingface_chat_target.ipynb).

### My application is not a chat model API. Can I still test it?

Yes. A target is anything you can send a prompt to. Start with [`HTTPTarget`](./code/targets/10_http_target.ipynb) if the application is a plain HTTP endpoint, or the [Playwright target](./code/targets/10_1_playwright_target.ipynb) if it only has a web UI. If neither fits, [write a custom target](./code/targets/6_custom_targets.ipynb) by subclassing `PromptTarget` and implementing `_send_prompt_to_target_async`.

If your application returns its answer through a second request, for example by polling with a conversation ID, make both calls inside that method and return the final text. PyRIT stores and scores whatever the target returns.

### Do the adversarial model and the scorer have to be the same model as the target?

No. There are up to three roles: the objective target is the system you are testing, the adversarial target generates attack prompts, and the scoring target judges responses. Each can be a different model. With `pyrit_scan` you pick them with `--target`, `--adversarial-target`, and `--scoring-target`. See [Scanner](./scanner/0_scanner.md).

The adversarial target works best without content moderation, so that it does not refuse to write attack prompts. See [Multi-Turn Attacks](./code/executor/2_multi_turn.ipynb), and [Choosing Adversarial Models](./blog/2026_09_03_adversarial_model_selection.md) for how to compare candidates.

### How do I stay within my provider's rate limit?

Set `max_requests_per_minute` on the target. See [Rate Limiting](./code/targets/9_rate_limiting.ipynb).

### Can I authenticate to Azure without an API key?

Yes. For Azure resources you can use Entra authentication through the Azure CLI. Run `az login`, and you do not need to set API keys for those resources. See [Configuration File](./getting_started/pyrit_conf.md).

## Running Attacks and Reading Results

### Where are my results stored?

In PyRIT's memory. Every prompt, response, and score is written there automatically. Memory has three backends, selected with `memory_db_type`: `in_memory`, `sqlite`, and `azure_sql`. Many examples in the docs use the in-memory backend, which loses everything when the process ends. Use `sqlite` or `azure_sql` to keep results. See [Memory](./code/memory/0_memory.md) and [Configuration File](./getting_started/pyrit_conf.md).

### How do I view or export results?

It depends on how you ran the attack:

- **Scanner.** `pyrit_scan scenario-history` lists recent runs. `pyrit_scan scenario-results <id>` shows one, and `--format json` or `--format html` with `--output` writes it to a file. See [PyRIT Scan](./scanner/1_pyrit_scan.ipynb).
- **Framework.** The [output module](./code/output/0_output.ipynb) prints attack results, conversations, and scores, and can write them to a file. You can also [query memory directly](./code/memory/4_manually_working_with_memory.md).
- **GUI.** The History tab in Attacks or Scanner shows saved results with their conversations and scores. See [GUI](./gui/0_gui.md).

### Why did an attack from the docs not work when I ran it?

Models differ, and so do versions of the same model and the safety systems around them. Responses also vary from one run to the next. The outputs in the docs were recorded when a notebook was last run, against the model the author used. A different result on your side is expected.

Try the attack more than once, try other converters and attack techniques, and try a multi-turn attack. Read the conversation to see where the target pushed back. An attack that does not succeed is information about that attack, not proof that the system is safe. See [Mix Human-Led and Automated Red Teaming](./red_teaming/0_red_teaming.md).

### Does the scorer affect the attack, or does it only grade the result?

Both. In every attack, the objective scorer decides whether the objective was met. In adaptive multi-turn attacks it also drives the loop: the attack keeps going until the scorer says the objective was met or the turn limit is reached. A scorer that judges wrongly can therefore end an attack too early or keep it running. See [Multi-Turn Attacks](./code/executor/2_multi_turn.ipynb) and [Scoring](./code/scoring/0_scoring.ipynb).

## Scoring

### How much can I trust the scorers?

Not blindly. Most scorers use a model to judge another model's output, and they make mistakes in both directions. PyRIT publishes metrics that show how well scorer configurations agree with human labels. See [Scorer Metrics](./code/scoring/4_scorer_metrics.ipynb) and the blog post [Scoring Scorers](./blog/2026_04_14_scoring_scorers.md). Whatever the metrics say, read a sample of the conversations yourself.

### Can I score without calling a model?

Yes. Scorers such as `SubStringScorer` and `RegexScorer` check the response text directly and need no model. They are fast and deterministic, and they fit objectives with a clear textual success condition, such as a secret appearing in the output. See [True/False Scorers](./code/scoring/1_true_false_scorers.ipynb).

## Versions and Upgrading

### The docs do not match what I have installed. Why?

The documentation site hosts several versions. Use the version picker to choose the release that matches your installation, or `latest` if you installed from the `main` branch. You can check your version with `python -c "import pyrit; print(pyrit.__version__)"`.

### I get an import error after upgrading. What changed?

Class and module names have changed between releases. For example, orchestrators became attacks under `pyrit.executor.attack`. Older tutorials, blog posts, and issue threads may still use the old names. The [release notes](https://github.com/microsoft/PyRIT/releases) list breaking changes for each release. Make sure the docs you are reading match your installed version.

### How do I install the latest code from `main`?

Clone the repository and run `uv sync` in it. See [Contributor Local Installation](./getting_started/install_local_dev.md), or [Contributor Docker Installation](./getting_started/install_devcontainers.md) if you prefer a dev container. `pip install pyrit` always gives you the latest stable release.

### When is the next release?

We do not publish a release schedule. Watch the [releases page](https://github.com/microsoft/PyRIT/releases) on GitHub to be notified. If you need a change that has been merged but not released, install from `main`.

## Getting Help and Contributing

### Where do I ask a question or report a bug?

Ask questions on [Discord](https://discord.gg/9fMpq3tc8u). Report bugs and request features through [GitHub issues](https://github.com/microsoft/PyRIT/issues), and search the existing issues first. Do not report security vulnerabilities in a public issue. Follow the [Security](./security.md) page instead. For setup problems, check [Troubleshooting](./getting_started/troubleshooting/README.md).

### Can I contribute if I am not an experienced red teamer?

Yes. You do not need to be a Microsoft employee or an experienced red teamer. See [Contributing](./contributing/README.md), which links to issues marked "help wanted" and "good first issue".

### Am I allowed to test any AI system with PyRIT?

Only test systems that you own or have explicit permission to test, and read the usage policies of any hosted model you use. See [Questions to Ask Before You Start](./red_teaming/0_red_teaming.md).
