# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Compile the JSON operations shared by SQLite and Azure SQL analytics.

These SQLAlchemy nodes contain expressions, not pre-rendered table names. Compiling
their children with the active compiler preserves aliases, correlation, and bound
JSON paths when a query is wrapped in a CTE. They never execute SQL or decode result
metadata. SQLite uses JSON1; Azure SQL uses OPENJSON scalar projections,
JSON_QUERY, and STRING_AGG rather than requiring newer native JSON functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, String, UnicodeText
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import BindParameter
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.selectable import Join, Lateral

if TYPE_CHECKING:
    from sqlalchemy.sql.compiler import SQLCompiler


class JsonScalar(FunctionElement[str]):
    """
    A scalar JSON property at a bound path; absent properties produce SQL NULL.

    SQL Server extracts nvarchar(max), not JSON_VALUE's capped nvarchar(4000).
    Its scalar subquery must be projected before grouping or aggregate arguments.
    Paths remain parameters in the expression tree, with safe execution-time
    literalization where SQL Server's OPENJSON WITH grammar requires a literal.
    """

    type = UnicodeText()
    inherit_cache = True


class JsonClassNamePresent(FunctionElement[bool]):
    """
    Whether an identifier object explicitly has ``class_name``, even when null.

    The second argument is the path to the containing object. A missing key may
    use the legacy ``__type__`` fallback; a present null key may not.
    """

    type = Boolean()
    inherit_cache = True


class ResolvedAttackIdentifierHash(FunctionElement[str]):
    """
    The indexed identifier reference, falling back to the hash saved inside legacy JSON.

    This reads the canonical ``hash`` property; it does not hash a JSON serialization.
    Keeping SQL Server's result at 64 characters makes the computed column indexable.
    """

    type = String(64)
    inherit_cache = True


@compiles(ResolvedAttackIdentifierHash, "sqlite")
def _sqlite_resolved_hash(element: ResolvedAttackIdentifierHash, compiler: Any, **kwargs: Any) -> str:
    """
    Prefer the normalized foreign key without losing legacy JSON-only references.

    Returns:
        str: A COALESCE expression over the reference and the stored JSON hash.
    """
    reference, document = _arguments(element, compiler, **kwargs)
    return f"coalesce({reference}, json_extract({document}, '$.hash'))"


@compiles(ResolvedAttackIdentifierHash, "mssql")
def _mssql_resolved_hash(element: ResolvedAttackIdentifierHash, compiler: Any, **kwargs: Any) -> str:
    """
    Bound the resolved hash's SQL type so it can be used as an index key.

    Returns:
        str: A varchar(64) expression, rather than JSON_VALUE's wider inferred type.
    """
    reference, document = _arguments(element, compiler, **kwargs)
    return f"CONVERT(varchar(64), coalesce({reference}, JSON_VALUE({document}, '$.hash')))"


class JsonContainer(FunctionElement[str]):
    """A serialized JSON array or object, not a scalar property or a Python value."""

    type = UnicodeText()
    inherit_cache = True


class JsonArrayItems(FunctionElement[str]):
    """
    A table-valued JSON expansion exposing ``key``, ``value``, and ``type`` columns.

    SQLite reports textual type names; OPENJSON uses numeric type codes. Array
    shape and member types must still be checked by the caller: objects can also
    be expanded, and JSON null members are different from an empty array.
    """

    type = UnicodeText()
    inherit_cache = True


class JsonArrayEmpty(FunctionElement[bool]):
    """
    A scalar 0/1 check for a recorded empty array, independent of JSON whitespace.

    Compare explicitly with ``true()`` when using it as a CASE condition: SQL Server
    does not accept a bare bit/integer expression as a predicate.
    """

    type = Boolean()
    inherit_cache = True


class JsonIsArray(FunctionElement[bool]):
    """A scalar 0/1 array-shape check, using the same predicate convention as ``JsonArrayEmpty``."""

    type = Boolean()
    inherit_cache = True


