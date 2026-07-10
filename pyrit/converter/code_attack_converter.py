# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import pathlib
import re
from enum import Enum
from typing import TYPE_CHECKING

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType, SeedPrompt

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier


class CodeAttackConverter(Converter):
    """
    Encodes a prompt as a code-completion task (CodeAttack, Ren et al. ACL 2024).

    The prompt is encoded word-by-word into a data-structure initialisation
    sequence embedded inside a partial code template. The model is asked to
    complete the code, which sidesteps natural-language safety training.

    **Separator normalisation (python_stack only):** tokens are split on
    ``[\\s\\-]+``, so hyphens and runs of whitespace are treated as delimiters
    and do not survive the encode/decode cycle. Byte-identical round-trips are
    only guaranteed for inputs whose words are separated by a single space with
    no hyphens. ``python_list`` uses ``str.split()`` and preserves token
    boundaries without consuming hyphens.

    CodeAttack [@ren2024codeattack].
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

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
    ) -> None:
        """
        Args:
            template: The encoding template to use. Pass a
                ``CodeAttackConverter.Template`` member to use one of the
                built-in templates, or a ``pathlib.Path`` to a custom YAML
                file. When a custom path is supplied the encoder defaults to
                the ``python_string`` structure because the language cannot be
                inferred from the path.

        Raises:
            TypeError: If ``template`` is not a ``CodeAttackConverter.Template``
                or a ``pathlib.Path``.
        """
        if isinstance(template, CodeAttackConverter.Template):
            self._template_path = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / f"{template.value}.yaml"
            self._language = _TEMPLATE_LANGUAGE[template]
        elif isinstance(template, pathlib.Path):
            # Custom template supplied by the caller. Encoder defaults to the
            # python_string structure since the language cannot be inferred.
            self._template_path = template
            self._language = "python_string"
        else:
            raise TypeError("template must be a CodeAttackConverter.Template or a pathlib.Path.")

    def _build_identifier(self) -> "ComponentIdentifier":
        return self._create_identifier(
            params={"template": str(self._template_path)},
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
        seed_prompt = SeedPrompt.from_yaml_file(self._template_path)
        formatted = seed_prompt.render_template_value(wrapped_input=wrapped_input)
        return ConverterResult(output_text=formatted, output_type="text")

    # ------------------------------------------------------------------
    # Internal encoding helpers
    # ------------------------------------------------------------------

    def _encode(self, prompt: str) -> str:
        """
        Dispatch to the appropriate encoding method for the selected language.

        Returns:
            The encoded wrapped_input string ready for template injection.

        Raises:
            ValueError: If ``self._language`` is not a recognised value (guard
                against future inconsistency).
        """
        match self._language:
            case "python_stack":
                return self._encode_python_stack(prompt)
            case "python_list":
                return self._encode_python_list(prompt)
            case "python_string":
                return self._encode_python_string(prompt)
            case "cpp":
                return self._encode_cpp(prompt)
            case "go":
                return self._encode_go(prompt)
            case _:
                raise ValueError(f"Unsupported language: {self._language!r}")

    def _encode_python_stack(self, prompt: str) -> str:
        words = re.split(r"[\s\-]+", prompt)
        if len(words) == 1:
            words = list(words[0])
        words = words[::-1]
        return "\n".join(f"    my_stack.append({json.dumps(word)})" for word in words)

    def _encode_python_list(self, prompt: str) -> str:
        words = prompt.split()
        return "\n".join(f"    my_list.append({json.dumps(word)})" for word in words)

    def _encode_python_string(self, prompt: str) -> str:
        return f"    my_string = {json.dumps(prompt)}"

    def _encode_cpp(self, prompt: str) -> str:
        return f"    std::string my_string = {json.dumps(prompt)};"

    def _encode_go(self, prompt: str) -> str:
        return f"        myQueue := {json.dumps(prompt)}"


# Maps each built-in Template to its encoding language.
# Defined after the class so the Template enum members are in scope.
_TEMPLATE_LANGUAGE: dict[CodeAttackConverter.Template, str] = {
    CodeAttackConverter.Template.PYTHON_STACK: "python_stack",
    CodeAttackConverter.Template.PYTHON_STACK_VERBOSE: "python_stack",
    CodeAttackConverter.Template.PYTHON_LIST: "python_list",
    CodeAttackConverter.Template.PYTHON_LIST_VERBOSE: "python_list",
    CodeAttackConverter.Template.PYTHON_STRING: "python_string",
    CodeAttackConverter.Template.PYTHON_STRING_VERBOSE: "python_string",
    CodeAttackConverter.Template.CPP: "cpp",
    CodeAttackConverter.Template.GO: "go",
}
