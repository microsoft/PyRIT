# Registry

Registries in PyRIT provide a centralized way to discover, manage, and access components. They support lazy loading, singleton access, and metadata introspection.

## Why Registries?

- **Discovery**: Automatically find available components (scenarios, scorers, etc.)
- **Consistency**: Access components through a uniform API
- **Metadata**: Inspect what's available without instantiating everything
- **Extensibility**: Register custom components alongside built-in ones

## Two Types of Registries

PyRIT has two registry patterns for different use cases:

| Type | Stores | Use Case |
|------|--------|----------|
| **Class Registry** | Classes (type[T]) | Components instantiated with user-provided parameters |
| **Instance Registry** | Pre-configured instances | Components requiring complex setup before use |

## Common API

Class catalogs share an interface for discovery and metadata:

| Method | Description |
|--------|-------------|
| `get_registry_singleton()` | Get the singleton registry instance |
| `get_class_names()` | List all registered class names |
| `get_all_registered_class_metadata()` | Get constructor metadata for registered classes |
| `reset_registry_singleton()` | Reset the singleton (useful for testing) |

Registries that also store configured objects expose `get_names()` and
`list_metadata()` through `.instances`.

This makes it easy to write code that inspects any registry:

```python
from pyrit.registry import ScenarioRegistry


def show_registry_contents(registry) -> None:
    for name in registry.get_class_names():
        print(name)


show_registry_contents(ScenarioRegistry.get_registry_singleton())
```


## Key Difference with Class and Instance Registries

| Aspect | Class Registry | Instance Registry |
|--------|----------------|-------------------|
| Stores | Classes (type[T]) | Instances (T) |
| Registration | Automatic discovery | Explicit via `register()` |
| Returns | Class to instantiate | Ready-to-use instance |
| Instantiation | Caller provides parameters | Pre-configured by initializer |
| When to use | Self-contained components with deferred configuration | Components requiring constructor parameters or compositional setup |

## Named Component Construction

Converter, target, and scorer registries use `InstanceHoldingRegistry` to build
components and store them in their `.instances` registry. Use
`create_named_instance(name=..., type_name=..., params=...)` to build and register
a component in one operation. The instance registry stores objects; it does not
construct them.

Duplicate names raise `ValueError`. Use `.instances.register(..., replace=True)`
only when replacement is intended. Converter and target registries also reject
reserved route names such as `catalog` and `types`. Use `.instances.unregister(name)`
to remove an instance.

The instance registry checks and inserts each name under one lock. Concurrent
creation can build more than one component for the same name, but only one
registration succeeds unless replacement is requested. The backend does not
need a separate registration lock.

Constructor annotations define parameter metadata and coercion. Enum parameters
accept member names or values. Types that inherit `StructuredParameterValue` declare their
allowed variants through `get_registry_input_variants()`; the registry
derives each variant's constructor fields and accepts `{ "type": "<name>",
"parameters": { ... } }`. Word-selection strategies use this shared mechanism.
Both enums and structured inputs also accept existing Python objects. Use `Path` for a
local file input. Use `Path | str` when a component also supports a remote URL.
For this union, the registry preserves the supplied type: a `Path` stays a `Path`,
and a string stays a string. It never passes a URL through `Path`. Both union
orders have the same metadata, `type_name: "Path | str"`, including after a JSON
round-trip. Optional forms accept `None` in Python; the display type omits `None`,
as it does for other optional parameters.

The backend exposes scorer types at `GET /api/scorers/types`, lists registered
instances at `GET /api/scorers`, retrieves one at `GET /api/scorers/{name}`, and
creates one with `POST /api/scorers` (`name`, `type`, and optional `params`). The
type response uses the registry's shared `Parameter` contract. Instance responses
include the complete `ScorerIdentifier`, including nested scorer and target
identifiers; target identifiers apply their existing credential-exclusion rules.
Construction and reference resolution remain owned by `ScorerRegistry`.
Scorer list responses build identifiers only for the requested page. Runtime
replacement clears the cached scorer service so requests use the new registry.

The backend owns file-upload handling and cleanup, not the registry. See the
[registry API migration notes](../../gui/0_gui.md#registry-api-migration-notes)
for the REST contract and temporary compatibility behavior.

## Attack Class Registry

`AttackRegistry` discovers concrete `AttackStrategy` classes from
`pyrit.executor.attack`. It stores classes, not live attack instances, and has no
`.instances` property. Discovery works when `AttackTechniqueRegistry` is empty.
Discovery and constructor metadata do not construct attacks or call targets.

Use `get_class_names()`, `get_class()`, and
`get_all_registered_class_metadata()` to inspect the class catalog. Use
`register_class(CustomAttack, name="custom")` to add a custom class.
`get_registry_singleton()` returns the shared registry;
`reset_registry_singleton()` clears it. PyRIT setup also resets this registry.

After PyRIT initialization, build an attack with constructor arguments:

```python
from pyrit.executor.attack import AttackConverterConfig
from pyrit.registry import AttackRegistry, TargetRegistry

