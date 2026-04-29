---
applyTo: '**/*.py'
---

# PyRIT Coding Style Guidelines

Follow these coding standards to ensure consistent, readable, and maintainable code across the PyRIT project.

## Function and Method Naming

### Async Functions
- **MANDATORY**: All async functions and methods MUST end with `_async` suffix
- This applies to ALL async functions without exception

```python
# CORRECT
async def send_prompt_async(self, prompt: str) -> Message:
    ...

# INCORRECT
async def send_prompt(self, prompt: str) -> Message:  # Missing _async suffix
    ...
```

### Private Methods
- Private methods MUST start with underscore
- This clearly indicates internal implementation details

```python
# CORRECT
def _validate_input(self, data: dict) -> None:
    ...

# INCORRECT
def validate_input(self, data: dict) -> None:  # Should be private
    ...
```

## Type Annotations

### Mandatory Type Hints
- **EVERY** function parameter MUST have explicit type declaration
- **EVERY** function MUST declare its return type
- Use `None` for functions that don't return a value

### Modern Type Syntax (Python 3.10+)
- Use built-in generics and union syntax:
  - `list[str]` not `List[str]`
  - `dict[str, Any]` not `Dict[str, Any]`
  - `str | None` not `Optional[str]`
  - `int | float` not `Union[int, float]`
- Still import `Any`, `Literal`, `TypeVar`, `Protocol`, `cast` etc. from `typing` as needed

```python
# CORRECT
def process_data(self, *, data: list[str], threshold: float = 0.5) -> dict[str, Any]:
    ...

def get_name(self) -> str | None:
    ...

# INCORRECT
def process_data(self, data, threshold=0.5):  # Missing all type annotations
    ...
```

## Function Signatures

### Keyword-Only Arguments
- Functions with more than 1 parameter MUST use `*` after self/cls to enforce keyword-only arguments
- This prevents positional argument errors and improves API clarity

```python
# CORRECT
def __init__(
    self,
    *,
    target: PromptTarget,
    scorer: Scorer | None = None,
    max_retries: int = 3
) -> None:
    ...

# INCORRECT
def __init__(self, target: PromptTarget, scorer: Scorer | None = None, max_retries: int = 3):
    ...
```

### Single Parameter Functions
- Functions with only one parameter don't need keyword-only enforcement

```python
# CORRECT
def process(self, data: str) -> str:
    ...
```

## Imports

### Placement and Organization

Top of file, grouped: stdlib → third-party → local.

### Deferred Imports for Performance

Imports may be placed inside functions/methods when they pull in expensive
third-party packages (`transformers`, `azure.storage.blob`, `alembic`, `openai`,
`scipy`, `pandas`, `av`). Two cases:

1. **CLI entry points** — defer heavy imports to after arg parsing so `--help` is instant.
2. **Internal modules** — when a method is the only consumer of a heavy package.

```python
def main() -> int:
    parsed_args = parse_args()
    from pyrit.cli import frontend_core  # deferred: heavy
    ...

async def _create_container_client_async(self):
    from azure.storage.blob.aio import ContainerClient  # deferred: heavy
    ...
```

Guard tests in `tests/unit/cli/test_import_guards.py` enforce that key import
paths stay fast.

### Lazy `__init__.py` Exports (PEP 562)

Public API packages (`pyrit.prompt_target`, `pyrit.prompt_converter`, `pyrit.score`)
use `__getattr__`-based lazy loading so heavy symbols can be imported from the
package without paying the cost at package load time. See
`pyrit/prompt_target/__init__.py` for the canonical example. Rules:

- Lazy names must remain in `__all__` and have a `TYPE_CHECKING` import for IDE support.
- Internal utility packages (e.g., `pyrit.common`) simply omit heavy submodules
  from `__init__.py` — consumers import directly from the specific file.

### Import Paths

Import from the package root when the symbol is exported from `__init__.py`:

```python
from pyrit.prompt_target import PromptChatTarget  # CORRECT
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget  # WRONG
```

Heavy submodules not re-exported from `__init__.py` are imported directly:

```python
from pyrit.common.net_utility import get_httpx_client
```

