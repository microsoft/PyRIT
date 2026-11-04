# Setup

PyRIT setup involves three main components to get you started with security testing:

1. **Configuration** - Initialize PyRIT with environment variables, database, and defaults
2. **Resiliency** - Understand retry mechanisms and error handling
3. **Customization** - Learn about default values and custom initializers

## Quick Start

For the fastest setup, use `TargetInitializer` and `ScorerInitializer`, which require only basic OpenAI environment variables:

```python
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[TargetInitializer(), ScorerInitializer()])
```

This explicitly registers environment-configured targets and scorers. Configure the
role-specific target settings needed by your scan; an incomplete optional provider is
not automatically replaced by `OPENAI_CHAT_MODEL`.

## Explicit scan targets and migration

Scenario discovery, descriptions, dataset selection, and offline estimates do not
require model credentials. Execution requires the dependencies of the selected
scenario and techniques, resolved before prompt generation or run admission:

| Role | Configure explicitly |
|------|----------------------|
| Objective target | Select the registered system to test. |
| Objective scorer | Supply a compatible true/false scorer, or use the scenario's real scoring rubric. |
| Scoring target | For a model-based default rubric, select `scoring_target` or register `objective_scorer_chat`. A deterministic scorer needs no model. |
| Generation target | For selected generative attacks, converters, or simulated conversations, select a generation target or register `adversarial_chat`. A plain single-turn technique needs none. |

**Breaking change:** missing helper defaults no longer construct `OpenAIChatTarget`
implicitly, even if `OPENAI_CHAT_*` exists. The objective target is never silently
reused as a helper. To reuse one target deliberately, select it separately for each
required role.

There are two supported setup workflows:

- In CoPyRIT, create/register targets with explicit provider connection fields, then
  select them in the scan or suite form. Target and scorer creation returns to the
  same configuration draft; cancelling preserves the draft.
- Deliberately run `TargetInitializer`, `ScorerInitializer`, or your custom
  initializer before scanning. Per-target environment namespaces remain supported,
  as do intentionally invoked Python constructors such as `OpenAIChatTarget()`.
  Merely setting ambient OpenAI variables does not configure scan roles.

Python callers may supply a scorer directly, set `scoring_target` in the scenario
parameter bag, and scope generation with
`override_default_adversarial_target(generation_target)` around initialization.
Call `scenario.get_readiness()` to inspect effective bindings and all discoverable
role issues without generating prompts. `initialize_async()` independently enforces
the same rules. Scorers retain scenario-specific questions, refusal handling, and
blocking policies; a missing role never disables scoring.

An already registered default scorer owns its rubric and scoring target. If you
also select a conflicting `scoring_target`, readiness rejects that configuration
instead of silently ignoring the selection or rebuilding the rubric. Clear the
target selection to keep the registered scorer, or select a scorer explicitly.

Readiness uses each scenario's execution-time factory precedence and validates
the actual selected generation consumers, including simulated conversations.
Native requirements (such as RedTeaming/Crescendo system prompts) cannot be
adapted; adaptable requirements use the target's configured capability policy.
`AdversarialBenchmark` validates and binds its own `adversarial_targets` registry
axis, without requiring an unrelated global generation default. Ordered additive
converters and their nested targets are also included in saved dependency
bindings, even for scenario-owned techniques.

API clients can preview `POST /api/scenarios/catalog/{name}/readiness` using the
same configuration as the estimate request, including `scoring_target_name` and
`adversarial_target_name`. Suite clients can preview
`POST /api/scenario-suites/{name}/readiness` with the reviewed suite launch request.
The suite preview may omit `target_name` while selecting roles; suite launch still
requires an explicit objective target.
Launch validates again, and suite launch rejects an invalid later child before
submitting any child. Saved runs pin effective component hashes: after a registry
reset or changed default, explicitly repair/reconfigure instead of substituting a
different target. Readiness checks configuration, not remote reachability or
authentication; provider failures remain runtime errors.

## Configuration Options

PyRIT offers flexible configuration through:
- **Environment variables** for API keys and endpoints
- **Database options** including InMemory, SQLite, and Azure SQL
- **Custom initializers** for project-specific defaults

See the detailed sections below for comprehensive setup guidance.

## Reinitializing a running backend

The CoPyRIT configuration page separates **Save** from **Reinitialize PyRIT**.
On a single-process, single-replica backend, administrators can apply saved
configuration, environment documents, and custom initializer scripts without
restarting Python. The saved `.pyrit_conf` must set `enable_live_reinitialization: true`.
Use the [GUI Configuration page](../../gui/0_gui.md#configuration) to manage these sources.

The backend first validates the saved sources without changing live setup state. It rejects live apply while any
runtime work is active. When the runtime is idle, it closes admission, checks for work again, and then rebuilds
setup-owned registries and defaults. The replacement reuses the same memory object, which preserves in-memory and
persisted history.
Memory type, connection, or storage changes require a process restart.
Library callers must provide their own admission and idle-runtime boundary
before using this configuration-level reinitialization path.

Reinitialization explicitly replaces process environment values supplied by the
selected sources, including deployment-provided values. Omitted variables remain;
there is no historical tracking or removal. Key Vault selection and `.env.local`
priority still apply, and interpolation uses newly selected values. Ordinary
initialization retains its existing environment precedence. The lower-level
`initialize_pyrit_async(reinitialize=True)` option provides replacement values and
memory reuse; `ConfigurationLoader` owns the complete registry/script rebuild.

Failure after mutation blocks backend runtime operations until a process restart.
Configuration repair stays available under the original authorization policy.
Initializer side effects and environment updates are not rolled back.
