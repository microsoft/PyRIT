---
applyTo: "pyrit/scenario/**"
---

# PyRIT Scenario Development Guidelines

Scenarios orchestrate multi-attack security testing campaigns. Each scenario groups `AtomicAttack` instances and executes them sequentially against a target.

**Does not own** (see [framework.md](../../doc/code/framework.md)): the per-objective conversation logic. Branching, turn-by-turn adaptation, and scoring-based decisions belong to the attack — a scenario selects and packages existing attack techniques and owns parallelism/resiliency, not new attack algorithms or datasets. Flag such bleed in review.

## Base Class Contract

All scenarios inherit from `Scenario` (ABC) and must:

1. **Define `VERSION`** as a class constant (increment on breaking changes)
2. **Optionally declare `BASELINE_ATTACK_POLICY`** (defaults to `BaselineAttackPolicy.Enabled` — a baseline `PromptSendingAttack` is prepended and callers can opt out per run by setting `include_baseline=False` in the run params, see "Run Parameters" below):
   - `BaselineAttackPolicy.Disabled` — baseline supported but off by default (e.g. `garak.doctor`, where the harm-probe technique set dominates the run).
   - `BaselineAttackPolicy.Forbidden` — baseline is meaningless for this scenario's comparison axis (e.g. `AdversarialBenchmark`, which compares against gold-standard answers). Supplying `include_baseline=True` raises `ValueError`.
3. **Pass `technique_class`, `default_technique`, and `default_dataset_config` to `super().__init__()`:**

```python
class MyScenario(Scenario):
    VERSION: int = 1
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    @apply_defaults
    def __init__(self, *, objective_scorer=None, scenario_result_id=None) -> None:
        super().__init__(
            version=self.VERSION,
            technique_class=MyTechnique,
            default_technique=MyTechnique.ALL,
            default_dataset_config=DatasetConfiguration(dataset_names=["my_dataset"]),
            objective_scorer=objective_scorer or self._get_default_objective_scorer(),
            scenario_result_id=scenario_result_id,
        )
```

For scenarios whose technique enum is built dynamically (RapidResponse pattern), build the
technique class in a module-level `@cache`-decorated function and pass the result through
the constructor — no classmethod indirection required.

4. **Implement `_build_atomic_attacks_async(self, *, context)`** — this is the single
   abstract extension point every scenario must define (see "AtomicAttack Construction" below).
   Matrix-shaped scenarios delegate to `build_matrix_atomic_attacks(context=...)` in one line.

## Constructor Pattern

```python
@apply_defaults
def __init__(
    self,
    *,
    adversarial_chat: PromptTarget | None = None,
    objective_scorer: TrueFalseScorer | None = None,
    scenario_result_id: str | None = None,
) -> None:
    # 1. Resolve defaults for optional params
    if not objective_scorer:
        objective_scorer = self._get_default_scorer()

    # 2. Store config objects for _build_atomic_attacks_async
    self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

    # 3. Call super().__init__ — required args: version, technique_class, objective_scorer
    super().__init__(
        version=self.VERSION,
        technique_class=MyTechnique,
        default_technique=MyTechnique.ALL,
        default_dataset_config=DatasetConfiguration(dataset_names=["my_dataset"]),
        objective_scorer=objective_scorer,
    )
```

Requirements:
- `@apply_defaults` decorator on `__init__`
- All parameters keyword-only via `*` — **enforced at class-definition time** by
  `Scenario.__init_subclass__` calling `enforce_keyword_only_init` (see
  `pyrit/common/brick_contract.py`). Violators raise `TypeError` at
  import time.
- **All constructor parameters must be optional** (default to `None`) so the registry can instantiate the scenario with no arguments for metadata introspection. Defer required-input validation to `initialize_async()` or `_build_atomic_attacks_async()`. `ScenarioRegistry._build_metadata` raises `TypeError` if `scenario_class()` cannot be called with no arguments.
- `super().__init__()` called with `version`, `technique_class`, `default_technique`, `default_dataset_config`, `objective_scorer`
- complex objects like `adversarial_chat` or `objective_scorer` should be passed into the constructor.

## Run Parameters

Run-time inputs (target, techniques, dataset config, concurrency, labels, baseline flag) are **not** arguments to `initialize_async`. They flow through a single parameter bag (`self.params`), populated by `set_params_from_args` from the merged CLI / config / programmatic arguments. `initialize_async` takes no arguments and reads everything from the bag:

```python
scenario.set_params_from_args(args={"objective_target": target, "max_concurrency": 8})
await scenario.initialize_async()
```

The base `Scenario` declares the common run inputs once in `_common_scenario_parameters()`: `objective_target` (a `RegistryReference` — resolved by name or supplied as an instance), the `opaque` live objects `scenario_techniques` / `technique_converters` / `dataset_config` / `memory_labels` (passed by identity, never coerced or deep-copied), and the scalars `max_concurrency` / `max_retries` / `include_baseline`.

