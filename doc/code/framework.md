# Framework

Learn how to use PyRIT's components to build red teaming workflows.

:::::{grid} 1 1 2 3

::::{card} 📦 Datasets
:link: ./datasets/0_dataset
Load, create, and manage seed datasets for red teaming campaigns.
::::

::::{card} 📋 Scenarios
:link: ./scenarios/0_scenarios
Run standardized evaluation scenarios at scale across harm categories.
::::

::::{card} 🧩 Attack Techniques
:link: ./scenarios/0_attack_techniques
Package an executor, converters, datasets, and strategy into a single named attack.
::::

::::{card} ⚔️ Executors and Attacks
:link: ./executor/0_executor
Run single-turn and multi-turn attacks — Crescendo, TAP, Skeleton Key, and more.
::::

::::{card} 🔄 Converters
:link: ./converters/0_converters
Transform prompts with text, audio, image, and video converters.
::::

::::{card} 🔌 Targets
:link: ./targets/0_prompt_targets
Connect to OpenAI, Azure, Anthropic, HuggingFace, HTTP endpoints, and custom targets.
::::

::::{card} 📊 Scorers
:link: ./scoring/0_scoring
Evaluate AI responses with true/false, Likert, classification, and custom scorers.
::::

:::::

---

The cards above link to a guide for each component. The rest of this page is the contributor guide: the principles every change follows and what each part of PyRIT owns. It is a north star for the architecture we are building toward. Some code doesn't match yet, and **Framework Plans** list the main gaps. Follow this page rather than copying an exception.

Update this page rarely. Change it only when maintainers agree to change a principle or a component's responsibility, or when a Framework Plan is done. Don't add a feature's details, examples, or edge cases; they belong in that component's guide.

# Principles

These decisions apply to every change. The [style guide](../contributing/3_style_guide), [notebook guide](../contributing/7_notebooks), and [unit test guide](../contributing/5_unit_tests) cover the details.

## Code

- **Give each responsibility one owner.** Put new code in the component that owns the responsibility, as described below. If nothing fits, raise it with the maintainers instead of stretching a component.
- **Keep logic in the library.** The backend, CLI, and frontend only expose and present library behavior. Implement new behavior in the owning library package, with any shared types in `pyrit.models`, then use it from the surfaces.
- **Reuse and compose before adding.** Express new capability as a configuration of existing pieces: an attack technique instead of a new attack, a composite of scorers instead of a new scorer type, an existing model instead of a near-copy. Add a class only when composition can't express it.
- **Be consistent, but do what's best.** Match the patterns in the code you change, so PyRIT reads as one codebase. Consistency never outranks doing what's best: when a better way exists, take it, and move existing uses to it in their own PR, not inside an unrelated change.
- **Don't over-engineer.** Build what the change needs now, as simply as possible. Don't add options, layers, abstractions, or branches for hypothetical needs. Handle the cases that occur in practice, and reject the rest with a clear error.
- **Fail fast.** Validate inputs and configuration at construction or at the boundary, and raise specific, actionable errors. Never guess: report an outcome you can't determine as undetermined or as an error, not as false.
- **Fail loudly rather than run partially.** If configuration a run needs, persisted state, or initialization is broken, refusing to start or returning 503 until it is fixed is the correct default, not a defect. Don't add fallbacks, partial-availability paths, or opt-in strict modes just to stay up; a different failure boundary needs an explicit requirement. Declared policies, such as skipping incompatible techniques or adapting to a target's capabilities, aren't fallbacks. Scope each failure to what actually failed, and leave a supported way to recover.
- **Write functions that do one thing and read clearly.** Use small functions, descriptive names, and explicit typed parameters. Readable beats clever.

## Security

- **Trust operators with the data.** PyRIT is a red teaming tool, not a consumer app. Operators need everything a test produces: prompts, responses (including harmful content), errors, and scorer rationales. Don't redact, mask, or truncate what operators need; needless redaction breaks tests and causes bugs. A shortened preview is fine when the full value stays available.
- **Secure the real boundaries.** Never expose PyRIT's own credentials, such as API keys and tokens, in logs, errors, identifiers, or results; secrets that appear in target output are test evidence. In shared deployments, authenticate users, and keep server-level powers, such as changing configuration, reading the server's files, or running code on the server, behind explicit administrator or configuration control. Treat target output as data: escape it when rendering, and never run it as code outside a sandbox.

