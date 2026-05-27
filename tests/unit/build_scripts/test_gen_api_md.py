# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from build_scripts.gen_api_md import (
    SymbolEntry,
    _build_symbol_index,
    _class_anchor,
    _function_anchor,
    _method_anchor,
    _rewrite_symbol_refs,
)


def _fake_class(name: str, methods: list[str] | None = None) -> dict:
    return {
        "name": name,
        "kind": "class",
        "methods": [{"name": m, "kind": "function"} for m in (methods or [])],
    }


def _fake_function(name: str) -> dict:
    return {"name": name, "kind": "function"}


def _fake_module(name: str, members: list[dict]) -> dict:
    return {"name": name, "kind": "module", "members": members}


def test_anchor_helpers_produce_unique_labels() -> None:
    assert _class_anchor("pyrit.prompt_target", "PromptTarget") == "api-pyrit_prompt_target-PromptTarget"
    assert _function_anchor("pyrit.common", "validate_log_level") == "api-pyrit_common-validate_log_level"
    assert (
        _method_anchor("pyrit.prompt_target", "PromptTarget", "send_prompt_async")
        == "api-pyrit_prompt_target-PromptTarget-send_prompt_async"
    )


def test_build_symbol_index_registers_classes_functions_and_methods() -> None:
    modules = [
        _fake_module(
            "pyrit.prompt_target",
            [
                _fake_class("PromptTarget", methods=["send_prompt_async", "apply_capabilities"]),
                _fake_function("limit_requests_per_minute"),
            ],
        ),
    ]
    index = _build_symbol_index(modules)

    # Short-name lookup
    assert len(index["PromptTarget"]) == 1
    assert index["PromptTarget"][0].kind == "class"
    assert index["PromptTarget"][0].anchor == "api-pyrit_prompt_target-PromptTarget"

    # Class.method lookup
    assert len(index["PromptTarget.send_prompt_async"]) == 1
    assert index["PromptTarget.send_prompt_async"][0].anchor == "api-pyrit_prompt_target-PromptTarget-send_prompt_async"

    # FQN lookup
    assert index["pyrit.prompt_target.PromptTarget"][0].kind == "class"
    assert index["pyrit.prompt_target.limit_requests_per_minute"][0].kind == "function"


def test_build_symbol_index_skips_private_members() -> None:
    modules = [
        _fake_module(
            "pyrit.example",
            [
                _fake_class("Public", methods=["do_thing", "_internal_helper"]),
                _fake_function("_private_func"),
            ],
        ),
    ]
    index = _build_symbol_index(modules)

    assert "_internal_helper" not in index
    assert "Public._internal_helper" not in index
    assert "_private_func" not in index
    assert "do_thing" in index


def test_build_symbol_index_marks_duplicates_as_ambiguous() -> None:
    modules = [
        _fake_module("pyrit.first", [_fake_class("Scorer")]),
        _fake_module("pyrit.second", [_fake_class("Scorer")]),
    ]
    index = _build_symbol_index(modules)

    assert len(index["Scorer"]) == 2
    # FQN entries stay distinct
    assert len(index["pyrit.first.Scorer"]) == 1
    assert len(index["pyrit.second.Scorer"]) == 1


def test_rewrite_symbol_refs_links_unique_class() -> None:
    index = {
        "SeedPrompt": [
            SymbolEntry(
                module="pyrit.models",
                kind="class",
                name="SeedPrompt",
                qualname="SeedPrompt",
                anchor="api-pyrit_models-SeedPrompt",
            )
        ]
    }
    out = _rewrite_symbol_refs("Returns a ``SeedPrompt`` instance.", index)
    assert out == "Returns a [``SeedPrompt``](#api-pyrit_models-SeedPrompt) instance."


def test_rewrite_symbol_refs_handles_single_backticks() -> None:
    index = {"Foo": [SymbolEntry(module="pyrit.x", kind="class", name="Foo", qualname="Foo", anchor="api-pyrit_x-Foo")]}
    out = _rewrite_symbol_refs("See `Foo` for details.", index)
    assert out == "See [`Foo`](#api-pyrit_x-Foo) for details."


def test_rewrite_symbol_refs_resolves_class_dot_method() -> None:
    index = {
        "PromptTarget.send_prompt_async": [
            SymbolEntry(
                module="pyrit.prompt_target",
                kind="method",
                name="send_prompt_async",
                qualname="PromptTarget.send_prompt_async",
                anchor="api-pyrit_prompt_target-PromptTarget-send_prompt_async",
            )
        ]
    }
    out = _rewrite_symbol_refs("Call ``PromptTarget.send_prompt_async`` to dispatch.", index)
    assert "[``PromptTarget.send_prompt_async``]" in out
    assert "#api-pyrit_prompt_target-PromptTarget-send_prompt_async" in out


