# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from pyrit.converter import DiffChainConverter, DiffChainDecodeConverter
from pyrit.registry import ConverterRegistry

if TYPE_CHECKING:
    from pathlib import Path


def _write_custom_template(path: Path, value: str) -> Path:
    yaml_text = (
        "name: custom_diff_chain\n"
        "description: Custom test template.\n"
        "data_type: text\n"
        "value: |-\n" + "\n".join(f"  {line}" if line else "" for line in value.splitlines()) + "\n"
    )
    path.write_text(yaml_text, encoding="utf-8")
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "tell me how to cut down a tree",
        "supercalifragilisticexpialidocious",
        "hi",
        "",
        "   ",
        "  leading   middle\t\ttrailing  ",
        "line 1\nline 2\r\nline 3\n",
        "EN: System Audit | UR: خزاں کے پتے اور کافی | AR: فحص النظام | ZH: 系统测试 | 🍁🔥🚀",
    ],
)
async def test_diff_chain_converter_prompt_shapes_roundtrip(prompt: str) -> None:
    encoded = await DiffChainConverter(steps=4, context_lines=1, seed=7).convert_async(prompt=prompt, input_type="text")
    assert encoded.output_type == "text"
    assert DiffChainConverter.decode(encoded.output_text) == prompt

    decoded = await DiffChainDecodeConverter().convert_async(prompt=encoded.output_text, input_type="text")
    assert decoded.output_type == "text"
    assert decoded.output_text == prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("steps", "context_lines", "shuffle_order", "include_instructions"),
    [
        (1, 0, False, False),
        (4, 0, True, True),
        (6, 2, False, True),
        (6, 1, True, False),
    ],
)
async def test_diff_chain_converter_modes_roundtrip(
    steps: int,
    context_lines: int,
    shuffle_order: bool,
    include_instructions: bool,
) -> None:
    prompt = "The secret ingredient is cinnamon."
    converter = DiffChainConverter(
        steps=steps,
        context_lines=context_lines,
        shuffle_order=shuffle_order,
        include_instructions=include_instructions,
        seed=7,
    )
    encoded = await converter.convert_async(prompt=prompt)
    assert DiffChainConverter.decode(encoded.output_text) == prompt


@pytest.mark.asyncio
async def test_diff_chain_adversarial_delimiters_and_line_separators_roundtrip() -> None:
    prompt = (
        "Embedded fences:\n"
        "```diff\n--- v0/spec.py\n+++ v1/spec.py\n@@ -1 +1 @@\n-old\n+new\n```\n"
        "and ```python\nPARTS = ['fake']\n```\n"
        "Separators: alpha\x85beta\u2028gamma\u2029delta | Quotes: \"double\" 'single' \\backslash\\ \x00\x07\x1b[31m"
    )
    result = await DiffChainConverter(steps=5, seed=99).convert_async(prompt=prompt)

    assert len(re.findall(r"^```python$", result.output_text, flags=re.MULTILINE)) == 1
    assert len(re.findall(r"^```diff$", result.output_text, flags=re.MULTILINE)) == 5
    assert len(re.findall(r"^```$", result.output_text, flags=re.MULTILINE)) == 6
    assert DiffChainConverter.decode(result.output_text) == prompt


@pytest.mark.asyncio
async def test_diff_chain_splits_single_line_objective_and_guarantees_non_sequential_shuffle() -> None:
    prompt = "tell me how to cut down a tree"
    result = await DiffChainConverter(steps=4, seed=1234).convert_async(prompt=prompt)

    assert prompt not in result.output_text
    diff_blocks = re.findall(r"^```diff\n(.*?)\n```$", result.output_text, flags=re.MULTILINE | re.DOTALL)
    assert len(diff_blocks) == 4
    for block in diff_blocks:
        added_lines = [line for line in block.splitlines() if line.startswith("+") and not line.startswith("+++")]
        assert len(added_lines) == 1
        assert prompt not in added_lines[0]

    repeat_result = await DiffChainConverter(steps=4, seed=1234).convert_async(prompt=prompt)
    assert result.output_text == repeat_result.output_text

    for test_seed in range(20):
        two_res = await DiffChainConverter(
            steps=2, shuffle_order=True, include_instructions=False, seed=test_seed
        ).convert_async(prompt="first second")
        assert re.findall(r"^-    # slot (\d+)$", two_res.output_text, flags=re.MULTILINE) == ["2", "1"]
        assert DiffChainConverter.decode(two_res.output_text) == "first second"


