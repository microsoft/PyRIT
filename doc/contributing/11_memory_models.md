# Memory Models & Migrations

This guide covers how to work with PyRIT's memory models — where they live, how to add or update them, and how the migration system works.

## Where Things Live

| What | Path |
|---|---|
| ORM models (SQLAlchemy) | `pyrit/memory/memory_models.py` |
| Domain objects they map to | `pyrit/models/` (e.g. `MessagePiece`, `Score`, `Seed`, `AttackResult`, `ScenarioResult`) |
| Alembic migration environment | `pyrit/memory/alembic/env.py` |
| Migration revisions | `pyrit/memory/alembic/versions/` |
| Migration helpers | `pyrit/memory/migration.py` |
| CLI migration tool | `build_scripts/memory_migrations.py` |
| Schema diagram | `doc/code/memory/9_schema_diagram.md` |

## Current Models

All models inherit from the SQLAlchemy `Base` declarative class and live in `memory_models.py`:

- **`PromptMemoryEntry`** — prompt/response data (`PromptMemoryEntries` table)
- **`ScoreEntry`** — evaluation results (`ScoreEntries` table)
- **`EmbeddingDataEntry`** — embeddings for semantic search (`EmbeddingData` table)
- **`SeedEntry`** — dataset prompts/templates (`SeedPromptEntries` table)
- **`AttackResultEntry`** — attack execution results (`AttackResultEntries` table)
- **`ScenarioResultEntry`** — scenario execution metadata (`ScenarioResultEntries` table)

Each entry model has a corresponding domain object and conversion methods (e.g. `PromptMemoryEntry.__init__(entry: MessagePiece)` and `get_message_piece()`).

## Adding or Updating a Model

### 1. Edit the model

Make your changes in `pyrit/memory/memory_models.py`. Follow these conventions:

- Use `mapped_column()` with explicit types.
- Use `CustomUUID` for all UUID columns (handles cross-database compatibility).
- Add foreign keys where relationships exist.
- Include `pyrit_version` on new entry models.

### 2. Generate a migration

```bash
python -m build_scripts.memory_migrations generate -m "short description of change"
```

This creates a new revision file under `pyrit/memory/alembic/versions/`. **Review the generated file carefully** — auto-generated migrations may need manual adjustments (e.g. for data migrations or default values).

### 3. Validate the migration

```bash
python -m build_scripts.memory_migrations check
```

This verifies the schema produced by running all migrations matches the current models. Both pre-commit hooks (see below) and CI run this check.

### 4. Update the schema diagram

If you changed the schema in a meaningful way (added a table, added a foreign key, etc.), update the Mermaid diagram in `doc/code/memory/9_schema_diagram.md`.

## How Migrations Run at Startup

Schema migrations are triggered inside each memory class constructor (`SQLiteMemory.__init__` and `AzureSQLMemory.__init__`). When `skip_schema_migration=False` (the default), the inherited `_run_schema_migration()` method on `MemoryInterface` runs:

```
SQLiteMemory.__init__() / AzureSQLMemory.__init__()
  → _run_schema_migration()                      # pyrit/memory/memory_interface.py
      → run_schema_migrations(engine=...)         # pyrit/memory/migration.py
          → alembic upgrade head
      → check_schema_migrations(engine=...)       # pyrit/memory/migration.py
          → alembic check
```

Both SQLite and AzureSQL follow the same migration path: first `run_schema_migrations` applies any pending Alembic revisions (`alembic upgrade head`), then `check_schema_migrations` verifies the resulting schema matches the current models (`alembic check`). The behavior depends on database state:

| Database state | What happens |
|---|---|
| **Fresh (no tables)** | All migrations apply from scratch |
| **Already versioned** | Only unapplied migrations run (idempotent) |
| **Legacy (tables exist, no version tracking)** | Validates schema matches models, stamps current version, then upgrades. Raises `RuntimeError` on mismatch to prevent data corruption |

Migrations run inside a transaction (`engine.begin()`), so a failed migration rolls back cleanly. The version tracking table is `pyrit_memory_alembic_version`.

Users can skip migrations by passing `skip_schema_migration=True` to the memory class constructor. When using `initialize_pyrit_async()`, this can be forwarded via `**memory_instance_kwargs`:

```python
await initialize_pyrit_async("SQLite", skip_schema_migration=True)
```

## Important Rules

### Stored-result analytics queries

