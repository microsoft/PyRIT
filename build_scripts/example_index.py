# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Index documentation pages that demonstrate public PyRIT API symbols."""

import ast
import json
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml

from build_scripts import validate_docs


@dataclass(frozen=True)
class SymbolEntry:
    """A resolved API symbol that can be cross-referenced from a docstring."""

    module: str  # dotted module path, e.g. "pyrit.prompt_target"
    kind: str  # "class" | "function" | "method"
    name: str  # short name (last segment)
    qualname: str  # "PromptTarget" or "PromptTarget.send_prompt_async"
    anchor: str  # MyST label, e.g. "api-pyrit_prompt_target-PromptTarget"


@dataclass(frozen=True)
class ExampleReference:
    """A user-guide page that demonstrates an API symbol."""

    title: str
    path: str


@dataclass(frozen=True)
class _ImportBinding:
    """A local import name bound to either a symbol or a PyRIT namespace."""

    symbol: SymbolEntry | None = None
    namespace: str | None = None


_EXAMPLE_DOC_EXCLUDED_PREFIXES = (
    "_api/",
    "api/",
    "blog/",
    "contributing/",
    "generate_docs/",
)


def _unique_public_symbol(entries: list[SymbolEntry]) -> SymbolEntry | None:
    """Return one public class/function target when all matches share an anchor."""
    by_anchor = {entry.anchor: entry for entry in entries if entry.kind in ("class", "function")}
    return next(iter(by_anchor.values())) if len(by_anchor) == 1 else None


def _resolve_imported_symbol(
    qualified_name: str,
    *,
    symbol_index: dict[str, list[SymbolEntry]],
) -> SymbolEntry | None:
    """Resolve an imported API name, with a conservative short-name fallback."""
    exact = _unique_public_symbol(symbol_index.get(qualified_name, []))
    if exact:
        return exact
    # Re-export imports may name a module other than the defining module, so
    # fall back only when the short name is unique across the entire API.
    short_entries = symbol_index.get(qualified_name.rsplit(".", 1)[-1], [])
    module_matches = [entry for entry in short_entries if qualified_name.startswith(f"{entry.module}.")]
    return _unique_public_symbol(module_matches) or _unique_public_symbol(short_entries)


def _is_pyrit_module(name: str) -> bool:
    """Return whether a dotted import path belongs to the PyRIT package."""
    return name == "pyrit" or name.startswith("pyrit.")


def _collect_import_bindings(
    trees: list[ast.AST],
    *,
    symbol_index: dict[str, list[SymbolEntry]],
) -> dict[str, _ImportBinding]:
    """Collect local names introduced by PyRIT imports."""
    bindings: dict[str, _ImportBinding] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and _is_pyrit_module(node.module):
                _add_from_import_bindings(node, bindings=bindings, symbol_index=symbol_index)
            elif isinstance(node, ast.Import):
                _add_import_bindings(node, bindings=bindings)
    return bindings


def _add_from_import_bindings(
    node: ast.ImportFrom,
    *,
    bindings: dict[str, _ImportBinding],
    symbol_index: dict[str, list[SymbolEntry]],
) -> None:
    """Add bindings from one ``from pyrit... import ...`` statement."""
    if not node.module:
        return
    for alias in node.names:
        if alias.name == "*":
            continue
        local_name = alias.asname or alias.name
        qualified_name = f"{node.module}.{alias.name}"
        symbol = _resolve_imported_symbol(qualified_name, symbol_index=symbol_index)
        bindings[local_name] = _ImportBinding(symbol=symbol, namespace=None if symbol else qualified_name)


def _add_import_bindings(
    node: ast.Import,
    *,
    bindings: dict[str, _ImportBinding],
) -> None:
    """Add namespace bindings from one ``import pyrit...`` statement."""
    for alias in node.names:
        if not _is_pyrit_module(alias.name):
            continue
        local_name = alias.asname or alias.name.split(".", 1)[0]
        namespace = alias.name if alias.asname else local_name
        bindings[local_name] = _ImportBinding(namespace=namespace)


def _attribute_parts(node: ast.Attribute) -> list[str] | None:
    """Return the dotted-name parts represented by an attribute expression."""
    parts = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return None
    parts.append(value.id)
    return list(reversed(parts))


def _collect_used_anchors(
    trees: list[ast.AST],
    *,
    bindings: dict[str, _ImportBinding],
    symbol_index: dict[str, list[SymbolEntry]],
) -> set[str]:
    """Find imported API symbols that are actually referenced by the page."""
    anchors: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                binding = bindings.get(node.id)
                if binding and binding.symbol:
                    anchors.add(binding.symbol.anchor)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                symbol = _resolve_attribute_symbol(node, bindings=bindings, symbol_index=symbol_index)
                if symbol:
                    anchors.add(symbol.anchor)
    return anchors


def _resolve_attribute_symbol(
    node: ast.Attribute,
    *,
    bindings: dict[str, _ImportBinding],
    symbol_index: dict[str, list[SymbolEntry]],
) -> SymbolEntry | None:
    """Resolve an attribute rooted in an imported PyRIT namespace."""
    parts = _attribute_parts(node)
    if not parts:
        return None
    binding = bindings.get(parts[0])
    if not binding or not binding.namespace:
        return None
    for end in range(1, len(parts)):
        qualified_name = ".".join([binding.namespace, *parts[1 : end + 1]])
        exact = _unique_public_symbol(symbol_index.get(qualified_name, []))
        if exact:
            return exact
        segment = parts[end]
        # Re-exported classes can be rooted in a namespace that differs from
        # their defining module; accept only an API-wide unique class name.
        if segment[:1].isupper():
            class_match = _unique_public_symbol(symbol_index.get(segment, []))
            if class_match and class_match.kind == "class":
                return class_match
    qualified_name = ".".join([binding.namespace, *parts[1:]])
    return _resolve_imported_symbol(qualified_name, symbol_index=symbol_index)