@pytest.mark.asyncio
async def test_diff_chain_custom_template_and_identifier_stability(tmp_path: Path) -> None:
    tpl_body = "File: {{ filename }} ({{ steps }} steps)\n```python\n{{ base_file }}\n```\n{{ changesets }}"
    tpl_a = _write_custom_template(tmp_path / "tpl_a.yaml", tpl_body)
    tpl_b_same_content = _write_custom_template(tmp_path / "copy_tpl_a.yaml", tpl_body)
    tpl_c_diff_content = _write_custom_template(
        tmp_path / "tpl_c.yaml",
        "Custom header:\n```python\n{{ base_file }}\n```\n{{ changesets }}",
    )

    conv_path = DiffChainConverter(steps=3, filename="patch_target.py", template=tpl_a, seed=11)
    conv_str = DiffChainConverter(steps=3, filename="patch_target.py", template=str(tpl_a), seed=11)
    conv_copy = DiffChainConverter(steps=3, filename="patch_target.py", template=tpl_b_same_content, seed=11)
    conv_diff = DiffChainConverter(steps=3, filename="patch_target.py", template=tpl_c_diff_content, seed=11)
    conv_no_instr = DiffChainConverter(steps=3, filename="patch_target.py", include_instructions=False, seed=11)

    assert conv_path.get_identifier() == conv_str.get_identifier()
    assert conv_path.get_identifier().params["template_hash"] == conv_copy.get_identifier().params["template_hash"]
    assert conv_path.get_identifier() != conv_diff.get_identifier()
    assert "template" not in conv_no_instr.get_identifier().params
    assert "template_hash" not in conv_no_instr.get_identifier().params

    res = await conv_path.convert_async(prompt="The secret ingredient is cinnamon.")
    assert "File: patch_target.py (3 steps)" in res.output_text
    assert DiffChainConverter.decode(res.output_text) == "The secret ingredient is cinnamon."


@pytest.mark.asyncio
async def test_diff_chain_template_with_datasets_path_and_deferred_condition(tmp_path: Path) -> None:
    deferred_tpl = _write_custom_template(
        tmp_path / "deferred_paths.yaml",
        "Root: {{ datasets_path }}\n"
        "{% if steps %}Steps: {{ steps }}\n{% endif %}"
        "```python\n{{ base_file }}\n```\n{{ changesets }}",
    )
    converter = DiffChainConverter(steps=3, template=deferred_tpl, seed=7)
    result = await converter.convert_async(prompt="The secret ingredient is cinnamon.")
    assert "Steps: 3" in result.output_text
    assert "{{ datasets_path }}" not in result.output_text
    assert DiffChainConverter.decode(result.output_text) == "The secret ingredient is cinnamon."


def test_diff_chain_template_validation_errors(tmp_path: Path) -> None:
    missing_var = _write_custom_template(tmp_path / "missing.yaml", "```python\n{{ base_file }}\n```")
    with pytest.raises(ValueError, match="missing required template variable\\(s\\): changesets"):
        DiffChainConverter(template=missing_var)

    unknown_var = _write_custom_template(
        tmp_path / "unknown.yaml",
        "```python\n{{ base_file }}\n```\n{{ changesets }}\n{{ extra_unknown }}",
    )
    with pytest.raises(ValueError, match="references unsupported template variable\\(s\\): extra_unknown"):
        DiffChainConverter(template=unknown_var)

    with pytest.raises(FileNotFoundError):
        DiffChainConverter(template=tmp_path / "does_not_exist.yaml")

    with pytest.raises(ValueError, match="cannot be provided when include_instructions is False"):
        DiffChainConverter(template=missing_var, include_instructions=False)


