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


def render_function(func: dict, heading_level: str = "###") -> str:
    """Render a function as markdown."""
    name = func["name"]
    is_async = func.get("is_async", False)
    prefix = "async " if is_async else ""
    sig = render_signature(func)
    ret = func.get("returns_annotation", "")
    ret_str = f" → {ret}" if ret else ""

    # Heading shows just the name; full signature in a code block below
    parts = [f"{heading_level} `{prefix}{name}`\n"]
    parts.append(f"```python\n{prefix}{name}{sig}{ret_str}\n```\n")

    ds = func.get("docstring", {})
    if ds:
        if ds.get("text"):
            parts.append(ds["text"] + "\n")
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
        parts.append(f"*Bases: {bases_str}*\n")

    ds = cls.get("docstring", {})
    if ds and ds.get("text"):
        parts.append(ds["text"] + "\n")

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


def render_module(data: dict) -> str:
    """Render a full module page."""
    mod_name = data["name"]
    short_name = mod_name.rsplit(".", 1)[-1]
    parts = [
        "---",
        f"short_title: {short_name}",
        "---\n",
        f"# {mod_name}\n",
    ]

    ds = data.get("docstring", {})
    if ds and ds.get("text"):
        parts.append(ds["text"] + "\n")

    members = data.get("members", [])

    # Separate classes and functions
    classes = [m for m in members if m.get("kind") == "class"]
    functions = [m for m in members if m.get("kind") == "function"]
    aliases = [m for m in members if m.get("kind") == "alias"]

    if functions:
        parts.append("## Functions\n")
        parts.extend(render_function(f) for f in functions)

    parts.extend(render_class(cls) for cls in classes)

    if aliases:
        parts.append("## Re-exports\n")
        for a in aliases:
            target = a.get("target", "")
            parts.append(f"- `{a['name']}` → `{target}`\n")

    return "\n".join(parts)


def split_aggregate_json(api_json_dir: Path) -> None:
    """Split aggregate JSON files that contain nested submodules into individual files.

    When pydoc2json.py runs with --submodules, it produces a single JSON file
    (e.g. pyrit_all.json) whose members are submodules. This function recursively
    splits those nested submodules into individual JSON files so that each
    submodule gets its own API reference page.
    """
    for jf in sorted(api_json_dir.glob("*.json")):
        data = json.loads(jf.read_text(encoding="utf-8"))
        _split_submodules(data, jf.name, api_json_dir)


def _split_submodules(data: dict, source_name: str, api_json_dir: Path) -> None:
    """Recursively extract and write submodule members to individual JSON files."""
    for member in data.get("members", []):
        if member.get("kind") != "module":
            continue
        sub_name = member["name"]
        sub_path = api_json_dir / f"{sub_name}.json"
        if not sub_path.exists():
            sub_path.write_text(json.dumps(member, indent=2, default=str), encoding="utf-8")
            print(f"Split {sub_name} from {source_name}")
        # Recurse into nested submodules
        _split_submodules(member, source_name, api_json_dir)


def main() -> None:
    API_MD_DIR.mkdir(parents=True, exist_ok=True)

    # Split aggregate JSON files (e.g. pyrit_all.json) into per-module files
    split_aggregate_json(API_JSON_DIR)

    # Exclude aggregate files that only contain submodules (no direct classes/functions)
    json_files = sorted(API_JSON_DIR.glob("*.json"))
    if not json_files:
        print("No JSON files found in", API_JSON_DIR)
        return

    # Collect module data, skipping pure-aggregate files
    modules = []
    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        members = data.get("members", [])
        # Skip files whose members are all submodules (aggregates like pyrit_all.json)
        non_module_members = [m for m in members if m.get("kind") != "module"]
        if not non_module_members and any(m.get("kind") == "module" for m in members):
            continue
        modules.append(data)

    # Generate index page
    index_parts = ["# API Reference\n"]
    for data in modules:
        mod_name = data["name"]
        members = data.get("members", [])
        member_count = len(members)
        slug = mod_name.replace(".", "_")
        classes = [m["name"] for m in members if m.get("kind") == "class"][:8]
        preview = ", ".join(f"`{c}`" for c in classes)
        if len(classes) < member_count:
            preview += f" ... ({member_count} total)"
        index_parts.append(f"## [{mod_name}]({slug}.md)\n")
        if preview:
            index_parts.append(preview + "\n")

    index_path = API_MD_DIR / "index.md"
    index_path.write_text("\n".join(index_parts), encoding="utf-8")
    print(f"Written {index_path}")

    # Generate per-module pages
    for data in modules:
        mod_name = data["name"]
        members = data.get("members", [])
        # Skip modules with no members and no meaningful docstring
        ds_text = (data.get("docstring") or {}).get("text", "")
        if not members and len(ds_text) < 50:
            continue
        slug = mod_name.replace(".", "_")
        md_path = API_MD_DIR / f"{slug}.md"
        content = render_module(data)
        md_path.write_text(content, encoding="utf-8")
        print(f"Written {md_path} ({len(members)} members)")


if __name__ == "__main__":
    main()
