# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import fields, is_dataclass
from inspect import isroutine
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, ClassVar

from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential, wait_none

from pyrit.common import REQUIRED_VALUE, apply_defaults
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.exceptions import get_retry_max_num_attempts
from pyrit.exceptions.exception_classes import _DynamicStopAfterAttempt
from pyrit.exceptions.exceptions_helpers import log_exception
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    PromptDataType,
    SeedPrompt,
)
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget

if TYPE_CHECKING:
    from tenacity.stop import stop_base
    from tenacity.wait import wait_base

logger = logging.getLogger(__name__)


def _reproducible_order(items: Iterable[Any]) -> list[Any]:
    """
    Sort set items in an order that does not depend on ``PYTHONHASHSEED``.

    Args:
        items (Iterable[Any]): The items of a set.

    Returns:
        list[Any]: Strings or integers sorted by value, and any other items sorted by their description.
    """
    items = list(items)
    if all(type(item) is str for item in items) or all(type(item) is int for item in items):
        return sorted(items)
    return sorted(items, key=_describe_template_value)


class _ReproducibleOrder:
    """
    Iterate in a reproducible order, and keep that order in the sets that set operations return.

    Mixed into ``set`` and ``frozenset`` subclasses. Their inherited operations would otherwise return
    plain sets, which a template could render in an order that depends on ``PYTHONHASHSEED``.
    """

    __slots__ = ()
    # The built-in set type that the class extends.
    _set_type: ClassVar[Any]

    def _keep_order(self, result: Any) -> Any:
        ordered_type: Any = type(self)
        return result if result is NotImplemented else ordered_type(result)

    def __iter__(self) -> Iterator[Any]:
        return iter(_reproducible_order(self._set_type.__iter__(self)))

    def copy(self) -> Any:
        return self._keep_order(self._set_type.copy(self))

    def union(self, *others: Iterable[Any]) -> Any:
        return self._keep_order(self._set_type.union(self, *others))

    def intersection(self, *others: Iterable[Any]) -> Any:
        return self._keep_order(self._set_type.intersection(self, *others))

    def difference(self, *others: Iterable[Any]) -> Any:
        return self._keep_order(self._set_type.difference(self, *others))

    def symmetric_difference(self, other: Iterable[Any]) -> Any:
        return self._keep_order(self._set_type.symmetric_difference(self, other))

    def __or__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__or__(self, other))

    def __ror__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__ror__(self, other))

    def __and__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__and__(self, other))

    def __rand__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__rand__(self, other))

    def __sub__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__sub__(self, other))

    def __rsub__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__rsub__(self, other))

    def __xor__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__xor__(self, other))

    def __rxor__(self, other: Any) -> Any:
        return self._keep_order(self._set_type.__rxor__(self, other))


class _OrderedSet(_ReproducibleOrder, set):
    """A ``set`` that iterates, and so renders, in a reproducible order."""

    __slots__ = ()
    _set_type = set

    def __repr__(self) -> str:
        return f"{{{', '.join(map(repr, self))}}}" if self else "set()"


class _OrderedFrozenSet(_ReproducibleOrder, frozenset):
    """A ``frozenset`` that iterates, and so renders, in a reproducible order."""

    __slots__ = ()
    _set_type = frozenset

    def __repr__(self) -> str:
        return f"frozenset({{{', '.join(map(repr, self))}}})" if self else "frozenset()"


def _holds_set(value: Any, *, seen: set[int]) -> bool:
    """
    Check whether a value holds a set or frozenset, directly or through lists, tuples and dictionaries.

    Args:
        value (Any): The template parameter value.
        seen (set[int]): Ids of the containers already checked, to stop at self-references.

    Returns:
        bool: Whether the value is or holds a set or frozenset.
    """
    if type(value) in (set, frozenset):
        return True
    if type(value) not in (list, tuple, dict) or id(value) in seen:
        return False
    seen.add(id(value))
    items = [*value.keys(), *value.values()] if type(value) is dict else value
    return any(_holds_set(item, seen=seen) for item in items)