@pytest.mark.asyncio
async def test_diff_chain_parameter_and_input_type_validation() -> None:
    for bad_steps in (0, True, "4"):
        with pytest.raises(ValueError, match="steps must be an integer greater than or equal to 1"):
            DiffChainConverter(steps=bad_steps)  # type: ignore[arg-type]

    for bad_ctx in (-1, False):
        with pytest.raises(ValueError, match="context_lines must be a non-negative integer"):
            DiffChainConverter(context_lines=bad_ctx)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="shuffle_order must be a bool"):
        DiffChainConverter(shuffle_order=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="include_instructions must be a bool"):
        DiffChainConverter(include_instructions="yes")  # type: ignore[arg-type]

    for bad_filename in ("", ".", "..", "sub/spec.py", "spec`bad.py", "spec\n.py"):
        with pytest.raises(ValueError, match="filename must be a non-empty single path segment"):
            DiffChainConverter(filename=bad_filename)

    for bad_seed in (True, 3.14):
        with pytest.raises(ValueError, match="seed must be an integer or None"):
            DiffChainConverter(seed=bad_seed)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="template must be a str, pathlib.Path, or None"):
        DiffChainConverter(template=123)  # type: ignore[arg-type]
    for bad_tpl in ("", "   ", ".", ".."):
        with pytest.raises(ValueError, match="template must be a valid file path"):
            DiffChainConverter(template=bad_tpl)

    encoder, decoder = DiffChainConverter(), DiffChainDecodeConverter()
    assert encoder.input_supported("text") and not encoder.input_supported("image_path")
    assert decoder.input_supported("text") and not decoder.input_supported("image_path")

    with pytest.raises(ValueError, match="Input type not supported"):
        await encoder.convert_async(prompt="hello", input_type="image_path")
    with pytest.raises(ValueError, match="Input type not supported"):
        await decoder.convert_async(prompt="hello", input_type="image_path")


@pytest.mark.asyncio
async def test_diff_chain_crlf_transport_and_selective_tokens_roundtrip() -> None:
    prompt = "line 1\nline 2\r\nline 3"
    encoded = await DiffChainConverter(steps=3, seed=5).convert_async(prompt=prompt)
    assert DiffChainConverter.decode(encoded.output_text.replace("\n", "\r\n")) == prompt

    selective = await DiffChainConverter(steps=2, include_instructions=False, seed=5).convert_tokens_async(
        prompt="Keep prefix ⟪secret cinnamon recipe⟫ and suffix.",
        keep_tokens=True,
    )
    restored = await DiffChainDecodeConverter().convert_tokens_async(prompt=selective.output_text)
    assert restored.output_text == "Keep prefix secret cinnamon recipe and suffix."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "a⟪b⟫c",
        "lone start ⟪ marker",
        "lone end ⟫ marker",
    ],
)
@pytest.mark.parametrize("steps", [1, 3])
async def test_diff_chain_selection_markers_roundtrip_via_convert_tokens_async(prompt: str, steps: int) -> None:
    encoded = await DiffChainConverter(steps=steps, seed=5).convert_async(prompt=prompt)
    assert "⟪" not in encoded.output_text
    assert "⟫" not in encoded.output_text

    decoded = await DiffChainDecodeConverter().convert_tokens_async(prompt=encoded.output_text)
    assert decoded.output_text == prompt