Within the same package, import from the specific file to avoid circular imports.

## Documentation Standards

### Docstring Format
- Use Google-style docstrings
- Include type information in parameter descriptions
- Document return types and values
- Include "Raises" section when applicable
- Use triple quotes even for single-line docstrings
- Do not include example calls for how it's used

```python
def calculate_score(
    self,
    *,
    response: str,
    objective: str,
    threshold: float = 0.8,
    max_attempts: int | None = None
) -> Score:
    """
    Calculate the score for a response against an objective.

    This method evaluates how well the response achieves the stated objective
    using the configured scoring mechanism.

    Args:
        response (str): The response text to evaluate.
        objective (str): The objective to evaluate against.
        threshold (float): The minimum score threshold. Defaults to 0.8.
        max_attempts (int | None): Maximum number of scoring attempts. Defaults to None.

    Returns:
        Score: The calculated score object containing value and metadata.

    Raises:
        ValueError: If response or objective is empty.
        ScoringException: If the scoring process fails.
    """
```

### Class-Level Constants
- Define constants as class attributes, not module-level
- Use UPPER_CASE naming for constants

```python
# CORRECT
class TreeOfAttacksAttack(AttackStrategy):
    DEFAULT_TREE_WIDTH: int = 3
    DEFAULT_TREE_DEPTH: int = 5
    MIN_CONFIDENCE_THRESHOLD: float = 0.7

# INCORRECT
DEFAULT_TREE_WIDTH = 3  # Should be inside class
DEFAULT_TREE_DEPTH = 5
MIN_CONFIDENCE_THRESHOLD = 0.7
```

## Code Organization

### Function Length
- Keep functions under 20 lines where possible
- Extract complex logic into well-named helper methods
- Each function should have a single, clear responsibility

```python
# CORRECT
async def execute_attack_async(self, *, context: AttackContext) -> AttackResult:
    """Execute the attack with the given context."""
    self._validate_context(context)

    prompt = await self._prepare_prompt_async(context)
    response = await self._send_prompt_async(prompt, context)
    result = self._evaluate_response(response, context)

    return result

def _validate_context(self, context: AttackContext) -> None:
    """Validate the attack context."""
    if not context.objective:
        raise ValueError("Context must have an objective")

# INCORRECT - Too long and doing too many things
async def execute_attack_async(self, *, context: AttackContext) -> AttackResult:
    # 50+ lines of mixed validation, preparation, sending, and evaluation logic
    ...
```

### Method Ordering
1. Class-level constants and class variables
2. `__init__` method
3. Public methods (API)
4. Protected methods (subclass API)
5. Private methods (internal implementation)
6. Static methods and class methods at the end

## Error Handling

### Specific Exceptions
- Raise specific exceptions with clear messages
- Create custom exceptions when appropriate
- Always include helpful context in error messages

```python
# CORRECT
if not self._model:
    raise ValueError(
        "Model not initialized. Call initialize_model() before executing attack."
    )

# INCORRECT
if not self._model:
    raise Exception("Error")  # Too generic, unhelpful message
```

### Early Returns
- Use early returns to reduce nesting
- Handle edge cases at the beginning of functions

```python
# CORRECT
def process_items(self, *, items: list[str]) -> list[str]:
    if not items:
        return []

    if len(items) == 1:
        return [self._process_single(items[0])]

    # Main logic for multiple items
    return [self._process_single(item) for item in items]

# INCORRECT - Excessive nesting
def process_items(self, *, items: list[str]) -> list[str]:
    if items:
        if len(items) == 1:
            return [self._process_single(items[0])]
        else:
            return [self._process_single(item) for item in items]
    else:
        return []
```

## Pythonic Patterns

### List Comprehensions
- Use comprehensions for simple transformations
- Don't use comprehensions for complex logic or side effects

```python
# CORRECT
filtered_scores = [s for s in scores if s.value > threshold]

# INCORRECT - Too complex for comprehension
results = [
    self._complex_transform(item, index, context)
    for index, item in enumerate(items)
    if self._should_process(item, context) and not item.processed
]
```

### Context Managers
- Use context managers for resource management
- Create custom context managers when appropriate

