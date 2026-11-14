# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Enforce the keyword-only convention from ``.github/instructions/style-guide.instructions.md``
("Function Signatures" > "Keyword-Only Arguments") on every function and method in ``pyrit/``.

Rule: a function or method may declare at most **one** positional parameter, not counting
the implicit ``self``/``cls`` of a method. Every further parameter must come after ``*``
(or ``*args``). Positional-only (``/``) and positional-or-keyword parameters both count;
``*args`` and ``**kwargs`` do not.

    def process(self, data: str) -> str: ...  # OK: one positional
    def process(self, *, data: str, threshold: float) -> str: ...  # OK: keyword-only
    def process(self, data: str, *, threshold: float) -> str: ...  # OK: one positional
    def process(self, data: str, threshold: float) -> str: ...  # flagged

Brick constructors (``Converter``, ``Scorer``, ``PromptTarget``, ...) are already enforced
at class-definition time by ``pyrit.common.brick_contract.enforce_keyword_only_init``. This
check is the static counterpart for everything else, including private helpers, module-level
functions and constructors of non-brick classes.

Exemptions (kept deliberately narrow):

1. **Python data-model dunders** other than ``__init__`` and ``__new__`` (e.g. ``__exit__``,
   ``__aexit__``, ``__eq__``, ``__setattr__``, ``__call__``): the interpreter or a protocol
   dictates their signature and calls them positionally.
2. **``@overload`` stubs**: their signatures mirror the implementation, which is checked.
3. **Hard-coded framework names** (``_FRAMEWORK_EXEMPT_NAMES``): overrides whose positional
   signature is dictated by a third-party base class and invoked positionally by it. Do NOT
   add to this set for one-off cases.
4. **Per-line ``# pyrit-keyword-only-exempt`` marker** on any line of the ``def`` header,
   for one-off third-party callbacks (argparse ``type=``, ``signal`` handlers, ...) or new
   overrides preserving a baselined PyRIT base method's positional contract. Add a short
   reason naming the contract after the marker. ``@override`` alone is not an exemption.
5. **Baseline** (``build_scripts/keyword_only_baseline.txt``): violations that existed when
   the check was introduced. Entries are keyed by ``module:qualname`` (no line numbers) and
   sorted, so the file only changes when a signature is added, fixed, moved or renamed.
   Fixed entries must be removed (the check fails on stale entries) so the baseline can only
   shrink over time. Regenerate it with::

       uv run python -m build_scripts.check_keyword_only_params --write-baseline

Run the check manually with::

    uv run python -m build_scripts.check_keyword_only_params

or rely on the ``check-keyword-only-params`` pre-commit hook in ``.pre-commit-config.yaml``.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

# Project layout: anchor everything off the repo root (directory containing pyrit/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_ROOTS = ("pyrit",)
_BASELINE_PATH = _REPO_ROOT / "build_scripts" / "keyword_only_baseline.txt"

# Maximum number of positional parameters (excluding self/cls) a signature may declare.
_MAX_POSITIONAL = 1

# Dunders that are constructors: their signature is the class's public API, so they are
# checked like any other method. All other dunders are exempt (signature set by Python).
_CHECKED_DUNDERS: frozenset[str] = frozenset({"__init__", "__new__"})

# Framework-mandated method names whose base class calls them positionally.
# Do NOT add to this set for one-off exemptions; use the per-line marker instead so each
# exemption is visible at the violation site.
_FRAMEWORK_EXEMPT_NAMES: frozenset[str] = frozenset(
    {
        "dispatch",  # Starlette BaseHTTPMiddleware.dispatch(request, call_next)
        "process_bind_param",  # SQLAlchemy TypeDecorator.process_bind_param(value, dialect)
        "process_result_value",  # SQLAlchemy TypeDecorator.process_result_value(value, dialect)
    }
)

_OVERLOAD_DECORATORS: frozenset[str] = frozenset({"overload", "typing.overload", "typing_extensions.overload"})
_STATICMETHOD_DECORATORS: frozenset[str] = frozenset({"staticmethod", "builtins.staticmethod"})

_NOQA_MARKER = "# pyrit-keyword-only-exempt"

_BASELINE_HEADER = """\
# Baseline for build_scripts/check_keyword_only_params.py.
#
# Functions and methods that predate the keyword-only check and declare more than one
# positional parameter. Each line is ``module:qualname``. Do not add entries by hand:
# new code must use ``*`` (see doc/contributing/3_style_guide.md). Remove an entry when the
# signature is migrated; the check fails on stale entries. Regenerate with:
#
#     uv run python -m build_scripts.check_keyword_only_params --write-baseline
"""


@dataclass(frozen=True, order=True)
class Violation:
    """A function whose signature declares too many positional parameters."""

    key: str  # ``module:qualname``, the stable baseline key
    path: str  # repo-relative, forward-slash path
    line: int
    positional: tuple[str, ...]