`pyrit.memory.attack_analytics.AttackAnalyticsReader` reads raw saved-outcome
counts, grouped metadata, lightweight result pages, and bounded facets.
`AttackAnalyticsQueryCompiler` builds the SQLite or SQL Server statements;
`analytics_sql` supplies dialect-specific JSON expressions. These internal memory
modules do not import an analytics SDK, calculate success rates, rescore outcomes,
or load scores, conversations, media, or complete `AttackResult` objects.

The query grain is a distinct stored `AttackResultEntry.id`. Different result IDs
sharing a conversation remain different results. For callers that need full result
objects, `MemoryInterface.get_attack_results(result_selection=AttackResultSelection.ALL_RESULTS)`
opts into the same identity rule. Its default remains `LATEST_PER_CONVERSATION`,
including existing History callers. Turn bounds apply after the selected identity
rule, and neither mode deletes or rewrites stored duplicates.

Revision `b6d8f0a2c4e1`, following `7a9c1e3f5b2d`, bounds `outcome` to 16 characters
and adds the computed `resolved_atomic_attack_identifier_hash` lookup. The lookup
prefers the canonical reference and falls back to the saved legacy JSON hash.
SQLite indexes the relevant JSON text with the scalar facts; SQL Server uses
bounded scalar index keys and includes the JSON columns. The migration rejects
oversized existing outcomes before altering the schema. It does not generate
result IDs or repair historical metadata.

Filters OR values within each predicate (converter `ALL` is the exception) and AND
separate predicates, including repeated dimensions. Missing metadata, recorded
empty converter pipelines, and real empty strings retain different typed keys.
Request and response converter membership remains separate. Repeated members
contribute once per result to a group or cell; different groups may overlap.
Canonical identifier tables and supported legacy JSON layouts remain queryable.
Where a converter pipeline is retained in identifier JSON, that recorded list
takes precedence over normalized edges: the published identifier backfill can
omit edges for individual hashless converters without removing those converters
from the saved list. Edges supply names only when no retained list exists.
Legacy `__type__` names are read when the canonical `class_name` key is absent.
Indexed fact compaction omits a result's embedded identifier only when doing so
preserves the requested metadata keys and display labels; incomplete normalized
documents continue to use the embedded fallback.
SQL Server uses full-width `OPENJSON` scalar projections before grouping, preserving
the shared 4096-character metadata contract.

A raw report and its first result page share a short consistent read transaction.
Later pages and facets use fresh reads. Cursors are bound to the current filters
and result-ID selection; updated bounds use a half-open UTC interval. Each request
is copied and revalidated before acquiring a session, so changes to the caller's
query during execution cannot mix different filters or axes in one report.
`QueryControl` supplies a monotonic deadline and request-local cancellation signal.
SQLite shared-connection acquisition accepts an optional timeout. Its per-connection
busy timeout is also bounded by the remaining budget before each statement and
restored afterward, so lock waits cannot use the full default busy timeout after
the analytics deadline. SQL Server connection acquisition remains governed by
its pool; ODBC query timeouts are set before statement cursors are created and
restored when the session closes. SQL Server reports require SNAPSHOT support;
analytics never changes server isolation settings or enables SQLite WAL
automatically. Query errors propagate rather than returning empty reports.

The optional SQLite compact-profile probe returns typed raw `RawAnalyticsProfile`
dictionaries with `source0` and optional `source1`/display fields, stored
`outcome`, and result-ID `weight` for a caller to aggregate, not statistics.
For converter axes, the bounded probe emits canonical name arrays so supported
legacy converter objects have the same memberships as SQL grouping.
Its row and per-value limits bound returned
metadata, not SQL scans or intermediate work. The combined-text cap is checked
after fetching the bounded probe, so it is not a peak-memory or network-byte
guarantee. Any overflow uses complete SQL aggregation, never partial counts.
Live Azure SQL validation, malformed legacy converter-name policy, and SQL Server
trailing-space comparison policy remain separate follow-ups.

### Migration revisions are immutable

Once a migration revision is committed, it **must not be modified or deleted**. This is enforced by a pre-commit hook (`enforce_alembic_revision_immutability`). If you need to fix a migration, create a new revision instead.

A release branch is the one exception. A patch release cherry-picks a fix onto a branch cut from an earlier tag, and that fix may legitimately amend a revision that has already shipped, so the hook does not compare history on a release branch. Review is the control there.

### Pre-commit hooks

Two hooks run automatically when you touch memory-related files:

1. **`enforce_alembic_revision_immutability`** — blocks modifications/deletions to existing revision files.
2. **`memory-migrations-check`** — runs `memory_migrations.py check` to verify the schema is in sync.

These hooks trigger on changes to `pyrit/memory/memory_models.py`, `pyrit/memory/migration.py`, and files under `pyrit/memory/alembic/`.
