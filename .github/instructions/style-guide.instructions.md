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

## Documentation Standards

### Import Placement
- **MANDATORY**: All import statements MUST be at the top of the file
- Do NOT use inline/local imports inside functions or methods
- The only exception is breaking circular import dependencies, which should be rare and documented

```python
# CORRECT — imports at the top of the file
from contextlib import closing
from sqlalchemy.exc import SQLAlchemyError

def update_entry(self, entry: Base) -> None:
    with closing(self.get_session()) as session:
        ...

# INCORRECT — inline import inside a function
def update_entry(self, entry: Base) -> None:
    from contextlib import closing          # ← WRONG, must be at top of file
    with closing(self.get_session()) as session:
        ...
```

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

### Import Organization
```python
# Standard library imports
import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# Third-party imports
import numpy as np
from tqdm import tqdm

# Local application imports
from pyrit.attacks.base import AttackStrategy
from pyrit.models import AttackResult
from pyrit.prompt_target import PromptTarget
```

Unless necessary, always import at the top of the file. Don't import inside a function or method.


### Import paths

Often, pyrit has specific files that can be imported. However IF you are importing from a different module than your namespace,
import from the root pyrit module if it's exposed from init.

In the same module, importing from the specific path is usually necessary to prevent circular imports.

- Always check __init__.py exports first - Before using a specific file path, verify if the class/function is exposed at a higher level
- Group related imports - Put all imports from the same root module together
- Use multi-line formatting for readability - When importing 3+ items from the same module, use parentheses


```python
# Correct
from pyrit.prompt_target import PromptChatTarget, OpenAIChatTarget

# Correct
from pyrit.score import (
    AzureContentFilterScorer,
    FloatScaleThresholdScorer,
    SelfAskRefusalScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

# Incorrect (if importing from a non-target module)
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.openai.openai_chat_target import OpenAIChatTarget

```

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
