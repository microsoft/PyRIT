---
applyTo: "pyrit/scenario/**"
---

# PyRIT Scenario Development Guidelines

Scenarios orchestrate multi-attack security testing campaigns. Each scenario groups `AtomicAttack` instances and executes them sequentially against a target.

## Base Class Contract

All scenarios inherit from `Scenario` (ABC) and must:

1. **Define `VERSION`** as a class constant (increment on breaking changes)
2. **Implement four abstract methods:**

```python
class MyScenario(Scenario):
    VERSION: int = 1

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        return MyStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        return MyStrategy.ALL

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        return DatasetConfiguration(dataset_names=["my_dataset"])

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        ...
```

## Constructor Pattern

```python
@apply_defaults
def __init__(
    self,
    *,
    adversarial_chat: PromptChatTarget | None = None,
    objective_scorer: TrueFalseScorer | None = None,
    scenario_result_id: str | None = None,
) -> None:
    # 1. Resolve defaults for optional params
    if not objective_scorer:
        objective_scorer = self._get_default_scorer()

    # 2. Store config objects for _get_atomic_attacks_async
    self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

    # 3. Call super().__init__ — required args: version, strategy_class, objective_scorer
    super().__init__(
        version=self.VERSION,
        strategy_class=MyStrategy,
        objective_scorer=objective_scorer,
    )
```

Requirements:
- `@apply_defaults` decorator on `__init__`
- All parameters keyword-only via `*`
- `super().__init__()` called with `version`, `strategy_class`, `objective_scorer`
- complex objects like `adversarial_chat` or `objective_scorer` should be passed into the constructor.

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
        # Filter by selected strategies via self._scenario_composites
        return filtered_result
```

Options:
- `dataset_names` — load by name from memory
- `seed_groups` — pass explicit groups (mutually exclusive with `dataset_names`)
- `max_dataset_size` — cap per dataset
- Override `_load_seed_groups_for_dataset()` for custom loading

## Strategy Enum

Strategies should be selectable by an axis. E.g. it could be harm category or and attack type, but likely not both or it gets confusing.

```python
class MyStrategy(ScenarioStrategy):
    ALL = ("all", {"all"})            # Required aggregate
    EASY = ("easy", {"easy"})

    Base64 = ("base64", {"easy", "converter"})
    Crescendo = ("crescendo", {"difficult", "multi_turn"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all", "easy", "difficult"}
```

- `ALL` aggregate is always required
- Each member: `NAME = ("string_value", {tag_set})`
- Aggregates expand to all strategies matching their tag

## AtomicAttack Construction

```python
AtomicAttack(
    atomic_attack_name=strategy_name,   # groups related attacks
    attack=attack_instance,             # AttackStrategy implementation
    seed_groups=list(seed_groups),       # must be non-empty
    memory_labels=self._memory_labels,   # from base class
)
```

- `seed_groups` must be non-empty — validate before constructing
- `self._objective_target` is only available after `initialize_async()` — don't access in `__init__`
- Pass `memory_labels` to every AtomicAttack

## Exports

New scenarios must be registered in `pyrit/scenario/__init__.py` as virtual package imports.

## Common Review Issues

- Accessing `self._objective_target` or `self._scenario_composites` before `initialize_async()`
- Forgetting `@apply_defaults` on `__init__`
- Empty `seed_groups` passed to `AtomicAttack`
- Missing `VERSION` class constant
- Missing `_async` suffix on `_get_atomic_attacks_async`