## Docs

- **Keep docs short, and let the code explain itself.** Show how to use something, not how it works inside or how it was built. Write the docstrings the style guide requires, and keep them short. Otherwise prefer clear names and types to comments, and comment only to explain why.
- **Don't center docs on the feature you just built.** Put feature details in that component's guide, and add to shared pages only what every reader needs.
- **Keep notebooks runnable.** Notebooks execute in a couple of minutes, and each percent-format `.py` file stays in sync with its `.ipynb` pair.

## Tests

- **Make tests reliable.** A unit test passes or fails the same way on every run: no network, sleeps, timing races, or shared state. Treat a flaky test as a bug: fix the cause, and don't hide it with retries or longer timeouts. Only tests against live services may rerun, and only for the service's nondeterminism.
- **Test one behavior per test.** Name the test for that behavior, and parametrize instead of copying tests.
- **Don't over-test.** Test behavior through public interfaces, including failure paths. Skip private details, behavior that belongs to our dependencies, and redundant permutations. Unit tests provide coverage; integration tests prove that real systems work together.

## Pull requests

- **Make each PR one unit of value, end to end.** Like a test, a PR does one thing. Solve one problem completely, with its tests, and docs when users need them, in the simplest form the next change can build on. Split unrelated work into separate PRs, and handle edge cases when they matter, not up front.

# Architecture

:::{div}
:class: col-page-right