```python
# CORRECT
async with self._get_client() as client:
    response = await client.send(request)

# For custom resources
from contextlib import asynccontextmanager

@asynccontextmanager
async def temporary_config(self, **kwargs):
    old_config = self._config.copy()
    self._config.update(kwargs)
    try:
        yield
    finally:
        self._config = old_config
```

### Property Decorators
- Use @property for simple computed attributes
- Use explicit getter/setter methods for complex logic

```python
# CORRECT
@property
def is_complete(self) -> bool:
    """Check if the attack is complete."""
    return self._status == AttackStatus.COMPLETE

# INCORRECT - Too complex for property
@property
def analysis_report(self) -> str:
    # 20+ lines of complex report generation
    ...
```

## Testing Considerations

### Dependency Injection
- Design classes to accept dependencies through constructor
- Avoid hard-coded dependencies
- For default behaviors, use factory class methods

```python
# CORRECT
class AttackExecutor:
    def __init__(
        self,
        *,
        target: PromptTarget,
        scorer: Scorer,
        logger: logging.Logger | None = None
    ) -> None:
        self._target = target
        self._scorer = scorer
        self._logger = logger or logging.getLogger(__name__)

# INCORRECT
class AttackExecutor:
    def __init__(self):
        self._target = AzureOpenAI()  # Hard-coded dependency
        self._scorer = DefaultScorer()  # Hard-coded dependency
```

### Pure Functions
- Prefer pure functions where possible
- Separate I/O from business logic

```python
# CORRECT
def calculate_score(response: str, objective: str) -> float:
    """Pure function for score calculation."""
    # Logic without side effects
    return score

async def evaluate_response_async(self, *, response: str) -> Score:
    """I/O function that uses the pure function."""
    score_value = calculate_score(response, self._objective)
    await self._save_score_async(score_value)
    return Score(value=score_value)
```

## Performance Considerations

### Lazy Evaluation
- Use generators for large sequences
- Don't load entire datasets into memory unnecessarily

```python
# CORRECT
def process_large_dataset(self, *, file_path: Path) -> Generator[Result, None, None]:
    with open(file_path) as f:
        for line in f:
            yield self._process_line(line)

# INCORRECT
def process_large_dataset(self, *, file_path: Path) -> list[Result]:
    with open(file_path) as f:
        lines = f.readlines()  # Loads entire file into memory
    return [self._process_line(line) for line in lines]
```

### Lazy Imports for Startup Performance
- When adding a new module that imports heavy third-party packages (e.g., `transformers`,
  `scipy`, `PIL`, `datasets`, `av`), consider whether it is re-exported from a package
  `__init__.py` that is on the CLI startup path
- If so, add it to the `_LAZY_IMPORTS` dict in that `__init__.py` instead of as an
  eager top-level import (see the Import Placement section for the pattern)
- This is especially important for `pyrit/common/__init__.py`, `pyrit/prompt_target/__init__.py`,
  `pyrit/prompt_converter/__init__.py`, and `pyrit/score/__init__.py` which are all on the
  import path for CLI startup

## Final Checklist

Before committing code, ensure:
- [ ] All async functions have `_async` suffix
- [ ] All functions have complete type annotations
- [ ] Functions with >1 parameter use keyword-only arguments
- [ ] Docstrings include parameter types
- [ ] Enums are used instead of Literals
- [ ] Functions are focused and under 20 lines
- [ ] Error messages are helpful and specific
- [ ] Code follows the import organization pattern
- [ ] New modules with heavy deps follow `__init__.py` startup guidance
- [ ] No hard-coded dependencies
- [ ] Complex logic is extracted to helper methods

---

## File Editing Rules

### Never Use `sed` for File Edits
- **MANDATORY**: Never use `sed` (or similar stream-editing CLI tools) to modify source files
- `sed` frequently corrupts files, applies partial edits, or silently fails
- Always use the editor's built-in replace/edit tools (e.g., `replace_string_in_file`, `multi_replace_string_in_file`) to make targeted, verifiable changes

---

**Remember**: Clean code is written for humans to read. Make your intent clear and your code self-documenting.