def _copy_with_ordered_sets(value: Any, *, copies: dict[int, Any]) -> Any:
    """
    Copy a value, replacing its sets with sets that iterate in a reproducible order.

    Lists, tuples and dictionaries are copied, so that the sets they hold can be replaced. Each
    container is copied once, so self-references and shared references point to the copies, as
    they pointed to the originals.

    Args:
        value (Any): The template parameter value.
        copies (dict[int, Any]): Copies made so far, by the id of the original.

    Returns:
        Any: The copied value.
    """
    if id(value) in copies:
        return copies[id(value)]

    def copy(item: Any) -> Any:
        return _copy_with_ordered_sets(item, copies=copies)

    if type(value) is set:
        return copies.setdefault(id(value), _OrderedSet(map(copy, value)))
    if type(value) is frozenset:
        return copies.setdefault(id(value), _OrderedFrozenSet(map(copy, value)))
    if type(value) is list:
        # Register the copy before filling it, so that references back to the list find it.
        copied_list: list[Any] = copies.setdefault(id(value), [])
        copied_list.extend(map(copy, value))
        return copied_list
    if type(value) is dict:
        copied_dict: dict[Any, Any] = copies.setdefault(id(value), {})
        copied_dict.update((copy(key), copy(item)) for key, item in value.items())
        return copied_dict
    if type(value) is tuple:
        items = tuple(map(copy, value))
        # A reference back to this tuple through one of its items may already have copied it.
        return copies.setdefault(id(value), items)
    return value


def _order_template_sets(value: Any) -> Any:
    """
    Replace sets with copies that iterate in a reproducible order.

    Set iteration order depends on ``PYTHONHASHSEED``, so a template that prints or loops over a set
    would otherwise render a different prompt, and get a different identifier, in each process.
    Sets nested in lists, tuples and dictionaries are replaced too, by copying the containers that
    hold them. Other values, including subclasses of these types, are returned unchanged.

    Args:
        value (Any): The template parameter value.

    Returns:
        Any: The value with its sets ordered, or the value itself if it holds no sets.
    """
    return _copy_with_ordered_sets(value, copies={}) if _holds_set(value, seen=set()) else value


def _describe_template_value(value: Any, *, enclosing: frozenset[int] = frozenset()) -> str:
    """
    Describe a template parameter value so that values which render differently are told apart.

    The description keeps container types, iteration order, path flavor, dataclass fields and
    instance attributes. These can all affect Jinja rendering even when values compare equal
    or have the same ``repr``. Opaque values without structural state use their ``repr``.

    Args:
        value (Any): The template parameter value.
        enclosing (frozenset[int]): Ids of the containers being described, to stop at self-references.

    Returns:
        str: A description of the value's rendering-relevant state, tagged with its type.
    """
    type_name = f"{type(value).__module__}.{type(value).__qualname__}"
    if id(value) in enclosing:
        return f"{type_name}(<recursive>)"
    nested = enclosing | {id(value)}

    def describe(item: Any) -> str:
        return _describe_template_value(item, enclosing=nested)

    attributes = getattr(value, "__dict__", None)
    state = f"(attributes={describe(attributes)})" if isinstance(attributes, dict) else ""
    if is_dataclass(value) and not isinstance(value, type):
        entries = ", ".join(f"{item.name!r}: {describe(getattr(value, item.name))}" for item in fields(value))
        return f"{type_name}{state}{{{entries}}}"
    if isinstance(value, dict):
        entries = ", ".join(f"{describe(key)}: {describe(item)}" for key, item in value.items())
        factory = f"(default_factory={describe(value.default_factory)})" if isinstance(value, defaultdict) else ""
        return f"{type_name}{state}{factory}{{{entries}}}"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"{type_name}{state}[{', '.join(describe(item) for item in value)}]"
    if isinstance(value, PurePath):
        return f"{type_name}({str(value)!r})"
    if state and not isinstance(value, (str, bytes, int, float, complex)) and not isroutine(value):
        return f"{type_name}{state}"
    return f"{type_name}{state}({value!r})"


def _hash_template_parameters(parameters: dict[str, Any]) -> str | None:
    """
    Hash template parameters for a converter identifier.

    Args:
        parameters (dict[str, Any]): Template parameter names and values.

    Returns:
        str | None: The first 16 hex characters of the SHA-256 digest, or ``None`` if there are no parameters.
    """
    if not parameters:
        return None
    # Keyword argument order does not change the rendered prompt, so sort by parameter name.
    description = ", ".join(
        f"{name!r}: {_describe_template_value(value)}" for name, value in sorted(parameters.items())
    )
    return hashlib.sha256(description.encode("utf-8", errors="backslashreplace")).hexdigest()[:16]


