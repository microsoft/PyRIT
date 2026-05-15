---
applyTo: "pyrit/printer/**"
---

# PyRIT Printer Module — Coding & Review Guidelines

For full architecture documentation, usage examples, and extension guides, see [doc/code/printer/0_printer.md](../../../doc/code/printer/0_printer.md).

This file covers the rules for **writing and reviewing** code in `pyrit/printer/`.

## Critical Rules

### Output goes through the sink — never call `print()` directly

All rendering methods return `str`. The inherited `write_async` calls `render_async` then `_write_async(content)`. No bare `print()` calls anywhere in the printer module except inside `StdoutSink`.

When reviewing: reject any `print()` call outside `StdoutSink`.

### Data fetching belongs in leaf classes only

Format classes (`PrettyAttackResultPrinter`, `MarkdownAttackResultPrinter`) must not import or reference `CentralMemory`. Only `*MemoryPrinter` leaf classes do data I/O.

When reviewing: reject any `CentralMemory` import in a non-leaf file (`pretty.py`, `markdown.py`, `json.py`).

### Never override `write_async`

`write_async` is concrete in `PrinterBase`. It calls `render_async` → `_write_async`. Subclasses implement `render_async` only.

When reviewing: reject any `write_async` override in a subclass.

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

## Coding Conventions

### Public API: `render_async` and `write_async` only

- `render_async(...)` → `str` — the primary method subclasses implement.
- `write_async(...)` → `None` — concrete in base, calls render + sink. Do not override.
- Old methods like `print_result_async`, `print_summary_async`, `print_objective_scorer` are deprecated wrappers that call through to `write_async`.

### All other methods are private

Prefix with `_`: `_format_colored`, `_render_header`, `_render_summary_async`, `_get_conversation_async`, `_get_scores_async`, etc.

### Leaf classes surface `render_async` at the top

Every `*MemoryPrinter` leaf class has a forwarding `render_async` override right after `__init__` so readers immediately see the full signature and entry point:

```python
class PrettyAttackResultMemoryPrinter(PrettyAttackResultPrinter):
    def __init__(self, ...): ...

    async def render_async(self, result, ...) -> str:
        return await super().render_async(result, ...)

    # data-fetching methods below
```

### Memory leaf classes must work with zero args

```python
printer = PrettyAttackResultMemoryPrinter()  # defaults: StdoutSink, matching sub-printers
await printer.write_async(result)
```

Pass `sink=` to redirect output. Pass sub-printers only to override defaults.

### Convenience functions live in `helpers.py`

Every new domain printer **must** have a corresponding convenience function added to `helpers.py`. This is the primary entry point most callers use.

```python
from pyrit.printer.helpers import print_attack_result_async
await print_attack_result_async(result, format="pretty")
```

`helpers.py` resolves `format` → printer class, `sink` → Sink, and calls `write_async`.

When reviewing: if a new domain printer is added without a helper function, request one.

### Deprecation

Use `print_deprecation_message` from `pyrit.common.deprecation` for all deprecated methods. Do not use `warnings.warn` directly.

## Adding a New Format

1. Create `<domain>/<format>.py` (e.g., `attack_result/json.py`)
2. Subclass the domain base (e.g., `AttackResultPrinterBase`)
3. Implement `render_async` — build and return a `str` from private `_render_*` methods
4. Add a `*MemoryPrinter` leaf class with forwarding `render_async` + data methods
5. Register the new format in the `helpers.py` convenience function dispatch

## Adding a New Sink

1. Subclass `Sink` in `sink.py`
2. Implement `async def write_async(self, data: str) -> None` using async I/O
3. Users pass it via `sink=MySink()` on any printer constructor

## Adding a New Domain Printer

1. Create `pyrit/printer/<domain>/base.py` with abstract data methods + abstract `render_async`
2. Create format files (`pretty.py`, etc.) with `render_async` implementation
3. Add Memory leaf classes with forwarding `render_async` + data methods
4. **Add a convenience function in `helpers.py`** — this is mandatory

## Review Checklist

When reviewing changes to `pyrit/printer/`:

- [ ] No `print()` calls outside `StdoutSink`
- [ ] No `CentralMemory` imports in format-layer classes
- [ ] No `write_async` overrides in subclasses
- [ ] Sink implementations use async I/O (no blocking calls)
- [ ] New domain printers have a convenience function in `helpers.py`
- [ ] Leaf classes have forwarding `render_async` at the top
- [ ] Deprecated methods use `print_deprecation_message`, not `warnings.warn`
- [ ] All private methods are prefixed with `_`
