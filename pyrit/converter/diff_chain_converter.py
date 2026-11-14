# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import SeedPrompt

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, PromptDataType

_DEFAULT_TEMPLATE_NAME = "diff_chain"
_REQUIRED_TEMPLATE_VARS = frozenset({"base_file", "changesets"})
_ALLOWED_TEMPLATE_VARS = frozenset({"base_file", "changesets", "steps", "filename"})
_FILENAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
_PYTHON_FENCE_RE = re.compile(r"^```python\n(.*?)\n```$", re.MULTILINE | re.DOTALL)
_DIFF_FENCE_RE = re.compile(r"^```diff\n(.*?)\n```$", re.MULTILINE | re.DOTALL)
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@$")
_SLOT_COMMENT_RE = re.compile(r"^    # slot (\d+)$")


def _partition_prompt(*, prompt: str, steps: int) -> list[str]:
    if len(prompt) < steps:
        return list(prompt) + [""] * (steps - len(prompt))
    tokens = re.findall(r"\S+\s*|\s+", prompt)
    units: list[str] | str = tokens if len(tokens) >= steps else prompt
    total = len(units)
    return ["".join(units[(i * total) // steps : ((i + 1) * total) // steps]) for i in range(steps)]


def _encode_chunk_literal(chunk: str) -> str:
    # Escape triple backticks (Markdown fences) and Unicode line separators (str.splitlines()).
    return (
        json.dumps(chunk, ensure_ascii=False)
        .replace("```", "``\\u0060")
        .replace("\x85", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _apply_unified_diff(*, lines: list[str], diff_text: str) -> list[str]:
    raw_lines = diff_text.splitlines()
    if len(raw_lines) < 3 or not raw_lines[0].startswith("--- ") or not raw_lines[1].startswith("+++ "):
        raise ValueError("Malformed unified diff header in changeset")

    patched: list[str] = []
    src_idx = 0
    idx = 2

    while idx < len(raw_lines):
        match = _HUNK_HEADER_RE.fullmatch(raw_lines[idx])
        if match is None:
            raise ValueError(f"Invalid unified diff hunk header: {raw_lines[idx]!r}")

        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        target_idx = old_start - 1 if old_count > 0 else old_start

        if target_idx < src_idx or target_idx > len(lines):
            raise ValueError("Unified diff hunk line range is out of bounds or out of order")

        patched.extend(lines[src_idx:target_idx])
        src_idx = target_idx
        idx += 1

        consumed_old = 0
        produced_new = 0
        while idx < len(raw_lines) and not raw_lines[idx].startswith("@@ "):
            hunk_line = raw_lines[idx]
            if not hunk_line:
                raise ValueError("Empty line without diff prefix inside unified diff hunk")
            prefix, content = hunk_line[0], hunk_line[1:]
            if prefix in (" ", "-"):
                if src_idx >= len(lines) or lines[src_idx] != content:
                    kind = "context" if prefix == " " else "removal"
                    raise ValueError(f"Unified diff {kind} line does not match current file state")
                src_idx += 1
                consumed_old += 1
                if prefix == " ":
                    patched.append(content)
                    produced_new += 1
            elif prefix == "+":
                patched.append(content)
                produced_new += 1
            else:
                raise ValueError(f"Unsupported unified diff line prefix: {prefix!r}")
            idx += 1

        if consumed_old != old_count or produced_new != new_count:
            raise ValueError("Unified diff hunk line counts do not match hunk header")

    patched.extend(lines[src_idx:])
    return patched


def _extract_parts_from_source(*, source: str, expected_slots: int) -> str:
    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ValueError("Patched diff chain output is not valid Python syntax") from exc

    if len(module.body) != 1 or not isinstance(module.body[0], ast.Assign):
        raise ValueError("Patched diff chain output must contain a single PARTS assignment")

    assign = module.body[0]
    if len(assign.targets) != 1 or not isinstance(assign.targets[0], ast.Name) or assign.targets[0].id != "PARTS":
        raise ValueError("Patched diff chain assignment target must be 'PARTS'")

    if not isinstance(assign.value, ast.List):
        raise ValueError("Patched PARTS value must be a list literal")

    if len(assign.value.elts) != expected_slots:
        raise ValueError(
            f"Incomplete diff chain: expected {expected_slots} patched slot(s), got {len(assign.value.elts)}"
        )

    decoded_chunks: list[str] = []
    for element in assign.value.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            raise ValueError("Every element in PARTS must be a string literal")
        decoded_chunks.append(element.value)

    return "".join(decoded_chunks)


class DiffChainConverter(Converter):
    """
    Encode a text prompt as an initial placeholder file and sequential unified diff changesets.

    Instead of sending the assembled prompt as a contiguous string, ``DiffChainConverter``
    creates an initial ``PARTS = [...]`` Python file with numbered placeholder slots and
    emits ``steps`` unified diff patches (``v0`` through ``vN``) that populate one slot
    per changeset. When ``shuffle_order=True`` (the default) and ``steps >= 2``, slots are
    patched in a deterministic pseudo-random non-sequential order so linear top-to-bottom
    reading does not match the reconstructed order until diff line numbers are applied.
    """

    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    def __init__(
        self,
        *,
        steps: int = 4,
        context_lines: int = 0,
        shuffle_order: bool = True,
        filename: str = "spec.py",
        include_instructions: bool = True,
        template: str | Path | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Initialize the diff-chain converter.

        Args:
            steps (int): Number of sequential unified diff changesets to emit (``>= 1``).
                Defaults to 4.
            context_lines (int): Number of unchanged context lines around each unified diff
                hunk (``>= 0``). Defaults to 0.
            shuffle_order (bool): Whether to patch slots in a seeded non-sequential order
                when ``steps >= 2``. Defaults to True.
            filename (str): Virtual filename used in unified diff headers (for example,
                ``v0/spec.py``). Defaults to ``"spec.py"``.
            include_instructions (bool): When True, wraps the initial file and changesets in
                the reconstruction prompt template. Defaults to True.
            template (str | Path | None): Optional path to a custom ``SeedPrompt`` YAML
                template referencing ``base_file`` and ``changesets`` (and optionally
                ``steps`` and ``filename``). Cannot be combined with
                ``include_instructions=False``. Defaults to None.
            seed (int | None): Optional integer seed for reproducible slot permutation when
                ``shuffle_order=True``. Defaults to None.

        Raises:
            TypeError: If ``shuffle_order``, ``include_instructions``, or ``template`` has an
                invalid type.
            ValueError: If ``steps < 1``, ``context_lines < 0``, ``filename`` is invalid,
                ``seed`` is invalid, ``template`` is passed with ``include_instructions=False``,
                or the YAML template fails variable validation.
            FileNotFoundError: If a custom ``template`` path does not exist.
        """
        super().__init__()
        if type(steps) is not int or steps < 1:
            raise ValueError("steps must be an integer greater than or equal to 1")
        if type(context_lines) is not int or context_lines < 0:
            raise ValueError("context_lines must be a non-negative integer")
        if not isinstance(shuffle_order, bool):
            raise TypeError("shuffle_order must be a bool")
        if not isinstance(include_instructions, bool):
            raise TypeError("include_instructions must be a bool")
        if not isinstance(filename, str) or not _FILENAME_RE.fullmatch(filename) or filename in {".", ".."}:
            raise ValueError(
                "filename must be a non-empty single path segment containing only letters, digits, '_', '.', or '-'"
            )
        if seed is not None and type(seed) is not int:
            raise ValueError("seed must be an integer or None")

        self._steps = steps
        self._context_lines = context_lines
        self._shuffle_order = shuffle_order
        self._filename = filename
        self._include_instructions = include_instructions
        self._seed = seed

        self._seed_prompt: SeedPrompt | None = None
        self._template_name: str | None = None
        if template is not None:
            if not include_instructions:
                raise ValueError("template cannot be provided when include_instructions is False")
            if not isinstance(template, (str, Path)):
                raise TypeError("template must be a str, pathlib.Path, or None")
            raw_template_str = str(template).strip()
            if not raw_template_str or raw_template_str in {".", ".."}:
                raise ValueError("template must be a valid file path")
            template_path = Path(raw_template_str)
            self._template_name = f"custom:{template_path.name}"
            self._seed_prompt = SeedPrompt.from_yaml_file(template_path)
            self._validate_template(seed_prompt=self._seed_prompt, path=template_path)
        elif include_instructions:
            default_path = Path(CONVERTER_SEED_PROMPT_PATH) / f"{_DEFAULT_TEMPLATE_NAME}.yaml"
            self._template_name = _DEFAULT_TEMPLATE_NAME
            self._seed_prompt = SeedPrompt.from_yaml_file(default_path)
            self._validate_template(seed_prompt=self._seed_prompt, path=default_path)

    @staticmethod
    def _validate_template(*, seed_prompt: SeedPrompt, path: Path) -> None:
        referenced = meta.find_undeclared_variables(SandboxedEnvironment().parse(seed_prompt.value))
        missing = sorted(_REQUIRED_TEMPLATE_VARS - referenced)
        if missing:
            raise ValueError(
                f"DiffChain template {path} is missing required template variable(s): {', '.join(missing)}."
            )
        unsupported = sorted(referenced - _ALLOWED_TEMPLATE_VARS)
        if unsupported:
            raise ValueError(
                f"DiffChain template {path} references unsupported template variable(s): {', '.join(unsupported)}. "
                f"Allowed variables are: {', '.join(sorted(_ALLOWED_TEMPLATE_VARS))}."
            )

    def _build_identifier(self) -> ComponentIdentifier:
        template_hash: str | None = None
        if self._seed_prompt is not None:
            template_hash = hashlib.sha256(str(self._seed_prompt.value).encode("utf-8")).hexdigest()[:16]
        return self._create_identifier(
            params={
                "steps": self._steps,
                "context_lines": self._context_lines,
                "shuffle_order": self._shuffle_order,
                "filename": self._filename,
                "include_instructions": self._include_instructions,
                "template": self._template_name,
                "template_hash": template_hash,
                "seed": self._seed,
            }
        )

    def _select_patch_order(self) -> list[int]:
        sequential = list(range(self._steps))
        if not self._shuffle_order or self._steps < 2:
            return sequential

        rng = self._get_random_generator(stream="patch_order")
        order = list(sequential)
        rng.shuffle(order)
        if order == sequential:
            swap_idx = rng.randrange(1, self._steps)
            order[0], order[swap_idx] = order[swap_idx], order[0]
        return order

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert ``prompt`` into an initial placeholder file and sequential unified diff blocks.

        Args:
            prompt (str): The text prompt to encode.
            input_type (PromptDataType): Input data type. Must be ``"text"``.

        Returns:
            ConverterResult: Converted diff-chain text with ``output_type="text"``.

        Raises:
            ValueError: If ``input_type`` is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        chunks = _partition_prompt(prompt=prompt, steps=self._steps)
        base_lines = ["PARTS = [", *[f"    # slot {idx + 1}" for idx in range(self._steps)], "]"]
        current_lines = list(base_lines)

        changeset_blocks: list[str] = []
        for step_num, slot_idx in enumerate(self._select_patch_order(), start=1):
            next_lines = list(current_lines)
            next_lines[slot_idx + 1] = f"    {_encode_chunk_literal(chunks[slot_idx])},"
            diff_lines = list(
                difflib.unified_diff(
                    current_lines,
                    next_lines,
                    fromfile=f"v{step_num - 1}/{self._filename}",
                    tofile=f"v{step_num}/{self._filename}",
                    n=self._context_lines,
                    lineterm="",
                )
            )
            changeset_blocks.append(f"ChangeSet {step_num}:\n```diff\n" + "\n".join(diff_lines) + "\n```")
            current_lines = next_lines

        base_file = "\n".join(base_lines)
        changesets = "\n\n".join(changeset_blocks)

        if self._include_instructions and self._seed_prompt is not None:
            output_text = self._seed_prompt.render_template_value(
                base_file=base_file,
                changesets=changesets,
                steps=str(self._steps),
                filename=self._filename,
            )
        else:
            output_text = f"Initial file (`v0/{self._filename}`):\n```python\n{base_file}\n```\n\n{changesets}"

        return ConverterResult(output_text=output_text, output_type="text")

    @staticmethod
    def decode(encoded_text: str) -> str:
        """
        Reconstruct the original prompt by applying sequential unified diff blocks to ``v0``.

        Args:
            encoded_text (str): Output previously produced by ``DiffChainConverter``.

        Returns:
            str: Reconstructed original prompt string.

        Raises:
            TypeError: If ``encoded_text`` is not a ``str``.
            ValueError: If ``encoded_text`` is empty, missing fenced blocks, contains malformed
                or mismatched unified diff hunks, or leaves placeholder slots unpatched.
        """
        if not isinstance(encoded_text, str):
            raise TypeError("encoded_text must be a str")
        normalized_text = encoded_text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized_text.strip():
            raise ValueError("encoded_text must be a non-empty string")

        python_matches = _PYTHON_FENCE_RE.findall(normalized_text)
        if not python_matches:
            raise ValueError("No initial ```python fenced block found in encoded_text")

        diff_matches = _DIFF_FENCE_RE.findall(normalized_text)
        if not diff_matches:
            raise ValueError("No ```diff changeset blocks found in encoded_text")

        current_lines = python_matches[0].splitlines()
        expected_slots = sum(1 for line in current_lines if _SLOT_COMMENT_RE.fullmatch(line) is not None)
        if expected_slots < 1:
            raise ValueError("Initial Python block does not contain any '# slot N' placeholders")

        for diff_block in diff_matches:
            current_lines = _apply_unified_diff(lines=current_lines, diff_text=diff_block)

        return _extract_parts_from_source(source="\n".join(current_lines), expected_slots=expected_slots)


class DiffChainDecodeConverter(Converter):
    """Decode a diff-chain prompt back into its reconstructed plaintext string (``text -> text``)."""

    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    def __init__(self) -> None:
        """Initialize the diff-chain decoder converter."""
        super().__init__()

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Decode a diff-chain formatted text prompt back into its original text.

        Args:
            prompt (str): Diff-chain encoded prompt text.
            input_type (PromptDataType): Input data type. Must be ``"text"``.

        Returns:
            ConverterResult: Reconstructed plaintext prompt with ``output_type="text"``.

        Raises:
            ValueError: If ``input_type`` is not supported or ``prompt`` is not valid diff-chain output.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        return ConverterResult(output_text=DiffChainConverter.decode(prompt), output_type="text")