class LLMGenericTextConverter(Converter):
    """
    Represents a generic LLM-backed converter for text-in/text-out transformations.

    Subclasses may override ``_process_response`` to parse, extract, or otherwise post-process
    the raw LLM response (e.g., JSON parsing). Subclasses opt into retry behavior by setting
    ``RETRY_EXCEPTIONS`` to the tuple of exception types that should trigger a retry; by default
    the attempt count is read from the ``RETRY_MAX_NUM_ATTEMPTS`` environment variable and no
    wait is applied between attempts (matching ``pyrit_json_retry``). Subclasses needing a
    fixed attempt count or exponential backoff can pass ``max_retry_attempts`` and/or
    ``retry_wait_max_seconds`` to ``__init__``.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = ()

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        system_prompt_template: SeedPrompt | None = None,
        user_prompt_template_with_objective: SeedPrompt | None = None,
        retry_exceptions: tuple[type[BaseException], ...] | None = None,
        max_retry_attempts: int | None = None,
        retry_wait_max_seconds: int | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the converter with a target and optional prompt templates.

        Args:
            converter_target (PromptTarget): The endpoint that converts the prompt. Must satisfy
                ``CHAT_TARGET_REQUIREMENTS`` (multi-turn + editable history capabilities, possibly
                via normalization-pipeline adaptation). Can be omitted if a default has been configured
                via PyRIT initialization.
            system_prompt_template (SeedPrompt | None): The prompt template to set as the system prompt.
                The input prompt is available as ``objective`` when rendering.
            user_prompt_template_with_objective (SeedPrompt | None): The prompt template to wrap the
                user input with. Must include an ``objective`` parameter; the raw user prompt is rendered
                as ``objective``. Additional ``**kwargs`` are also forwarded to the renderer, so subclasses
                can pass static template parameters (e.g., ``language``).
            retry_exceptions (tuple[type[BaseException], ...] | None): Exception types that should
                trigger a retry. Overrides the class-level ``RETRY_EXCEPTIONS`` for this instance only.
                If ``None``, ``RETRY_EXCEPTIONS`` is used.
            max_retry_attempts (int | None): Maximum number of retry attempts. If ``None``, the
                value is read at retry time from the ``RETRY_MAX_NUM_ATTEMPTS`` environment variable.
            retry_wait_max_seconds (int | None): Upper bound (in seconds) for exponential backoff
                between retry attempts. If ``None``, no wait is applied between attempts (matches
                ``pyrit_json_retry``).
            kwargs: Additional parameters forwarded to both the system prompt and user prompt templates
                during rendering. Their constructor-time state contributes to the identifier, including
                container iteration order, path flavor and object fields. Parameters should remain static
                after construction. Sets and frozensets, including those nested in lists, tuples and
                dictionaries, are rendered in a reproducible order so that the prompt and identifier do not
                change between processes. The incoming prompt supplies ``objective`` at render time.

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
            ValueError: If ``user_prompt_template_with_objective`` does not declare an ``objective``
                parameter.
        """
        super().__init__(converter_target=converter_target)
        self._converter_target = converter_target
        self._system_prompt_template = system_prompt_template
        kwargs = {name: _order_template_sets(value) for name, value in kwargs.items()}
        self._prompt_kwargs = kwargs
        # Some subclasses add per-call values (such as the current prompt) to ``_prompt_kwargs``
        # before converting, so the identifier is based on the parameters given here only.
        self._template_parameters_hash = _hash_template_parameters(kwargs)
        self._retry_exceptions = retry_exceptions if retry_exceptions is not None else self.RETRY_EXCEPTIONS
        self._max_retry_attempts = max_retry_attempts
        self._retry_wait_max_seconds = retry_wait_max_seconds

        if user_prompt_template_with_objective and (
            user_prompt_template_with_objective.parameters is None
            or "objective" not in user_prompt_template_with_objective.parameters
        ):
            raise ValueError("user_prompt_template_with_objective must contain the 'objective' parameter")

        self._user_prompt_template_with_objective = user_prompt_template_with_objective

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with LLM and template parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        # Hash templates if they exist and have a value attribute
        system_prompt_hash = None
        if self._system_prompt_template and hasattr(self._system_prompt_template, "value"):
            system_prompt_hash = hashlib.sha256(str(self._system_prompt_template.value).encode("utf-8")).hexdigest()[
                :16
            ]

        user_prompt_hash = None
        if self._user_prompt_template_with_objective and hasattr(self._user_prompt_template_with_objective, "value"):
            user_prompt_hash = hashlib.sha256(
                str(self._user_prompt_template_with_objective.value).encode("utf-8")
            ).hexdigest()[:16]

        return self._create_identifier(
            params={
                "system_prompt_template_hash": system_prompt_hash,
                "user_prompt_template_hash": user_prompt_hash,
                # Template parameters are rendered into both prompts, so they change what is sent.
                "template_parameters_hash": self._template_parameters_hash,
            },
            converter_target=self._converter_target.get_identifier(),
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using an LLM via the specified converter target.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        system_prompt = None
        if self._system_prompt_template:
            system_prompt = self._system_prompt_template.render_template_value(
                **self._render_values(template=self._system_prompt_template, objective=prompt)
            )

        converted_prompt = prompt
        if self._user_prompt_template_with_objective:
            converted_prompt = self._user_prompt_template_with_objective.render_template_value(
                **self._render_values(template=self._user_prompt_template_with_objective, objective=prompt)
            )

        # The system prompt template is sent in the user turn; see Converter._prepend_instructions.
        request = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value=prompt,
                    converted_value=self._prepend_instructions(instructions=system_prompt, prompt=converted_prompt),
                    conversation_id=str(uuid.uuid4()),
                    sequence=1,
                    original_value_data_type=input_type,
                    converted_value_data_type=input_type,
                    converter_identifiers=[self.get_identifier()],
                )
            ]
        )

        response_text = await self._send_with_retries_async(request)
        return ConverterResult(output_text=response_text, output_type="text")

    def _render_values(self, *, template: SeedPrompt, objective: str | None) -> dict[str, Any]:
        """
        Supply constructor template values and the current input prompt.

        Args:
            template (SeedPrompt): The template to render.
            objective (str | None): The incoming prompt, when it should fill ``objective``.

        Returns:
            dict[str, Any]: Render values for the template.
        """
        values = self._prompt_kwargs.copy()
        if objective is not None:
            values["objective"] = objective
        return values

    async def _send_with_retries_async(self, request: Message) -> str:
        """
        Send the request to the converter target, retrying on configured exception types.

        When ``self._retry_exceptions`` is empty, the request is sent once with no retry.
        Otherwise, the attempt count comes from ``self._max_retry_attempts`` (or the
        ``RETRY_MAX_NUM_ATTEMPTS`` env variable when unset) and the wait between attempts
        comes from ``self._retry_wait_max_seconds`` (or no wait when unset). The final
        exception is re-raised unchanged.

        Args:
            request (Message): The message to send to the converter target.

        Returns:
            str: The post-processed response text from ``_process_response``.

        Raises:
            RuntimeError: Defensive guard for an unreachable code path; tenacity always
                re-raises the underlying exception when retries are exhausted.
        """
        if not self._retry_exceptions:
            response = await self._converter_target.send_prompt_async(message=request)
            return self._process_response(response[0].get_value())

        stop_strategy: stop_base = (
            stop_after_attempt(self._max_retry_attempts)
            if self._max_retry_attempts is not None
            else _DynamicStopAfterAttempt(get_retry_max_num_attempts)
        )
        wait_strategy: wait_base = (
            wait_exponential(multiplier=1, min=1, max=self._retry_wait_max_seconds)
            if self._retry_wait_max_seconds is not None
            else wait_none()
        )

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(self._retry_exceptions),
            stop=stop_strategy,
            wait=wait_strategy,
            reraise=True,
            after=log_exception,
        ):
            with attempt:
                response = await self._converter_target.send_prompt_async(message=request)
                return self._process_response(response[0].get_value())

        raise RuntimeError("unreachable: tenacity reraises on exhaustion")  # pragma: no cover

    def _process_response(self, response_text: str) -> str:
        """
        Post-process the raw LLM response text.

        Subclasses override this to parse JSON, extract fields, strip whitespace, etc.
        The default implementation returns the response unchanged.

        Args:
            response_text (str): The raw text returned by the LLM.

        Returns:
            str: The processed text used as the converter output.
        """
        return response_text
