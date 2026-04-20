# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import inspect
import json
import pathlib
import re
import textwrap
from typing import Any, Callable, Optional

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import PromptDataType, SeedPrompt
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class CodeChameleonConverter(PromptConverter):
    """
    Encrypts user prompt, adds stringified decrypt function in markdown and instructions.

    The user prompt is encrypted, and the target is asked to solve the encrypted problem by completing
    a ProblemSolver class utilizing the decryption function while following the instructions.

    Supports the following encryption types:
        - `custom`:
            User provided encryption and decryption functions. Encryption function used to encode prompt.
            Markdown formatting and plaintext instructions appended to decryption function, used as text only.
            Should include imports.
        - `reverse`:
            Reverse the prompt. "How to cut down a tree?" becomes "tree? a down cut to How".
        - `binary_tree`:
            Encode prompt using binary tree. "How to cut down a tree"?" becomes
            ``{'value': 'cut', 'left': {'value': 'How', 'left': None,
            'right': {'value': 'to', 'left': None, 'right': None}},
            'right': {'value': 'a', 'left': {'value': 'down', 'left': None, 'right': None},
            'right': {'value': 'tree?', 'left': None, 'right': None}}}``
        - `odd_even`:
            All words in odd indices of prompt followed by all words in even indices.
            "How to cut down a tree?" becomes "How cut a to down tree?".
        - `length`:
            List of words in prompt sorted by length, use word as key, original index as value.
            "How to cut down a tree?" becomes
            ``[{'a': 4}, {'to': 1}, {'How': 0}, {'cut': 2}, {'down': 3}, {'tree?': 5}]``

    Code Chameleon Converter based on https://arxiv.org/abs/2402.16717 by Lv, Huijie, et al.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        encrypt_type: str,
        encrypt_function: Optional[Callable[..., Any]] = None,
        decrypt_function: Optional[Callable[..., Any] | list[Callable[..., Any] | str]] = None,
    ) -> None:
        """
        Initialize the converter with the specified encryption type and optional functions.

        Args:
            encrypt_type (str): Must be one of "custom", "reverse", "binary_tree", "odd_even" or "length".
            encrypt_function (Callable, optional): User provided encryption function.
                Only used if `encrypt_mode` is "custom". Used to encode user prompt.
            decrypt_function (Callable or list, optional): User provided encryption function.
                Only used if `encrypt_mode` is "custom".
                Used as part of markdown code block instructions in system prompt.
                If list is provided, strings will be treated as single statements for imports or comments.
                Functions will take the source code of the function.

        Raises:
            ValueError: If ``encrypt_type`` is not valid or if ``encrypt_function`` or ``decrypt_function`` are not
                provided when ``encrypt_type`` is "custom".
        """
        match encrypt_type:
            case "custom":
                if encrypt_function is None or decrypt_function is None:
                    raise ValueError("Encryption and decryption functions not provided for custom encrypt_type.")
                self.encrypt_function = encrypt_function
                if isinstance(decrypt_function, list):
                    self.decrypt_function = self._stringify_decrypt(decrypt_function)
                else:
                    self.decrypt_function = self._stringify_decrypt([decrypt_function])
            case "reverse":
                self.encrypt_function = self._encrypt_reverse
                self.decrypt_function = self._decrypt_reverse
            case "binary_tree":
                self.encrypt_function = self._encrypt_binary_tree
                self.decrypt_function = self._decrypt_binary_tree
            case "odd_even":
                self.encrypt_function = self._encrypt_odd_even
                self.decrypt_function = self._decrypt_odd_even
            case "length":
                self.encrypt_function = self._encrypt_length
                self.decrypt_function = self._decrypt_length
            case _:
                raise ValueError(
                    'Encryption type not valid! Must be one of "custom", '
                    '"reverse", "binary_tree", "odd_even" or "length".'
                )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by applying the specified encryption function.

        Args:
            prompt (str): The input prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if self.encrypt_function:
            encoded_prompt = str(self.encrypt_function(prompt))
        else:
            encoded_prompt = prompt

        seed_prompt = SeedPrompt.from_yaml_file(
            pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "codechameleon_converter.yaml"
        )

        formatted_prompt = seed_prompt.render_template_value(
            encoded_prompt=encoded_prompt, decrypt_function=self.decrypt_function
        )

        return ConverterResult(output_text=formatted_prompt, output_type="text")

    def _stringify_decrypt(self, elements: list[Callable[..., Any] | str]) -> str:
        output_text = "The following is the decryption function.\n```python\n"
        for element in elements:
            if inspect.isfunction(element):
                output_text += textwrap.dedent(str(inspect.getsource(element))) + "\n"
            elif isinstance(element, str):
                output_text += str(element) + "\n"
        output_text += "```\n"
        illegal_chars = re.findall(r"\\\d+", output_text)  # If using regex, use named groups
        if len(illegal_chars) > 0:
            # Prevent 'invalid group reference' errors during convert when applying .yaml template with re.sub
            raise ValueError(f"Illegal character sequence in user decrypt function! Invalid chars: {illegal_chars}")
        return output_text

    def _encrypt_binary_tree(self, sentence: str) -> str:
        class TreeNode:
            """A node in the binary tree."""

            def __init__(self, value: str) -> None:
                self.value = value
                self.left: Optional[TreeNode] = None
                self.right: Optional[TreeNode] = None

        def build_tree(words: list[str], start: int, end: int) -> Optional[TreeNode]:
            """
            Recursively build a balanced binary tree from a sublist of words.

            Args:
                words (list): List of words to build the tree from.
                start (int): Starting index (inclusive) of the current sublist.
                end (int): Ending index (inclusive) of the current sublist.

            Returns:
                TreeNode or None: The root node of the subtree, or None if start > end.
            """
            if start > end:
                return None

            mid = (start + end) // 2
            node = TreeNode(words[mid])

            node.left = build_tree(words, start, mid - 1)
            node.right = build_tree(words, mid + 1, end)

            return node

        def tree_to_json(node: Optional[TreeNode]) -> Optional[dict[str, Any]]:
            """
            Convert a tree to a JSON representation.

            Args:
                node (TreeNode): The root node of the tree.

            Returns:
                dict or None: JSON representation of the tree, or None if the node is None.
            """
            if node is None:
                return None
            return {"value": node.value, "left": tree_to_json(node.left), "right": tree_to_json(node.right)}

        words = sentence.split()
        root = build_tree(words, 0, len(words) - 1)
        tree_representation = tree_to_json(root)

        return json.dumps(tree_representation)

    def _encrypt_reverse(self, sentence: str) -> str:
        reverse_sentence = " ".join(sentence.split(" ")[::-1])
        return reverse_sentence

    def _encrypt_odd_even(self, sentence: str) -> str:
        words = sentence.split()
        odd_words = words[::2]
        even_words = words[1::2]
        encrypted_words = odd_words + even_words
        encrypted_sentence = " ".join(encrypted_words)
        return encrypted_sentence

    def _encrypt_length(self, sentence: str) -> str:
        class WordData:
            def __init__(self, word: str, index: int) -> None:
                self.word = word
                self.index = index

        def to_json(word_data: list[WordData]) -> list[dict[str, int]]:
            word_datas: list[dict[str, int]] = []
            for data in word_data:
                word = data.word
                index = data.index
                word_datas.append({word: index})
            return word_datas

        words = sentence.split()
        word_data_list = [WordData(word, i) for i, word in enumerate(words)]
        word_data_list.sort(key=lambda x: len(x.word))
        import json

        return json.dumps(to_json(word_data_list))

    _decrypt_reverse = textwrap.dedent(
        """
    The following is the decryption function.
    ```python
    def decryption(encrypted_problem):
        # reverse the sentence to get original problem
        original_problem = ' '.join(encrypted_problem.split(' ')[::-1])

        return original_problem
    ```
    """
    )

    _decrypt_binary_tree = textwrap.dedent(
        """
    The following is the decryption function.
    ```python
    import json

    class TreeNode:
        '''A node in the binary tree.'''
        def __init__(self, value):
            self.value = value
            self.left = None
            self.right = None

    def string_to_tree(problem_description):
        '''Converts a JSON string representation of a tree back to a tree data structure.'''
        def helper(node_dict):
            if node_dict is None:
                return None
            node = TreeNode(node_dict['value'])
            node.left = helper(json.loads(node_dict['left'])) if node_dict['left'] else None
            node.right = helper(json.loads(node_dict['right'])) if node_dict['right'] else None
            return node

        return helper(json.loads(problem_description))

    def inorder_traversal(node, words):
        '''Performs inorder traversal of the tree and collects the words.'''
        if node is not None:
            inorder_traversal(node.left, words)
            words.append(node.value)
            inorder_traversal(node.right, words)

    # This is the decryption function
    def decryption(encrypted_problem):
        tree_root = string_to_tree(encrypted_problem)
        words = []
        inorder_traversal(tree_root, words)
        original_problem = ' '.join(words)

        return original_problem
    ```
    """
    )

    _decrypt_odd_even = textwrap.dedent(
        """
    The following is the decryption function.
    ```python
    def decryption(encrypted_problem):
        # split sentence in half
        words = encrypted_problem.split()
        half_length = (len(words) + 1) // 2
        decrypted_words = []

        # Place the first half in an odd numbered position
        # Put the last half in an even number position
        for i in range(half_length):
            decrypted_words.append(words[i])
            if i + half_length < len(words):
                decrypted_words.append(words[i + half_length])

        original_problem = ' '.join(decrypted_words)
        return original_problem
    ```
    """
    )

    _decrypt_length = textwrap.dedent(
        """
    The following is the decryption function.
    ```python
    def decryption(encrypted_problem):
        # Create an empty sentence
        sentence = [''] * len(encrypted_problem)
        # Iterate through the list and place each word at the correct position
        for word_dict in encrypted_problem:
            for word, index in word_dict.items():
                sentence[index] = word
        # Join the list of words to get the original problem
        original_problem = ' '.join(sentence)
        return original_problem
    ```
    """
    )