class JsonArrayAggregate(FunctionElement[str]):
    """
    Aggregate scalar strings as a JSON array, including null elements.

    Null converter names remain observable missing metadata. Dropping them, as
    STRING_AGG normally does, would confuse incomplete identifiers with empty pipelines.
    """

    type = UnicodeText()
    inherit_cache = True


class JsonObjectAggregate(FunctionElement[str]):
    """Aggregate already-unique outcome/count pairs into a JSON object without changing counts."""

    type = UnicodeText()
    inherit_cache = True


class JsonArrayJoin(Join):
    """
    An outer lateral array expansion preserving a row for empty/missing metadata.

    This is deliberately limited to an outer join on ``true()``. SQLite permits
    correlation in a table-valued function's arguments; SQL Server requires
    OUTER APPLY instead of LEFT JOIN and has no ON clause for that operation.
    SQL Server also uses it for explicitly correlated lateral SELECT projections
    that move JSON scalar subqueries out of GROUP BY and aggregate arguments.
    """

    inherit_cache = False


def _arguments(element: FunctionElement[Any], compiler: Any, **kwargs: Any) -> list[str]:
    """
    Compile children in the current alias and parameter-binding context.

    Returns:
        list[str]: SQL fragments in argument order, preserving the caller's compile options.
    """
    return [compiler.process(argument, **kwargs) for argument in element.clauses]


@compiles(JsonScalar, "sqlite")
@compiles(JsonContainer, "sqlite")
def _sqlite_json_property(element: FunctionElement[str], compiler: Any, **kwargs: Any) -> str:
    """
    Use SQLite's shared extractor for scalar properties and serialized containers.

    Returns:
        str: JSON1 extraction SQL with a bound or compiler-rendered literal path.
    """
    return f"json_extract({', '.join(_arguments(element, compiler, **kwargs))})"


@compiles(JsonClassNamePresent, "sqlite")
def _sqlite_class_name_present(element: JsonClassNamePresent, compiler: Any, **kwargs: Any) -> str:
    """
    Distinguish an absent key from a JSON null without expanding the object.

    Returns:
        str: A scalar 0/1 expression, including for a missing parent object.
    """
    document, object_path = _arguments(element, compiler, **kwargs)
    return f"CASE WHEN json_type({document}, {object_path} || '.class_name') IS NOT NULL THEN 1 ELSE 0 END"


@compiles(JsonScalar, "mssql")
def _mssql_json_scalar(element: JsonScalar, compiler: Any, **kwargs: Any) -> str:
    """
    Extract full-width scalar text without silently converting long values to NULL.

    Persisted metadata documents are containers. Validate/extract that container
    with JSON_QUERY before wrapping it as one array element: malformed empty text
    must not accidentally become a valid []. The wrapper keeps OPENJSON WITH at
    most one row even for an unexpected array document. A MAX-typed prefix also
    prevents CONCAT from truncating the document. Lax property extraction preserves
    missing, null, and non-scalar values; it does not coerce containers to strings.
    WITH requires a literal column path, so let SQLAlchemy quote the bound path
    at execution rather than interpolating input or baking values into cached SQL.

    Returns:
        str: A correlated scalar subquery returning nvarchar(max).

    Raises:
        CompileError: If the JSON path is not a bound string.
    """
    document, path = element.clauses
    if not isinstance(path, BindParameter) or not isinstance(path.value, str):
        raise CompileError("SQL Server scalar JSON paths must be a bound string.")
    document_sql = compiler.process(document, **kwargs)
    path_sql = compiler.process(path, **{**kwargs, "literal_execute": True})
    return (
        "(SELECT [value] FROM "
        f"OPENJSON(CONCAT(CAST(N'[' AS nvarchar(max)), JSON_QUERY({document_sql}), N']')) "
        f"WITH ([value] nvarchar(max) {path_sql}))"
    )