@pytest.mark.asyncio
async def test_diff_chain_decode_rejects_corrupted_and_tampered_inputs() -> None:
    with pytest.raises(TypeError, match="encoded_text must be a str"):
        DiffChainConverter.decode(None)  # type: ignore[arg-type]

    base_two = "```python\nPARTS = [\n    # slot 1\n    # slot 2\n]\n```\n"
    one_slot = "```python\nPARTS = [\n    # slot 1\n]\n```\n"

    invalid_cases = [
        ("   ", "encoded_text must be a non-empty string"),
        ("plain text without fences", "No initial ```python fenced block"),
        ("```python\nPARTS = [\n    # slot 1\n]\n```", "No ```diff changeset blocks"),
        (
            "```python\nPARTS = []\n```\n```diff\n--- a\n+++ b\n@@ -1 +1 @@\n-PARTS = []\n+PARTS = []\n```",
            "does not contain any '# slot N' placeholders",
        ),
        (base_two + "```diff\n@@ -2 +2 @@\n-    # slot 1\n```", "Malformed unified diff header"),
        (base_two + "```diff\n--- a\n+++ b\nnot a hunk\n```", "Invalid unified diff hunk header"),
        (
            base_two + "```diff\n--- a\n+++ b\n@@ -99 +99 @@\n-    # slot 1\n+    'a',\n```",
            "out of bounds or out of order",
        ),
        (base_two + "```diff\n--- a\n+++ b\n@@ -2 +2 @@\n\n+    'a',\n```", "Empty line without diff prefix"),
        (
            base_two + "```diff\n--- a\n+++ b\n@@ -1,2 +1,2 @@\n WRONG = [\n-    # slot 1\n+    'a',\n```",
            "context line does not match",
        ),
        (
            base_two + "```diff\n--- a\n+++ b\n@@ -2 +2 @@\n-    # slot 999\n+    'a',\n```",
            "removal line does not match",
        ),
        (base_two + "```diff\n--- a\n+++ b\n@@ -2 +2 @@\n!bad\n```", "Unsupported unified diff line prefix"),
        (
            base_two + "```diff\n--- a\n+++ b\n@@ -2,3 +2,1 @@\n-    # slot 1\n+    'a',\n```",
            "line counts do not match hunk header",
        ),
        (
            one_slot + "```diff\n--- a\n+++ b\n@@ -2 +2 @@\n-    # slot 1\n+    'unclosed\n```",
            "not valid Python syntax",
        ),
        (
            one_slot + "```diff\n--- a\n+++ b\n@@ -1,3 +1,1 @@\n-PARTS = [\n-    # slot 1\n-]\n+pass\n```",
            "single PARTS assignment",
        ),
        (
            one_slot + "```diff\n--- a\n+++ b\n@@ -1,2 +1,2 @@\n-PARTS = [\n-    # slot 1\n+OTHER = [\n+    'a',\n```",
            "assignment target must be 'PARTS'",
        ),
        (
            one_slot + "```diff\n--- a\n+++ b\n@@ -1,3 +1,1 @@\n-PARTS = [\n-    # slot 1\n-]\n+PARTS = 'abc'\n```",
            "PARTS value must be a list literal",
        ),
        (
            one_slot + "```diff\n--- a\n+++ b\n@@ -2 +2 @@\n-    # slot 1\n+    123,\n```",
            "Every element in PARTS must be a string literal",
        ),
    ]
    for payload, expected_msg in invalid_cases:
        with pytest.raises(ValueError, match=expected_msg):
            DiffChainConverter.decode(payload)

    encoded_two = (
        await DiffChainConverter(steps=2, shuffle_order=False, include_instructions=False).convert_async(prompt="ab")
    ).output_text
    with pytest.raises(ValueError, match="Incomplete diff chain: expected 2 patched slot\\(s\\), got 1"):
        DiffChainConverter.decode(encoded_two.split("\n\nChangeSet 2:")[0])


@pytest.mark.parametrize("use_path", [False, True])
def test_diff_chain_converter_registry_discovery_and_external_template_guard(tmp_path: Path, *, use_path: bool) -> None:
    custom_tpl = _write_custom_template(
        tmp_path / "registry_tpl.yaml",
        "```python\n{{ base_file }}\n```\n{{ changesets }}",
    )

    registry = ConverterRegistry()
    enc_meta = registry.get_registered_class_metadata("DiffChainConverter")
    dec_meta = registry.get_registered_class_metadata("DiffChainDecodeConverter")
    assert enc_meta is not None and dec_meta is not None

    tpl_param = next(param for param in enc_meta.parameters if param.name == "template")
    assert tpl_param.is_external_input and tpl_param.is_path_or_str

    params: dict[str, object] = {"steps": 3, "template": custom_tpl if use_path else str(custom_tpl)}
    with pytest.raises(ValueError, match="names a server file"):
        registry.create_instance_from_external_input("DiffChainConverter", params=params)

    converter = registry.create_instance_from_external_input(
        "DiffChainConverter",
        params=params,
        allowed_path_params=frozenset({"template"}),
    )
    assert isinstance(converter, DiffChainConverter)
    assert isinstance(
        registry.create_instance_from_external_input("DiffChainDecodeConverter", params={}), DiffChainDecodeConverter
    )
