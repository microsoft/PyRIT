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

import json
from pathlib import Path

API_JSON_DIR = Path("doc/_api")
API_MD_DIR = Path("doc/api")

# Modules excluded from generated API docs (internal implementation details)
EXCLUDED_MODULES = {
    "pyrit.backend",
}


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


def render_function(func: dict, heading_level: str = "###") -> str:
    """Render a function as markdown."""
    name = func["name"]
    is_async = func.get("is_async", False)
    prefix = "async " if is_async else ""
    sig = render_signature(func)
    ret = func.get("returns_annotation", "")
    ret_str = f" → {ret}" if ret else ""

    parts = [f"{heading_level} `{prefix}{name}`\n"]
    parts.append(f"```python\n{prefix}{name}{sig}{ret_str}\n```\n")

    ds = func.get("docstring", {})
    if ds:
        if ds.get("text"):
            parts.append(_escape_docstring_examples(ds["text"]) + "\n")
        params_table = render_params(ds.get("params", []))
        if params_table:
            parts.append(params_table + "\n")
        returns = render_returns(ds.get("returns", []))
        if returns:
            parts.append(returns + "\n")
        raises = render_raises(ds.get("raises", []))
        if raises:
            parts.append(raises + "\n")

    return "\n".join(parts)


def render_class(cls: dict) -> str:
    """Render a class as markdown."""
    name = cls["name"]
    bases = cls.get("bases", [])
    bases_str = f"({', '.join(bases)})" if bases else ""

    parts = [f"## `{name}`\n"]
    if bases_str:
        parts.append(f"Bases: `{bases_str[1:-1]}`\n")

    ds = cls.get("docstring", {})
    if ds and ds.get("text"):
        parts.append(_escape_docstring_examples(ds["text"]) + "\n")

    # __init__
    init = cls.get("init")
    if init:
        init_ds = init.get("docstring", {})
        if init_ds and init_ds.get("params"):
            parts.append("**Constructor Parameters:**\n")
            parts.append(render_params(init_ds["params"]) + "\n")

    # Methods
    methods = cls.get("methods", [])
    if methods:
        parts.append("**Methods:**\n")
        parts.extend(render_function(m, heading_level="####") for m in methods)

    return "\n".join(parts)


def render_alias(alias: dict) -> str:
    """Render an alias as markdown."""
    name = alias["name"]
    target = alias.get("target", "")
    parts = [f"### `{name}`\n"]
    if target:
        parts.append(f"Alias of `{target}`.\n")
    return "\n".join(parts)


def render_module(data: dict) -> str:
    """Render a full module page."""
    mod_name = data["name"]
    parts = [f"# {mod_name}\n"]

    ds = data.get("docstring", {})
    if ds and ds.get("text"):
        parts.append(ds["text"] + "\n")

    members = data.get("members", [])

    classes = [m for m in members if m.get("kind") == "class"]
    functions = [m for m in members if m.get("kind") == "function"]

    if functions:
        parts.append("## Functions\n")
        parts.extend(render_function(f) for f in functions)

    parts.extend(render_class(cls) for cls in classes)

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

    # Generate per-module pages
    for data in modules:
        mod_name = data["name"]
        slug = mod_name.replace(".", "_")
        md_path = API_MD_DIR / f"{slug}.md"
        content = render_module(data)
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

        classes = [f"`{m['name']}`" for m in members if m.get("kind") == "class"]
        functions = [f"`{m['name']}()`" for m in members if m.get("kind") == "function"]
        rendered_count = len(classes) + len(functions)
        preview_items = (classes + functions)[:8]
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


if __name__ == "__main__":
    main()
