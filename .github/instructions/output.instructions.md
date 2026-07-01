---
applyTo: "pyrit/output/**"
---

# PyRIT Output Module — Coding & Review Guidelines

For full architecture documentation, usage examples, and extension guides, see [doc/code/output/0_output.py](../../../doc/code/output/0_output.py).

This file covers the rules for **writing and reviewing** code in `pyrit/output/`.

**Does not own** (see [framework.md](../../doc/code/framework.md)): deciding *what* to render or *when*. Components hand results to output; format classes only turn data into strings and must never fetch data, touch `CentralMemory`, or call `print()` directly (that's isolated to leaf printer classes). Flag such bleed in review.

## Critical Rules

### Output goes through the sink — never call `print()` directly

All rendering methods return `str`. The inherited `write_async` calls `render_async` then `_write_async(content)`. No bare `print()` calls anywhere in the output module except inside `StdoutSink`.

When reviewing: reject any `print()` call outside `StdoutSink`.

### Data fetching belongs in leaf classes only

Format classes (`PrettyAttackResultPrinter`, `MarkdownAttackResultPrinter`) must not import or reference `CentralMemory`. Only `*MemoryPrinter` leaf classes do data I/O.

When reviewing: reject any `CentralMemory` import in a non-leaf file (`pretty.py`, `markdown.py`, `json.py`).

### Sinks must use async I/O

Sink implementations must not block the event loop. Use `asyncio.to_thread()` or native async libraries for I/O operations. `FileSink` uses an `asyncio.Lock` to prevent concurrent write races.

When reviewing: reject synchronous `open()`, `write()`, or network calls inside a sink's `write_async`.

## Three-Layer Hierarchy

Every domain follows this structure. Do not mix responsibilities across layers.

| Layer | File | Responsibility | May import CentralMemory? |
|-------|------|---------------|---------------------------|
| **Base** | `base.py` | Abstract data-fetching methods + abstract `render_async` | No |
| **Format** | `pretty.py`, `markdown.py`, `json.py` | Implements `render_async`, returns `str` | No |
| **Leaf** | Same file as format (e.g., `PrettyAttackResultMemoryPrinter`) | Implements data methods via CentralMemory; forwarding `render_async` | Yes |

### File names = output format

- `pretty.py` — ANSI-colored human-readable
- `markdown.py` — Markdown
- `json.py` — structured JSON

### Memory leaf classes must work with zero args

```python
printer = PrettyAttackResultMemoryPrinter()  # defaults: StdoutSink, matching sub-printers
await printer.write_async(result)
```

Pass `sink=` to redirect output. Pass sub-printers only to override defaults.

### Convenience functions live in `helpers.py`

Every new domain printer **must** have a corresponding convenience function added to `helpers.py`. This is the primary entry point most callers use.

```python
from pyrit.output.helpers import output_attack_async
await output_attack_async(result, format="pretty")
```

`helpers.py` resolves `format` → printer class, `sink` → Sink, and calls `write_async`.

When reviewing: if a new domain printer is added without a helper function, request one.