### Declaring custom parameters — add via `additional_parameters`

The base `Scenario` composes `supported_parameters()` as `_common_scenario_parameters() + additional_parameters()`. To add your own parameters, override **`additional_parameters()`** and return just your extras — the common inputs are included for you, so there's no `super()` call to forget:

```python
@classmethod
def additional_parameters(cls) -> list[Parameter]:
    return [
        Parameter(name="max_turns", description="...", param_type=int, default=5),
    ]
```

- **Add (common case):** override `additional_parameters` and return `[Parameter(...)]`
- **Remove / replace a common input (rare):** override `supported_parameters` directly and compose against `super()`, e.g. `return [p for p in super().supported_parameters() if p.name != "dataset_config"]`

Dropping a common input is not silent: `set_params_from_args` rejects any value supplied for an undeclared parameter, so the registry/CLI/programmatic path fails loudly. If a scenario resolves its techniques differently (e.g. pairing attacks with converters), override the `_resolve_scenario_techniques` hook rather than `initialize_async` (see `RedTeamAgent`).

## Dataset Loading

Datasets are read from `CentralMemory`.

### Basic — named datasets:
```python
DatasetConfiguration(
    dataset_names=["airt_hate", "airt_violence"],
    max_dataset_size=10,  # optional: sample up to N per dataset
)
```

### Advanced — custom subclass for filtering:
```python
class MyDatasetConfiguration(DatasetConfiguration):
    def get_seed_groups(self) -> dict[str, list[SeedGroup]]:
        result = super().get_seed_groups()
        # Filter by selected techniques via self._scenario_techniques
        return filtered_result
```

Options:
- `dataset_names` — load by name from memory
- `seed_groups` — pass explicit groups (mutually exclusive with `dataset_names`)
- `max_dataset_size` — cap per dataset
- Override `_load_seed_groups_for_dataset()` for custom loading

## Technique Enum

Technique members should represent **attack techniques** — the *how* of an attack (e.g., prompt sending, role play, TAP).  Datasets control *what* is tested (e.g., harm categories, compliance topics).  Avoid mixing dataset/category selection into the technique enum; use `DatasetConfiguration` and `--dataset-names` for that axis.

```python
class MyTechnique(ScenarioTechnique):
    ALL = ("all", {"all"})                  # Required aggregate
    DEFAULT = ("default", {"default"})      # Recommended default aggregate
    SINGLE_TURN = ("single_turn", {"single_turn"})  # Category aggregate

    PromptSending = ("prompt_sending", {"single_turn", "default"})
    RolePlay = ("role_play", {"single_turn"})
    ManyShot = ("many_shot", {"multi_turn", "default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all", "default", "single_turn", "multi_turn"}
```

- `ALL` aggregate is always required
- Each member: `NAME = ("string_value", {tag_set})`
- Aggregates expand to all techniques matching their tag

### Result grouping (`display_group`)

`display_group` controls how attack results are aggregated for display. It is set per
`AtomicAttack` at construction time — there is no `_build_display_group` hook. When you build
via `build_matrix_atomic_attacks`/`MatrixAtomicAttackBuilder`, pass a `display_group_fn`
callback that maps each `MatrixCombo` to a group string:

```python
build_matrix_atomic_attacks(
    context=context,
    objective_scorer=self._objective_scorer,
    display_group_fn=lambda combo: combo.technique_name,  # default: group by technique
    # Group by dataset/harm category: lambda combo: combo.dataset_name
    # Cross-product:                   lambda combo: f"{combo.technique_name}_{combo.dataset_name}"
)
```

Note: `atomic_attack_name` must remain unique per `AtomicAttack` for correct resume behaviour.
`display_group` controls user-facing aggregation only.

## AtomicAttack Construction — `_build_atomic_attacks_async(context)`

Every scenario implements the single abstract extension point:

```python
async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
    ...
```

`initialize_async` resolves the run's inputs once (objective target, techniques, dataset
config, memory labels, baseline flag, and seed groups), snapshots them into an immutable
`ScenarioContext`, and calls this method. Each scenario emits its own baseline from within
`_build_atomic_attacks_async` (see the Baseline section below). Scenario authors never read
half-initialized `self._*` state to build attacks — read everything from `context`.

### Zero-boilerplate matrix scenarios

Scenarios whose construction is the plain technique × dataset cross-product delegate to the
`build_matrix_atomic_attacks` helper in one line (see `Cyber`, `RapidResponse`):

```python
from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks

async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
    return build_matrix_atomic_attacks(
        context=context,
        objective_scorer=self._objective_scorer,
        technique_converters=self._technique_converters,  # optional CLI converter stacks
    )
```

`build_matrix_atomic_attacks`:
1. Calls `resolve_technique_factories(context=context)` to map the selected techniques to their
   registered `AttackTechniqueFactory` instances (reads the `AttackTechniqueRegistry` singleton;
   techniques with no registered factory are dropped).