![PyRIT's architecture. Surfaces (Python and notebooks, or the CLI and GUI through the backend REST API) run scenarios and attacks and keep domain logic in the library. A scenario packages the complete run: dataset objectives, the objective target, and scorers are run inputs, while each attack technique packages an attack with its converters, attack prompts, and adversarial or helper targets. In the shared library, the registry instantiates components and memory stores conversations, scores, and results.](../assets/pyrit-components.svg)

:::

Surfaces run the core components, and the shared library supports both. The orchestration layers nest from broadest to narrowest, and each owns less than the one above it:

- **Scenario**: what to run. It pairs datasets with attack techniques, supplies the objective target and scorers, and owns parallelism and resiliency.
- **Attack technique**: a named, reusable recipe. It configures one attack with its converters, attack prompts, and helper targets.
- **Attack**: the engine. It runs the conversation and decides each next step from scores.

Converters, targets, and scorers are the bricks that attacks use. The registry builds components, and memory holds the shared record of conversations, scores, and results.

(core-components)=
# Core Components

Core components are pluggable bricks. Each one can be swapped, combined, or replaced with a no-op, such as a converter that doesn't convert or a target that only prints. Most contributions belong in one of them. If yours doesn't fit, open an issue so we can discuss the architecture, and tell us what you want, or don't want, under Framework Plans.

## [Datasets](./datasets/0_dataset)

**Responsibility**: Define, load, and store seeds: the objectives, prompts, and conversations that attacks use.

- Load datasets into memory through providers; scenarios do this when they prepare a new run. Everything else takes dataset seeds, such as objectives and attack prompts, as arguments or from memory, never from providers or file paths. Packaged helper prompts, such as a scorer's system prompt, load from their YAML files.
- Use the seed type that matches the data: `SeedObjective`, `SeedPrompt`, `SeedSimulatedConversation`, or a group such as `SeedGroup` or `AttackSeedGroup`.
- Attach an objective's expected result as typed conditions (`SeedObjective.conditions`). Execution passes them to scorers as a `ScoringExpectation`.
- For generated datasets, the generation strategy owns model calls and validation; the provider turns its results into typed seeds.
- Decompose a multi-step objective into child `SeedObjective`s with ordered `DecompositionPath`s (objective decomposition). The dataset defines the tree; executing its paths belongs to an attack.
- **Does not own**: choosing which seeds a run uses (scenarios and attack techniques) or rendering prompts at send time (converters and normalizers).

**Framework Plans**:

- Manage datasets at scale and load them more intelligently. Expect some redesign here.
- Finish moving components from file paths and providers to seeds passed in or read from memory.
- Express expected results as objective conditions in every executor, including benchmarks, instead of adding new seed types.
- Execute objective decomposition paths in attacks (likely compound attacks), and reuse the same parent/child model for findings.

**Contributing (difficulty easy)**: Add seed dataset providers, prompts, and jailbreak templates for what you're testing.

## [Scenarios](./scenarios/0_scenarios)

**Responsibility**: Run PyRIT against something. A scenario turns user input into datasets paired with attack techniques, and runs them against an objective target with its scorers.

- **Declare the scenario; let the base class run it.** Subclass `Scenario`, give the base its technique class, default datasets, and objective scorer, and implement `_build_atomic_attacks_async`, its one required hook. Override optional hooks, such as `additional_parameters()`, only when you need them. For a technique × dataset matrix, the build is one call to `build_matrix_atomic_attacks`; `Cyber` is a good model.
- Leave the run to the base class: run parameters (including the objective target), dataset preparation, concurrency, retries, resume, and results. Don't rebuild any of it in a subclass. If the base can't express what you need, propose a base change in its own PR.
- Decide whether to skip or fail on incompatible techniques.
- Keep the results of a run independent; none depends on another. Anything that adapts to results within a run belongs in an attack, such as a compound attack.
- **Does not own**: conversation logic, new attack algorithms, or new datasets. A scenario selects and packages existing techniques and datasets.

**Framework Plans**:

- Scenarios are still new. Expect refactoring as patterns settle.

**Contributing (difficulty medium)**: Is there a scanner that does something PyRIT doesn't? Add it as a scenario. Expect more churn here than in other components.

## [Attack Techniques](./scenarios/0_attack_techniques)

**Responsibility**: Package a named, reusable attack: an attack class plus its configuration (converters, attack prompts, and helper targets). The technique is the recipe; the attack is the engine.

- Express a new attack as a technique whenever an existing attack can run it.
- Leave the objective target and scorers to the scenario. A technique may only choose whether the attacker sees score feedback.
- Define techniques as `AttackTechniqueFactory` instances: configuration written in code. The catalog lives in `pyrit/setup/initializers/techniques/`. `core.py` is a small, curated default set; `extra.py` holds the broad collection; source modules such as `airt.py` hold techniques that one source owns but others can reuse.
- Tags describe techniques (`single_turn`, `multi_turn`, `airt`) and select subsets of them. Each scenario declares its own default by name or tag; there is no global `default` tag.
- Declare target and dataset requirements on the technique. Targets own their capabilities and adapters.
- **Does not own**: the conversation algorithm. Sending, branching, and scoring decisions live in the attack it configures.

**Framework Plans**:

- Grow `extra` toward hundreds of techniques while keeping `core` small, and make subsets easy to select (tags, initializers, CLI) without slow, noisy default runs.

**Contributing (difficulty easy)**: Add an `AttackTechniqueFactory` to `extra.py`, or to a source module like `airt.py` if one source owns it. Keep a technique that only one scenario can use with that scenario, and promote it to the catalog when others could reuse it. Reserve `core` for the curated set, and never tag a catalog technique `default`.

## [Executors and Attacks](./executor/0_executor)

**Responsibility**: An executor is an algorithm for interacting with an objective target: an objective and configuration go in, and a result comes out. Attacks are the most common executors; workflows, benchmarks, and prompt generators are the others.

- An attack manages the conversation to reach its objective. It sends through the prompt normalizer with the converters it was given, and decides success and strategy from scores, not by inspecting raw responses; "Was this blocked?" is a scorer's question. Forwarding a reply to the adversarial chat, or stopping on an error response, is fine.
- Put every decision that depends on an earlier result in an attack: branching, choosing the next prompt, or switching strategy. Compound attacks combine other attacks.
- Pass the objective's `ScoringExpectation` to scorers. The objective scorer decides success and must cover every condition. Auxiliary scorers are optional diagnostics: give each one only the conditions it supports, and skip it when a condition it requires is absent.
- Each execution owns its conversations and has a unique `attack_result_id`. To reuse history in another execution, a child attack, or a branch, check the target with `validate_history_replay()`, then copy the history under a new conversation ID.
- Support every modality, and adapt to target capabilities instead of assuming a text chat.
- **Does not own**: packaging. System prompts, role-play framing, converter stacks, and dataset selection come from the attack technique. An attack doesn't build its own components (the registry does) or format and persist results (output and memory do).

**Framework Plans**:

- Move attacks that are really configurations, such as `ManyShotJailbreakAttack` and `SkeletonKeyAttack`, into attack techniques.
- Look for algorithms that can merge. Are Crescendo and TAP ultimately the same?
- Use target capabilities implicitly, so attacks need no per-target options.
- Give benchmarks and other non-attack executors the same end-to-end support as attacks, using objective conditions for expected results.
- Keep adding flexible compound attacks.

**Contributing (difficulty high)**: Most new attacks should be techniques. If you hit a limitation, open an issue.

## [Converters](./converters/0_converters)

**Responsibility**: Transform a prompt into something else: rephrase, encode, translate, or change its modality (for example, text to audio, an image, or a document). Converters can be stacked.

- Take a value and its data type, and return the converted value and its data type. A converter may call a target to do the work, such as an LLM that rephrases.
- Make each converter do one transformation. Build combinations by stacking converters, not by adding modes to one.
- **Does not own**: conversation state or decisions. A converter doesn't branch on results, score, persist to memory (the normalizer does), or decide when it runs (the attack technique configures the stack).

**Framework Plans**:

- Refactor the converter pipeline so partial and conditional conversion are more dynamic. Some of this works today, but it's limited.

**Contributing (difficulty low)**: The pattern is well-defined. Add conversions that help attacks.

## [Targets](./targets/0_prompt_targets)

**Responsibility**: Send a prepared `Message` and return the response. A target is usually an LLM, but it can be any endpoint: an HTTP API, an agent, or a storage account for cross-domain prompt injection. Attacks, converters, and scorers all use targets.

- Declare capabilities (`TargetCapabilities`, such as modalities, multi-turn, system prompts, and editable history) so other components can check compatibility before they run.
- Adapt messages to what the target supports with its `TargetConfiguration` and message normalizer.
- Return what the target produced. A response can hold several pieces or tool calls, or none.
- Return a content-filter block as a blocked response (`error="blocked"`), not an exception, so scorers can detect it.
- If the target runs tools itself, bound its tool loop. When the target's own tool infrastructure fails (`ToolInfrastructureError`), answer every pending call and end the turn with an error response that says why (`ToolLoopStop`), instead of asking the model to fix it.
- Retry only transient target errors, such as rate limits.
- **Does not own**: what to send or what to do with the response. Converting (converters), scoring (scorers), conversation flow (attacks), and persistence (the prompt normalizer) belong elsewhere.

**Framework Plans**:

- Better agent support may need more on a `Message`.
- Better surface support may need richer return types.

**Contributing (difficulty low)**: The pattern is well-defined. Before adding a target, check whether an existing one already reaches your model, for example through an OpenAI-compatible endpoint.

## [Scorers](./scoring/0_scoring)

**Responsibility**: Tell the attack what happened. Was this blocked? Was the objective achieved? Was this tool called? Was this file written?

- Score the evidence a `Scorable` identifies (a message, a trace, a file location, or a program run) against an optional `ScoringExpectation`: the objective and its typed conditions.
- Build on a result family: `TrueFalseScorer`, `FloatScaleScorer`, or `MultiLabelTrueFalseScorer` for independent labeled verdicts, where `TrueFalseScoreSelector` picks the verdict an attack uses. `MessageScorer` adds message handling. Declare the evidence a scorer reads, such as roles and data types, instead of making callers filter it.
- Score what the target actually produced. Simulated and injected messages are excluded unless a scorer explicitly selects them.
- Compose instead of special-casing. A condition-based leaf checks exactly one condition type, and a leaf configured through its constructor takes none. Composites and wrappers, such as `TrueFalseCompositeScorer` and `TrueFalseInverterScorer`, combine results and route conditions. Every condition must reach a leaf that supports it.
- Acquire evidence through injected sources and clients (traces, file surfaces, program execution), and judge what they return. The caller owns the environment: isolation, workspaces, and sandboxes.
- Report evidence that couldn't be acquired or judged as undetermined, never as false.
- Record what was judged (the expectation and, where captured, an `Observation` of the evidence) so a score can be audited, matched, or replayed without calling the target again.
- **Does not own**: acting on its result (the attack branches) or aggregating across runs (analytics). A scorer may call a target to judge, but it never sends the attack's objective prompt or manages the conversation.

**Framework Plans**:

- Snapshot media evidence the way text and file evidence are snapshotted, so media observations can be captured.

**Contributing (difficulty low)**: The pattern is well-defined. Make probabilistic scorers more accurate, or add scorers for evidence you want to decide on.

# Shared Library

These modules support every component.

## [Memory](./memory/0_memory)

**Responsibility**: Persist and retrieve everything that flows between components: seeds, prompts, responses, conversations, scores, and results. Memory is the shared record that lets components stay swappable while sharing context.

- Access memory through `CentralMemory`. Share conversation and result state through memory, not by passing it between components. Inputs such as seeds and expectations are still passed as arguments.
- Treat memory as canonical. Never write derived views, such as normalized payloads or reports, back to it.
- Keep backends (SQLite, Azure SQL) swappable without changing the components that use them.
- Own storage: queries, transactions, consistent reads, and migrations. Return data, not conclusions.
- **Does not own**: business logic or decisions. Components decide; memory stores.

## [Models](../contributing/11_memory_models)

**Responsibility**: Define the core types that PyRIT shares (messages, seeds, scores, results, and identifiers) once, and use them everywhere, including the REST API, so representations don't drift.

- Before writing a class that overlaps an existing one, or passing a dict across a boundary, use or extend a model here.
- Depend only on the standard library, pydantic, and `pyrit.common`; the import-boundary test enforces this. Each model owns its validation.

## [Registry](./registry/0_registry)

**Responsibility**: Build and store components.

- Build every component that comes from user input (configuration, REST, or the CLI) through the registry, and store reusable instances there.
- Construction doesn't require registration; the caller owns temporary instances.
- Accept only explicitly supported external inputs, and leave argument validation to constructors.

## [Normalizers](./targets/11_message_normalizer)

**Responsibility**: Move messages between attacks and targets in the right shape.

- `PromptNormalizer` applies converters, persists requests and responses, and sends to the target. Targets never persist messages.
- Message normalizers reshape a conversation into what a target accepts. They own representation, not attack policy or conversation state.
- **Does not own**: the conversation of record. Memory is canonical; a normalized payload is a temporary view.

## [Exceptions](../contributing/9_exception)

**Responsibility**: Define PyRIT's exception hierarchy and the retry behavior built on it.

- Retry only known, transient exceptions, at the layer that owns them: targets retry rate limits, components that parse model output retry invalid JSON, and scenarios retry failed attacks. Let everything else propagate, except content-filter blocks, which targets return as blocked responses.
- When you raise a PyRIT exception, include context (a status code, a clear message, and the raising component from `ExecutionContext`) so failures point to their source. Invalid arguments stay `ValueError`.

## [Setup](./setup/0_setup)

**Responsibility**: Bootstrap a PyRIT session: memory, defaults, and registered components.

- Start with `initialize_pyrit_async`, or reproduce a session from a configuration file. By default, the file is `~/.pyrit/.pyrit_conf`, and environment variables come from `~/.pyrit/.env` and `~/.pyrit/.env.local`.
- Write a `PyRITInitializer` to configure one part of PyRIT, such as targets, scorers, techniques, or datasets. Teams package their own defaults as initializers.

## [Output](./output/0_output)

**Responsibility**: Render results (attack and scenario results, conversations, scores, and findings) in any format to any destination.

- Keep the format (pretty, Markdown, JSON, or HTML) separate from the sink (stdout, a file, or a notebook). Surfaces use these renderers instead of writing their own.
- **Does not own**: what or when to render. Formatters turn data into strings and sinks write them. Formatters never fetch data, touch `CentralMemory`, or print; only `*MemoryPrinter` classes read memory.

**Contributing (difficulty low)**: Adding a format or sink is well-defined. Give every new domain printer a matching convenience function in `helpers.py`.

## [Analytics](./analytics/0_attack_results)

**Responsibility**: Answer questions across stored results. Which technique works best? How often did an objective succeed? Which responses match known content?

- Work after the fact, on stored results. Decisions during an attack are a scorer's job.
- Implement each statistic once and share it across the SDK, backend, reports, and GUI (for example, `compute_outcome_statistics` and `compute_scenario_statistics`). When memory must also aggregate in SQL for performance, a parity test keeps the two in step.
- Get data from memory; analytics owns the calculations. Don't duplicate them in the backend, CLI, GUI, or elsewhere in the SDK.

## Auth

**Responsibility**: Authenticate to the external services PyRIT calls, behind a common `Authenticator`. Components get credentials through these helpers instead of handling tokens themselves.

## Code Execution (Experimental)

**Responsibility**: Run programs through caller-configured execution backends and return typed results. The API (`pyrit._code_execution`) is private and may change without deprecation.

- Give every component that runs code (targets, converters, dataset generators, and scorers) the same injected client contract, `SandboxClient`. Select a runtime profile by name; the client owns its configuration.
- Leave environments to the caller, who provisions and isolates them. The one exception is `DockerSandboxClient`, which manages disposable containers from a caller-supplied daemon and pinned image.
- **Does not own**: interpreting results, scoring criteria, or persistence. The consumer does.

# Application Surfaces

Surfaces put PyRIT in front of people. Keep them thin: they expose and present the library and own no domain logic.

## Backend

**Responsibility**: Expose the library through a FastAPI REST API for the GUI and CLI.

The backend is an adapter, not a second library. It owns HTTP and process concerns: routes, request validation and limits, authentication, error mapping, background runs, and runtime lifecycle. Everything else belongs in the library.

- **Implement new behavior in the library first.** A new scoring rule, attack option, statistic, or result type goes in its library package (`pyrit.score`, `pyrit.executor`, `pyrit.analytics`, `pyrit.memory`, and so on), with tests there. Types stored in memory or exchanged over REST, including CLI request bodies, go in `pyrit.models`; component configuration stays in its own package. Then expose it through a thin route.
- **Ask whether a notebook user would want it.** If so, it belongs in the library, not the backend.
- **Don't add domain models.** `pyrit/backend/models` holds backend-only envelopes, such as pagination and presentation views, built from `pyrit.models` types. If a backend model or a service's dataclass would describe a domain concept, put it in `pyrit.models` instead.
- Keep each layer thin. Routes and middleware handle HTTP, services call the library for a request, and mappers translate library objects into API views. None of them make domain decisions.
- Build components through the registry and persist through memory, as everywhere else.

**Framework Plans**:

- Move domain logic that has accumulated in backend services into the library. Examples include chat scoring selection and result reuse in `ChatScoringService`, and the Word finding export in `finding_export.py`, which belongs in `pyrit.output`.

## [Frontend](../gui/0_gui)

**Responsibility**: Provide CoPyRIT, the graphical interface for human-led red teaming, on top of the backend REST API. It's built with TypeScript, React, Vite, and Fluent UI.

- **Present; don't decide.** Render what the API returns, and send the user's intent back. Don't compute outcomes, statistics, identities, or validation rules; if the UI needs a derived domain value, have the backend provide it from the library. Deriving display state while rendering is fine.
- **Build from reusable pieces.** Reuse shared components and hooks instead of copying them for each feature: one viewer, table, or dialog per concept. For example, saved scenario results reuse Chat's read-only viewer.
- Keep components presentational. Put data fetching and shared state in hooks, make every API call through `src/services/api.ts`, and mirror the API's types in `src/types`.
- Run the frontend and backend together with `dev.py`. Test with Jest (unit) and Playwright (end to end).

**Framework Plans**:

- Move domain logic out of the frontend, for example the conversation export formats in `conversationExport.ts`, which `pyrit.output` owns.

## [CLI](../scanner/0_scanner)

**Responsibility**: Provide command-line entry points. `pyrit_scan` and `pyrit_shell` are thin REST clients over the backend.

- Run PyRIT through the backend, and depend only on `pyrit.models`, `pyrit.common`, and `pyrit.output`. Administrative tools such as `pyrit_migrate` are the exception.
- Keep startup fast. Defer imports until after argument parsing, and never import `pyrit.models` or other heavy modules at module level; the CLI import-guard tests enforce this.

# Conventions

These patterns cut across components. Use them instead of inventing a local variant.

- **Pass in built components.** Attacks, scorers, converters, and targets accept the components they use as objects: a scorer rather than scorer settings, a target rather than an endpoint, and a `SeedPrompt` or seed rather than a YAML path. A component may build its own default, as Crescendo builds a default objective scorer, as long as a caller can pass an object that replaces it.
- **Match requirements to capabilities, not types.** Components declare what they need and what they offer: target requirements and capabilities, seed group requirements, scorer condition types and evidence, and converter input and output types. Shared checks, such as `check_target_compatibility` and `filter_seed_groups`, match them before any model call, then adapt, skip, or fail according to the caller's policy. Don't branch on a component's concrete class or class name; checking data types or abstract families is fine.
- **Make identifiers complete and stable.** A component's identifier deterministically records every setting that shapes its output. It leaves out runtime settings, such as rate limits, retries, and timeouts, and never holds a credential, so rotating a key keeps the identity. Results record the identifiers of what produced them, even on error. Identity is fixed at construction: to change an identity-bearing setting, build a new instance.
- **Resolve defaults in one order.** An explicit argument wins, then an initializer default (`@apply_defaults`), then the environment variable that the class names, read at construction, then a declared default or an error. Components don't read `os.environ` anywhere else; setup, the backend, and the CLI read process settings at startup.
- **Give data PyRIT queries a typed field.** If memory queries, filters, or sorts by a value, make it a typed field on the narrowest model that owns it (`MessagePiece`, not `Message`). Other structured data travels in metadata through a value object (below). Labels are user tags: PyRIT stores and filters them for users but never derives behavior from them.
- **Let one value object own each metadata key.** A small model in `pyrit.models` declares a namespaced key and is the only code that reads or writes it, through `to_metadata()` and `from_metadata()`; see `ToolExecutionMetadata`. A missing key, or a legacy value from older rows, reads as unset.
- **Keep shared components free of per-call state.** Registered targets, converters, and scorers serve concurrent callers, so instance state must be safe to share: configuration, caches, clients, and maps keyed by conversation or attempt are fine. Keep per-call data in arguments, the attack context, or memory.
- **Keep helper-model prompts in YAML seeds.** Prompts for judges, converters, and adversarial chat are YAML `SeedPrompt`s that declare their parameters. Render them only through `SeedPrompt`'s sandboxed rendering, never with f-strings or `str.format`; per-call content, such as the text being judged, can be plain message text.
- **Declare structured output, and parse it once.** When you need JSON from a model, ask for it with `JsonResponseConfig`, and let the target enforce or adapt it. Parse each kind of response in one function that raises `InvalidJsonException`, and retry through `send_json_with_retry_async`, which starts each retry from a clean history.
- **Store media through serializers.** Write media and other files that results reference only through `data_serializer_factory` into results storage, point to them by the returned path or URL, and read them back the same way. Don't persist base64, temporary files, or paths outside results storage.
- **Use aware UTC timestamps.** Create them with `datetime.now(tz=UTC)`, keep them in typed fields, and convert to local time only for display.
