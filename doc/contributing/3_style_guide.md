# 3. Style Guide

Currently the code in PyRIT should try to have a consistent style. A consistent coding style aids in efficient development of capabilities.


We use the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
For [docstrings](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings),
there are rules for modules, classes, and functions.

While we can't expect all documentation to be perfect from day 1, we'll improve any docstring
that we touch in a pull request to be compliant with this style guide.

The PyRIT codebase uses whitespace for organization / visual clues so developers can quickly scan and understand relation based on whitespace in addition to other features.

Deviations from any particular rule can occur depending on context and need.

- Strict rules should be included in the pre-commit hooks. This is currently run as part of the build pipelines. If we want to enforce style, it's best to do here.
- Use explicit types and return values when possible
- Prefer self-explanatory code to comments, and comment only to explain why. We use docstrings under the function, in the Google format.
- Long line lengths are discouraged (>= ~120)
- Large files are discouraged. If possible put one class per file or at least put only related classes in the same file. Large files should be broken up.
- Fight indentation and scoping. Lots of scopes makes code more difficult to read and properly write. Use the following to fight indentation: early function exits, logic inversion/continue, exceptions (as appropriate), creating smaller functions, goto's, etc.
- Test code should adhere to these coding guidelines as well.
- One empty line between related (in-class things)
- Two empty lines between unrelated things (classes, global functions, etc)
- One parameter per line
- Spaces not tabs. Tab value must be 4 spaces
- Test names should be test_foo test_bar, test_baz, etc.
- In the case of type name conflicts, the desired type should be used in its fully-qualified (or disambiguating relatively-qualified) form: e.g. `pyrit.shared.foo`
- Naming should follow typical Python naming. e.g. some_descriptive_name
- PyRIT imports go last (after a newline)
- Imports should go in alphabetical order

## Keyword-Only Parameters

Functions and methods in `pyrit/` may declare at most one positional parameter, not counting the
implicit `self` or `cls`. Every further parameter goes after `*`, so callers have to pass it by name:

```python
# CORRECT
def score_text(self, text: str, *, objective: str | None = None) -> Score: ...


def build_target(*, endpoint: str, model_name: str) -> PromptTarget: ...


# INCORRECT: two positional parameters after self
def score_text(self, text: str, objective: str | None = None) -> Score: ...
```

The `check-keyword-only-params` pre-commit hook (`build_scripts/check_keyword_only_params.py`) enforces this
for every `def` and `async def` under `pyrit/`, including private helpers, nested functions and constructors.
Positional-only (`/`) and ordinary positional parameters both count; `*args` and `**kwargs` do not.
Constructors of the pluggable base classes (converters, scorers, targets, scenarios, attacks and seed dataset
providers) are also checked at class-definition time, and they may not take any positional parameter.

Run the check by hand with:

```bash
uv run python -m build_scripts.check_keyword_only_params
```

### Exemptions

Exemptions are deliberately narrow. The check skips:

- **Python data-model methods** such as `__exit__`, `__aexit__`, `__eq__` and `__call__`, because the
  interpreter or a protocol calls them positionally. `__init__` and `__new__` are still checked, since their
  signature is the class's public API.
- **`@overload` stubs**, because they mirror the implementation signature, and the implementation is checked.
- **A short list of framework method names** whose third-party base class calls them positionally:
  `dispatch` (Starlette middleware), `process_bind_param` and `process_result_value` (SQLAlchemy
  `TypeDecorator`). Do not add names to this list for one-off cases.
- **Functions marked `# pyrit-keyword-only-exempt`** on any line of the `def` header. Use the marker only when
  third-party code dictates the positional signature (an argparse `type=` callback, a `signal` handler or a
  library callback), or a new override must preserve a baselined PyRIT base method's positional contract.
  Give the reason after the marker and name the contract being preserved:

```python
def _handle_signal(signum: int, frame: FrameType | None) -> None:  # pyrit-keyword-only-exempt signal handler
    ...
```

`@override` alone is not an exemption. Existing implementations stay in the baseline until they can migrate
together with the base method and its callers. A new implementation uses a marker instead of adding a
baseline entry or making its signature incompatible with existing positional callers. For example:

```python
class CustomStorageIO(StorageIO):
    async def write_file_async(  # pyrit-keyword-only-exempt StorageIO.write_file_async positional contract
        self, path: Path | str, data: bytes
    ) -> None: ...
```

Remove the compatibility marker when the base contract and its callers migrate to keyword-only arguments.

### Baseline

Functions that already broke the rule when the check was added are listed in
`build_scripts/keyword_only_baseline.txt`, so the check only flags new code. Each line is `module:qualname`
(for example `pyrit.auth.azure_auth:get_speech_config`). Line numbers are not stored, so moving or editing a
function does not change the file, but renaming or moving it to another module does. The file is sorted.

- New code must not be added to the baseline. Use `*`, or the exempt marker when a third-party contract or
  a baselined PyRIT base method requires positional compatibility.
- Many baseline entries are public APIs. Changing them to keyword-only breaks positional callers, so migrate
  them deliberately in separate pull requests, with a deprecation if needed, and not as a side effect of
  unrelated work.
- When you migrate a function, delete its line from the baseline. The check fails while a baseline entry no
  longer matches a violation, so the baseline can only shrink.
- If you rename or move a baselined function, regenerate the file and check in the diff that only the renamed
  entries changed:

```bash
uv run python -m build_scripts.check_keyword_only_params --write-baseline
```

## Public PyRIT imports

Use the shortest package that already exports a symbol. This keeps callers
independent of implementation filenames:

```python
from pyrit.analytics import analyze_results
from pyrit.executor.attack import AttackStrategy
from pyrit.models import Message
```

Do not import these from `pyrit.analytics.result_analysis`,
`pyrit.executor.attack.core.attack_strategy`, or `pyrit.models.messages.message`.
Keep distinct executor APIs such as `pyrit.executor.attack` and
`pyrit.executor.workflow`; do not flatten them into `pyrit.executor`.

Internal imports within the package owning the public export still use
absolute implementation-module paths to avoid circular imports. Ruff's `TID252`
rejects relative imports so they cannot bypass public-import checks.
Helpers without a public export, such as
`pyrit.common.net_utility.get_httpx_client`, also keep their direct imports.
Package initializers and immutable Alembic revisions are excluded.

Tests follow the same public import convention so implementation file moves do not
break public API tests. Tests of helpers without public exports still import them directly.
Notebook code cells are checked independently because synchronization with paired
Python files is not enforced.

The `check-public-imports` pre-commit hook uses existing `_LAZY_EXPORTS` declarations.
Local commits check changed files; CI checks all files. After changing public exports,
check all consumers, including unchanged files:

```bash
uv run pre-commit run check-public-imports --all-files
```

The direct command `uv run python -m build_scripts.check_public_imports` also
checks all tracked Python files and notebooks when no filenames are supplied.

Ruff's [`TID251`](https://docs.astral.sh/ruff/rules/banned-api/) can ban fixed API
paths, but cannot infer public re-exports or exempt internal sibling imports
selectively. The export-aware hook avoids maintaining a second list of public APIs.

### Mocking lazy exports

Patch the name where the consumer looks it up. For example, if the consumer imports
inside a function:

```python
from pyrit.score import find_objective_metrics_by_eval_hash
```

patch `pyrit.score.find_objective_metrics_by_eval_hash`, not
`pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash`.
Lazy packages cache resolved exports. Patching only the implementation module can
either miss the cached function or cause the package to cache a mock after the patch
ends. Internal consumers that import the implementation module still need patches there.
For names imported at module scope, patch the consumer module's bound name instead.