def test_rewrite_symbol_refs_resolves_bare_method_with_current_class() -> None:
    index = {
        "PromptTarget.send_prompt_async": [
            SymbolEntry(
                module="pyrit.prompt_target",
                kind="method",
                name="send_prompt_async",
                qualname="PromptTarget.send_prompt_async",
                anchor="api-pyrit_prompt_target-PromptTarget-send_prompt_async",
            )
        ],
        "send_prompt_async": [
            SymbolEntry(
                module="pyrit.prompt_target",
                kind="method",
                name="send_prompt_async",
                qualname="PromptTarget.send_prompt_async",
                anchor="api-pyrit_prompt_target-PromptTarget-send_prompt_async",
            )
        ],
    }
    out = _rewrite_symbol_refs("Then ``send_prompt_async`` is invoked.", index, current_class="PromptTarget")
    assert "[``send_prompt_async``]" in out


def test_rewrite_symbol_refs_skips_ambiguous_names() -> None:
    entry_a = SymbolEntry(module="pyrit.a", kind="class", name="Scorer", qualname="Scorer", anchor="api-pyrit_a-Scorer")
    entry_b = SymbolEntry(module="pyrit.b", kind="class", name="Scorer", qualname="Scorer", anchor="api-pyrit_b-Scorer")
    index = {"Scorer": [entry_a, entry_b]}
    out = _rewrite_symbol_refs("Use ``Scorer``.", index)
    assert out == "Use ``Scorer``."


def test_rewrite_symbol_refs_leaves_unknown_names_alone() -> None:
    out = _rewrite_symbol_refs("This is ``True`` and ``None``.", {})
    assert out == "This is ``True`` and ``None``."


def test_rewrite_symbol_refs_resolves_fully_qualified_name() -> None:
    entry = SymbolEntry(
        module="pyrit.models",
        kind="class",
        name="SeedPrompt",
        qualname="SeedPrompt",
        anchor="api-pyrit_models-SeedPrompt",
    )
    index = {"SeedPrompt": [entry], "pyrit.models.SeedPrompt": [entry]}
    out = _rewrite_symbol_refs("Use ``pyrit.models.SeedPrompt`` here.", index)
    assert "[``pyrit.models.SeedPrompt``](#api-pyrit_models-SeedPrompt)" in out


def test_rewrite_symbol_refs_preserves_fenced_code_blocks() -> None:
    index = {
        "SeedPrompt": [
            SymbolEntry(
                module="pyrit.models",
                kind="class",
                name="SeedPrompt",
                qualname="SeedPrompt",
                anchor="api-pyrit_models-SeedPrompt",
            )
        ]
    }
    text = (
        "Outside: ``SeedPrompt``.\n"
        "```python\n"
        "x = SeedPrompt()\n"
        "# ``SeedPrompt`` should not be linked here\n"
        "```\n"
        "After: ``SeedPrompt``."
    )
    out = _rewrite_symbol_refs(text, index)
    assert "[``SeedPrompt``](#api-pyrit_models-SeedPrompt)" in out.split("```")[0]
    assert "# ``SeedPrompt`` should not be linked here" in out
    # The closing "After" sentence should also be rewritten
    assert out.endswith("After: [``SeedPrompt``](#api-pyrit_models-SeedPrompt).")


def test_rewrite_symbol_refs_skips_existing_links() -> None:
    index = {"Foo": [SymbolEntry(module="pyrit.x", kind="class", name="Foo", qualname="Foo", anchor="api-pyrit_x-Foo")]}
    text = "Already-linked: [``Foo``](#api-pyrit_x-Foo)."
    out = _rewrite_symbol_refs(text, index)
    # No double-wrap
    assert out == text


def test_rewrite_symbol_refs_handles_tilde_and_dotted_prefix() -> None:
    entry = SymbolEntry(
        module="pyrit.models",
        kind="class",
        name="SeedPrompt",
        qualname="SeedPrompt",
        anchor="api-pyrit_models-SeedPrompt",
    )
    index = {"pyrit.models.SeedPrompt": [entry]}
    out = _rewrite_symbol_refs("Tilde form ``~pyrit.models.SeedPrompt`` works.", index)
    assert "(#api-pyrit_models-SeedPrompt)" in out


def test_rewrite_symbol_refs_empty_string_passthrough() -> None:
    assert _rewrite_symbol_refs("", {}) == ""
    assert _rewrite_symbol_refs(None, {}) is None  # type: ignore[arg-type]
