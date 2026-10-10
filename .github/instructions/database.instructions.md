---
description: "Use when editing database models, SQLAlchemy schemas, Alembic migrations, indexes, keys, constraints, or database migration tests for SQLite and SQL Server."
applyTo: "pyrit/memory/**/*.py, tests/**/memory/**/*.py"
---

# Database Portability Guidelines

- Test schema and migration changes against both SQLite and SQL Server semantics. SQLite accepts
  text columns as index keys, while SQL Server rejects `VARCHAR(MAX)` / `NVARCHAR(MAX)` key columns.
- Give every `String` / `Unicode` column used in a primary key, foreign key, index, or unique
  constraint an explicit, appropriately bounded length in both ORM models and Alembic migrations.
- When correcting an existing unbounded indexed column, alter it to the bounded type before
  creating the index. Keep the operation dialect-aware when a backend such as SQLite does not
  support the same `ALTER COLUMN` operation.
