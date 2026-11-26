# Frequently Asked Questions

Short answers to questions that come up often, each with a link to the page that covers the topic in depth. Use the documentation that matches your installed PyRIT version. If your question is not here, ask on [Discord](https://discord.gg/9fMpq3tc8u) or [open an issue](https://github.com/microsoft/PyRIT/issues).

## Getting Started

### I am new to AI red teaming. Where do I start?

Read [Learn AI Red Teaming](./red_teaming/0_red_teaming.md). It covers how to approach the work before you pick a tool, and it ends with a list of guides, talks, and places to practice.

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

### Do I need to set up a database?

No. `await initialize_pyrit_async(memory_db_type=IN_MEMORY)` gives you an in-memory database that needs no setup and is discarded when the process ends. The `sqlite` backend keeps results in a local file. Azure SQL is optional and is mainly useful when several people share results. See [Memory](./code/memory/0_memory.md).

### Where does my `.env` file go?

In `~/.pyrit/.env`. PyRIT finds it there automatically. Values in `~/.pyrit/.env.local` override it, which is useful for local changes. To load files from other locations, list them under `env_files` in `~/.pyrit/.pyrit_conf`. See [Populating Secrets](./getting_started/populating_secrets.md) and [Configuration File](./getting_started/pyrit_conf.md).

### Do I need all optional dependencies and credentials?

No. Install the extras that the components you choose need, and configure credentials only for the services those components call. For example, `pip install "pyrit[litellm]"` and `pip install "pyrit[playwright]"` are separate extras, and Playwright also needs `playwright install` for the browsers. Calling Hugging Face's hosted OpenAI-compatible endpoint needs no extra, but loading model weights locally needs the `huggingface` extra.

A workflow may need credentials for helper models, such as the adversarial model and the scorer, as well as for the system you test. You do not need to fill in every entry of the example environment file. See [Install PyRIT](./getting_started/install.md) and [Populating Secrets](./getting_started/populating_secrets.md), then the guide of each component you use.

### Is PyRIT free, and what can a run cost?

PyRIT is open source under the [MIT license](https://github.com/microsoft/PyRIT/blob/main/LICENSE). The services it calls are not necessarily free: external APIs, hosted infrastructure, and local compute can all cost money.

One objective can lead to many calls. Generating attack prompts, scoring responses, converting inputs, and retrying requests all add to the calls sent to the system you test. Start with a small run, and check the usage and billing controls of every service involved. [Request rate limits](./code/targets/9_rate_limiting.ipynb) control how often PyRIT sends requests. They are not a spending cap.

## Models and Providers

### Do I need a Microsoft, Azure, or OpenAI account? Which providers can I connect to?

No particular provider account is required. The accounts and credentials you need depend on the services you choose. For example, you can connect to a local Ollama endpoint without an Azure subscription or an OpenAI API key.

The `OpenAI*` targets, such as `OpenAIChatTarget`, are named after the OpenAI API, not the company. Many providers serve that API, the chat completions part in particular: OpenAI, Azure OpenAI, Anthropic, Amazon Bedrock, Google Gemini, Groq, OpenRouter, Hugging Face's hosted router, and local runners such as Ollama all do. `OpenAIChatTarget` works with any of them.

Set `OPENAI_CHAT_ENDPOINT`, `OPENAI_CHAT_KEY`, and `OPENAI_CHAT_MODEL` for the provider you use. For OpenAI itself, the endpoint is `https://api.openai.com/v1`. [Populating Secrets](./getting_started/populating_secrets.md) has a tab with the exact values for each provider.

The optional `LiteLLMChatTarget` is another route. It reaches providers such as Anthropic, AWS Bedrock, Google Vertex AI, and Cohere through LiteLLM, with each provider's own authentication. The [target API reference](./api/pyrit_prompt_target.md) lists the targets available in your version.

These are ways to connect. They do not guarantee that every model supports the same features or request parameters. Some optional features use Azure services, such as Azure SQL as a memory backend or Azure AI Content Safety for scoring.

### Can I use local models or run without cloud services?

Yes. [Ollama](./getting_started/populating_secrets.md) and [LM Studio](./code/targets/1_openai_chat_target.ipynb) serve local models through an OpenAI-compatible endpoint, so `OpenAIChatTarget` works with both. `HuggingFaceChatTarget` loads a Hugging Face model directly in your Python process. See [HuggingFace Chat Target](./code/targets/use_huggingface_chat_target.ipynb).

A local target alone does not make a run offline. The system you test is only one part of a workflow. The model that generates attack prompts, a model-based scorer, or a converter may each call a separate service, and datasets or model weights may need to be downloaded. Check every component you selected before you assume that nothing leaves your machine. The [Framework](./code/framework.md) overview shows how the components fit together.

### Can I test a complete application instead of a bare model?

Yes. A target is anything you can send a prompt to, so PyRIT can send prompts to your application's own interface and evaluate its responses. Which target to use depends on how the application is exposed:

- [`HTTPTarget`](./code/targets/10_http_target.ipynb) for an HTTP endpoint.
- [`WebsocketTarget`](./code/targets/websocket_target.ipynb) for a WebSocket protocol.
- The [Playwright target](./code/targets/10_1_playwright_target.ipynb) for an application that only has a web UI.

PyRIT does not discover how to operate an arbitrary application. You may need to supply authentication, request builders, response parsers, session handling, or browser interaction logic. If none of these targets fit, [write a custom target](./code/targets/6_custom_targets.ipynb) by subclassing `PromptTarget` and implementing `_send_prompt_to_target_async`.

If your application returns its answer through a second request, for example by polling with a conversation ID, make both calls inside that method and return the final text. PyRIT stores and scores whatever the target returns.

### Can I test agents and copilots?

Yes. `A2ATarget` talks to agents that expose the A2A protocol. See [Prompt Targets](./code/targets/0_prompt_targets.md). For Microsoft Copilots there are the [Playwright Copilot target](./code/targets/10_2_playwright_target_copilot.ipynb) and the [WebSocket Copilot target](./code/targets/10_3_websocket_copilot_target.ipynb). For any other agent, use `HTTPTarget`, the Playwright target, or a custom target, as described in the previous answer.

Before you test a hosted agent or copilot, read its terms of service and find out what they allow. Do not attack a product without understanding them. See [Am I allowed to test any AI system with PyRIT?](#faq-permission)

### Can I test image, audio, or video?

Yes. Targets, converters, and scorers can all be multimodal. [Prompt Targets](./code/targets/0_prompt_targets.md) lists the multimodal targets, and the [Realtime target](./code/targets/realtime_target.ipynb) holds audio conversations. Converters can turn a text prompt into another modality. See the [audio](./code/converters/2_audio_converters.ipynb), [image](./code/converters/3_image_converters.ipynb), and [video](./code/converters/4_video_converters.ipynb) converter guides.

### Does every target support every attack and modality?

No. Compatibility depends on both the PyRIT target and the endpoint or model behind it: the input and output types, system messages, structured output, and how conversations are handled. For example, a text-only WebSocket application does not accept audio, and an application that remembers a conversation does not necessarily let an attack edit that history.

Some attacks need capabilities that others do not, so choosing a provider is not enough to establish compatibility. See [Target Capabilities](#target-capabilities) for what each capability means, and the [capability guide](./code/targets/6_1_target_capabilities.ipynb) for how to inspect and validate a target.

### Do the adversarial model and the scorer have to be the same model as the target?

No. There are up to three roles: the objective target is the system you are testing, the adversarial target generates attack prompts, and the scoring target judges responses. Each can be a different model. With `pyrit_scan` you pick them with `--target`, `--adversarial-target`, and `--scoring-target`. See [Scanner](./scanner/0_scanner.md).

The adversarial target works best without content moderation, so that it does not refuse to write attack prompts. See [Multi-Turn Attacks](./code/executor/2_multi_turn.ipynb).

That said, the choice of scoring model matters too. A scorer has to read harmful responses in order to judge them. If your scoring model sits behind a content filter or rejects harmful inputs, it may block or refuse exactly the responses that matter most, and that affects your results.

### Where do I get an adversarial model?

Adaptive attacks need a model that writes attack prompts, and most hosted models with safety training refuse to do that. A model with fewer restrictions works better. There are two places to look:

- **Open-weight models.** Hugging Face hosts abliterated models, which are open-weight models modified so that they refuse less. They can be a decent starting point. You can run one on your own machine or deploy it yourself.
- **Hosted providers.** Several providers serve such models behind an OpenAI-compatible API, so `OpenAIChatTarget` works with them. Providers that have been suggested to us include [Abliteration.ai](https://abliteration.ai), [AbliteratedLLM](https://abliteratedllm.dev), and [DemonRoute](https://demonroute.com), listed alphabetically. [Issue #791](https://github.com/microsoft/PyRIT/issues/791) collects more pointers.

:::{important}
We do not endorse, recommend, or vouch for any of these models or providers. This list only reflects what others have suggested to us, and being listed says nothing about quality, safety, or suitability. Any interaction is between you and the provider: their terms, pricing, and handling of your data are yours to review, and using them is your decision and at your own risk.
:::

A model that refuses less is not automatically a good attacker. It may be less consistent at reaching the objective, and the best choice depends on the objective, the technique, and the target. The `AdversarialBenchmark` scenario compares candidates for you. See [Benchmark](./scanner/benchmark.ipynb) and [Choosing Adversarial Models](./blog/2026_09_03_adversarial_model_selection.md).

### How do I deal with rate limits?

Both sides are configurable:

- **Request rate.** Set `max_requests_per_minute` on the target to stay under your provider's limit. See [Rate Limiting](./code/targets/9_rate_limiting.ipynb).
- **Retries.** When a provider still returns a rate limit error, the target retries automatically with exponential backoff. Set the number of attempts and the wait time with the `RETRY_MAX_NUM_ATTEMPTS`, `RETRY_WAIT_MIN_SECONDS`, and `RETRY_WAIT_MAX_SECONDS` environment variables. See [Resiliency and Retry](./code/setup/2_resiliency.ipynb).

### Can I control model parameters such as temperature or reasoning effort?

Generally yes. Which parameters exist depends on the target. `OpenAIChatTarget` takes `temperature`, `top_p`, `max_completion_tokens`, and more when you create it, and `extra_body_parameters` for anything else the provider accepts. `OpenAIResponseTarget` adds `reasoning_effort` and `reasoning_summary` for reasoning models. On targets that support it, you can also override temperature for a single request.

See [OpenAI Chat Target](./code/targets/1_openai_chat_target.ipynb), [OpenAI Responses Target](./code/targets/2_openai_responses_target.ipynb), and [Prompt Targets](./code/targets/0_prompt_targets.md).

### Can I authenticate to Azure without an API key?

Yes. For Azure resources you can use Entra authentication through the Azure CLI. Run `az login`, then pass `api_key=get_azure_openai_auth(endpoint)` when you create the target. `get_azure_openai_auth` is in `pyrit.auth`. You do not need an API key for those resources. See [OpenAI Chat Target](./code/targets/1_openai_chat_target.ipynb) for an example and [Configuration File](./getting_started/pyrit_conf.md) for the configuration side.

Key-based authentication is inherently less secure than identity-based authentication. An API key is a long-lived secret that anyone who obtains it can use, and it has to be stored somewhere. We encourage everyone to use identity-based authentication wherever the provider offers it. Azure does.

## Running Attacks and Reading Results

### Where are my results stored?

In PyRIT's memory. Every prompt, response, and score is written there automatically. Memory has three backends, selected with `memory_db_type`: `in_memory`, `sqlite`, and `azure_sql`. Many examples in the docs use the in-memory backend, which loses everything when the process ends. Use `sqlite` or `azure_sql` to keep results. See [Memory](./code/memory/0_memory.md) and [Configuration File](./getting_started/pyrit_conf.md).

### How do I view or export results?

It depends on how you ran the attack:

- **Scanner.** `pyrit_scan scenario-history` lists recent runs. `pyrit_scan scenario-results <id>` shows one, and `--format json` or `--format html` with `--output` writes it to a file. See [PyRIT Scan](./scanner/1_pyrit_scan.ipynb).
- **Framework.** The [output module](./code/output/0_output.ipynb) prints attack results, conversations, and scores, and can write them to a file. You can also [query memory directly](./code/memory/4_manually_working_with_memory.md).
- **GUI.** The History tab in Attacks or Scanner shows saved results with their conversations and scores. See [GUI](./gui/0_gui.md).

### How do I run attacks in parallel?

With `pyrit_scan`, pass `--max-concurrency`. Scenarios take a `max_concurrency` setting, which defaults to 4. In the framework, `AttackExecutor(max_concurrency=...)` runs several attacks at once. Higher concurrency reaches your provider's rate limit sooner, so set it together with `max_requests_per_minute`. See [PyRIT Scan](./scanner/1_pyrit_scan.ipynb), [Scenarios](./code/scenarios/0_scenarios.ipynb), and [Rate Limiting](./code/targets/9_rate_limiting.ipynb).

### Why did an attack from the docs not work when I ran it?

Models differ, and so do versions of the same model and the safety systems around them. Responses also vary from one run to the next. The outputs in the docs were recorded when a notebook was last run, against the model the author used. A different result on your side is expected.

The examples are also not chosen to show the most successful attacks. We do not necessarily want to publish harmful outputs in public documentation, so an example in the docs may show a refusal or a mild result on purpose.

Try the attack more than once, try other converters and attack techniques, and try a multi-turn attack. Read the conversation to see where the target pushed back. An attack that does not succeed is information about that attack, not proof that the system is safe. See [Mix Human-Led and Automated Red Teaming](./red_teaming/0_red_teaming.md).

### Does the scorer affect the attack, or does it only grade the result?

Both. In every attack, the objective scorer decides whether the objective was met. In adaptive multi-turn attacks it also drives the loop: the attack keeps going until the scorer says the objective was met or the turn limit is reached. A scorer that judges wrongly can therefore end an attack too early or keep it running. See [Multi-Turn Attacks](./code/executor/2_multi_turn.ipynb) and [Scoring](./code/scoring/0_scoring.ipynb).

## Scoring

### How much can I trust the scorers?

Not blindly. Most scorers use a model to judge another model's output, and they make mistakes in both directions. PyRIT publishes metrics that show how well scorer configurations agree with human labels. See [Scorer Metrics](./code/scoring/4_scorer_metrics.ipynb) and the blog post [Scoring Scorers](./blog/2026_04_14_scoring_scorers.md). Whatever the metrics say, read a sample of the conversations yourself.

### Can I score without calling a model?

Yes. Scorers such as `SubStringScorer` and `RegexScorer` check the response text directly and need no model. They are fast and deterministic, and they fit objectives with a clear textual success condition, such as a secret appearing in the output. See [True/False Scorers](./code/scoring/1_true_false_scorers.ipynb).

## Troubleshooting

### Something is not working. How do I narrow it down?

Work from the bottom up:

1. **Check your version and how you installed it.** Many problems come from mixing a release from PyPI with docs or examples for `main`, or the other way around. Print `pyrit.__version__` and read the docs for that version.
2. **Check initialization.** Call `await initialize_pyrit_async(...)` before you create targets, scorers, or attacks.
3. **Test the target on its own.** Send one simple prompt straight to the target with a `PromptSendingAttack`, with no converters and no scorer. If that fails, the problem is the connection. An authentication error from the provider usually means that the endpoint, key, or model name is not what the provider expects.
4. **Add one piece at a time.** Add the scorer, then converters, then the multi-turn attack, and see which step breaks.

If it still fails, [open an issue](https://github.com/microsoft/PyRIT/issues) with your version, a minimal example, and the full error. For installation problems, see [Troubleshooting](./getting_started/troubleshooting/README.md).

## Error Messages

Specific errors and what they mean.

### `Central memory instance has not been set`

PyRIT was not initialized. Call `await initialize_pyrit_async(...)` once, before you create targets, scorers, or attacks. The message mentions `set_memory_instance`, but `initialize_pyrit_async` does that for you. Older examples call `initialize_pyrit`, which no longer exists. See [Memory](./code/memory/0_memory.md) and [Configure PyRIT](./getting_started/configuration.md).

### `ImportError: cannot import name ...` after upgrading

Class and module names have changed between releases. For example, orchestrators became attacks under `pyrit.executor.attack`. Older tutorials, blog posts, and issue threads may still use the old names. The [release notes](https://github.com/microsoft/PyRIT/releases) list breaking changes for each release. Make sure the docs you are reading match your installed version.

## Versions and Upgrading

### Which package and documentation versions should I use? The docs do not match what I have installed.

The documentation site hosts several versions. Use the version picker to choose the release that matches your installation, and use notebooks from that same release. `latest` describes development on the `main` branch, which is not necessarily the package published on PyPI. You can check your version with `python -c "import pyrit; print(pyrit.__version__)"`.

Python requirements, optional dependencies, target APIs, and provider SDK compatibility can differ between releases. Do not assume that an example from `latest` works unchanged with an older installation. See "Matching Notebooks to Your Version" in [User Local Installation](./getting_started/install_local.md).

### How do I install the latest code from `main`?

Clone the repository and run `uv sync` in it. See [Contributor Local Installation](./getting_started/install_local_dev.md), or [Contributor Docker Installation](./getting_started/install_devcontainers.md) if you prefer a dev container. `pip install pyrit` always gives you the latest stable release.

### When is the next release?

We do not publish a release schedule. Watch the [releases page](https://github.com/microsoft/PyRIT/releases) on GitHub to be notified. If you need a change that has been merged but not released, install from `main`.

## Getting Help and Contributing

### Where do I ask a question or report a bug?

Ask questions on [Discord](https://discord.gg/9fMpq3tc8u). Report bugs and request features through [GitHub issues](https://github.com/microsoft/PyRIT/issues), and search the existing issues first. Do not report security vulnerabilities in a public issue. Follow the [Security](./security.md) page instead. For setup problems, check [Troubleshooting](./getting_started/troubleshooting/README.md).

### Can I contribute if I am not an experienced red teamer?

Yes. You do not need to be a Microsoft employee or an experienced red teamer. See [Contributing](./contributing/README.md), which links to issues marked "help wanted" and "good first issue".

(faq-permission)=
### Am I allowed to test any AI system with PyRIT?

Only test systems that you own or have explicit permission to test. For hosted models, agents, and copilots, read the terms of service and usage policies first, and stay within what they allow. See [Questions to Ask Before You Start](./red_teaming/0_red_teaming.md).
