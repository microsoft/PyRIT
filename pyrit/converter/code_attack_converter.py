# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import pathlib
import re
from enum import Enum
from typing import TYPE_CHECKING

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType, SeedPrompt

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier

# Template parameter that receives the encoded objective.
_WRAPPED_INPUT = "wrapped_input"


class CodeAttackConverter(Converter):
    """
    Encodes a prompt as a code-completion task (CodeAttack, Ren et al. ACL 2024).

    The prompt is encoded into a data-structure initialisation sequence embedded
    inside a partial code template. The model is asked to complete the code,
    which sidesteps natural-language safety training.

    **Separator normalisation.** How much of the input survives the encode step
    depends on the encoding, because each one splits the prompt differently:

    - ``PYTHON_STRING``, ``CPP`` and ``GO`` embed the prompt as a single string
      literal. Every character survives, including whitespace runs, hyphens,
      tabs, newlines and non-BMP characters such as emoji. These round-trip
      byte-identically.
    - ``PYTHON_LIST`` splits on ``str.split()``, so *any* run of whitespace
      (spaces, tabs, newlines) collapses to a single token boundary and leading
      and trailing whitespace is dropped. Hyphens are preserved inside tokens.
      Round-trips losslessly only when words are separated by single spaces.
    - ``PYTHON_STACK`` splits on ``[\\s\\-]+``, so it collapses whitespace runs
      exactly like ``PYTHON_LIST`` *and additionally* consumes hyphens as
      delimiters. It also reverses token order (the template's ``decode()``
      pops the stack). A prompt that yields a single token is exploded into
      individual characters. Round-trips losslessly only when words are
      separated by single spaces and contain no hyphens.

    In every case the encoded literals are escaped for the target language, so
    quotes, backslashes and control characters cannot break out of the literal.

    **Template and encoding pairing.** Each built-in ``Template`` ships a wrapper
    that only works with one ``Encoding``, so the enum implies the encoding and
    passing ``encoding=`` alongside a built-in is rejected. A ``pathlib.Path``
    demands an explicit ``encoding=``, because the data structure cannot be
    inferred from a custom file. In short: enum implies, Path demands.

    CodeAttack [@ren2024codeattack].
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    class Encoding(Enum):
        """
        The data structure the objective is encoded into, and the language whose
        string-literal escaping rules apply.

        Only supply this alongside a custom ``pathlib.Path`` template, where it is
        required. A built-in ``Template`` already implies its encoding and rejects
        this parameter, because pairing a built-in wrapper with a different data
        structure would populate one structure while the wrapper decodes another.
        """

        PYTHON_STACK = "python_stack"
        PYTHON_LIST = "python_list"
        PYTHON_STRING = "python_string"
        CPP = "cpp"
        GO = "go"

    class Template(Enum):
        """
        Built-in CodeAttack templates. The *_VERBOSE members use the _plus
        variant (detailed paragraphs); the non-verbose members request numbered
        steps. cpp and go have no verbose variant in the reference implementation.
        """

        PYTHON_STACK = "code_attack_python_stack"
        PYTHON_STACK_VERBOSE = "code_attack_python_stack_plus"
        PYTHON_LIST = "code_attack_python_list"
        PYTHON_LIST_VERBOSE = "code_attack_python_list_plus"
        PYTHON_STRING = "code_attack_python_string"
        PYTHON_STRING_VERBOSE = "code_attack_python_string_plus"
        CPP = "code_attack_cpp"
        GO = "code_attack_go"

    def __init__(
        self,
        *,
        template: "CodeAttackConverter.Template | pathlib.Path" = Template.PYTHON_STACK_VERBOSE,
        encoding: "CodeAttackConverter.Encoding | None" = None,
    ) -> None:
        """
        Args:
            template: The code template to render. Pass a
                ``CodeAttackConverter.Template`` member to use one of the
                built-in templates, or a ``pathlib.Path`` to a custom YAML file.
            encoding: The data structure the objective is encoded into. A
                built-in ``Template`` implies its encoding, so passing this
                alongside one is rejected: each built-in ships a wrapper that
                only works with its mapped encoding, and pairing it with another
                would declare one data structure, populate a different one, and
                decode from the empty original. A ``pathlib.Path`` demands it,
                because the data structure cannot be inferred from a custom file.

        Raises:
            TypeError: If ``template`` is not a ``CodeAttackConverter.Template``
                or a ``pathlib.Path``, or if ``encoding`` is not a
                ``CodeAttackConverter.Encoding``.
            ValueError: If ``encoding`` is passed alongside a built-in
                ``Template``, if ``template`` is a ``pathlib.Path`` and
                ``encoding`` is not supplied, if the template file is malformed,
                if the template does not reference ``wrapped_input``, or if it
                references any other template variable.
            FileNotFoundError: If the template file does not exist.
        """
        if encoding is not None and not isinstance(encoding, CodeAttackConverter.Encoding):
            raise TypeError("encoding must be a CodeAttackConverter.Encoding.")

        if isinstance(template, CodeAttackConverter.Template):
            mapped_encoding = _TEMPLATE_ENCODING[template]
            if encoding is not None:
                raise ValueError(
                    f"encoding must not be passed with the built-in template Template.{template.name}, "
                    f"which ships a wrapper that only works with Encoding.{mapped_encoding.name}; got "
                    f"Encoding.{encoding.name}. Built-in templates imply their encoding. To use a "
                    "different data structure, pass a pathlib.Path to a matching custom template "
                    "together with encoding=."
                )
            self._template_path = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / f"{template.value}.yaml"
            self._template_name: str = template.name
            resolved_encoding = mapped_encoding
        elif isinstance(template, pathlib.Path):
            if encoding is None:
                valid = ", ".join(f"Encoding.{member.name}" for member in CodeAttackConverter.Encoding)
                raise ValueError(
                    "encoding is required when template is a pathlib.Path, because the data "
                    f"structure cannot be inferred from a custom file. Pass one of: {valid}."
                )
            self._template_path = template
            self._template_name = f"custom:{encoding.value}"
            resolved_encoding = encoding
        else:
            raise TypeError("template must be a CodeAttackConverter.Template or a pathlib.Path.")

        self._encoding = resolved_encoding

        # Load and validate the template once, so a broken custom file fails at
        # construction rather than on the first conversion.
        self._seed_prompt = SeedPrompt.from_yaml_file(self._template_path)
        self._validate_template(self._seed_prompt, self._template_path)

    @staticmethod
    def _validate_template(seed_prompt: SeedPrompt, path: pathlib.Path) -> None:
        """
        Ensure the template injects the encoded objective and nothing else.

        A template whose value never references ``wrapped_input`` renders to a
        constant string and silently discards the objective. A template that
        references any other variable cannot render at all, because
        ``wrapped_input`` is the only value supplied at conversion time; without
        this check that failure surfaces on the first conversion instead of at
        construction.

        Args:
            seed_prompt: The loaded template.
            path: Path the template was loaded from, used in the error message.

        Raises:
            ValueError: If the template does not reference ``wrapped_input``, or
                references any variable other than ``wrapped_input``.
        """
        environment = SandboxedEnvironment()
        referenced = meta.find_undeclared_variables(environment.parse(seed_prompt.value))
        if _WRAPPED_INPUT not in referenced:
            raise ValueError(
                f"CodeAttack template {path} does not reference the '{_WRAPPED_INPUT}' parameter, "
                "so the objective would be silently discarded. Add "
                f"'{{{{ {_WRAPPED_INPUT} }}}}' to the template value."
            )

        unsupported = sorted(referenced - {_WRAPPED_INPUT})
        if unsupported:
            raise ValueError(
                f"CodeAttack template {path} references unsupported template "
                f"variable(s): {', '.join(unsupported)}. '{_WRAPPED_INPUT}' is the only value "
                "supplied at conversion time, so rendering would fail. Remove them or replace "
                "them with literal text."
            )

    def _build_identifier(self) -> "ComponentIdentifier":
        """
        Build identifier from the template contents rather than its location.

        Hashing the loaded template value keeps the identifier stable when the
        same template is read from a different path, and changes it when a
        custom template's contents change.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        template_hash = hashlib.sha256(str(self._seed_prompt.value).encode("utf-8")).hexdigest()[:16]
        return self._create_identifier(
            params={
                "template": self._template_name,
                "template_hash": template_hash,
                "encoding": self._encoding.value,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Encode the prompt into a code-completion template.

        Args:
            prompt: The natural-language prompt to encode.
            input_type: Must be ``"text"``.

        Returns:
            ConverterResult containing the code-completion template with the
            prompt encoded as data-structure initialisation calls.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        wrapped_input = self._encode(prompt)
        formatted = self._seed_prompt.render_template_value(wrapped_input=wrapped_input)
        return ConverterResult(output_text=formatted, output_type="text")

    # ------------------------------------------------------------------
    # Internal encoding helpers
    # ------------------------------------------------------------------

    def _encode(self, prompt: str) -> str:
        """
        Dispatch to the appropriate encoding method for the selected encoding.

        Returns:
            The encoded wrapped_input string ready for template injection.

        Raises:
            ValueError: If ``self._encoding`` is not a recognised value (guard
                against future inconsistency).
        """
        match self._encoding:
            case CodeAttackConverter.Encoding.PYTHON_STACK:
                return self._encode_python_stack(prompt)
            case CodeAttackConverter.Encoding.PYTHON_LIST:
                return self._encode_python_list(prompt)
            case CodeAttackConverter.Encoding.PYTHON_STRING:
                return self._encode_python_string(prompt)
            case CodeAttackConverter.Encoding.CPP:
                return self._encode_cpp(prompt)
            case CodeAttackConverter.Encoding.GO:
                return self._encode_go(prompt)
            case _:
                raise ValueError(f"Unsupported encoding: {self._encoding!r}")

    def _encode_python_stack(self, prompt: str) -> str:
        words = re.split(r"[\s\-]+", prompt)
        if len(words) == 1:
            words = list(words[0])
        words = words[::-1]
        literals = (_escape_string_literal(word, CodeAttackConverter.Encoding.PYTHON_STACK) for word in words)
        return "\n".join(f"    my_stack.append({literal})" for literal in literals)

    def _encode_python_list(self, prompt: str) -> str:
        literals = (_escape_string_literal(word, CodeAttackConverter.Encoding.PYTHON_LIST) for word in prompt.split())
        return "\n".join(f"    my_list.append({literal})" for literal in literals)

    def _encode_python_string(self, prompt: str) -> str:
        return f"    my_string = {_escape_string_literal(prompt, CodeAttackConverter.Encoding.PYTHON_STRING)}"

    def _encode_cpp(self, prompt: str) -> str:
        return f"    std::string my_string = {_escape_string_literal(prompt, CodeAttackConverter.Encoding.CPP)};"

    def _encode_go(self, prompt: str) -> str:
        return f"        myQueue := {_escape_string_literal(prompt, CodeAttackConverter.Encoding.GO)}"


def _escape_string_literal(value: str, encoding: CodeAttackConverter.Encoding) -> str:
    """
    Render ``value`` as a double-quoted string literal valid in the target language.

    Non-ASCII characters, including non-BMP characters such as emoji, are emitted
    as literal UTF-8 rather than escaped. Python, Go and C++ source is UTF-8, so
    the character survives intact. This avoids ``json.dumps``' default
    ``ensure_ascii=True`` behaviour, which encodes non-BMP characters as a
    surrogate pair (``\\ud83d\\ude00``); Go and C++ reject surrogate escapes and
    Python evaluates them to two lone surrogates rather than the original
    character.

    Backslash, double quote and control characters are escaped. Control
    characters without a short escape use a hex escape, except in C++ where hex
    escapes consume an unbounded run of hex digits and would swallow a following
    literal digit; C++ uses a three-digit octal escape instead, which is capped
    by the language.

    Args:
        value: The raw text to embed.
        encoding: Selects the target language's escaping rules.

    Returns:
        str: The quoted literal, including the surrounding double quotes.
    """
    pieces: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "\\":
            pieces.append("\\\\")
        elif character == '"':
            pieces.append('\\"')
        elif character == "\n":
            pieces.append("\\n")
        elif character == "\r":
            pieces.append("\\r")
        elif character == "\t":
            pieces.append("\\t")
        elif codepoint < 0x20 or codepoint == 0x7F:
            if encoding is CodeAttackConverter.Encoding.CPP:
                pieces.append(f"\\{codepoint:03o}")
            else:
                pieces.append(f"\\x{codepoint:02x}")
        else:
            pieces.append(character)
    return '"' + "".join(pieces) + '"'


# Maps each built-in Template to the encoding it expects.
# Defined after the class so the Template and Encoding members are in scope.
_TEMPLATE_ENCODING: dict[CodeAttackConverter.Template, CodeAttackConverter.Encoding] = {
    CodeAttackConverter.Template.PYTHON_STACK: CodeAttackConverter.Encoding.PYTHON_STACK,
    CodeAttackConverter.Template.PYTHON_STACK_VERBOSE: CodeAttackConverter.Encoding.PYTHON_STACK,
    CodeAttackConverter.Template.PYTHON_LIST: CodeAttackConverter.Encoding.PYTHON_LIST,
    CodeAttackConverter.Template.PYTHON_LIST_VERBOSE: CodeAttackConverter.Encoding.PYTHON_LIST,
    CodeAttackConverter.Template.PYTHON_STRING: CodeAttackConverter.Encoding.PYTHON_STRING,
    CodeAttackConverter.Template.PYTHON_STRING_VERBOSE: CodeAttackConverter.Encoding.PYTHON_STRING,
    CodeAttackConverter.Template.CPP: CodeAttackConverter.Encoding.CPP,
    CodeAttackConverter.Template.GO: CodeAttackConverter.Encoding.GO,
}