# objective_target is an existing configured target.
TargetRegistry.get_registry_singleton().instances.register(objective_target, name="objective")
attack = AttackRegistry.get_registry_singleton().create_instance(
    "PromptSendingAttack",
    objective_target="objective",
    attack_converter_config=AttackConverterConfig(),
    max_attempts_on_failure=1,
)
```

`objective_target` accepts a registered target name or a live target object.
Simple scalar inputs use the shared resolver. Pass live Python configuration
objects, such as `AttackAdversarialConfig`, `AttackConverterConfig`, and
`AttackScoringConfig`, for nested components. Advanced Python values, such as a
prompt normalizer or a parameter class, pass through unchanged. The shared
resolver also constructs explicitly declared structured configuration variants.
Use technique definitions for JSON-native, reusable attack configurations.

An attack class implements the conversation algorithm. An attack technique
factory selects and configures that class, converters, scorers, and seeds.
`AttackTechniqueRegistry` continues to store those factories separately.

## Technique Definitions

`TechniqueDefinition` is a JSON-native construction contract under
`pyrit.models.technique_definition`. It holds names, tags, attack arguments,
factory options, and typed technique seeds. Models do not import attacks,
resolve components, or select modules.

`AttackTechniqueRegistry.build_from_definition()` resolves the registered attack
class and its declared inputs without registering or constructing an attack.
`register_definition()` also checks selector collisions and registers the factory
atomically. A failed definition leaves no partial entry. Existing Python factory
registration and live configuration values remain supported.
The selectors `all` and `default` are reserved. `types` is also a reserved name.
Names and tags cannot conflict with existing selectors or differ only by letter case.

After initialization, register this definition through Python or send its JSON
representation to `POST /api/techniques`:

```python
from pyrit.models.technique_definition import TechniqueDefinition
from pyrit.registry import AttackTechniqueRegistry

definition = TechniqueDefinition.model_validate({
    "name": "encoded_example",
    "description": "Encode the request with an existing converter.",
    "tags": ["custom"],
    "attack_type": "PromptSendingAttack",
    "attack_args": {
        "attack_converter_config": {
            "type": "AttackConverterConfig",
            "parameters": {
                "request_converters": [{
                    "type": "ConverterConfiguration",
                    "parameters": {
                        "converters": ["registered_base64", "registered_base64"],
                        "indexes_to_apply": [],
                    },
                }],
                "response_converters": [],
            },
        },
        "prepended_conversation_config": {
            "type": "PrependedConversationConfig",
            "parameters": {"apply_converters_to_roles": ["user"]},
        },
    },
    "factory_options": {"use_score_as_feedback": False},
    "seed_technique": {
        "insertion_index": 0,
        "prompt_placement": "prepend",
        "seeds": [{
            "type": "SeedPrompt",
            "parameters": {
                "value": "A static prefix",
                "role": "system",
                "is_general_technique": True,
            },
        }],
    },
})
factory = AttackTechniqueRegistry.get_registry_singleton().register_definition(definition)
```

The converter name must already be registered. Converter order and duplicate
entries are retained. An explicit empty `indexes_to_apply` list applies the
converter to no pieces; it is not the same as omitting the field.

Supported structured inputs include `AttackConverterConfig`,
`ConverterConfiguration`, `AttackScoringConfig`, `TAPAttackScoringConfig`,
and `PrependedConversationConfig`. Scoring configurations accept registered
scorer names, including ordered auxiliary-scorer lists. The factory keeps the
existing execution scorer-override policy (`warn`, `raise`, or `skip`).

Seed groups accept declared `SeedPrompt` and `SeedSimulatedConversation`
variants. A simulated-conversation seed uses typed `SeedPrompt` values for
`adversarial_chat_system_prompt`, `simulated_target_system_prompt`, and optional
`next_message_system_prompt`. It also accepts `num_turns` and `sequence`.
Seed validators check general-technique flags, roles, sequence overlap, and
conversation constraints. Legacy server file-path inputs are not accepted by
this contract. Seed construction does not generate a conversation.

For adversarial attacks, `factory_options` accepts an optional registered
`adversarial_chat` name and inline strings or typed `SeedPrompt` values for
system, seed, and per-turn prompts. The objective target belongs to execution
and cannot be supplied in `attack_args`. A default adversarial target is resolved
only when needed at execution.

Custom registered attack classes use the same contract. A custom configuration
can inherit `StructuredParameterValue` and declare its allowed variants, or
trusted Python code can call `register_structured_input()` in
`pyrit.registry.resolution` to declare variants and nested component references.
Only server-declared types can be constructed. Clients cannot select modules,
execute code, or deserialize arbitrary objects. Callables and other Python-only
values need a Python initializer or programmatic factory.

`GET /api/techniques/types` exposes the shared `Parameter` metadata and the
definition schema. The GUI is a basic subset of this contract; it is not the
backend's construction boundary. `GET /api/techniques` and detail responses
use `AttackTechniqueFactory.get_configuration()` for safe display. Identity
hashes and display projections are not lossless construction recipes.

New definitions are **runtime only**. They are lost on restart or setup reset.
The instance registry exposes a mutation revision. Compatible scenario enums,
metadata, estimates, and summary caches use this revision and registry identity,
including direct `.instances.register()` calls and registry replacement.
Old scenario instances retain their technique snapshot. Active tasks are not
cancelled on catalog changes. Scenario filters and fixed local catalogs remain
in control of which techniques they support.

## See Also

- [Class Registries](1_class_registry.ipynb) - ScenarioRegistry, InitializerRegistry
- [Instance Registries](2_instance_registry.ipynb) - ConverterRegistry, ScorerRegistry, TargetRegistry