def _module_name(rel_path: str) -> str:
    """Return the dotted module name for a repo-relative ``.py`` path."""
    parts = rel_path[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the dotted names of ``node``'s decorators (call arguments stripped)."""
    names: set[str] = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        names.add(ast.unparse(target))
    return names


def _is_exempt_name(name: str) -> bool:
    """Return True if ``name`` is exempt by the dunder or framework rules."""
    if name in _FRAMEWORK_EXEMPT_NAMES:
        return True
    is_dunder = name.startswith("__") and name.endswith("__") and len(name) > 4
    return is_dunder and name not in _CHECKED_DUNDERS


def _positional_params(node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_method: bool) -> list[str]:
    """Return the positional parameter names of ``node``, minus the implicit ``self``/``cls``."""
    params = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
    if is_method and params and not (_decorator_names(node) & _STATICMETHOD_DECORATORS):
        params = params[1:]
    return params


def _header_has_noqa(node: ast.FunctionDef | ast.AsyncFunctionDef, *, source_lines: list[str]) -> bool:
    """Return True if a comment in the ``def`` header carries the exempt marker.

    The header spans ``node.lineno`` (the ``def`` line, after decorators) through the line
    holding the colon that ends the signature, so a marker the formatter moved onto a later
    line of a multi-line signature still counts, including one on the closing line after an
    inline body (``): pass  # marker``). Comments in the body never count.
    """
    start = node.lineno
    lines = iter(source_lines[start - 1 :])
    depth = 0
    colon_row: int | None = None
    try:
        for token in tokenize.generate_tokens(lambda: next(lines, "")):
            row = token.start[0]
            if colon_row is not None and row > colon_row:
                break
            if token.type == tokenize.COMMENT and _NOQA_MARKER in token.string:
                return True
            if colon_row is None and token.type == tokenize.OP:
                if token.string in "([{":
                    depth += 1
                elif token.string in ")]}":
                    depth -= 1
                elif token.string == ":" and depth == 0:
                    colon_row = row
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return False
    return False


class _SignatureVisitor(ast.NodeVisitor):
    """Collect violations while tracking ``__qualname__``-style scopes."""

    def __init__(self, *, module: str, rel_path: str, source_lines: list[str]) -> None:
        self._module = module
        self._rel_path = rel_path
        self._source_lines = source_lines
        self._scope: list[str] = []
        self._in_class_body: list[bool] = [False]
        self.violations: list[Violation] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # ast.NodeVisitor API
        self._scope.append(node.name)
        self._in_class_body.append(True)
        self.generic_visit(node)
        self._in_class_body.pop()
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # ast.NodeVisitor API
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # ast.NodeVisitor API
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self._scope, node.name])
        self._check(node, qualname=qualname)
        self._scope.extend([node.name, "<locals>"])
        self._in_class_body.append(False)
        self.generic_visit(node)
        self._in_class_body.pop()
        del self._scope[-2:]

    def _check(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, qualname: str) -> None:
        if _is_exempt_name(node.name):
            return
        if _decorator_names(node) & _OVERLOAD_DECORATORS:
            return
        positional = _positional_params(node, is_method=self._in_class_body[-1])
        if len(positional) <= _MAX_POSITIONAL:
            return
        if _header_has_noqa(node, source_lines=self._source_lines):
            return
        self.violations.append(
            Violation(
                key=f"{self._module}:{qualname}",
                path=self._rel_path,
                line=node.lineno,
                positional=tuple(positional),
            )
        )


def scan_file(path: Path, *, repo_root: Path = _REPO_ROOT) -> list[Violation]:
    """Return the keyword-only violations in ``path``.

    Raises:
        SyntaxError: If ``path`` cannot be parsed. Surfaced so an unparseable file cannot
            silently slip past the check.
    """
    rel_path = path.relative_to(repo_root).as_posix()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel_path)
    visitor = _SignatureVisitor(module=_module_name(rel_path), rel_path=rel_path, source_lines=source.splitlines())
    visitor.visit(tree)
    return visitor.violations


def scan_repo(*, repo_root: Path = _REPO_ROOT, scan_roots: tuple[str, ...] = _SCAN_ROOTS) -> list[Violation]:
    """Return all violations across ``scan_roots``, sorted for determinism."""
    violations: list[Violation] = []
    for root in scan_roots:
        for path in sorted((repo_root / root).rglob("*.py")):
            violations.extend(scan_file(path, repo_root=repo_root))
    return sorted(violations)


def read_baseline(path: Path) -> set[str]:
    """Return the ``module:qualname`` keys recorded in the baseline file (empty if missing)."""
    if not path.exists():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def write_baseline(path: Path, *, violations: list[Violation]) -> None:
    """Write the sorted, de-duplicated keys of ``violations`` to ``path``."""
    keys = sorted({violation.key for violation in violations})
    body = "".join(f"{key}\n" for key in keys)
    path.write_text(_BASELINE_HEADER + body, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce keyword-only parameters in pyrit/.")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate the baseline from the current violations instead of checking.",
    )
    parser.add_argument("--baseline", type=Path, default=_BASELINE_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        violations = scan_repo(repo_root=args.repo_root)
    except SyntaxError as exc:
        print(f"[ERROR] could not parse {exc.filename}:{exc.lineno}: {exc.msg}")
        return 1

    if args.write_baseline:
        write_baseline(args.baseline, violations=violations)
        print(f"Wrote {len({v.key for v in violations})} entries to {args.baseline}")
        return 0

    baseline = read_baseline(args.baseline)
    new = [violation for violation in violations if violation.key not in baseline]
    stale = sorted(baseline - {violation.key for violation in violations})

    if new:
        print(
            "[ERROR] Functions declare more than one positional parameter "
            "(see doc/contributing/3_style_guide.md, 'Keyword-Only Parameters'):"
        )
        for violation in new:
            params = ", ".join(violation.positional)
            print(f"  {violation.path}:{violation.line}: {violation.key}({params})")
        print("")
        print("Add `*,` so that at most one parameter (after self/cls) is positional. If a third-party caller")
        print("or a baselined PyRIT base method requires the positional contract, add")
        print("`# pyrit-keyword-only-exempt <reason>` to the `def` header and name that contract.")
    if stale:
        print("[ERROR] Baseline entries no longer violate the keyword-only check; remove them from")
        print(f"{args.baseline.as_posix()} (or rerun with --write-baseline):")
        for key in stale:
            print(f"  {key}")
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main())