@compiles(JsonClassNamePresent, "mssql")
def _mssql_class_name_present(element: JsonClassNamePresent, compiler: Any, **kwargs: Any) -> str:
    """
    Inspect OPENJSON's keys so an explicitly null canonical name blocks legacy fallback.

    Returns:
        str: A scalar 0/1 expression; path literalization is safe at execution time.

    Raises:
        CompileError: If the containing-object path is not a bound string.
    """
    document, object_path = element.clauses
    if not isinstance(object_path, BindParameter) or not isinstance(object_path.value, str):
        raise CompileError("SQL Server identifier object paths must be a bound string.")
    document_sql = compiler.process(document, **kwargs)
    path_sql = compiler.process(object_path, **{**kwargs, "literal_execute": True})
    return (
        f"CASE WHEN EXISTS (SELECT 1 FROM OPENJSON({document_sql}, {path_sql}) "
        "WHERE [key] = N'class_name') THEN 1 ELSE 0 END"
    )


@compiles(JsonContainer, "mssql")
def _mssql_json_container(element: JsonContainer, compiler: Any, **kwargs: Any) -> str:
    """
    Keep objects and arrays as JSON text rather than JSON_VALUE's scalar result.

    Returns:
        str: JSON_QUERY SQL, yielding container text or NULL for an absent/non-container property.
    """
    return f"JSON_QUERY({', '.join(_arguments(element, compiler, **kwargs))})"


@compiles(JsonArrayItems, "sqlite")
def _sqlite_json_items(element: JsonArrayItems, compiler: Any, **kwargs: Any) -> str:
    """
    Expose JSON1 members without adding an independent result-table reference.

    Returns:
        str: A json_each table-valued expression that can reference the outer source alias.
    """
    return f"json_each({', '.join(_arguments(element, compiler, **kwargs))})"


@compiles(JsonArrayItems, "mssql")
def _mssql_json_items(element: JsonArrayItems, compiler: Any, **kwargs: Any) -> str:
    """
    Expose OPENJSON's default key/value/type schema for lateral expansion.

    Returns:
        str: A table-valued expression to be joined with OUTER APPLY.
    """
    return f"OPENJSON({', '.join(_arguments(element, compiler, **kwargs))})"


@compiles(JsonArrayEmpty, "sqlite")
def _sqlite_array_empty(element: JsonArrayEmpty, compiler: Any, **kwargs: Any) -> str:
    """
    Require array shape because SQLite also reports zero array length for non-arrays.

    Returns:
        str: A scalar CASE yielding 1 only for an empty JSON array.
    """
    value = _arguments(element, compiler, **kwargs)[0]
    return f"CASE WHEN json_type({value}) = 'array' AND json_array_length({value}) = 0 THEN 1 ELSE 0 END"


def _mssql_without_json_whitespace(value: str) -> str:
    """
    Build a whitespace-free expression used only to inspect a JSON container's shape.

    All four legal JSON whitespace characters are removed, including leading tabs
    and newlines that LTRIM alone misses. Never use this expression to read member
    values: whitespace inside a JSON string must remain intact.

    Args:
        value (str): Already-compiled SQL expression, not raw JSON or user input.

    Returns:
        str: A scalar SQL expression with no subqueries, safe inside GROUP BY.
    """
    for whitespace in ("N' '", "NCHAR(9)", "NCHAR(10)", "NCHAR(13)"):
        value = f"REPLACE({value}, {whitespace}, N'')"
    return value


@compiles(JsonArrayEmpty, "mssql")
def _mssql_array_empty(element: JsonArrayEmpty, compiler: Any, **kwargs: Any) -> str:
    """
    Recognize [] without OPENJSON/EXISTS, which SQL Server forbids inside a GROUP BY expression.

    Returns:
        str: A scalar 0/1 CASE that accepts all legal JSON whitespace around the brackets.
    """
    value = _mssql_without_json_whitespace(_arguments(element, compiler, **kwargs)[0])
    return f"CASE WHEN {value} = N'[]' THEN 1 ELSE 0 END"


@compiles(JsonIsArray, "sqlite")
def _sqlite_is_array(element: JsonIsArray, compiler: Any, **kwargs: Any) -> str:
    """
    Distinguish arrays from other JSON values before assigning membership keys.

    Returns:
        str: A scalar 0/1 JSON1 type check, also defined for SQL NULL.
    """
    value = _arguments(element, compiler, **kwargs)[0]
    return f"CASE WHEN json_type({value}) = 'array' THEN 1 ELSE 0 END"


