# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Generate API reference JSON from Python source using griffe.

WORKAROUND: Jupyter Book 2 (MyST engine) does not yet have native support for
auto-generating API documentation from Python source code. This script and
gen_api_md.py are a workaround that generates API reference pages from source.
Once JB2/MyST adds native API doc support, these scripts can be replaced.
Tracking issue: https://github.com/jupyter-book/mystmd/issues/1259

Walks the pyrit package, parses Google-style docstrings, and outputs
structured JSON that gen_api_md.py converts to MyST markdown pages.

Usage:
    python scripts/pydoc2json.py pyrit -o doc/_api/pyrit.json
    python scripts/pydoc2json.py pyrit.score -o doc/_api/pyrit.score.json
"""

import argparse
import json
import sys
from pathlib import Path

import griffe


def docstring_to_dict(docstring: griffe.Docstring | None) -> dict | None:
    """Parse a griffe Docstring into a structured dict."""
    if not docstring:
        return None

    parsed = docstring.parse("google")
    result = {"text": "", "params": [], "returns": [], "raises": [], "examples": []}

    for section in parsed:
        if section.kind == griffe.DocstringSectionKind.text:
            result["text"] = section.value.strip()
        elif section.kind == griffe.DocstringSectionKind.parameters:
            for param in section.value:
                result["params"].append(
                    {
                        "name": param.name,
                        "type": str(param.annotation) if param.annotation else "",
                        "desc": param.description or "",
                        "default": str(param.default) if param.default else "",
                    }
                )
        elif section.kind == griffe.DocstringSectionKind.returns:
            for ret in section.value:
                result["returns"].append(
                    {
                        "type": str(ret.annotation) if ret.annotation else "",
                        "desc": ret.description or "",
                    }
                )
        elif section.kind == griffe.DocstringSectionKind.raises:
            for exc in section.value:
                result["raises"].append(
                    {
                        "type": str(exc.annotation) if exc.annotation else "",
                        "desc": exc.description or "",
                    }
                )
        elif section.kind == griffe.DocstringSectionKind.examples:
            for example in section.value:
                if isinstance(example, tuple):
                    result["examples"].append({"kind": example[0].value, "value": example[1]})

    # Remove empty fields
    return {k: v for k, v in result.items() if v}


def function_to_dict(func: griffe.Function) -> dict:
    """Convert a griffe Function to a structured dict."""
    sig_params = []
    for param in func.parameters:
        p = {"name": param.name}
        if param.annotation:
            p["type"] = str(param.annotation)
        if param.default is not None and str(param.default) != "":
            p["default"] = str(param.default)
        if param.kind:
            p["kind"] = param.kind.value
        sig_params.append(p)

    result = {
        "name": func.name,
        "kind": "function",
        "signature": sig_params,
        "docstring": docstring_to_dict(func.docstring),
        "is_async": func.is_async if hasattr(func, "is_async") else False,
    }
    if func.returns:
        result["returns_annotation"] = str(func.returns)

    return result


def class_to_dict(cls: griffe.Class) -> dict:
    """Convert a griffe Class to a structured dict."""
    result = {
        "name": cls.name,
        "kind": "class",
        "docstring": docstring_to_dict(cls.docstring),
        "bases": [str(b) for b in cls.bases] if cls.bases else [],
        "methods": [],
        "attributes": [],
    }

    # Get __init__ if it has docstring/params
    init = cls.members.get("__init__")
    if init and isinstance(init, griffe.Function):
        result["init"] = function_to_dict(init)

    # Public methods
    for name, member in sorted(cls.members.items()):
        if name.startswith("_") and name != "__init__":
            continue
        try:
            if isinstance(member, griffe.Function) and name != "__init__":
                result["methods"].append(function_to_dict(member))
            elif isinstance(member, griffe.Attribute):
                attr = {"name": name}
                if member.annotation:
                    attr["type"] = str(member.annotation)
                if member.docstring:
                    attr["docstring"] = member.docstring.value.strip()
                result["attributes"].append(attr)
        except Exception:
            continue

    # Remove empty fields
    if not result["methods"]:
        del result["methods"]
    if not result["attributes"]:
        del result["attributes"]
    if not result["bases"]:
        del result["bases"]

    return result


def _resolve_alias_from_source(target_path: str) -> dict | None:
    """Try to resolve an unresolvable alias by loading the target .py file directly.

    When griffe cannot resolve an alias (e.g. due to missing __init__.py in
    namespace packages), fall back to parsing the individual source file and
    extracting the class or function definition.
    """
    parts = target_path.rsplit(".", 1)
    if len(parts) != 2:
        return None
    module_path, member_name = parts
    source_file = Path(module_path.replace(".", "/") + ".py")
    if not source_file.exists():
        return None
    try:
        code = source_file.read_text(encoding="utf-8")
        file_mod = griffe.visit(module_path, code=code, filepath=source_file)
        member = file_mod.members.get(member_name)
        if isinstance(member, griffe.Class):
            return class_to_dict(member)
        if isinstance(member, griffe.Function):
            return function_to_dict(member)
    except Exception:
        pass
    return None


def module_to_dict(mod: griffe.Module, include_submodules: bool = False) -> dict:
    """Convert a griffe Module to a structured dict."""
    result = {
        "name": mod.path,
        "kind": "module",
        "docstring": docstring_to_dict(mod.docstring),
        "members": [],
    }

    for name, member in sorted(mod.members.items()):
        if name.startswith("_"):
            continue
        try:
            if isinstance(member, griffe.Class):
                result["members"].append(class_to_dict(member))
            elif isinstance(member, griffe.Function):
                result["members"].append(function_to_dict(member))
            elif isinstance(member, griffe.Alias):
                # Re-exported names — try to resolve
                try:
                    target = member.final_target
                    if isinstance(target, griffe.Class):
                        result["members"].append(class_to_dict(target))
                    elif isinstance(target, griffe.Function):
                        result["members"].append(function_to_dict(target))
                except Exception:
                    # Griffe cannot resolve (e.g. namespace package) — try source file
                    resolved = _resolve_alias_from_source(str(member.target_path))
                    if resolved:
                        resolved["name"] = name
                        result["members"].append(resolved)
                    else:
                        result["members"].append({"name": name, "kind": "alias", "target": str(member.target_path)})
            elif isinstance(member, griffe.Module) and include_submodules:
                result["members"].append(module_to_dict(member, include_submodules=True))
        except Exception as e:
            print(f"  Warning: skipping {name}: {e}", file=sys.stderr)
            continue

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate API reference JSON using griffe")
    parser.add_argument("module", help="Python module path (e.g. pyrit or pyrit.score)")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON file")
    parser.add_argument("--submodules", action="store_true", help="Include submodules recursively")
    args = parser.parse_args()

    loader = griffe.GriffeLoader(search_paths=[Path(".")])
    try:
        mod = loader.load(args.module)
    except Exception as e:
        print(f"Error loading {args.module}: {e}", file=sys.stderr)
        sys.exit(1)

    data = module_to_dict(mod, include_submodules=args.submodules)

    output_json = json.dumps(data, indent=2, default=str)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_json, encoding="utf-8")
        print(f"Written to {args.output} ({len(data['members'])} members)")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
