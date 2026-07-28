# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Generate MyST markdown API reference pages from griffe JSON.

WORKAROUND: Jupyter Book 2 (MyST engine) does not yet have native support for
auto-generating API documentation from Python source code. This script and
pydoc2json.py are a workaround that generates API reference pages from source.
Once JB2/MyST adds native API doc support, these scripts can be replaced.
Tracking issue: https://github.com/jupyter-book/mystmd/issues/1259

Reads the JSON files produced by pydoc2json.py and generates clean
MyST markdown pages suitable for Jupyter Book 2.

Usage:
    python build_scripts/gen_api_md.py
"""

import ast
import json
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml

# Import sibling script for post-generation TOC validation.
sys.path.insert(0, str(Path(__file__).parent))
import validate_docs

API_JSON_DIR = Path("doc/_api")
API_MD_DIR = Path("doc/api")

# Modules excluded from generated API docs (internal implementation details)
EXCLUDED_MODULES = {
    "pyrit.backend",
}


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


# Backtick code spans that look like Python identifiers (with optional
# dotted paths) — candidates for symbol cross-reference rewriting. Matches
# either `name` or ``name``. The leading negative lookbehind prevents
# touching spans inside an already-rendered MyST link such as
# ``[`Name`](#anchor)`` and also prevents the single-backtick branch from
# matching the inner portion of a ``\u0060\u0060Name\u0060\u0060`` pair.
# A leading tilde or dot is tolerated because reST cross-reference syntax
# like ``:class:`~pyrit.foo.Bar``` may have leaked through earlier cleanups.
_SYMBOL_REF_RE = re.compile(r"(?<![\[`])(``([~.]?[A-Za-z_][\w.]*)``|`([~.]?[A-Za-z_][\w.]*)`)")


def _module_slug(module: str) -> str:
    """Convert a dotted module path to a MyST-label-safe slug."""
    return module.replace(".", "_")


def _class_anchor(module: str, class_name: str) -> str:
    return f"api-{_module_slug(module)}-{class_name}"


def _function_anchor(module: str, func_name: str) -> str:
    return f"api-{_module_slug(module)}-{func_name}"


def _method_anchor(module: str, class_name: str, method_name: str) -> str:
    return f"api-{_module_slug(module)}-{class_name}-{method_name}"


def render_params(params: list[dict]) -> str:
    """Render parameter list as a markdown table."""
    if not params:
        return ""
    lines = ["| Parameter | Type | Description |", "|---|---|---|"]
    for p in params:
        name = f"`{p['name']}`"
        ptype = p.get("type", "")
        desc = p.get("desc", "").replace("\n", " ")
        default = p.get("default", "")
        if default:
            desc += f" Defaults to `{default}`."
        lines.append(f"| {name} | `{ptype}` | {desc} |")
    return "\n".join(lines)


def render_returns(returns: list[dict]) -> str:
    """Render returns section."""
    if not returns:
        return ""
    parts = ["**Returns:**\n"]
    for r in returns:
        rtype = r.get("type", "")
        desc = r.get("desc", "")
        parts.append(f"- `{rtype}` — {desc}")
    return "\n".join(parts)


def render_raises(raises: list[dict]) -> str:
    """Render raises section."""
    if not raises:
        return ""
    parts = ["**Raises:**\n"]
    for r in raises:
        rtype = r.get("type", "")
        desc = r.get("desc", "")
        parts.append(f"- `{rtype}` — {desc}")
    return "\n".join(parts)


def render_signature(member: dict) -> str:
    """Render a function/method signature as a single line."""
    params = member.get("signature", [])
    if not params:
        return "()"
    parts = []
    for p in params:
        name = p["name"]
        if name in ("self", "cls"):
            continue
        ptype = p.get("type", "")
        default = p.get("default", "")
        if ptype and default:
            parts.append(f"{name}: {ptype} = {default}")
        elif ptype:
            parts.append(f"{name}: {ptype}")
        elif default:
            parts.append(f"{name}={default}")
        else:
            parts.append(name)
    # Always single line for heading use
    sig = ", ".join(parts)
    return f"({sig})"


def _escape_docstring_examples(text: str) -> str:
    """Wrap doctest-style examples (>>> lines) in code fences."""
    lines = text.split("\n")
    result: list[str] = []
    in_example = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">>>") and not in_example:
            in_example = True
            result.append("```python")
            result.append(line)
        elif in_example and stripped.startswith((">>>", "...")):
            result.append(line)
        elif in_example:
            result.append("```")
            in_example = False
            result.append(line)
        else:
            result.append(line)
    if in_example:
        result.append("```")
    return "\n".join(result)


def _build_symbol_index(modules: list[dict]) -> dict[str, list[SymbolEntry]]:
    """Build a lookup of every API symbol that the rewriter can target.

    The returned dict is keyed by both the short name (e.g. ``"PromptTarget"``,
    ``"send_prompt_async"``) and several qualified forms
    (``"PromptTarget.send_prompt_async"``, ``"pyrit.prompt_target.PromptTarget"``,
    ``"pyrit.prompt_target.PromptTarget.send_prompt_async"``). Each entry holds
    the module, kind, and final anchor that ``_rewrite_symbol_refs`` will link
    to.

    Multiple entries under the same key indicate an ambiguous reference; the
    rewriter intentionally skips those so we don't pick a wrong target.
    """
    index: dict[str, list[SymbolEntry]] = {}

    def _add(key: str, entry: SymbolEntry) -> None:
        index.setdefault(key, []).append(entry)

    for module in modules:
        mod_name = module.get("name", "")
        for member in module.get("members", []):
            kind = member.get("kind", "")
            name = member.get("name", "")
            if not name or name.startswith("_"):
                continue
            if kind == "class":
                entry = SymbolEntry(
                    module=mod_name,
                    kind="class",
                    name=name,
                    qualname=name,
                    anchor=_class_anchor(mod_name, name),
                )
                _add(name, entry)
                _add(f"{mod_name}.{name}", entry)
                for method in member.get("methods", []) or []:
                    mname = method.get("name", "")
                    if not mname or mname.startswith("_"):
                        continue
                    m_entry = SymbolEntry(
                        module=mod_name,
                        kind="method",
                        name=mname,
                        qualname=f"{name}.{mname}",
                        anchor=_method_anchor(mod_name, name, mname),
                    )
                    _add(mname, m_entry)
                    _add(f"{name}.{mname}", m_entry)
                    _add(f"{mod_name}.{name}.{mname}", m_entry)
            elif kind == "function":
                entry = SymbolEntry(
                    module=mod_name,
                    kind="function",
                    name=name,
                    qualname=name,
                    anchor=_function_anchor(mod_name, name),
                )
                _add(name, entry)
                _add(f"{mod_name}.{name}", entry)
    return index


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


def _resolve_symbol(raw: str, index: dict[str, list[SymbolEntry]], current_class: str | None) -> SymbolEntry | None:
    """Return the cross-reference target for a bare backtick-quoted symbol.

    ``raw`` is the contents between backticks — already stripped of surrounding
    syntax. The lookup is conservative: if more than one symbol matches, we
    return ``None`` to leave the original markup untouched. Trailing tilde
    prefixes (``~pyrit.foo.Bar``) and leading dots are tolerated because they
    occasionally survive Sphinx-style imports.
    """
    cleaned = raw.lstrip("~").lstrip(".")
    if not cleaned:
        return None

    # Try the literal lookup first (handles FQN and Class.method forms).
    entries = index.get(cleaned)
    if entries and len(entries) == 1:
        return entries[0]

    # When inside a class context, a bare method name should resolve to that
    # class's method even if other classes share the same method name.
    if current_class and "." not in cleaned:
        scoped = index.get(f"{current_class}.{cleaned}")
        if scoped and len(scoped) == 1:
            return scoped[0]

    return None


def _rewrite_symbol_refs(
    text: str,
    index: dict[str, list[SymbolEntry]],
    *,
    current_class: str | None = None,
) -> str:
    """Convert ``Name`` / ``Class.method`` backtick spans to MyST links.

    Fenced code blocks are preserved verbatim so doctest examples and Python
    snippets don't get mangled. Within prose, each backtick code span is
    looked up against ``index``; matches become ``[`Name`](#anchor)`` links,
    and everything else is left unchanged.
    """
    if not text:
        return text

    lines = text.split("\n")
    output: list[str] = []
    in_fence = False
    fence_marker: str | None = None

    for line in lines:
        stripped = line.lstrip()
        if not in_fence and stripped.startswith(("```", "~~~")):
            in_fence = True
            fence_marker = stripped[:3]
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            if stripped.startswith(fence_marker or "```"):
                in_fence = False
                fence_marker = None
            continue

        def _sub(match: re.Match[str]) -> str:
            full = match.group(1)
            symbol = match.group(2) or match.group(3) or ""
            entry = _resolve_symbol(symbol, index, current_class)
            if entry is None:
                return full
            return f"[{full}](#{entry.anchor})"

        output.append(_SYMBOL_REF_RE.sub(_sub, line))

    return "\n".join(output)


def _rewrite_param_table(params: list[dict], index: dict[str, list[SymbolEntry]], current_class: str | None) -> None:
    """Run the symbol rewriter over parameter descriptions in-place."""
    for p in params:
        if p.get("desc"):
            p["desc"] = _rewrite_symbol_refs(p["desc"], index, current_class=current_class)


def _format_bases(bases: list[str], symbol_index: dict[str, list[SymbolEntry]] | None) -> str:
    """Render each base class as an individually-linkable code span.

    Each base is wrapped in single backticks and run through the symbol
    rewriter separately so that known PyRIT bases become MyST cross-reference
    links while external bases (e.g. ``str``, ``Enum``) stay as plain code
    spans. The comma-joined output keeps the rendered ``Bases:`` line readable
    even when only some bases resolve.
    """
    if not bases:
        return ""
    if symbol_index is None:
        return ", ".join(f"`{b}`" for b in bases if b)
    return ", ".join(_rewrite_symbol_refs(f"`{b}`", symbol_index) for b in bases if b)


def _format_reexport_alias(
    mod_name: str,
    name: str,
    symbol_index: dict[str, list[SymbolEntry]] | None,
) -> str:
    """Render a re-export alias name as a MyST link when unambiguous.

    Aliases usually live on the current module, so the module-qualified path
    is tried first. If that lookup is unambiguous we link directly to it;
    otherwise we fall back to the regular short-name rewriter so unresolvable
    aliases get the same plain code-span treatment as the rest of the docs.
    """
    if not name:
        return ""
    if symbol_index is None:
        return f"`{name}`"
    fqn = f"{mod_name}.{name}" if mod_name else name
    entries = symbol_index.get(fqn)
    if entries and len(entries) == 1:
        return f"[`{name}`](#{entries[0].anchor})"
    return _rewrite_symbol_refs(f"`{name}`", symbol_index)


def _format_reexport_target(
    target: str,
    symbol_index: dict[str, list[SymbolEntry]] | None,
) -> str:
    """Render a re-export target FQN as a MyST link when it resolves."""
    if not target:
        return ""
    if symbol_index is None:
        return f"`{target}`"
    return _rewrite_symbol_refs(f"`{target}`", symbol_index)


def _rewrite_returns_or_raises(
    items: list[dict], index: dict[str, list[SymbolEntry]], current_class: str | None
) -> None:
    """Run the symbol rewriter over returns/raises description text in-place."""
    for item in items:
        if item.get("desc"):
            item["desc"] = _rewrite_symbol_refs(item["desc"], index, current_class=current_class)


def _process_docstring_text(
    text: str | None,
    symbol_index: dict[str, list[SymbolEntry]] | None,
    current_class: str | None,
) -> str | None:
    """Apply doctest-fence wrapping then symbol cross-reference rewriting."""
    if not text:
        return text
    escaped = _escape_docstring_examples(text)
    if symbol_index is None:
        return escaped
    return _rewrite_symbol_refs(escaped, symbol_index, current_class=current_class)


def render_examples(examples: list[ExampleReference] | None) -> str:
    """Render links to user-guide pages that exercise an API symbol."""
    if not examples:
        return ""
    parts = ["**Examples:**\n"]
    for example in examples:
        title = example.title.replace("[", r"\[").replace("]", r"\]")
        parts.append(f"- [{title}](../{example.path})")
    return "\n".join(parts)


def render_function(
    func: dict,
    *,
    heading_level: str = "###",
    module: str,
    class_name: str | None = None,
    symbol_index: dict[str, list[SymbolEntry]] | None = None,
    examples_by_anchor: dict[str, list[ExampleReference]] | None = None,
) -> str:
    """Render a function as markdown."""
    name = func["name"]
    is_async = func.get("is_async", False)
    prefix = "async " if is_async else ""
    sig = render_signature(func)
    ret = func.get("returns_annotation", "")
    ret_str = f" → {ret}" if ret else ""

    anchor = _method_anchor(module, class_name, name) if class_name else _function_anchor(module, name)

    # Anchor label precedes the heading so MyST cross-refs can target it.
    parts = [f"({anchor})=", f"{heading_level} `{prefix}{name}`\n"]
    parts.append(f"```python\n{prefix}{name}{sig}{ret_str}\n```\n")

    ds = func.get("docstring", {})
    if ds:
        text = _process_docstring_text(ds.get("text"), symbol_index, current_class=class_name)
        if text:
            parts.append(text + "\n")
        params = list(ds.get("params", []))
        if params and symbol_index is not None:
            params = [dict(p) for p in params]
            _rewrite_param_table(params, symbol_index, class_name)
        params_table = render_params(params)
        if params_table:
            parts.append(params_table + "\n")
        returns = list(ds.get("returns", []))
        if returns and symbol_index is not None:
            returns = [dict(r) for r in returns]
            _rewrite_returns_or_raises(returns, symbol_index, class_name)
        returns_md = render_returns(returns)
        if returns_md:
            parts.append(returns_md + "\n")
        raises = list(ds.get("raises", []))
        if raises and symbol_index is not None:
            raises = [dict(r) for r in raises]
            _rewrite_returns_or_raises(raises, symbol_index, class_name)
        raises_md = render_raises(raises)
        if raises_md:
            parts.append(raises_md + "\n")

    examples_md = render_examples(examples_by_anchor.get(anchor) if examples_by_anchor else None)
    if examples_md:
        parts.append(examples_md + "\n")

    return "\n".join(parts)


def render_class(
    cls: dict,
    *,
    module: str,
    symbol_index: dict[str, list[SymbolEntry]] | None = None,
    examples_by_anchor: dict[str, list[ExampleReference]] | None = None,
) -> str:
    """Render a class as markdown."""
    name = cls["name"]
    bases = cls.get("bases", [])

    anchor = _class_anchor(module, name)
    parts = [f"({anchor})=", f"## `{name}`\n"]
    bases_md = _format_bases(bases, symbol_index)
    if bases_md:
        parts.append(f"Bases: {bases_md}\n")

    ds = cls.get("docstring", {})
    text = _process_docstring_text(ds.get("text") if ds else None, symbol_index, current_class=name)
    if text:
        parts.append(text + "\n")

    # __init__
    init = cls.get("init")
    if init:
        init_ds = init.get("docstring", {})
        if init_ds and init_ds.get("params"):
            init_params = [dict(p) for p in init_ds["params"]]
            if symbol_index is not None:
                _rewrite_param_table(init_params, symbol_index, name)
            parts.append("**Constructor Parameters:**\n")
            parts.append(render_params(init_params) + "\n")

    examples_md = render_examples(examples_by_anchor.get(anchor) if examples_by_anchor else None)
    if examples_md:
        parts.append(examples_md + "\n")

    # Methods
    methods = cls.get("methods", [])
    if methods:
        parts.append("**Methods:**\n")
        parts.extend(
            render_function(
                m,
                heading_level="####",
                module=module,
                class_name=name,
                symbol_index=symbol_index,
                examples_by_anchor=examples_by_anchor,
            )
            for m in methods
        )

    return "\n".join(parts)


def render_alias(alias: dict) -> str:
    """Render an alias as markdown."""
    name = alias["name"]
    target = alias.get("target", "")
    parts = [f"### `{name}`\n"]
    if target:
        parts.append(f"Alias of `{target}`.\n")
    return "\n".join(parts)


def render_module(
    data: dict,
    *,
    symbol_index: dict[str, list[SymbolEntry]] | None = None,
    examples_by_anchor: dict[str, list[ExampleReference]] | None = None,
) -> str:
    """Render a full module page."""
    mod_name = data["name"]
    short_name = mod_name.rsplit(".", 1)[-1]
    mod_label = f"api-{_module_slug(mod_name)}"
    parts = [
        "---",
        f"label: {mod_label}",
        f"short_title: {short_name}",
        "---\n",
        f"# {mod_name}\n",
    ]

    ds = data.get("docstring", {})
    text = _process_docstring_text(ds.get("text") if ds else None, symbol_index, current_class=None)
    if text:
        parts.append(text + "\n")

    members = data.get("members", [])

    classes = [m for m in members if m.get("kind") == "class"]
    functions = [m for m in members if m.get("kind") == "function"]
    aliases = [m for m in members if m.get("kind") == "alias"]

    if functions:
        parts.append("## Functions\n")
        parts.extend(
            render_function(
                f,
                module=mod_name,
                symbol_index=symbol_index,
                examples_by_anchor=examples_by_anchor,
            )
            for f in functions
        )

    parts.extend(
        render_class(
            cls,
            module=mod_name,
            symbol_index=symbol_index,
            examples_by_anchor=examples_by_anchor,
        )
        for cls in classes
    )

    if aliases:
        parts.append("## Re-exports\n")
        for a in aliases:
            name_md = _format_reexport_alias(mod_name, a.get("name", ""), symbol_index)
            target_md = _format_reexport_target(a.get("target", ""), symbol_index)
            if target_md:
                parts.append(f"- {name_md} → {target_md}\n")
            else:
                parts.append(f"- {name_md}\n")

    return "\n".join(parts)


def _build_definition_index(
    data: dict,
    index: dict | None = None,
    name_to_modules: dict[str, list[str]] | None = None,
) -> tuple[dict, dict[str, list[str]]]:
    """Build a flat lookup from fully-qualified name to member definition.

    Also builds a reverse lookup mapping each short member name to the list of
    module paths where it is defined, so imports can be distinguished from native
    definitions.
    """
    if index is None:
        index = {}
    if name_to_modules is None:
        name_to_modules = {}
    mod_name = data.get("name", "")
    for member in data.get("members", []):
        kind = member.get("kind", "")
        name = member.get("name", "")
        if kind in ("class", "function") and name:
            fqn = f"{mod_name}.{name}" if mod_name else name
            index[fqn] = member
            name_to_modules.setdefault(name, []).append(mod_name)
        if kind == "module":
            _build_definition_index(member, index, name_to_modules)
    return index, name_to_modules


def _resolve_aliases(modules: list[dict], definition_index: dict, name_to_modules: dict[str, list[str]]) -> None:
    """Replace bare alias entries with the full definition they point to.

    Aliases whose targets resolve to a class or function in the definition index
    are swapped in-place so they render with full documentation.  Unresolvable
    aliases that appear to reference a pyrit class (capitalized name with a
    pyrit target) are kept as minimal class stubs.  Aliases pointing outside the
    pyrit namespace are dropped.

    Also removes classes/functions that griffe reports as direct members but are
    actually imported from a different pyrit module (the same short name is
    defined in another module in the index).
    """
    for module in modules:
        mod_name = module.get("name", "")
        resolved_members: list[dict] = []
        for member in module.get("members", []):
            kind = member.get("kind", "")
            name = member.get("name", "")

            if kind == "alias":
                target = member.get("target", "")
                if not target.startswith("pyrit."):
                    continue  # External import (stdlib, third-party) – skip
                if target in definition_index:
                    defn = definition_index[target].copy()
                    defn["name"] = name
                    resolved_members.append(defn)
                elif name and name[0].isupper():
                    resolved_members.append({"name": name, "kind": "class"})
            elif kind in ("class", "function"):
                # Keep only if this module's tree contains a definition.
                # A member defined in this module or its children is native;
                # appearances in unrelated modules are just imports.
                defining_modules = name_to_modules.get(name, [])
                is_native = not defining_modules or any(
                    m == mod_name or m.startswith(mod_name + ".") for m in defining_modules
                )
                if is_native:
                    resolved_members.append(member)
            else:
                resolved_members.append(member)

        module["members"] = resolved_members


def _expand_module(module: dict) -> list[dict]:
    """Recursively expand pure-aggregate modules into their children.

    A pure-aggregate module has only submodule members and no direct public API
    (classes, functions, aliases).  Its children are returned instead, recursing
    further if a child is also a pure aggregate.
    """
    members = module.get("members", [])
    has_api = any(m.get("kind") in ("class", "function", "alias") for m in members)
    submodules = [m for m in members if m.get("kind") == "module"]

    if has_api or not submodules:
        # Module has its own API, or is a leaf – keep it (filter empty later)
        return [module]

    # Pure aggregate – recurse into children
    result: list[dict] = []
    for sub in submodules:
        result.extend(_expand_module(sub))
    return result


def collect_top_level_modules(api_json_dir: Path) -> list[dict]:
    """Collect top-level modules from aggregate JSON files.

    When pydoc2json.py runs with --submodules, it produces a single JSON file
    (e.g. pyrit_all.json) whose members are submodules.  We only generate pages
    for the public packages users import from, not for deeply nested internal
    submodules whose content is re-exported by the parent.

    Pure-aggregate modules (those with only submodule members) are recursively
    expanded so their children with real API surface get their own pages.
    """
    modules: list[dict] = []
    for jf in sorted(api_json_dir.glob("*.json")):
        data = json.loads(jf.read_text(encoding="utf-8"))
        modules.extend(_expand_module(data))

    # Drop excluded and empty modules
    return [
        m
        for m in modules
        if not any(m.get("name", "").startswith(ex) for ex in EXCLUDED_MODULES)
        and any(member.get("kind") in ("class", "function", "alias") for member in m.get("members", []))
    ]


def main() -> None:
    API_MD_DIR.mkdir(parents=True, exist_ok=True)

    json_files = sorted(API_JSON_DIR.glob("*.json"))
    if not json_files:
        print("No JSON files found in", API_JSON_DIR)
        return

    modules = collect_top_level_modules(API_JSON_DIR)

    # Build a lookup of all definitions and resolve aliases to their targets
    definition_index: dict = {}
    name_to_modules: dict[str, list[str]] = {}
    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        _build_definition_index(data, definition_index, name_to_modules)
    _resolve_aliases(modules, definition_index, name_to_modules)

    # Build a symbol index over the post-resolution module tree so the
    # docstring rewriter can turn backticked names into MyST cross-references.
    symbol_index = _build_symbol_index(modules)
    example_index = _build_example_index(
        doc_root=Path("doc"),
        toc_path=Path("doc/myst.yml"),
        symbol_index=symbol_index,
    )

    # Generate per-module pages
    for data in modules:
        mod_name = data["name"]
        slug = mod_name.replace(".", "_")
        md_path = API_MD_DIR / f"{slug}.md"
        content = render_module(data, symbol_index=symbol_index, examples_by_anchor=example_index)
        members = data.get("members", [])
        rendered_count = sum(1 for m in members if m.get("kind") in ("class", "function"))
        md_path.write_text(content, encoding="utf-8")
        print(f"Written {md_path} ({rendered_count} members)")

    # Generate index page
    index_parts = ["# API Reference\n"]
    for data in modules:
        mod_name = data["name"]
        members = data.get("members", [])
        slug = mod_name.replace(".", "_")

        # Link each class/function in the preview directly to its anchor so the
        # index page is a fast jumping-off point.
        class_links = [
            f"[`{m['name']}`](#{_class_anchor(mod_name, m['name'])})" for m in members if m.get("kind") == "class"
        ]
        function_links = [
            f"[`{m['name']}()`](#{_function_anchor(mod_name, m['name'])})"
            for m in members
            if m.get("kind") == "function"
        ]
        rendered_count = len(class_links) + len(function_links)
        preview_items = (class_links + function_links)[:8]
        preview = ", ".join(preview_items)
        if rendered_count > len(preview_items):
            preview += f" ... ({rendered_count} total)"

        index_parts.append(f"## [{mod_name}]({slug}.md)\n")
        if preview:
            index_parts.append(preview + "\n")
        else:
            index_parts.append("_No public API members detected._\n")

    index_path = API_MD_DIR / "index.md"
    index_path.write_text("\n".join(index_parts), encoding="utf-8")
    print(f"Written {index_path}")

    # Fail loudly if doc/myst.yml's api/ TOC entries no longer match what we
    # generated. Without this check, mismatches only manifest as easy-to-miss
    # warnings in the jupyter-book log (--strict does not treat them as errors)
    # and silently break the Read the Docs build downstream.
    print("Validating doc/myst.yml stays in sync with generated API pages...")
    rc = validate_docs.main()
    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