@compiles(JsonIsArray, "mssql")
def _mssql_is_array(element: JsonIsArray, compiler: Any, **kwargs: Any) -> str:
    """
    Inspect the first non-whitespace character without treating [ as a LIKE character class.

    Returns:
        str: A scalar 0/1 shape check. OPENJSON performs actual JSON parsing when expanded.
    """
    value = _mssql_without_json_whitespace(_arguments(element, compiler, **kwargs)[0])
    return f"CASE WHEN LEFT({value}, 1) = N'[' THEN 1 ELSE 0 END"


@compiles(JsonArrayAggregate, "sqlite")
def _sqlite_json_array(element: JsonArrayAggregate, compiler: Any, **kwargs: Any) -> str:
    """
    Retain JSON null members while collecting deduplicated converter names.

    Returns:
        str: JSON1 array aggregation SQL, without Python-side collection.
    """
    return f"json_group_array({', '.join(_arguments(element, compiler, **kwargs))})"


@compiles(JsonArrayAggregate, "mssql")
def _mssql_json_array(element: JsonArrayAggregate, compiler: Any, **kwargs: Any) -> str:
    """
    Escape members and preserve nulls, using nvarchar(max) to avoid STRING_AGG's short-string limit.

    Returns:
        str: Wide-string JSON array aggregation, including explicit null elements.
    """
    value = _arguments(element, compiler, **kwargs)[0]
    item = (
        f"CASE WHEN {value} IS NULL THEN N'null' "
        f"ELSE CONCAT(N'\"', STRING_ESCAPE(CAST({value} AS nvarchar(max)), 'json'), N'\"') END"
    )
    return f"CONCAT(N'[', STRING_AGG(CAST({item} AS nvarchar(max)), N','), N']')"


@compiles(JsonObjectAggregate, "sqlite")
def _sqlite_json_object(element: JsonObjectAggregate, compiler: Any, **kwargs: Any) -> str:
    """
    Serialize raw outcome counts after SQL has grouped each outcome exactly once.

    Returns:
        str: JSON1 object aggregation SQL with one key per persisted outcome.
    """
    return f"json_group_object({', '.join(_arguments(element, compiler, **kwargs))})"


@compiles(JsonObjectAggregate, "mssql")
def _mssql_json_object(element: JsonObjectAggregate, compiler: Any, **kwargs: Any) -> str:
    """
    Escape outcome keys and emit numeric counts with wide-string aggregation.

    Returns:
        str: JSON object construction SQL that leaves integer counts unquoted.
    """
    key, value = _arguments(element, compiler, **kwargs)
    pair = f"CONCAT(N'\"', STRING_ESCAPE(CAST({key} AS nvarchar(max)), 'json'), N'\":', CAST({value} AS nvarchar(max)))"
    return f"CONCAT(N'{{', STRING_AGG({pair}, N','), N'}}')"


@compiles(JsonArrayJoin, "sqlite")
def _sqlite_array_join(element: JsonArrayJoin, compiler: SQLCompiler, **kwargs: Any) -> str:
    """
    Delegate to SQLite's ordinary LEFT JOIN rendering, including its ON condition.

    Returns:
        str: The compiled correlated array join.

    Raises:
        TypeError: If the SQLAlchemy join compiler does not produce SQL text.
    """
    sql = compiler.visit_join(element, **kwargs)
    if not isinstance(sql, str):
        raise TypeError("The SQLite join compiler did not return SQL text")
    return sql


@compiles(JsonArrayJoin, "mssql")
def _mssql_array_join(element: JsonArrayJoin, compiler: Any, **kwargs: Any) -> str:
    """
    Render the array function against each left-hand row, retaining rows with no members.

    Returns:
        str: OUTER APPLY SQL using the active aliases on both sides.
    """
    options = {**kwargs, "asfrom": True}
    left = compiler.process(element.left, **options)
    right = (
        compiler.visit_alias(element.right, **{**options, "lateral": True})
        if isinstance(element.right, Lateral)
        else compiler.process(element.right, **options)
    )
    return f"{left} OUTER APPLY {right}"