def _parse_python_chunks(chunks: list[str]) -> list[ast.AST]:
    """Parse independent code chunks, ignoring notebook magics or invalid snippets."""
    trees: list[ast.AST] = []
    for chunk in chunks:
        try:
            trees.append(ast.parse(chunk))
        except SyntaxError:
            continue
    return trees


def _jupytext_code_chunks(text: str) -> list[str]:
    """Extract Python cells from a percent-format Jupytext file."""
    chunks: list[str] = []
    current: list[str] = []
    is_markdown = False
    for line in text.splitlines():
        if line.startswith("# %%"):
            if current and not is_markdown:
                chunks.append("\n".join(current))
            current = []
            is_markdown = "[markdown]" in line
        elif not is_markdown:
            current.append(line)
    if current and not is_markdown:
        chunks.append("\n".join(current))
    return chunks


def _markdown_code_chunks(text: str) -> list[str]:
    """Extract fenced Python examples from a Markdown page."""
    pattern = re.compile(r"^[ \t]{0,3}```(?:python|py)\s*\n(.*?)^[ \t]{0,3}```\s*$", flags=re.MULTILINE | re.DOTALL)
    return [textwrap.dedent(match.group(1)) for match in pattern.finditer(text)]


def _notebook_content(path: Path) -> tuple[list[str], str | None]:
    """Return code-cell sources and the first H1 from a notebook."""
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], None
    cells = notebook.get("cells", [])
    chunks = ["".join(cell.get("source", [])) for cell in cells if cell.get("cell_type") == "code"]
    markdown = "\n".join("".join(cell.get("source", [])) for cell in cells if cell.get("cell_type") == "markdown")
    return chunks, _first_markdown_h1(markdown)


def _first_markdown_h1(text: str) -> str | None:
    """Return the first level-one Markdown heading."""
    match = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _markdown_title(text: str) -> str | None:
    """Return a frontmatter title when present, otherwise the first H1."""
    frontmatter = re.match(r"^---\s*\n(.*?)^---\s*$", text, flags=re.MULTILINE | re.DOTALL)
    if frontmatter:
        match = re.search(r"^title:\s*(.+?)\s*$", frontmatter.group(1), flags=re.MULTILINE)
        if match:
            return match.group(1).strip().strip("\"'")
    return _first_markdown_h1(text)


def _jupytext_h1(text: str) -> str | None:
    """Return the first level-one heading encoded in Jupytext comments."""
    match = re.search(r"^#\s+#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _humanize_stem(path: Path) -> str:
    """Create a readable fallback title from a documentation filename."""
    stem = re.sub(r"^\d+(?:_\d+)*_", "", path.stem)
    return stem.replace("_", " ").replace("-", " ").title()


def _read_example_page(page_path: Path) -> tuple[list[str], str]:
    """Read one TOC page and return its Python chunks and display title."""
    if page_path.suffix == ".ipynb" and page_path.with_suffix(".py").exists():
        source_path = page_path.with_suffix(".py")
        text = source_path.read_text(encoding="utf-8")
        return _jupytext_code_chunks(text), _jupytext_h1(text) or _humanize_stem(page_path)
    if page_path.suffix == ".ipynb":
        chunks, title = _notebook_content(page_path)
        return chunks, title or _humanize_stem(page_path)
    text = page_path.read_text(encoding="utf-8")
    if page_path.suffix == ".py":
        return _jupytext_code_chunks(text), _jupytext_h1(text) or _humanize_stem(page_path)
    return _markdown_code_chunks(text), _markdown_title(text) or _humanize_stem(page_path)


def _example_toc_paths(*, doc_root: Path, toc_path: Path) -> list[Path]:
    """Return existing user-guide pages referenced by the MyST TOC."""
    config = yaml.safe_load(toc_path.read_text(encoding="utf-8"))
    entries = config.get("project", {}).get("toc", [])
    references = validate_docs.parse_toc_files(entries)
    paths = []
    for reference in sorted(references):
        normalized = Path(reference).as_posix()
        if normalized.startswith(_EXAMPLE_DOC_EXCLUDED_PREFIXES):
            continue
        path = doc_root / reference
        if path.is_file() and path.suffix in (".md", ".ipynb", ".py"):
            paths.append(path)
    return paths


def _build_example_index(
    *,
    doc_root: Path,
    toc_path: Path,
    symbol_index: dict[str, list[SymbolEntry]],
) -> dict[str, list[ExampleReference]]:
    """Map API anchors to TOC pages that import and use those symbols."""
    examples: dict[str, set[ExampleReference]] = {}
    for page_path in _example_toc_paths(doc_root=doc_root, toc_path=toc_path):
        chunks, title = _read_example_page(page_path)
        trees = _parse_python_chunks(chunks)
        bindings = _collect_import_bindings(trees, symbol_index=symbol_index)
        anchors = _collect_used_anchors(trees, bindings=bindings, symbol_index=symbol_index)
        reference = ExampleReference(title=title, path=page_path.relative_to(doc_root).as_posix())
        for anchor in anchors:
            examples.setdefault(anchor, set()).add(reference)
    return {
        anchor: sorted(references, key=lambda reference: (reference.title.casefold(), reference.path))
        for anchor, references in examples.items()
    }
