# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Keep credentials out of saved instance recipes.

A credential parameter of a saved recipe names the server environment variable that holds its
value, and the value is read only when the instance is built. These checks reject a recipe that
would store a credential anyway, read the referenced variables, and keep their values out of
error messages.
"""

from __future__ import annotations

import json
import os
import re
from types import UnionType
from typing import TYPE_CHECKING, Any, ClassVar, Union, get_args, get_origin
from urllib.parse import quote, quote_plus, unquote_plus

from pyrit.common.credential_names import CredentialNames
from pyrit.common.url_credentials import UrlCredentials
from pyrit.models import ComponentIdentifier

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pyrit.models import Parameter
    from pyrit.models.catalog.instance_recipe import InstanceRecipe


class InstanceCredentials:
    """
    Check, read, and redact the credentials of saved instance recipes.

    Credential parameters are never saved as values: those the type's catalog parameters mark
    ``sensitive`` (which the GUI sends as references) and, for any type, the names
    ``ComponentIdentifier.get_sensitive_parameter_names()`` lists. Other values are saved as
    sent, so they are checked for the credentials that commonly hide in them: a URL that carries
    one (``UrlCredentials``), and, inside free-form settings such as ``extra_body_parameters`` or
    ``httpx_client_kwargs``, a text or number field whose name labels one (``CredentialNames``)
    such as ``Authorization``, in any letter case. Query parameters and authentication settings
    (such as ``params`` or ``authentication``) are checked in every form they take: a map, a list
    of maps or of name-value pairs, or a query string. A field holding an object, such as a
    property of a tool's JSON schema, or a flag such as ``true``, is not a credential, and neither
    is a list of property names under ``dependentRequired`` or ``dependencies`` in a JSON Schema
    object (one with type ``object`` or a keyword such as ``properties``) or in a schema nested
    inside it. Typed mappings, such as a scorer's regex patterns, are data the component
    interprets, so their keys are not checked.
    """

    MIN_REDACTED_FRAGMENT_LENGTH: ClassVar[int] = 4
    # A percent-escape or a JSON \u escape; either writes its hex digits in either letter case.
    ESCAPE_PATTERN: ClassVar[str] = r"%[0-9A-Fa-f]{2}|\\u[0-9A-Fa-f]{4}"
    # Folded names of JSON Schema keywords whose maps are keyed by property names, not settings.
    SCHEMA_NAME_MAPS: ClassVar[frozenset[str]] = frozenset({"dependencies", "dependentrequired"})
    # Keywords that mark a map as a JSON Schema object rather than a map of settings.
    SCHEMA_OBJECT_KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {"$ref", "$schema", "additionalProperties", "allOf", "anyOf", "oneOf", "patternProperties", "properties"}
    )

    @classmethod
    def check_recipe(cls, *, recipe: InstanceRecipe, parameters: Sequence[Parameter]) -> InstanceRecipe:
        """
        Reject a recipe that would save a credential, and drop credential parameters left empty.

        Args:
            recipe (InstanceRecipe): The recipe to check.
            parameters (Sequence[Parameter]): The constructor parameters of the recipe's type.

        Returns:
            InstanceRecipe: The recipe to save, without credential parameters set to ``None``.

        Raises:
            ValueError: If the recipe sends a credential as a value, references a credential for a
                parameter that is not one, or embeds a credential in another value.
        """
        sensitive = ComponentIdentifier.get_sensitive_parameter_names() | {
            parameter.name for parameter in parameters if parameter.sensitive
        }
        for name in sorted(sensitive & recipe.params.keys()):
            if recipe.params[name] is not None:
                raise ValueError(
                    f"'{name}' cannot be sent as a value because saved instances never store credentials. "
                    f'Send credentials.{name} = {{"env_var": "<VARIABLE NAME>"}} to read it from a server '
                    "environment variable, or leave it out to use the default."
                )
        recipe = recipe.model_copy(
            update={"params": {key: value for key, value in recipe.params.items() if key not in sensitive}}
        )
        cls._check_reference_names(recipe=recipe, parameters=parameters, sensitive=sensitive)
        cls._check_embedded_credentials(recipe=recipe, parameters=parameters, sensitive=sensitive)
        return recipe

    @classmethod
    def resolve(cls, *, recipe: InstanceRecipe, parameters: Sequence[Parameter]) -> dict[str, object]:
        """
        Read each referenced environment variable.

        Returns:
            dict[str, object]: Constructor arguments read from the environment.

        Raises:
            ValueError: If a variable is unset or empty, or a mapping parameter's variable
                does not hold a JSON object of strings.
        """
        by_name = {parameter.name: parameter for parameter in parameters}
        resolved: dict[str, object] = {}
        for name, reference in sorted(recipe.credentials.items()):
            value = os.environ.get(reference.env_var, "")
            if not value.strip():
                raise ValueError(f"Environment variable '{reference.env_var}' (for '{name}') is not set.")
            if cls._expects_mapping(by_name[name]):
                resolved[name] = cls._decode_mapping(name=name, env_var=reference.env_var, value=value)
            else:
                resolved[name] = value
        return resolved

    @classmethod
    def redact(cls, *, message: str, credentials: dict[str, object]) -> str:
        """
        Remove credential values, and the secrets inside them, from an error message.

        A value is also removed in its JSON-escaped and URL-encoded forms, whose escapes may be
        written in either letter case, and so are the secrets inside it: the user information
        and credential parameters of URLs, read as written so a malformed one counts too
        (including the URL a raw HTTP request's target and ``Host`` header make), the values of
        credential headers, and the values of a JSON object, because an error can quote just
        that part of a request template. Each is removed wherever it appears when at least
        ``MIN_REDACTED_FRAGMENT_LENGTH`` long. A shorter one is removed only where it stands
        alone as a word, with a percent-escape before it counting as a word break, so short
        common words are not replaced inside other words. Last, the user information and
        credential parameters of every URL left in the message are masked.

        Returns:
            str: The message with every credential value replaced by ``***``.
        """
        fragments = {fragment for value in credentials.values() for fragment in cls._secret_fragments(value)}
        for fragment in sorted(fragments, key=lambda fragment: (-len(fragment), fragment)):
            pattern = cls._fragment_pattern(fragment)
            if len(fragment) < cls.MIN_REDACTED_FRAGMENT_LENGTH:
                pattern = rf"(?:(?<![A-Za-z0-9])|(?<=%[0-9A-Fa-f]{{2}})){pattern}(?![A-Za-z0-9])"
            message = re.sub(pattern, "***", message)
        return UrlCredentials.mask_text(message)

    @classmethod
    def _fragment_pattern(cls, fragment: str) -> str:
        """
        Get a pattern that matches a fragment as written, except that its escapes match in either letter case.

        Returns:
            str: The regular expression.
        """
        parts = re.split(f"({cls.ESCAPE_PATTERN})", fragment)
        return "".join(
            "".join(f"[{char.lower()}{char.upper()}]" if char in "abcdefABCDEF" else re.escape(char) for char in part)
            if index % 2
            else re.escape(part)
            for index, part in enumerate(parts)
        )

    @classmethod
    def _check_reference_names(
        cls, *, recipe: InstanceRecipe, parameters: Sequence[Parameter], sensitive: frozenset[str]
    ) -> None:
        """
        Reject credential references for parameters that are not credentials.

        Raises:
            ValueError: If a reference names an unknown parameter or one that is not a credential.
        """
        declared = {parameter.name for parameter in parameters}
        for name in sorted(recipe.credentials):
            if name not in declared:
                raise ValueError(f"Unknown parameter '{name}' for '{recipe.type}'.")
            if name not in sensitive:
                allowed = ", ".join(sorted(sensitive & declared)) or "none for this type"
                raise ValueError(f"'{name}' is not a credential parameter. Credential references can set: {allowed}.")

    @classmethod
    def _check_embedded_credentials(
        cls, *, recipe: InstanceRecipe, parameters: Sequence[Parameter], sensitive: frozenset[str]
    ) -> None:
        """
        Reject a value that embeds a credential.

        Raises:
            ValueError: If a URL carries a credential, or a free-form setting has a credential field.
        """
        free_form = {parameter.name for parameter in parameters if cls._is_free_form(parameter.param_type)}
        options = ", ".join(f"credentials.{name}" for name in sorted(sensitive & {p.name for p in parameters}))
        hint = f" Send credentials through {options} instead." if options else ""
        for name, value in sorted(recipe.params.items()):
            walk = cls._find_embedded_credentials(
                value=value,
                path=name,
                check_keys=name in free_form,
                in_credential_map=CredentialNames.is_credential_map(name),
            )
            found = next(walk, None)
            if found is not None:
                path, is_url = found
                problem = "contains a credential in its URL" if is_url else "holds a credential"
                raise ValueError(f"'{path}' {problem}. Remove it; saved instances never store credentials.{hint}")

    @classmethod
    def _find_embedded_credentials(
        cls,
        *,
        value: object,
        path: str,
        check_keys: bool,
        in_credential_map: bool = False,
        in_schema: bool = False,
        lists_property_names: bool = False,
    ) -> Iterator[tuple[str, bool]]:
        """
        Walk a value and yield the path of each credential inside it.

        Args:
            value (object): The value to walk.
            path (str): Where the value sits in the recipe's parameters, for the error message.
            check_keys (bool): Whether names are checked, which only free-form settings need.
            in_credential_map (bool): Whether the value holds query parameters or authentication
                settings, where names such as ``key`` carry credentials too.
            in_schema (bool): Whether the value is inside a JSON Schema object, such as a subschema
                under ``allOf`` or ``then``.
            lists_property_names (bool): Whether the value is a ``dependentRequired`` or
                ``dependencies`` map inside a JSON Schema object, whose lists of names are not credentials.

        Yields:
            tuple[str, bool]: The path, and whether the credential is in a URL rather than a named field.
        """
        names_checked = check_keys and in_credential_map
        if isinstance(value, str):
            if UrlCredentials.has_credentials(value):
                yield path, True
            elif names_checked and cls._query_string_has_credentials(value):
                yield path, False
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_path = f"{path}[{index}]"
                if names_checked and cls._is_credential_pair(item):
                    yield item_path, False
                yield from cls._find_embedded_credentials(
                    value=item,
                    path=item_path,
                    check_keys=check_keys,
                    in_credential_map=in_credential_map,
                    in_schema=in_schema,
                )
        elif isinstance(value, dict):
            in_schema = in_schema or cls._is_schema_object(value)
            for key, item in sorted(value.items()):
                item_path = f"{path}.{key}"
                if (
                    check_keys
                    and not (lists_property_names and cls._is_name_list(item))
                    and cls._may_hold_credential(item)
                    and CredentialNames.is_credential(key, where_expected=in_credential_map)
                ):
                    yield item_path, False
                yield from cls._find_embedded_credentials(
                    value=item,
                    path=item_path,
                    check_keys=check_keys,
                    in_credential_map=CredentialNames.is_credential_map(key),
                    in_schema=in_schema,
                    lists_property_names=in_schema and CredentialNames.fold(key) in cls.SCHEMA_NAME_MAPS,
                )

    @classmethod
    def _is_schema_object(cls, value: dict[Any, Any]) -> bool:
        """
        Return whether a map is a JSON Schema object, such as a tool's parameters, rather than settings.

        Returns:
            bool: Whether the map has type ``object`` or a keyword only a schema object uses.
        """
        schema_type = value.get("type")
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        return "object" in types or not cls.SCHEMA_OBJECT_KEYWORDS.isdisjoint(value)

    @staticmethod
    def _is_name_list(item: object) -> bool:
        """
        Return whether an item is a list of names, as a ``dependentRequired`` value is.

        Returns:
            bool: Whether the item is a list of strings.
        """
        return isinstance(item, list) and all(isinstance(name, str) for name in item)

    @classmethod
    def _is_credential_pair(cls, item: object) -> bool:
        """
        Return whether an item is a ``[name, value]`` query parameter whose name labels a credential.

        Returns:
            bool: Whether the item is a two-item list naming a credential and holding what one could be.
        """
        return (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and CredentialNames.is_credential(item[0], where_expected=True)
            and cls._may_hold_credential(item[1])
        )

    @classmethod
    def _is_free_form(cls, annotation: object) -> bool:
        """
        Return whether a parameter takes free-form values that are passed through, such as ``dict[str, Any]``.

        Returns:
            bool: Whether ``Any`` appears in the annotation, the parameter is not annotated, or it is
            a bare ``dict`` or ``list``.
        """
        if annotation in (None, Any, object, dict, list):
            return True
        return any(cls._is_free_form(argument) for argument in get_args(annotation) if argument is not type(None))

    @classmethod
    def _secret_fragments(cls, value: object) -> set[str]:
        """
        Get the strings to remove from an error message for one credential value.

        Returns:
            set[str]: The value and the secrets inside it, each in its plain, JSON-escaped, and URL-encoded
            forms, and those of the values inside it when it is a mapping or a JSON object or array.
        """
        if not isinstance(value, str):
            return {fragment for text in cls._texts_in(value) for fragment in cls._secret_fragments(text)}
        forms = {
            form
            for fragment in (value, *cls._inner_secrets(value))
            for form in (fragment, json.dumps(fragment)[1:-1], quote(fragment, safe=""), quote_plus(fragment))
        }
        try:
            decoded = json.loads(value)
        except RecursionError:
            # Nested too deeply to decode; its strings are still found between their quotes.
            decoded = re.findall(r'"((?:[^"\\]|\\.)*)"', value)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict | list):
            forms |= cls._secret_fragments(decoded)
        return {form for form in forms if form.strip()}

    @staticmethod
    def _texts_in(value: object) -> Iterator[str]:
        """
        Yield the text values inside a mapping or a decoded JSON value, however deeply nested.

        Yields:
            str: Each text value.
        """
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                yield item
            elif isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)

    @classmethod
    def _inner_secrets(cls, value: str) -> Iterator[str]:
        """
        Yield the secrets inside a value: the token after an authentication scheme, credential
        header values, and the credentials of URLs, including those of a raw HTTP request's
        target and ``Host`` header, read as written.

        Yields:
            str: Each secret found.
        """
        words = value.split()
        if "\n" not in value and words:
            yield words[-1]
        lines = value.splitlines()
        request_line = lines[0].split() if lines else []
        target = request_line[1] if len(request_line) == 3 else None
        for line in lines:
            header, separator, header_value = line.partition(":")
            header_value = header_value.strip().strip("\"', ")
            if separator and header_value and CredentialNames.is_credential(header.strip().strip("\"'{, ")):
                yield header_value
                yield header_value.split()[-1]
            if separator and header_value and header.strip().lower() == "host":
                yield from cls._written_url_secrets(header_value, whole_authority=True)
        for word in words:
            yield from UrlCredentials.secrets(word)
            authority_form = word == target and "://" not in word and not word.startswith("/")
            yield from cls._written_url_secrets(word, whole_authority=authority_form)

    @staticmethod
    def _written_url_secrets(text: str, *, whole_authority: bool) -> Iterator[str]:
        """
        Yield the credentials a URL, a request target, or a host carries, read as written.

        A constructor can quote a raw HTTP request's target or ``Host`` header as written even
        where a URL parser rejects it, so nothing is parsed: the user information is all of a
        ``Host`` value or an authority-form target before its last ``@``, since neither holds a
        path, and otherwise what ``UrlCredentials.user_information`` reads after a ``://`` or a
        leading ``//``; an origin-form path such as ``/users/@me`` holds none. The values of
        credential parameters are read after a ``?`` or ``#``.

        Yields:
            str: Each secret found, as written and decoded.
        """
        if whole_authority:
            user_information = text.rpartition("@")[0]
        elif "://" in text:
            user_information = UrlCredentials.user_information(text.split("://", 1)[1]) or ""
        elif text.startswith("//"):
            user_information = UrlCredentials.user_information(text[2:]) or ""
        else:
            user_information = ""
        user, _, password = user_information.partition(":")
        found = [user_information, user, password]
        for separator in "?#":
            if separator in text:
                found += UrlCredentials.query_credentials(text.split(separator, 1)[1])
        for secret in found:
            if secret:
                yield secret
                yield unquote_plus(secret)

    @staticmethod
    def _query_string_has_credentials(value: str) -> bool:
        """
        Return whether text is a query string, such as ``api-version=1&code=abc``, with a credential parameter.

        A part whose name holds whitespace is prose rather than a parameter, so it is skipped.

        Returns:
            bool: Whether a ``name=value`` part names a credential and holds what one could be.
        """
        parameters = (part for part in value.split("&") if not any(c.isspace() for c in part.partition("=")[0]))
        return next(UrlCredentials.query_credentials("&".join(parameters)), None) is not None

    @staticmethod
    def _may_hold_credential(value: object) -> bool:
        """
        Return whether a field holds what a credential could be: text, or a number, which clients
        send as text, alone or in a list, other than a flag such as ``true`` or ``0``.

        Returns:
            bool: Whether the value, or an item of a list value, could be a credential.
        """
        items = value if isinstance(value, list) else [value]
        return any(
            isinstance(item, str | int | float)
            and not isinstance(item, bool)
            and str(item).strip() != ""
            and not CredentialNames.is_flag(str(item))
            for item in items
        )

    @staticmethod
    def _expects_mapping(parameter: Parameter) -> bool:
        """
        Return whether a parameter takes a mapping rather than a string.

        Returns:
            bool: Whether the declared type is a ``dict``.
        """
        annotation = parameter.param_type
        candidates = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else (annotation,)
        return any(candidate is dict or get_origin(candidate) is dict for candidate in candidates)

    @staticmethod
    def _decode_mapping(*, name: str, env_var: str, value: str) -> dict[str, str]:
        """
        Decode the JSON object of strings an environment variable holds for a mapping parameter.

        Returns:
            dict[str, str]: The decoded mapping.

        Raises:
            ValueError: If the value is not a JSON object of strings.
        """
        message = f"Environment variable '{env_var}' (for '{name}') must hold a JSON object of strings."
        try:
            decoded = json.loads(value)
        except ValueError:
            raise ValueError(message) from None
        if not isinstance(decoded, dict):
            raise ValueError(message)
        mapping: dict[str, str] = {}
        for key, item in decoded.items():
            if not isinstance(item, str):
                raise ValueError(message)
            mapping[key] = item
        return mapping