2. Iterates every (technique × dataset) pair from `context.seed_groups_by_dataset`.
3. Calls `factory.create()` with the objective target, conditional scorer override, and any
   per-technique converters (from `--techniques <technique>:converter.<name>`) as
   `extra_request_converters`.
4. Builds each `AtomicAttack` with a unique `atomic_attack_name` and a `display_group`
   (customizable via `display_group_fn`).

Scenarios needing extra axes (adversarial targets, caching, converter stacks) call
`MatrixAtomicAttackBuilder` directly; scenarios whose construction is composite or
per-objective build the `AtomicAttack` list themselves (see "Manual AtomicAttack construction").

### AttackTechniqueFactory

Techniques are described by `AttackTechniqueFactory` instances rather than a separate spec
dataclass.  The canonical catalog lives in
`pyrit.setup.initializers.techniques` (`build_technique_factories()`)
and is loaded into the registry by `TechniqueInitializer`.

```python
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

AttackTechniqueFactory(
    name="prompt_sending",                  # REQUIRED — must match the technique enum value
    attack_class=PromptSendingAttack,
    technique_tags=["core", "single_turn", "default"],
    attack_kwargs={"max_turns": 5},
    adversarial_chat=None,                  # None = resolve adversarial target lazily at create()
    seed_technique=None,
    uses_adversarial=None,                  # None = auto-derive from attack signature/seeds
    scorer_override_policy=ScorerOverridePolicy.WARN,
)
```

Key points:
- `name` is required and must match the technique enum value the scenario looks up.
- `technique_tags` on the factory drives `TagQuery` filters used by
  `AttackTechniqueRegistry.build_technique_class_from_factories(...)`. This is **distinct**
  from the per-entry `tags` argument passed to `registry.register_technique(...)`.
- `uses_adversarial` is auto-derived from the attack class signature (presence of
  `attack_adversarial_config`) and seed shape; pass `False` explicitly to opt out, or
  `True` to force opt-in.
- `kwargs` are validated against the attack class constructor signature at
  factory-construction time, so typos fail loudly and early.

The canonical catalog factories live in `pyrit/setup/initializers/techniques/`; see
[setup-techniques.instructions.md](setup-techniques.instructions.md) for how to add one
(define factories inline, prefer reusable config constructors over bespoke builders).

### Registering factories

```python
registry = AttackTechniqueRegistry.get_registry_singleton()
registry.register_from_factories(build_technique_factories())
```

`register_from_factories` reads `factory.technique_tags` to populate the per-entry tags used
by the registry. Tests that exercise scenarios should reset both `AttackTechniqueRegistry`
and `TargetRegistry` and re-register a mock `adversarial_chat` so the catalog builder
resolves without falling back to `OpenAIChatTarget`.

### Baseline

The baseline is a `PromptSendingAttack` over the run's seeds. Each scenario emits its **own**
baseline from within `_build_atomic_attacks_async`, gated on `context.include_baseline` (which
`initialize_async` resolves from the scenario's `BASELINE_ATTACK_POLICY` class var and the runtime
`include_baseline` flag). Matrix scenarios get it for free — `build_matrix_atomic_attacks` prepends
it when `context.include_baseline` is set. Hand-built scenarios prepend it themselves via the
`build_baseline_atomic_attack` helper (at index 0), reusing `context.seed_groups` so the baseline
samples the same seeds as the techniques. Emit **one baseline per independently scored population**
and only when `context.include_baseline` is set: most scenarios score a single population and emit
exactly one, while a scenario with several separately-scored populations (e.g. Psychosocial's
per-sub-harm scorers) emits one per population. Never double-emit a baseline for the *same*
population — that reintroduces baseline-vs-technique population divergence under `max_dataset_size`.

### Manual AtomicAttack construction:

```python
AtomicAttack(
    atomic_attack_name=technique_name,   # groups related attacks
    attack_technique=AttackTechnique(attack=attack_instance),  # bundles the AttackStrategy
    seed_groups=list(seed_groups),       # must be non-empty
    memory_labels=context.memory_labels, # from the context snapshot
)
```

- `seed_groups` must be non-empty — validate before constructing
- Read runtime inputs from `context`, not `self._*` — `self._objective_target` and
  `self._scenario_techniques` are only populated after `initialize_async()`
- Pass `memory_labels` to every AtomicAttack

## Exports

New scenarios must be registered in `pyrit/scenario/__init__.py` as virtual package imports.

## Common Review Issues

- Accessing `self._objective_target` or `self._scenario_techniques` before `initialize_async()`
- Overriding `supported_parameters()` without composing against `super()` (silently drops the common run inputs)
- Adding arguments back onto `initialize_async` instead of declaring them via `supported_parameters()` and reading from `self.params`
- Forgetting `@apply_defaults` on `__init__`
- Empty `seed_groups` passed to `AtomicAttack`
- Missing `VERSION` class constant
- Missing `_async` suffix on `_build_atomic_attacks_async`
