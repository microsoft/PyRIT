# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bind operations on supplied paths without granting file access to later template renders."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from jinja2 import meta, nodes

if TYPE_CHECKING:
    from collections.abc import Callable

    from jinja2.sandbox import SandboxedEnvironment


def _bound_path_values(
    *, tree: nodes.Template, parameters: dict[str, Any], environment: SandboxedEnvironment
) -> dict[str, Any]:
    """
    Identify supplied paths and unconditional aliases that are not rebound in another scope.

    Returns:
        The available values, including statically bound path aliases.
    """
    assigned = Counter(node.name for node in tree.find_all(nodes.Name) if node.ctx != "load")
    values = {name: value for name, value in parameters.items() if not assigned[name]}
    for statement in tree.body:
        if not isinstance(statement, nodes.Assign) or not isinstance(statement.target, nodes.Name):
            continue
        name = statement.target.name
        if assigned[name] != 1 or name.startswith("_"):
            continue
        assignment = nodes.Template([statement])
        assignment.set_environment(environment)
        if meta.find_undeclared_variables(assignment) <= values.keys():
            module = environment.from_string(assignment).make_module(values)
            value = getattr(module, name)
            if isinstance(value, PurePath):
                values[name] = value
    return values


def _access_end(*, tokens: list[tuple[int, str, str]], start: int) -> int:
    """
    Find the end of an attribute, index, or call chain in Jinja's lexer tokens.

    Returns:
        The index immediately after the access chain.
    """
    end = start + 1
    while end < len(tokens):
        cursor = end
        while cursor < len(tokens) and tokens[cursor][1] == "whitespace":
            cursor += 1
        if cursor < len(tokens) and tokens[cursor][2] == ".":
            attribute = cursor + 1
            while attribute < len(tokens) and tokens[attribute][1] == "whitespace":
                attribute += 1
            if attribute == len(tokens) or tokens[attribute][1] != "name":
                break
            end = attribute + 1
        elif cursor < len(tokens) and tokens[cursor][2] in ("(", "["):
            end = _balanced_end(tokens=tokens, start=cursor)
        else:
            break
    return end


def _balanced_end(*, tokens: list[tuple[int, str, str]], start: int) -> int:
    """
    Skip balanced brackets, respecting Jinja's atomic string tokens.

    Returns:
        The index immediately after the closing bracket.

    Raises:
        ValueError: If a parsed access chain has unbalanced brackets.
    """
    closing = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    for index in range(start, len(tokens)):
        _, kind, value = tokens[index]
        if kind != "operator":
            continue
        if value in closing:
            stack.append(closing[value])
        elif stack and value == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
    raise ValueError("Unbalanced access in a parsed Jinja template.")


def _is_literal(value: Any) -> bool:
    """
    Check whether a result can be retained as a Jinja literal or a lexical path.

    Returns:
        Whether the value can be safely serialized without a runtime callable.
    """
    if value is None or isinstance(value, str | bool | int | float | PurePath):
        return True
    if isinstance(value, list | tuple):
        return all(_is_literal(item) for item in value)
    if isinstance(value, dict):
        return all(_is_literal(key) and _is_literal(item) for key, item in value.items())
    return False


def _fragment_end(*, tokens: list[tuple[int, str, str]], start: int) -> int:
    """
    Find the end of an output expression or a complete for block.

    Returns:
        The index after the fragment, or the original index when it is not a supported fragment.

    Raises:
        ValueError: If a parsed for block has no closing tag.
    """
    if tokens[start][1] == "variable_begin":
        return next(index + 1 for index in range(start, len(tokens)) if tokens[index][1] == "variable_end")
    if tokens[start][1] != "block_begin":
        return start
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index][1] != "block_begin":
            continue
        keyword = next((token[2] for token in tokens[index + 1 :] if token[1] != "whitespace"), "")
        if index == start and keyword != "for":
            return start
        if keyword == "for":
            depth += 1
        elif keyword == "endfor":
            depth -= 1
            if not depth:
                return next(end + 1 for end in range(index, len(tokens)) if tokens[end][1] == "block_end")
    raise ValueError("Unbalanced for block in a parsed Jinja template.")


def bind_template_path_operations(
    *,
    value: str,
    parameters: dict[str, Any],
    environment: SandboxedEnvironment,
    serialize: Callable[[Any], str],
) -> str:
    """
    Fold fully bound path operations into literals while preserving unresolved template syntax.

    Evaluation happens only at trusted load time. Later renders receive the result, not a new
    filesystem-capable object. Names assigned by loops or macros are excluded so their local
    values are never mistaken for a supplied path.

    Returns:
        The template with supplied path operations bound to their load-time results.
    """
    tree = environment.parse(value)
    used = meta.find_undeclared_variables(tree)
    if not any(isinstance(parameters.get(name), PurePath) for name in used):
        return value
    values = _bound_path_values(tree=tree, parameters=parameters, environment=environment)
    tokens = list(environment.lex(value))
    output: list[str] = []
    index = 0
    previous: str | None = None
    while index < len(tokens):
        _, kind, name = tokens[index]
        fragment_end = _fragment_end(tokens=tokens, start=index)
        if fragment_end > index:
            fragment = "".join(token[2] for token in tokens[index:fragment_end])
            dependencies = meta.find_undeclared_variables(environment.parse(fragment))
            if dependencies <= values.keys() and any(isinstance(values[name], PurePath) for name in dependencies):
                rendered = environment.from_string(fragment).render(values)
                output.append("{{ " + repr(rendered) + " }}")
                previous = tokens[fragment_end - 1][2]
                index = fragment_end
                continue
        if kind == "name" and previous != "." and isinstance(values.get(name), PurePath):
            end = _access_end(tokens=tokens, start=index)
            if end > index + 1:
                expression = "".join(token[2] for token in tokens[index:end])
                dependencies = meta.find_undeclared_variables(environment.parse("{{ " + expression + " }}"))
                if dependencies <= values.keys():
                    result = environment.compile_expression(expression)(**values)
                    if _is_literal(result):
                        output.append(f"({serialize(result)})")
                        previous = tokens[end - 1][2]
                        index = end
                        continue
        output.append(tokens[index][2])
        if kind != "whitespace":
            previous = name
        index += 1
    return "".join(output)
