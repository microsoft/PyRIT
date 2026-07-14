---
applyTo: "pyrit/setup/initializers/techniques/**"
---

# Setup Technique Catalog Guidelines

**Responsibility**: This directory is the canonical catalog of `AttackTechniqueFactory`
instances — the declarative "which attack, configured how" entries that scenarios select by
name. Each group module (`core.py`, `extra.py`) exposes `get_technique_factories()`;
`technique_initializer.py` aggregates the groups, injects the group name as a technique tag,
and registers them into the `AttackTechniqueRegistry`.

**Does not own** (see [framework.md](../../doc/code/framework.md)): attack algorithms,
converters, scorers, or datasets. A factory only *packages* existing bricks as configuration.
If an entry needs new branching, prompt scaffolding, or transformation logic, that logic
belongs in an attack / converter / scorer — not here. See also
[scenarios.instructions.md](scenarios.instructions.md) for the `AttackTechniqueFactory` API.

## Define factories inline

Define each `AttackTechniqueFactory` **inline** in the `get_technique_factories()` list, not in
a per-technique `_build_<name>_technique()` helper. A factory is declarative configuration, so
keeping every entry in the one list makes the whole catalog readable at a glance.

**No module-level constants or helper functions for factory arguments.** Do not hoist a
factory's arguments — YAML paths, turn counts, converter stacks, path-building helpers like
`_role_play_yaml(name)`, or numeric constants like `ROLE_PLAY_SIMULATED_NUM_TURNS` — into
module-level names or functions. Even when several factories repeat the same literal (e.g. the
same `next_message_system_prompt_path` or `num_turns=2`), write the literal inline in each entry.
Repetition here is intentional: it keeps every factory self-contained and readable at a glance,
and it is the single most common thing that gets "snuck back in" during a refactor. If you catch
yourself extracting a `_CONSTANT`, a `_build_*`/`_*_yaml` helper, or a shared config object,
stop — put the value back inline.

- If an entry needs a few lines of setup (a `seed_technique`, a converter stack), prefer a
  reusable model/config constructor over a bespoke module-level builder — e.g.
  `AttackTechniqueSeedGroup.from_system_prompt(...)` for a system-prompt technique. If you find
  yourself writing a builder, consider whether the reusable piece belongs on the model instead.
- Reach for a helper function **only** when construction is genuinely dynamic (e.g. a
  `@cache`-decorated builder for a dynamically-enumerated set of techniques). A helper that just
  concatenates a path or returns a fixed literal does not qualify — inline it.
- Keep entries free of long explanatory comments and paper citations. The factory should read as
  plain configuration; if a technique's mechanics are subtle, document them on the reusable
  converter/model it uses, or here in this file, not as an inline comment block.
