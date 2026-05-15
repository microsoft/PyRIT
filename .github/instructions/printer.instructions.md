---
applyTo: "pyrit/printer/**"
---

# PyRIT Printer Module Guidelines

The printer module renders attack results, scenario results, and scorer information. It separates **what** the output looks like (format) from **where** it goes (sink) and **where data comes from** (abstract methods).

## Architecture

### Three-layer hierarchy per domain

```
DomainPrinterBase(PrinterBase)          # base.py — abstract data methods + write_async
  ├─ PrettyDomainPrinter               # pretty.py — ANSI formatting, returns str
  │     └─ PrettyDomainMemoryPrinter   # same file — fetches data via CentralMemory
  ├─ MarkdownDomainPrinter             # markdown.py — Markdown formatting
  │     └─ MarkdownDomainMemoryPrinter
  └─ JsonDomainPrinter                 # json.py — structured JSON
        └─ JsonDomainMemoryPrinter
```

- **Base** (`base.py`): declares abstract data-fetching methods and `write_async`
- **Format** (`pretty.py`, `markdown.py`, `json.py`): all rendering logic, builds `str`, writes to sink — **no data I/O here**
- **Leaf** (e.g., `PrettyAttackResultMemoryPrinter`): implements abstract data methods via `CentralMemory` — **no formatting logic here**

### Sink — where output goes

`Sink` ABC in `sink.py`. Printers take a `Sink` in their constructor (default: `StdoutSink`).

```python
class Sink(ABC):
    async def write_async(self, data: str) -> None: ...
```

Current sinks: `StdoutSink`, `FileSink`. Add new sinks as needed (IPython, Blob, etc.).

### PrinterBase — common base

All printers inherit `PrinterBase`. It provides:
- `sink` constructor param (default `StdoutSink`)
- `_write_async(data: str)` to write through the sink
- Abstract `write_async(...)` as the **public entry point** (signature varies per domain)

## Key Rules

### Output goes through the sink — never call `print()` directly

All `_render_*` methods return `str`. The `write_async` entry point concatenates renders and calls `_write_async(content)`. No bare `print()` calls anywhere in the printer module except inside `StdoutSink`.

### Data fetching belongs in leaf classes only

Format classes (`PrettyAttackResultPrinter`, `MarkdownAttackResultPrinter`) must not import or reference `CentralMemory`. Only `*MemoryPrinter` leaf classes do data I/O.

### File names = output format

- `pretty.py` — ANSI-colored human-readable
- `markdown.py` — Markdown
- `json.py` — structured JSON

### `write_async` is the only public entry point

Each printer has one public method: `write_async(...)`. Old methods like `print_result_async`, `print_summary_async`, `print_objective_scorer` are deprecated wrappers that call `write_async`.

### All other methods are private

Prefix with `_`: `_format_colored`, `_render_header`, `_render_summary_async`, `_get_conversation_async`, `_get_scores_async`, etc.

### Memory leaf classes must work with zero args

```python
printer = PrettyAttackResultMemoryPrinter()  # defaults: StdoutSink, matching sub-printers
await printer.write_async(result)
```

Pass `sink=` to redirect output. Pass sub-printers only to override defaults.

### Convenience functions live in `helpers.py`

```python
from pyrit.printer.helpers import print_attack_result_async
await print_attack_result_async(result, format="pretty", to=Path("out.txt"))
```

`helpers.py` resolves `format` → printer class, `to` → sink, and calls `write_async`.

## Adding a New Format

1. Create `<domain>/<format>.py` (e.g., `attack_result/json.py`)
2. Subclass the domain base (e.g., `AttackResultPrinterBase`)
3. Implement `write_async` — build a `str` from `_render_*` methods, call `_write_async`
4. Add a `*MemoryPrinter` leaf class that implements the abstract data methods
5. Register in `helpers.py` format dispatch

## Adding a New Sink

1. Subclass `Sink` in `sink.py`
2. Implement `write_async(self, data: str) -> None`
3. Users pass it via `sink=MySink()` on any printer constructor

## Adding a New Domain Printer

1. Create `pyrit/printer/<domain>/base.py` with abstract data methods + `write_async`
2. Create format files (`pretty.py`, etc.) with rendering logic
3. Add Memory leaf classes
4. Add convenience function in `helpers.py`
