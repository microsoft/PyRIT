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
| Schema diagram | `doc/code/memory/10_schema_diagram.md` |

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
python build_scripts/memory_migrations.py generate -m "short description of change"
```

This creates a new revision file under `pyrit/memory/alembic/versions/`. **Review the generated file carefully** — auto-generated migrations may need manual adjustments (e.g. for data migrations or default values).

### 3. Validate the migration

```bash
python build_scripts/memory_migrations.py check
```

This verifies the schema produced by running all migrations matches the current models. Both pre-commit hooks (see below) and CI run this check.

### 4. Update the schema diagram

If you changed the schema in a meaningful way (added a table, added a foreign key, etc.), update the Mermaid diagram in `doc/code/memory/10_schema_diagram.md`.

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

### Migration revisions are immutable

Once a migration revision is committed, it **must not be modified or deleted**. This is enforced by a pre-commit hook (`enforce_alembic_revision_immutability`). If you need to fix a migration, create a new revision instead.

### Pre-commit hooks

Two hooks run automatically when you touch memory-related files:

1. **`enforce_alembic_revision_immutability`** — blocks modifications/deletions to existing revision files.
2. **`memory-migrations-check`** — runs `memory_migrations.py check` to verify the schema is in sync.

These hooks trigger on changes to `pyrit/memory/memory_models.py`, `pyrit/memory/migration.py`, and files under `pyrit/memory/alembic/`.
