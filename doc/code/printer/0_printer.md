# Printer Module

The printer module renders attack results, scenario results, conversation histories, scores, and scorer information. It separates **what** the output looks like (format) from **where** it goes (sink) and **where data comes from** (abstract methods).

## Quick Start

The simplest way to print results is through the convenience functions in `helpers.py`:

```python
from pyrit.printer.helpers import print_attack_result_async, print_scenario_result_async, print_scorer_async

# Print an attack result (pretty ANSI format to stdout by default)
await print_attack_result_async(result)

# Print with markdown format (auto-detects Jupyter for rich rendering)
await print_attack_result_async(result, format="markdown")

# Print a scenario result
await print_scenario_result_async(scenario_result)

# Print scorer evaluation metrics
await print_scorer_async(scorer_identifier=scorer.get_identifier())
```

### Redirecting Output

Pass a `sink=` argument to send output to a file or other destination:

```python
from pyrit.printer.sink import FileSink
from pathlib import Path

await print_attack_result_async(result, sink=FileSink(path=Path("report.txt")))
```

## Architecture

### The `render_async` / `write_async` Contract

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
- **`write_async`** — concrete in base. Calls `render_async` → `_write_async`. Nobody overrides this.
- Composition: printers call each other's `render_async` to embed sub-sections.

### Three-Layer Hierarchy

Each domain (attack result, conversation, score, scorer, scenario result) follows a three-layer hierarchy:

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
- **Format** (`pretty.py`, `markdown.py`, `json.py`): implements `render_async`, returns `str` — no data I/O here
- **Leaf** (e.g., `PrettyAttackResultMemoryPrinter`): implements abstract data methods via `CentralMemory`, has a forwarding `render_async` at the top for discoverability — no formatting logic here

### Module Layout

```
pyrit/printer/
├── base.py                    # PrinterBase — render_async (abstract) + write_async (concrete)
├── sink.py                    # Sink, StdoutSink, FileSink, IPythonMarkdownSink
├── helpers.py                 # Convenience functions (print_attack_result_async, etc.)
├── attack_result/             # Attack result printing — composes conversation + score printers
├── conversation/              # Conversation/message rendering
├── score/                     # Individual Score object rendering
├── scorer/                    # Scorer metrics/evaluation display
└── scenario_result/           # Scenario result printing
```

### Sinks — Where Output Goes

`Sink` is an ABC in `sink.py`. Printers take a `Sink` in their constructor (default: `StdoutSink`).

```python
class Sink(ABC):
    async def write_async(self, data: str) -> None: ...
```

Built-in sinks:

| Sink | Description |
|------|-------------|
| `StdoutSink` | Prints to stdout (default) |
| `FileSink` | Writes to a file (`mode="w"` or `"a"`) |
| `IPythonMarkdownSink` | Renders markdown via `IPython.display.Markdown` in Jupyter; falls back to `print()` outside notebooks |

`get_default_sink()` auto-detects the environment: returns `IPythonMarkdownSink` inside Jupyter, `StdoutSink` otherwise.

### Composition Pattern

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

## Output Formats

### Pretty (ANSI)

Human-readable output with color-coded sections, emoji icons, and text wrapping. Best for terminal / CLI use.

```python
from pyrit.printer.attack_result.pretty import PrettyAttackResultMemoryPrinter

printer = PrettyAttackResultMemoryPrinter()
await printer.write_async(result)
```

### Markdown

Structured markdown output optimized for Jupyter notebooks. Headers, tables, code blocks, and inline images.

```python
from pyrit.printer.attack_result.markdown import MarkdownAttackResultMemoryPrinter
from pyrit.printer.sink import IPythonMarkdownSink

printer = MarkdownAttackResultMemoryPrinter(sink=IPythonMarkdownSink())
await printer.write_async(result)
```

## Using Printers Directly

For more control, instantiate printers directly instead of using the convenience functions:

```python
from pyrit.printer.attack_result.pretty import PrettyAttackResultMemoryPrinter
from pyrit.printer.sink import FileSink
from pathlib import Path

# Write to file with custom settings
printer = PrettyAttackResultMemoryPrinter(
    sink=FileSink(path=Path("report.txt")),
    width=120,
    enable_colors=False,  # no ANSI codes in file output
)
await printer.write_async(
    result,
    include_auxiliary_scores=True,
    include_pruned_conversations=True,
)
```

### Render Without Writing

Use `render_async` to get the formatted string without writing it anywhere:

```python
printer = PrettyAttackResultMemoryPrinter(enable_colors=False)
text = await printer.render_async(result)
# Use `text` however you want — log it, embed it, etc.
```

## Convenience Functions Reference

All convenience functions live in `pyrit.printer.helpers`:

| Function | Domain | Formats |
|----------|--------|---------|
| `print_attack_result_async` | Attack results | `pretty`, `markdown` |
| `print_scenario_result_async` | Scenario results | `pretty` |
| `print_scorer_async` | Scorer info/metrics | `pretty` |
| `print_conversation_async` | Conversation history | `pretty` |
| `print_score_async` | Score list | `pretty` |

All accept `format=` and `sink=` keyword arguments with sensible defaults.

## Extending the Printer Module

### Adding a New Format

1. Create `<domain>/<format>.py` (e.g., `attack_result/json.py`)
2. Subclass the domain base (e.g., `AttackResultPrinterBase`)
3. Implement `render_async` — build and return a `str` from private `_render_*` methods
4. Add a `*MemoryPrinter` leaf class with forwarding `render_async` + data methods
5. Register in `helpers.py` format dispatch

### Adding a New Sink

1. Subclass `Sink` in `sink.py`
2. Implement `async def write_async(self, data: str) -> None`
3. Users pass it via `sink=MySink()` on any printer constructor

### Adding a New Domain Printer

1. Create `pyrit/printer/<domain>/base.py` with abstract data methods + abstract `render_async`
2. Create format files (`pretty.py`, etc.) with `render_async` implementation
3. Add Memory leaf classes with forwarding `render_async` + data methods
4. Add a convenience function in `helpers.py`
