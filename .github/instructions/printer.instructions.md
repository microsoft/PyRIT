---
applyTo: "pyrit/printer/**"
---

# PyRIT Printer Module Guidelines

The printer module renders attack results, scenario results, conversation histories, scores, and scorer information. It separates **what** the output looks like (format) from **where** it goes (sink) and **where data comes from** (abstract methods).

## Architecture

### The `render_async` / `write_async` contract

Every printer follows this contract, enforced by `PrinterBase`:

```python
class PrinterBase(ABC):
    @abstractmethod
    async def render_async(self, *args, **kwargs) -> str:
        """Return the rendered output string. Subclasses define the real signature."""

    async def write_async(self, *args, **kwargs) -> None:
        """Concrete. Calls render_async then writes to sink. Do not override."""
        content = await self.render_async(*args, **kwargs)
        await self._write_async(content)
```

- **`render_async`** — abstract, returns `str`. Pure formatting. Easy to test and compose.
- **`write_async`** — concrete in base. Calls `render_async` → `_write_async`. **Nobody overrides this.**
- Composition: printers call each other's `render_async` to embed sub-sections.

### Three-layer hierarchy per domain

```
DomainPrinterBase(PrinterBase)          # base.py — abstract data methods
  ├─ PrettyDomainPrinter               # pretty.py — ANSI formatting, implements render_async
  │     └─ PrettyDomainMemoryPrinter   # same file — fetches data via CentralMemory
  ├─ MarkdownDomainPrinter             # markdown.py — Markdown formatting
  │     └─ MarkdownDomainMemoryPrinter
  └─ JsonDomainPrinter                 # json.py — structured JSON
        └─ JsonDomainMemoryPrinter
```

- **Base** (`base.py`): declares abstract data-fetching methods and abstract `render_async`
- **Format** (`pretty.py`, `markdown.py`, `json.py`): implements `render_async`, returns `str` — **no data I/O here**
- **Leaf** (e.g., `PrettyAttackResultMemoryPrinter`): implements abstract data methods via `CentralMemory`, has a forwarding `render_async` at the top for discoverability — **no formatting logic here**

### Domain modules

```
pyrit/printer/
├── base.py                    # PrinterBase — render_async (abstract) + write_async (concrete)
├── sink.py                    # Sink, StdoutSink, FileSink
├── helpers.py                 # Convenience functions (print_attack_result_async, etc.)
├── attack_result/             # Attack result printing — composes conversation + score printers
├── conversation/              # Conversation/message rendering (extracted from attack_result)
├── score/                     # Individual Score object rendering (extracted from attack_result)
├── scorer/                    # Scorer metrics/evaluation display
└── scenario_result/           # Scenario result printing
```

### Sink — where output goes

`Sink` ABC in `sink.py`. Printers take a `Sink` in their constructor (default: `StdoutSink`).

```python
class Sink(ABC):
    async def write_async(self, data: str) -> None: ...
```

Current sinks: `StdoutSink`, `FileSink`. Add new sinks as needed (IPython, Blob, etc.).

### Composition pattern

The attack result printer composes conversation and score printers:

```python
class PrettyAttackResultPrinter(AttackResultPrinterBase):
    def __init__(self, *, conversation_printer=None, score_printer=None, ...):
        self._conversation_printer = conversation_printer or PrettyConversationPrinter(...)
        self._score_printer = score_printer or PrettyScorePrinter(...)

    async def render_async(self, result, ...) -> str:
        # Uses self._conversation_printer.render_async(messages) for conversation sections
        # Uses self._score_printer._render_score(score) for inline scores
```

## Key Rules

### Output goes through the sink — never call `print()` directly

All `_render_*` methods return `str`. The inherited `write_async` calls `render_async` then `_write_async(content)`. No bare `print()` calls anywhere in the printer module except inside `StdoutSink`.

### Data fetching belongs in leaf classes only

Format classes (`PrettyAttackResultPrinter`, `MarkdownAttackResultPrinter`) must not import or reference `CentralMemory`. Only `*MemoryPrinter` leaf classes do data I/O.

### File names = output format

- `pretty.py` — ANSI-colored human-readable
- `markdown.py` — Markdown
- `json.py` — structured JSON

### `render_async` and `write_async` are the public entry points

- `render_async(...)` → `str` — the primary method subclasses implement
- `write_async(...)` → `None` — concrete in base, calls render + sink. Do not override.
- Old methods like `print_result_async`, `print_summary_async`, `print_objective_scorer` are deprecated wrappers with `DeprecationWarning`.

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

```python
from pyrit.printer.helpers import print_attack_result_async
await print_attack_result_async(result, format="pretty", sink=Path("out.txt"))
```

`helpers.py` resolves `format` → printer class, `sink` → Sink, and calls `write_async`.

## Adding a New Format

1. Create `<domain>/<format>.py` (e.g., `attack_result/json.py`)
2. Subclass the domain base (e.g., `AttackResultPrinterBase`)
3. Implement `render_async` — build and return a `str` from `_render_*` methods
4. Add a `*MemoryPrinter` leaf class with forwarding `render_async` + data methods
5. Register in `helpers.py` format dispatch

## Adding a New Sink

1. Subclass `Sink` in `sink.py`
2. Implement `write_async(self, data: str) -> None`
3. Users pass it via `sink=MySink()` on any printer constructor

## Adding a New Domain Printer

1. Create `pyrit/printer/<domain>/base.py` with abstract data methods + abstract `render_async`
2. Create format files (`pretty.py`, etc.) with `render_async` implementation
3. Add Memory leaf classes with forwarding `render_async` + data methods
4. Add convenience function in `helpers.py`
