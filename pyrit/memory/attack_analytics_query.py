# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Compile saved-result cohorts into metadata projections, not ORM object graphs.

The aggregation pipeline has four grains: one row per saved result, optional
weighted facts sharing identifier references, pre-counted metadata profiles, and
deduplicated dimension memberships. Only then are weights summed into groups or
cells. Expanding arrays earlier would multiply counts for repeated converters or
harm categories. ``_group_compiler`` describes the indexed/legacy fact split.

SQL returns bounded chart/page projections, but the database still considers the
entire filtered cohort. The optional SQLite profile query instead transfers a
strictly capped set of pre-counted profiles to the SDK; it never samples results.
No statement joins scores, messages, or conversation histories.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    Unicode,
    UnicodeText,
    and_,
    case,
    cast,
    column,
    func,
    literal,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.sql import visitors

from pyrit.memory.analytics_sql import (
    JsonArrayAggregate,
    JsonArrayEmpty,
    JsonArrayItems,
    JsonArrayJoin,
    JsonClassNamePresent,
    JsonContainer,
    JsonIsArray,
    JsonObjectAggregate,
    JsonScalar,
)
from pyrit.memory.memory_models import (
    AtomicAttackIdentifierEntry,
    AttackIdentifierEntry,
    AttackRequestConverterIdentifierEntry,
    AttackResponseConverterIdentifierEntry,
    AttackResultEntry,
    AttackTechniqueIdentifierEntry,
    ConverterIdentifierEntry,
    CustomUUID,
    ScenarioResultEntry,
    TargetIdentifierEntry,
)
from pyrit.models import (
    AttackAnalyticsConverterDirection,
    AttackAnalyticsDimension,
    AttackAnalyticsDimensionName,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFilter,
    AttackAnalyticsFilters,
    AttackAnalyticsMatchMode,
    AttackAnalyticsQuery,
    AttackAnalyticsValue,
    AttackAnalyticsValueKind,
)

if TYPE_CHECKING:
    from sqlalchemy.sql import ColumnElement, Select
    from sqlalchemy.sql.selectable import CTE, CompoundSelect, FromClause

    from pyrit.common.pagination import DecodedKeysetCursor


@dataclass
class _Source:
    """
    SQL expressions and key semantics for one dimension before array expansion.

    ``value`` is a scalar key or serialized JSON array; ``label`` is the scalar's
    display text (array labels come from members). ``converters`` permits legacy
    converter objects and distinguishes [] from missing metadata. ``insensitive``
    folds membership keys with the database's LOWER, never their display labels.
    """

    value: ColumnElement[Any]
    label: ColumnElement[Any]
    array: bool = False
    converters: bool = False
    insensitive: bool = False


class AttackAnalyticsQueryCompiler:
    """
    Build one request's count and metadata statements without executing them.

    Source lookup lazily extends ``_from`` with one-to-one joins. Build sources and
    predicates before capturing that FROM clause. Aliases and cached expressions
    belong to this compiler only; neither the source cache nor the weighted CTEs
    cache results across requests.
    """

    _IDENTIFIER_DIMENSIONS: ClassVar[frozenset[AttackAnalyticsDimensionName]] = frozenset(
        {
            AttackAnalyticsDimensionName.ATTACK_TYPE,
            AttackAnalyticsDimensionName.CONVERTER_TYPE,
            AttackAnalyticsDimensionName.OBJECTIVE_TARGET,
            AttackAnalyticsDimensionName.MODEL,
        }
    )
    _COMPACT_MATRIX_DIMENSIONS: ClassVar[frozenset[AttackAnalyticsDimensionName]] = frozenset(
        {
            AttackAnalyticsDimensionName.TARGETED_HARM_CATEGORY,
            AttackAnalyticsDimensionName.ATTACK_TYPE,
            AttackAnalyticsDimensionName.CONVERTER_TYPE,
        }
    )

    def __init__(
        self,
        *,
        dialect: str,
        filters: AttackAnalyticsFilters,
        root: CTE | None = None,
        label_columns: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize a result-row compiler or the internal weighted-fact continuation.

        Args:
            dialect (str): ``sqlite`` or ``mssql``; controls JSON types and key collation.
            filters (AttackAnalyticsFilters): Validated cohort selection. A weighted
                continuation receives empty filters because its input is already filtered.
            root (CTE | None): Internal compacted facts with ``analytics_weight`` and a
                resolved identifier hash. None selects the saved-result table and its
                indexed computed hash, including canonical hashes saved only in JSON.
            label_columns (dict[str, str] | None): Literal label keys already projected
                into compacted facts, avoiding retention of the entire labels document.

        Raises:
            NotImplementedError: If the memory dialect is unsupported.
        """
        if dialect not in {"sqlite", "mssql"}:
            raise NotImplementedError(f"Attack analytics does not support the {dialect!r} dialect")
        self.dialect = dialect
        self.filters = filters
        self.root = root if root is not None else AttackResultEntry.__table__.alias("analytics_results")
        self._atomic_hash = (
            self.root.c.atomic_attack_identifier_hash
            if root is not None
            else self.root.c.resolved_atomic_attack_identifier_hash
        )
        self._label_columns = label_columns or {}
        self._from: FromClause = self.root
        self._sources: dict[str, _Source] = {}
        self._identifiers_ready = False
        self.atomic = AtomicAttackIdentifierEntry.__table__.alias("analytics_atomic")
        self.technique = AttackTechniqueIdentifierEntry.__table__.alias("analytics_technique")
        self.attack = AttackIdentifierEntry.__table__.alias("analytics_attack")
        self.target = TargetIdentifierEntry.__table__.alias("analytics_target")
        self._converter_arrays: dict[
            AttackAnalyticsConverterDirection, tuple[ColumnElement[Any], ColumnElement[Any]]
        ] = {}

    def totals(self) -> Select[Any]:
        """
        Select raw counts by persisted outcome over distinct result IDs.

        This runs on the un-compacted result root. Its joins are one-to-one and
        array filters use EXISTS, so COUNT(*) counts saved IDs even when several
        results share a conversation. All saved outcomes are retained; the SDK,
        not memory, chooses the decided-result denominator for rates.

        Returns:
            Select[Any]: One count row per recorded outcome, with no result hydration.
        """
        conditions = self._conditions()
        return (
            select(self.root.c.outcome, func.count().label("count"))
            .select_from(self._from)
            .where(*conditions)
            .group_by(self.root.c.outcome)
        )

    def compact_profiles(self, *, query: AttackAnalyticsQuery, limit: int, max_value_length: int) -> Select[Any] | None:
        """
        Offer the SQLite fast path before expanding categorical arrays.

        Only harm category, attack type, and converter type use this path. The
        reader asks for one extra profile to detect overflow, checks per-value
        and combined text limits, and falls back to SQL grouping if any limit is
        exceeded. Oversized strings are replaced with NULL only in this rejected
        probe; they must never be interpreted as missing metadata.

        Args:
            query (AttackAnalyticsQuery): Requested group and optional comparison dimension.
            limit (int): Maximum transferred profiles, including the reader's overflow row.
            max_value_length (int): Maximum characters in each raw metadata value.

        Returns:
            Select[Any] | None: Profiles with raw sources, outcome, weight, and an
                ``oversized`` flag, or None when the general SQL path is required.
        """
        dimensions = [query.group_by] + ([query.compare_by] if query.compare_by is not None else [])
        if self.dialect != "sqlite" or not all(
            dimension.name in self._COMPACT_MATRIX_DIMENSIONS for dimension in dimensions
        ):
            return None
        compiler = self._group_compiler(dimensions)
        profiles = compiler._profiles([compiler._source(dimension) for dimension in dimensions])
        canonical_sources = {
            f"source{index}": self._profile_converter_array(
                profile=profiles, raw=profiles.c[f"source{index}"], name=f"profile_converter{index}"
            )
            for index, dimension in enumerate(dimensions)
            if dimension.name is AttackAnalyticsDimensionName.CONVERTER_TYPE
        }
        canonical = (
            select(*[canonical_sources.get(key, value).label(key) for key, value in profiles.c.items()]).cte(
                "canonical_analytics_profiles"
            )
            if canonical_sources
            else profiles
        )
        oversized = or_(
            *[
                and_(
                    canonical.c[f"source{index}"].isnot(None),
                    func.length(canonical.c[f"source{index}"]) > max_value_length,
                )
                for index in range(len(dimensions))
            ]
        )
        return select(
            *[
                (
                    case((func.length(value) > max_value_length, None), else_=value).label(key)
                    if key.startswith(("source", "display"))
                    else value
                )
                for key, value in canonical.c.items()
            ],
            oversized.label("oversized"),
        ).limit(limit)

    def _profile_converter_array(self, *, profile: CTE, raw: ColumnElement[Any], name: str) -> ColumnElement[Any]:
        """
        Emit converter names, not legacy objects, from a pre-counted SQLite profile.

        Leave NULL, JSON null, [], and malformed member types distinct. Only
        profiled converter arrays need this transformation; result pages and SQL
        memberships already understand both identifier-object layouts.

        Returns:
            ColumnElement[Any]: A canonical JSON string array when the source is usable,
                or its original value so unsupported metadata remains observable.
        """
        items = JsonArrayItems(raw).table_valued(column("key"), column("value"), column("type"))
        items = items.alias(f"{name}_items")
        document = case((items.c.type == "object", items.c.value), else_="{}")
        member = case(
            (items.c.type == "object", self._identifier_property(document=document, path="class_name")),
            else_=items.c.value,
        )
        name_type = case(
            (
                JsonClassNamePresent(document, "$") == true(),
                func.json_type(document, "$.class_name"),
            ),
            else_=func.json_type(document, "$.__type__"),
        )
        unsupported = (
            select(1)
            .select_from(items)
            .where(
                or_(
                    ~items.c.type.in_(("text", "object", "null")),
                    and_(items.c.type == "object", name_type.isnot(None), ~name_type.in_(("text", "null"))),
                )
            )
            .correlate(profile)
            .exists()
        )
        names = select(JsonArrayAggregate(member)).select_from(items).correlate(profile).scalar_subquery()
        return case((and_(JsonIsArray(raw) == true(), ~unsupported), names), else_=raw)

    def groups(self, query: AttackAnalyticsQuery) -> Select[Any]:
        """
        Select one bounded page of grouped raw outcome counts.

        Memberships are deduplicated within each profile before its weight is
        added to a bucket. Groups sort by total count, then typed key; the extra
        row indicates another page without changing cohort totals.

        Args:
            query (AttackAnalyticsQuery): Group dimension, offset, and visible group limit.

        Returns:
            Select[Any]: Groups with JSON outcome/count dictionaries.
        """
        grouped = self._group_compiler([query.group_by])
        if grouped is not self:
            return grouped.groups(query)
        sources = [self._source(query.group_by)]
        profiles = self._profiles(sources)
        members = self._memberships(profiles=profiles, sources=sources, dimensions=[0], name="group_members")
        grouped = self._aggregate(members, dimensions=[0])
        return (
            select(grouped)
            .order_by(grouped.c.total.desc(), grouped.c.kind0, grouped.c.value0)
            .offset(query.group_offset)
            .limit(query.group_limit + 1)
        )

    def matrix(self, query: AttackAnalyticsQuery) -> CompoundSelect[Any]:
        """
        Select bounded axes and cells in one statement sharing compacted profiles.

        Categorical matrices aggregate cells once and rank their keys. Other
        matrices discover each axis separately before aggregating visible cells,
        avoiding a full high-cardinality Cartesian expansion. Both strategies
        choose axes by typed key, not frequency, and preserve counts for the
        entire cohort contributing to each visible cell.

        Args:
            query (AttackAnalyticsQuery): Row/column dimensions and visible axis limit.

        Returns:
            CompoundSelect[Any]: Tagged axis and cell rows.

        Raises:
            ValueError: If a comparison dimension is absent.
        """
        if query.compare_by is None:
            raise ValueError("A matrix requires compare_by")
        grouped = self._group_compiler([query.group_by, query.compare_by])
        if grouped is not self:
            return grouped.matrix(query)
        sources = [self._source(query.group_by), self._source(query.compare_by)]
        profiles = self._profiles(sources)
        if all(dimension.name in self._COMPACT_MATRIX_DIMENSIONS for dimension in (query.group_by, query.compare_by)):
            return self._compact_matrix(profiles=profiles, sources=sources, limit=query.axis_limit)
        candidates = [
            self._axis(profiles=profiles, sources=sources, index=index, limit=query.axis_limit + 1)
            for index in range(2)
        ]
        visible = [
            select(axis)
            .order_by(axis.c[f"kind{index}"], axis.c[f"value{index}"])
            .limit(query.axis_limit)
            .cte(f"visible_axis{index}")
            for index, axis in enumerate(candidates)
        ]
        members = self._memberships(profiles=profiles, sources=sources, dimensions=[0, 1], name="cell_members")
        selected = members.join(visible[0], self._same_key(left=members, right=visible[0], index=0)).join(
            visible[1], self._same_key(left=members, right=visible[1], index=1)
        )
        bounded = select(members).select_from(selected).cte("bounded_cell_members")
        cells = self._aggregate(bounded, dimensions=[0, 1])
        truncated = case(
            (
                or_(
                    *[
                        select(func.count()).select_from(axis).scalar_subquery() > query.axis_limit
                        for axis in candidates
                    ]
                ),
                True,
            ),
            else_=False,
        ).label("truncated")
        return self._matrix_output(axes=visible, cells=cells, truncated=truncated)

    def _compact_matrix(self, *, profiles: CTE, sources: list[_Source], limit: int) -> CompoundSelect[Any]:
        """
        Aggregate categorical cells once, then select axes with dense key ranks.

        Args:
            profiles (CTE): One row per raw dimension tuple and outcome, with a weight.
            sources (list[_Source]): The row and column source semantics.
            limit (int): Maximum distinct keys per visible axis.

        Returns:
            CompoundSelect[Any]: Tagged axes and cells, with truncation derived from
                all ranks. This bounds output, not the SQL aggregation's working set.
        """
        members = self._memberships(profiles=profiles, sources=sources, dimensions=[0, 1], name="compact_cell_members")
        cells = self._aggregate(members, dimensions=[0, 1])
        ranked = select(
            cells,
            func.dense_rank().over(order_by=[cells.c.kind0, cells.c.value0]).label("rank0"),
            func.dense_rank().over(order_by=[cells.c.kind1, cells.c.value1]).label("rank1"),
        ).cte("ranked_analytics_cells")
        axes = [
            select(
                ranked.c[f"kind{index}"],
                ranked.c[f"value{index}"],
                func.min(ranked.c[f"label{index}"]).label(f"label{index}"),
            )
            .where(ranked.c[f"rank{index}"] <= limit)
            .group_by(ranked.c[f"kind{index}"], ranked.c[f"value{index}"])
            .cte(f"compact_axis{index}")
            for index in range(2)
        ]
        truncated = case(
            (
                or_(*[select(func.max(ranked.c[f"rank{index}"])).scalar_subquery() > limit for index in range(2)]),
                True,
            ),
            else_=False,
        ).label("truncated")
        bounded = select(ranked).where(ranked.c.rank0 <= limit, ranked.c.rank1 <= limit).cte("visible_cells")
        return self._matrix_output(axes=axes, cells=bounded, truncated=truncated)

    @classmethod
    def _matrix_output(cls, *, axes: list[CTE], cells: CTE, truncated: ColumnElement[Any]) -> CompoundSelect[Any]:
        """
        Put axis metadata and cell counts in one consistently shaped result stream.

        Returns:
            CompoundSelect[Any]: UNION ALL rows tagged ``row``, ``column``, or ``cell``.
                The truncation flag is repeated so no separate metadata query is needed.
        """
        records = [cls._axis_record(axes[index], index=index, truncated=truncated) for index in range(2)]
        records.append(
            select(
                literal("cell").label("record"),
                cells.c.kind0,
                cells.c.value0,
                cells.c.label0,
                cells.c.kind1,
                cells.c.value1,
                cells.c.label1,
                cells.c.counts,
                truncated,
            )
        )
        return union_all(*records).order_by("record", "kind0", "value0", "kind1", "value1")

    def facet(self, query: AttackAnalyticsFacetQuery) -> Select[Any]:
        """
        Select only the opened facet's keys, without computing outcome aggregates.

        The reader has already removed predicates for this exact dimension
        (including its label key or converter direction) from this compiler's
        filters. Scalar facets skip profile construction; array facets deduplicate
        memberships without outcome weights. Search matches literal display text.

        Args:
            query (AttackAnalyticsFacetQuery): Dimension, search text, offset, and limit.

        Returns:
            Select[Any]: A bounded, searchable facet page.
        """
        sources = [self._source(query.dimension)]
        if not sources[0].array:
            return self._scalar_facet(query=query, source=sources[0])
        profiles = self._profiles(sources, counts=False)
        members = self._memberships(profiles=profiles, sources=sources, dimensions=[0], name="facet_members")
        statement = select(
            members.c.kind0,
            members.c.value0,
            func.min(members.c.label0).label("label0"),
        )
        if query.search:
            statement = statement.where(self._search_condition(label=members.c.label0, search=query.search))
        return (
            statement.group_by(members.c.kind0, members.c.value0)
            .order_by(members.c.kind0, members.c.value0)
            .offset(query.offset)
            .limit(query.limit + 1)
        )

    def _scalar_facet(self, *, query: AttackAnalyticsFacetQuery, source: _Source) -> Select[Any]:
        """
        Read distinct scalar keys directly, avoiding profile and outcome aggregation.

        Returns:
            Select[Any]: A key-ordered page plus one lookahead row. Labels use binary
                MIN so case-insensitive database defaults do not choose arbitrary spellings.
        """
        conditions = self._conditions()
        kind, value, label = self._scalar_key(value=source.value, label=source.label, insensitive=source.insensitive)
        origin = self._from
        if self.dialect == "mssql":
            origin, projected = self._grouping_projection(
                origin=origin,
                values=[kind.label("kind0"), value.label("value0"), label.label("label0")],
                name="facet_values",
            )
            kind, value, label = projected
        if query.search:
            conditions.append(self._search_condition(label=label, search=query.search))
        return (
            select(kind.label("kind0"), value.label("value0"), func.min(self._collate(label)).label("label0"))
            .select_from(origin)
            .where(*conditions)
            .group_by(kind, value)
            .order_by(kind, value)
            .offset(query.offset)
            .limit(query.limit + 1)
        )

    @staticmethod
    def _search_condition(*, label: ColumnElement[Any], search: str) -> ColumnElement[bool]:
        """
        Match literal display-text substrings with the backend's LOWER semantics.

        Escape LIKE wildcards and its escape character, including SQL Server's
        bracket character classes. The bound pattern remains data, never SQL text.

        Returns:
            ColumnElement[bool]: A case-insensitive substring predicate.
        """
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("[", "\\[")
        return func.lower(label).like(func.lower(literal(f"%{escaped}%", UnicodeText())), escape="\\")

    def results(self, *, limit: int, after: DecodedKeysetCursor | None = None) -> Select[Any]:
        """
        Select a page of metadata, never responses or scores.

        The seek uses descending (last-modified timestamp, result ID), not a
        conversation ID or an offset. The caller validates the cursor's filter
        fingerprint first. JSON columns keep their normal SQLAlchemy decoding;
        converter expressions remain serialized JSON for the reader to validate.

        Args:
            limit (int): Visible results; SQL fetches one additional row for has_more.
            after (DecodedKeysetCursor | None): Last visible timestamp/ID from the
                preceding page. Timestamps are converted to the database's naive UTC.

        Returns:
            Select[Any]: A recency-ordered projection with a 200-character objective
                preview, not a full AttackResult or related object graph.
        """
        attack = self._source(AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.ATTACK_TYPE))
        target = self._source(AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.OBJECTIVE_TARGET))
        model = self._source(AttackAnalyticsDimension(name=AttackAnalyticsDimensionName.MODEL))
        request = self._converter_source(direction=AttackAnalyticsConverterDirection.REQUEST)
        response = self._converter_source(direction=AttackAnalyticsConverterDirection.RESPONSE)
        conditions = self._conditions()
        if after is not None:
            timestamp = after.timestamp.astimezone(UTC).replace(tzinfo=None)
            conditions.append(
                or_(
                    self.root.c.timestamp < timestamp,
                    and_(self.root.c.timestamp == timestamp, self.root.c.id < after.identifier),
                )
            )
        return (
            select(
                self.root.c.id.label("attack_result_id"),
                func.substr(self.root.c.objective, 1, 200).label("objective_preview")
                if self.dialect == "sqlite"
                else func.substring(self.root.c.objective, 1, 200).label("objective_preview"),
                self.root.c.outcome,
                self.root.c.timestamp.label("updated_at"),
                self.root.c.operation,
                self.root.c.operator,
                attack.label.label("attack_type"),
                model.label.label("target_model"),
                target.value.label("target_identifier_hash"),
                self.root.c.attribution_parent_id.label("scenario_result_id"),
                self.root.c.targeted_harm_categories,
                request.value.label("request_converters"),
                response.value.label("response_converters"),
                self.root.c.labels,
            )
            .select_from(self._from)
            .where(*conditions)
            .order_by(self.root.c.timestamp.desc(), self.root.c.id.desc())
            .limit(limit + 1)
        )

    def _source(self, dimension: AttackAnalyticsDimension) -> _Source:
        """
        Reuse one dimension's expressions and add its joins only once per compiler.

        Returns:
            _Source: Metadata expressions keyed by the complete dimension, so different
                label keys and request/response converters do not accidentally share joins.
        """
        key = dimension.model_dump_json()
        if key not in self._sources:
            self._sources[key] = self._create_source(dimension=dimension)
        return self._sources[key]

    def _create_source(self, *, dimension: AttackAnalyticsDimension, embedded: bool = True) -> _Source:
        """
        Map a validated dimension to its stored key, display text, and membership rules.

        Operation, operator, labels, model names, and identifier hashes retain exact
        spelling. Attack types, harm categories, and converter types fold case.
        Scenario keys use the persisted UUID, not a possibly non-unique scenario
        name; target keys likewise use the hash rather than the model label.

        Args:
            dimension (AttackAnalyticsDimension): Metadata axis to resolve.
            embedded (bool): Include fallbacks from the saved result's identifier JSON.

        Returns:
            _Source: A scalar or JSON-array projection. Looking it up may extend
                ``_from``, but never executes SQL or loads a component instance.
        """
        name = dimension.name
        if name in {AttackAnalyticsDimensionName.OPERATION, AttackAnalyticsDimensionName.OPERATOR}:
            value = self.root.c[name.value]
            return _Source(value=value, label=value)
        if name is AttackAnalyticsDimensionName.LABEL:
            projected = self._label_columns.get(dimension.label_key or "")
            value = (
                self.root.c[projected]
                if projected is not None
                else JsonScalar(self.root.c.labels, f"$.{json.dumps(dimension.label_key)}")
            )
            return _Source(value=value, label=value)
        if name is AttackAnalyticsDimensionName.TARGETED_HARM_CATEGORY:
            value = cast(self.root.c.targeted_harm_categories, UnicodeText())
            return _Source(value=value, label=value, array=True, insensitive=True)
        if name is AttackAnalyticsDimensionName.SCENARIO:
            scenario = ScenarioResultEntry.__table__.alias("analytics_scenario")
            self._from = self._from.outerjoin(scenario, scenario.c.id == self.root.c.attribution_parent_id)
            value = func.lower(cast(self.root.c.attribution_parent_id, Unicode(36)))
            return _Source(value=value, label=func.coalesce(scenario.c.scenario_name, value))
        self._ensure_identifiers()
        if name is AttackAnalyticsDimensionName.CONVERTER_TYPE:
            return self._converter_source(direction=dimension.converter_direction, embedded=embedded)
        if name is AttackAnalyticsDimensionName.ATTACK_TYPE:
            value = func.coalesce(self.attack.c.class_name, self._attack_property(path="class_name", embedded=embedded))
            return _Source(value=value, label=value, insensitive=True)
        if name is AttackAnalyticsDimensionName.OBJECTIVE_TARGET:
            value = func.coalesce(self.target.c.hash, self._target_property(path="hash", embedded=embedded))
            label = func.coalesce(
                self.target.c.model_name,
                self._target_property(path="params.model_name", embedded=embedded),
                self.target.c.class_name,
                self._target_property(path="class_name", embedded=embedded),
                value,
            )
            return _Source(value=value, label=label)
        value = func.coalesce(
            self.target.c.model_name,
            self.target.c.underlying_model_name,
            self._target_property(path="params.model_name", embedded=embedded),
            self._target_property(path="params.underlying_model_name", embedded=embedded),
        )
        return _Source(value=value, label=value)

    def _ensure_identifiers(self) -> None:
        """
        Attach the normalized identifier chain using outer, one-to-one hash joins.

        Missing projections must not remove a saved result from the cohort. JSON
        fallbacks resolve its metadata later. These aliases belong to this root,
        including when the root is already weighted facts rather than result rows.
        """
        if self._identifiers_ready:
            return
        self._from = (
            self._from.outerjoin(self.atomic, self.atomic.c.hash == self._atomic_hash)
            .outerjoin(self.technique, self.technique.c.hash == self.atomic.c.attack_technique_identifier_hash)
            .outerjoin(self.attack, self.attack.c.hash == self.technique.c.attack_identifier_hash)
            .outerjoin(self.target, self.target.c.hash == self.attack.c.objective_target_hash)
        )
        self._identifiers_ready = True

    @staticmethod
    def _identifier_property(*, document: ColumnElement[Any], path: str) -> ColumnElement[Any]:
        """
        Read a property, accepting ``__type__`` only when ``class_name`` is absent.

        Returns:
            ColumnElement[Any]: The canonical value, including JSON null, or the legacy type.
        """
        canonical = JsonScalar(document, f"$.{path}")
        if path.rsplit(".", 1)[-1] != "class_name":
            return canonical
        parent = path.rpartition(".")[0]
        object_path = f"$.{parent}" if parent else "$"
        return case(
            (JsonClassNamePresent(document, object_path) == true(), canonical),
            else_=JsonScalar(document, f"$.{path[: -len('class_name')]}__type__"),
        )

    def _attack_property(self, *, path: str, embedded: bool = True) -> ColumnElement[Any]:
        """
        Resolve an attack scalar across normalized and historical identifier layouts.

        Args:
            path (str): Internal property path relative to the attack identifier.
            embedded (bool): Include both historical saved-result layouts.

        Returns:
            ColumnElement[Any]: The first non-NULL value from normalized identifier JSON,
                the technique-wrapped legacy layout, or the older direct attack child.
                A real blank string is not replaced by a fallback.
        """
        properties = [self._identifier_property(document=self.attack.c.identifier_json, path=path)]
        if embedded:
            properties.extend(
                [
                    self._identifier_property(
                        document=self.root.c.atomic_attack_identifier,
                        path=f"children.attack_technique.children.attack.{path}",
                    ),
                    self._identifier_property(
                        document=self.root.c.atomic_attack_identifier, path=f"children.attack.{path}"
                    ),
                ]
            )
        return func.coalesce(*properties) if embedded else properties[0]

    def _target_property(self, *, path: str, embedded: bool = True) -> ColumnElement[Any]:
        """
        Read promoted target properties and their older ``params`` representation.

        Returns:
            ColumnElement[Any]: A target scalar, preferring normalized JSON and then
                the embedded objective-target child in either historical attack layout.
        """
        canonical_path = path.removeprefix("params.")
        properties = [
            self._identifier_property(document=self.target.c.identifier_json, path=canonical_path),
            self._identifier_property(document=self.target.c.identifier_json, path=path),
        ]
        if embedded:
            properties.extend(
                [
                    self._attack_property(path=f"children.objective_target.{canonical_path}"),
                    self._attack_property(path=f"children.objective_target.{path}"),
                ]
            )
        return func.coalesce(*properties)

    def _converter_source(self, *, direction: AttackAnalyticsConverterDirection, embedded: bool = True) -> _Source:
        """
        Collapse converter edges to one JSON array per attack before joining results.

        Joining edges directly would multiply saved-result counts. Deduplicate
        names here, then fold case and deduplicate memberships after expansion.
        A retained converter list takes precedence: migration backfills can have
        fewer edges than recorded members when a child has no usable hash.
        Edges remain a fallback for identifiers without a retained converter list.
        A recorded [] means no converters, unlike absent identifier metadata.

        Args:
            direction (AttackAnalyticsConverterDirection): Request or response pipeline;
                each has independent edges, aliases, and fallback properties.
            embedded (bool): Include the saved result's converter list.

        Returns:
            _Source: An array of normalized names or legacy converter objects. These
                CTEs aggregate identifier edges, not conversations, scores, or result objects.
        """
        self._ensure_identifiers()
        if direction not in self._converter_arrays:
            table = (
                AttackRequestConverterIdentifierEntry.__table__
                if direction is AttackAnalyticsConverterDirection.REQUEST
                else AttackResponseConverterIdentifierEntry.__table__
            )
            edge = table.alias(f"analytics_{direction.value}_edge")
            converter = ConverterIdentifierEntry.__table__.alias(f"analytics_{direction.value}_converter")
            canonical_name: ColumnElement[Any] = func.coalesce(
                converter.c.class_name, JsonScalar(converter.c.identifier_json, "$.class_name")
            )
            if self.dialect == "mssql":
                canonical_name = self._collate(canonical_name)
            identified = (
                select(
                    edge.c.attack_identifier_hash,
                    canonical_name.label("class_name"),
                    JsonScalar(converter.c.identifier_json, "$.__type__").label("legacy_class_name"),
                    JsonClassNamePresent(converter.c.identifier_json, "$").label("canonical_present"),
                )
                .select_from(edge.outerjoin(converter, converter.c.hash == edge.c.converter_identifier_hash))
                .cte(f"{self.root.name}_{direction.value}_converter_identified")
            )
            converter_name = case(
                (identified.c.class_name.isnot(None), identified.c.class_name),
                (identified.c.canonical_present == true(), cast(null(), UnicodeText())),
                else_=identified.c.legacy_class_name,
            )
            if self.dialect == "mssql":
                converter_name = self._collate(converter_name)
            names = (
                select(
                    identified.c.attack_identifier_hash,
                    converter_name.label("class_name"),
                )
                .distinct()
                .cte(f"{self.root.name}_{direction.value}_converter_names")
            )
            arrays = (
                select(
                    names.c.attack_identifier_hash,
                    JsonArrayAggregate(names.c.class_name).label("names"),
                )
                .group_by(names.c.attack_identifier_hash)
                .cte(f"{self.root.name}_{direction.value}_converter_arrays")
            )
            self._from = self._from.outerjoin(arrays, arrays.c.attack_identifier_hash == self.attack.c.hash)
            path = f"children.{direction.value}_converters"
            retained = JsonContainer(self.attack.c.identifier_json, f"$.{path}")
            self._converter_arrays[direction] = (
                func.coalesce(
                    retained,
                    JsonContainer(
                        self.root.c.atomic_attack_identifier,
                        f"$.children.attack_technique.children.attack.{path}",
                    ),
                    JsonContainer(self.root.c.atomic_attack_identifier, f"$.children.attack.{path}"),
                    arrays.c.names,
                ),
                func.coalesce(retained, arrays.c.names),
            )
        value = self._converter_arrays[direction][0 if embedded else 1]
        return _Source(value=value, label=value, array=True, converters=True, insensitive=True)

    def _conditions(self) -> list[ColumnElement[bool]]:
        """
        Build the AND-combined cohort constraints and any joins they require.

        Outcome filters use saved outcomes. Timestamp bounds form a half-open
        interval over stored last-modified UTC times. Each dimension predicate
        stays separate: appending a drill-down must narrow an existing ANY filter,
        not replace it or merge its values into a broader OR.

        Returns:
            list[ColumnElement[bool]]: Predicates to pass together to WHERE. An empty
                list leaves the cohort unrestricted.
        """
        conditions: list[ColumnElement[bool]] = []
        if self.filters.outcomes:
            conditions.append(self.root.c.outcome.in_([value.value for value in self.filters.outcomes]))
        if self.filters.updated_after is not None:
            conditions.append(self.root.c.timestamp >= self.filters.updated_after.astimezone(UTC).replace(tzinfo=None))
        if self.filters.updated_before is not None:
            conditions.append(self.root.c.timestamp < self.filters.updated_before.astimezone(UTC).replace(tzinfo=None))
        for index, predicate in enumerate(self.filters.dimensions):
            conditions.append(self._predicate(predicate, index=index))
        return conditions

    def _predicate(self, predicate: AttackAnalyticsFilter, *, index: int) -> ColumnElement[bool]:
        """
        Match one dimension without multiplying the outer result rows.

        Scalar predicates are ANY-only by contract. Array predicates use a
        correlated EXISTS per requested key; converter ALL requires each key to
        exist, not one array element to equal several values. EXISTS references
        this compiler's source aliases, while ``index`` keeps repeated predicates'
        expanded-array aliases distinct.

        Args:
            predicate (AttackAnalyticsFilter): Validated values and ANY/ALL mode.
            index (int): Position in the outer AND-combined predicate list.

        Returns:
            ColumnElement[bool]: A scalar comparison or combination of membership
                tests. Missing metadata and a recorded empty pipeline remain distinct.
        """
        source = self._source(predicate.dimension)
        if not source.array:
            value = func.lower(source.value) if source.insensitive else source.value
            matches = []
            for option in predicate.values:
                if option.kind is AttackAnalyticsValueKind.MISSING:
                    matches.append(source.value.is_(None))
                else:
                    expected = literal(option.value, UnicodeText())
                    if source.insensitive:
                        expected = func.lower(expected)
                    matches.append(self._collate(value) == self._collate(expected))
            return or_(*matches)
        items = JsonArrayItems(source.value).table_valued(column("key"), column("value"), column("type"))
        items = items.alias(f"filter_items_{index}")
        kind, value, _ = self._array_key(source=source, raw=source.value, items=items)
        matches = []
        for option in predicate.values:
            if option.kind is AttackAnalyticsValueKind.NO_CONVERTERS:
                matches.append(JsonArrayEmpty(source.value) == true())
            elif option.kind is AttackAnalyticsValueKind.MISSING:
                matches.append(
                    or_(
                        source.value.is_(None),
                        source.value == "null",
                        JsonArrayEmpty(source.value) == true() if not source.converters else literal(False),
                        select(1).select_from(items).where(kind == "missing").exists(),
                    )
                )
            else:
                matches.append(
                    select(1)
                    .select_from(items)
                    .where(self._key_condition(kind=kind, value=value, option=option, source=source))
                    .exists()
                )
        return and_(*matches) if predicate.match_mode is AttackAnalyticsMatchMode.ALL else or_(*matches)

    def _key_condition(
        self,
        *,
        kind: ColumnElement[Any],
        value: ColumnElement[Any],
        option: AttackAnalyticsValue,
        source: _Source,
    ) -> ColumnElement[bool]:
        """
        Compare both the key's type and its exact, optionally case-folded text.

        Returns:
            ColumnElement[bool]: A membership test that cannot confuse missing or
                no-converters keys with literal ``Unknown``, blank, or display-label text.
        """
        if option.kind is not AttackAnalyticsValueKind.VALUE:
            return kind == option.kind.value
        expected = literal(option.value, UnicodeText())
        if source.insensitive:
            expected = func.lower(expected)
        return and_(kind == "value", self._collate(value) == self._collate(expected))

    def _collate(self, value: ColumnElement[Any]) -> ColumnElement[Any]:
        """
        Make key equality independent of a database's default case-insensitive collation.

        Returns:
            ColumnElement[Any]: A binary-collated expression. Deliberate case folding
                happens separately, with the engine's LOWER semantics.
        """
        return value.collate("BINARY" if self.dialect == "sqlite" else "Latin1_General_100_BIN2")

    def _grouping_projection(
        self, *, origin: FromClause, values: list[ColumnElement[Any]], name: str
    ) -> tuple[FromClause, list[ColumnElement[Any]]]:
        """
        Give SQL Server's full-width JSON scalar reads columns that can be grouped.

        OPENJSON scalar extraction is a subquery; SQL Server forbids subqueries
        directly in GROUP BY or aggregate arguments. Project only affected
        expressions through a one-row lateral SELECT first. Explicitly correlate
        every source: an accidental FROM inside this SELECT would independently
        scan result/identifier tables and multiply the outer rows.
        Leave unaffected constants outside APPLY so an ungrouped NULL does not
        become an ungrouped column reference.

        Args:
            origin (FromClause): Outer row source containing all referenced columns.
            values (list[ColumnElement[Any]]): Named non-aggregate projections.
            name (str): Statement-local alias for the projected columns.

        Returns:
            tuple[FromClause, list[ColumnElement[Any]]]: Joined source and replacement
                columns in the same order. SQLite and scalar-free expressions are unchanged.
        """
        if self.dialect != "mssql":
            return origin, values
        json_values = [
            value for value in values if any(isinstance(node, JsonScalar) for node in visitors.iterate(value))
        ]
        if not json_values:
            return origin, values
        projected = select(*json_values).correlate_except().lateral(name)
        return JsonArrayJoin(origin, projected, true(), isouter=True), [
            projected.c.get(value.key, value) if value.key is not None else value for value in values
        ]

    def _same_metadata(self, *, original: ColumnElement[Any], compacted: ColumnElement[Any]) -> ColumnElement[bool]:
        """
        Compare nullable metadata without discarding rows on SQL UNKNOWN.

        Returns:
            ColumnElement[bool]: True only when the raw key or label survives compaction.
        """
        return or_(
            and_(original.is_(None), compacted.is_(None)),
            and_(
                original.isnot(None),
                compacted.isnot(None),
                self._collate(original) == self._collate(compacted),
            ),
        )

    def _complete_identifier_source(self, *, dimension: AttackAnalyticsDimension) -> ColumnElement[bool]:
        """
        Check that removing a result's embedded identifier preserves this axis.

        Returns:
            ColumnElement[bool]: A completeness predicate for the requested axis.
        """
        if dimension.name is AttackAnalyticsDimensionName.CONVERTER_TYPE:
            self._ensure_identifiers()
            path = f"children.{dimension.converter_direction.value}_converters"
            retained = JsonContainer(self.attack.c.identifier_json, f"$.{path}")
            wrapped = JsonContainer(
                self.root.c.atomic_attack_identifier, f"$.children.attack_technique.children.attack.{path}"
            )
            direct = JsonContainer(self.root.c.atomic_attack_identifier, f"$.children.attack.{path}")
            return or_(retained.isnot(None), and_(wrapped.is_(None), direct.is_(None)))
        original = self._create_source(dimension=dimension)
        compacted = self._create_source(dimension=dimension, embedded=False)
        checks = [self._same_metadata(original=original.value, compacted=compacted.value)]
        if original.value is not original.label:
            checks.append(self._same_metadata(original=original.label, compacted=compacted.label))
        return and_(*checks)

    def _group_compiler(self, dimensions: list[AttackAnalyticsDimension]) -> AttackAnalyticsQueryCompiler:
        """
        Compact filtered result rows before resolving repeated identifier metadata.

        The input grain is one saved result ID. Apply all cohort filters first,
        then build ``analytics_facts`` at the grain of resolved atomic hash,
        outcome, and only the result-level values needed by the requested axes.
        ``analytics_weight`` counts the result IDs represented by each fact; later
        stages must sum that weight, never count fact rows.

        Three disjoint UNION ALL branches preserve both speed and legacy metadata:

        * Identifier chains whose requested metadata is unchanged without
          embedded JSON use indexed result columns and project NULL.
        * Missing hashes retain their embedded identifier JSON.
        * Non-NULL hashes whose normalized chain is incomplete also retain JSON.

        "Complete" means every requested identifier key and display label survives
        removal of embedded JSON. Converter lists must be retained on the attack
        row or absent from the result; edges alone can omit hashless legacy members.
        Promoted columns and retained JSON can each supply normalized metadata.
        Legacy facts also group by their retained document, so unrelated missing
        or unresolved identifiers cannot collapse into one metadata value.
        Text retains binary collation; native scenario UUIDs do not take collation.

        The continuation compiler receives no filters: its root is already the
        filtered cohort, and columns needed only for filtering were deliberately
        not retained. It resolves metadata, forms profiles, expands memberships,
        and sums weights. Queries without identifier axes skip this extra stage;
        roots that already carry weights must not be compacted again.

        Args:
            dimensions (list[AttackAnalyticsDimension]): One group axis or both matrix axes.

        Returns:
            AttackAnalyticsQueryCompiler: This compiler when compaction is unnecessary,
                otherwise a compiler rooted in weighted facts with literal label projections.
        """
        if "analytics_weight" in self.root.c or not any(
            dimension.name in self._IDENTIFIER_DIMENSIONS for dimension in dimensions
        ):
            return self
        base = AttackAnalyticsQueryCompiler(dialect=self.dialect, filters=self.filters)
        conditions = base._conditions()
        valid_atomic = (
            select(AtomicAttackIdentifierEntry.hash)
            .join(
                AttackTechniqueIdentifierEntry,
                AttackTechniqueIdentifierEntry.hash == AtomicAttackIdentifierEntry.attack_technique_identifier_hash,
            )
            .join(
                AttackIdentifierEntry,
                AttackIdentifierEntry.hash == AttackTechniqueIdentifierEntry.attack_identifier_hash,
            )
        )
        if any(
            dimension.name
            in {
                AttackAnalyticsDimensionName.OBJECTIVE_TARGET,
                AttackAnalyticsDimensionName.MODEL,
            }
            for dimension in dimensions
        ):
            valid_atomic = valid_atomic.join(
                TargetIdentifierEntry,
                TargetIdentifierEntry.hash == AttackIdentifierEntry.objective_target_hash,
            )
        values: dict[str, ColumnElement[Any]] = {
            "atomic_attack_identifier_hash": base._atomic_hash,
            "outcome": base.root.c.outcome,
            "atomic_attack_identifier": cast(null(), UnicodeText()),
        }
        label_columns: dict[str, str] = {}
        for index, dimension in enumerate(dimensions):
            if dimension.name in {
                AttackAnalyticsDimensionName.OPERATION,
                AttackAnalyticsDimensionName.OPERATOR,
            }:
                values[dimension.name.value] = base.root.c[dimension.name.value]
            elif dimension.name is AttackAnalyticsDimensionName.TARGETED_HARM_CATEGORY:
                values["targeted_harm_categories"] = base.root.c.targeted_harm_categories
            elif dimension.name is AttackAnalyticsDimensionName.SCENARIO:
                values["attribution_parent_id"] = base.root.c.attribution_parent_id
            elif dimension.name is AttackAnalyticsDimensionName.LABEL and dimension.label_key is not None:
                name = f"analytics_label_{index}"
                values[name] = JsonScalar(base.root.c.labels, f"$.{json.dumps(dimension.label_key)}")
                label_columns[dimension.label_key] = name
        if self.dialect == "mssql":
            values = {
                name: value if isinstance(value.type, CustomUUID) else self._collate(value)
                for name, value in values.items()
            }
        complete = and_(
            base._atomic_hash.in_(valid_atomic),
            *[
                base._complete_identifier_source(dimension=dimension)
                for dimension in dimensions
                if dimension.name in self._IDENTIFIER_DIMENSIONS
            ],
        )
        modern_origin, modern_values = self._grouping_projection(
            origin=base._from,
            values=[expression.label(name) for name, expression in values.items()],
            name="modern_fact_values",
        )
        modern = (
            select(
                *modern_values,
                func.count().label("analytics_weight"),
            )
            .select_from(modern_origin)
            .where(*conditions, complete)
            .group_by(*[value for value in modern_values if value.key != "atomic_attack_identifier"])
        )
        values["atomic_attack_identifier"] = self._grouping_text(base.root.c.atomic_attack_identifier)
        legacy_origin, legacy_values = self._grouping_projection(
            origin=base._from,
            values=[expression.label(name) for name, expression in values.items()],
            name="legacy_fact_values",
        )
        legacy = (
            select(
                *legacy_values,
                func.count().label("analytics_weight"),
            )
            .select_from(legacy_origin)
            .where(*conditions)
            .group_by(*legacy_values)
        )
        facts = union_all(
            modern,
            legacy.where(base._atomic_hash.is_(None)),
            legacy.where(base._atomic_hash.isnot(None), ~complete),
        ).cte("analytics_facts")
        return AttackAnalyticsQueryCompiler(
            dialect=self.dialect, filters=AttackAnalyticsFilters(), root=facts, label_columns=label_columns
        )

    def _profiles(self, sources: list[_Source], *, counts: bool = True) -> CTE:
        """
        Coalesce equal raw source tuples before expanding any array memberships.

        Args:
            sources (list[_Source]): Axis expressions, including scalar display labels.
            counts (bool): Include outcome and weight for charts. False selects only
                distinct raw metadata for facets, with no outcome aggregation.

        Returns:
            CTE: One row per raw source/display tuple and outcome, with ``weight``
                equal to COUNT(result rows) or SUM(fact weights). Source values are
                text or SQL NULL; JSON arrays are still serialized, not hydrated.
                Exact raw spellings must survive this stage even if keys later fold case.
        """
        conditions = self._conditions()
        values: list[ColumnElement[Any]] = []
        for index, source in enumerate(sources):
            values.append(self._grouping_text(source.value).label(f"source{index}"))
            if not source.array:
                values.append(self._grouping_text(source.label).label(f"display{index}"))
        origin, values = self._grouping_projection(origin=self._from, values=values, name="profile_values")
        if not counts:
            return select(*values).select_from(origin).where(*conditions).distinct().cte("analytics_profiles")
        weight = func.sum(self.root.c.analytics_weight) if "analytics_weight" in self.root.c else func.count()
        return (
            select(*values, self.root.c.outcome, weight.label("weight"))
            .select_from(origin)
            .where(*conditions)
            .group_by(*values, self.root.c.outcome)
            .cte("analytics_profiles")
        )

    def _grouping_text(self, value: ColumnElement[Any]) -> ColumnElement[Any]:
        """
        Preserve exact metadata through text-based profile and legacy-document grouping.

        SQL Server's default collation can otherwise merge differently cased
        model names, labels, or JSON documents before typed keys are constructed.
        SQLite already uses binary text grouping; leave its indexed path unchanged.

        Returns:
            ColumnElement[Any]: A wide text expression with explicit binary collation
                on SQL Server, without truncating metadata to an arbitrary cast length.
        """
        value = cast(value, UnicodeText())
        return self._collate(value) if self.dialect == "mssql" else value

    def _memberships(self, *, profiles: CTE, sources: list[_Source], dimensions: list[int], name: str) -> CTE:
        """
        Produce one membership per profile and selected typed-key tuple.

        Outer array expansion retains empty/missing sources. Grouping by the
        original profile columns plus derived keys removes repeated members,
        including case variants, without merging different profiles or changing
        their weights. A multi-valued result can belong to several groups/cells,
        but contributes at most once to any particular group/cell.

        Args:
            profiles (CTE): Pre-counted chart profiles or distinct unweighted facet profiles.
            sources (list[_Source]): Semantics for every projected source.
            dimensions (list[int]): Source positions to expand: one axis or both cell axes.
            name (str): Statement-local CTE/array-alias prefix.

        Returns:
            CTE: Original profile columns plus kind/value/label columns per requested
                axis. Labels use binary MIN to choose a stable spelling for equal keys.
        """
        origin: FromClause = profiles
        values = list(profiles.c)
        raw_values: list[ColumnElement[Any]] = list(profiles.c)
        keys: list[ColumnElement[Any]] = list(profiles.c)
        overlapping = any(sources[index].array for index in dimensions)
        for index in dimensions:
            source = sources[index]
            raw = profiles.c[f"source{index}"]
            if source.array:
                items = JsonArrayItems(raw).table_valued(column("key"), column("value"), column("type"))
                items = items.alias(f"{name}_items{index}")
                origin = JsonArrayJoin(origin, items, true(), isouter=True)
                kind, value, label = self._array_key(source=source, raw=raw, items=items)
            else:
                kind, value, label = self._scalar_key(
                    value=raw, label=profiles.c[f"display{index}"], insensitive=source.insensitive
                )
            keys.extend([kind, value])
            member_keys = [kind.label(f"kind{index}"), value.label(f"value{index}")]
            values.extend(
                [
                    *member_keys,
                    (func.min(label) if overlapping else label).label(f"label{index}"),
                ]
            )
            raw_values.extend([*member_keys, label.label(f"label{index}")])
        if overlapping and self.dialect == "mssql":
            origin, projected = self._grouping_projection(origin=origin, values=raw_values, name=f"{name}_values")
            label_names = {f"label{index}" for index in dimensions}
            keys = [value for value in projected if value.key not in label_names]
            values = [func.min(value).label(value.key) if value.key in label_names else value for value in projected]
        statement = select(*values).select_from(origin)
        if overlapping:
            statement = statement.group_by(*keys)
        return statement.cte(name)

    def _scalar_key(
        self, *, value: ColumnElement[Any], label: ColumnElement[Any], insensitive: bool = False
    ) -> tuple[ColumnElement[Any], ColumnElement[Any], ColumnElement[Any]]:
        """
        Encode scalar absence separately from the non-NULL text used for grouping and joins.

        Returns:
            tuple[ColumnElement[Any], ColumnElement[Any], ColumnElement[Any]]: Kind,
                binary-collated key text, and display label. Only SQL NULL is missing:
                an actual blank string or literal ``Unknown`` remains a value.
        """
        key = func.lower(value) if insensitive else value
        return case((value.is_(None), "missing"), else_="value"), self._collate(func.coalesce(key, "")), label

    def _array_key(
        self, *, source: _Source, raw: ColumnElement[Any], items: FromClause
    ) -> tuple[ColumnElement[Any], ColumnElement[Any], ColumnElement[Any]]:
        """
        Classify an expanded member while preserving its parent array's absence state.

        Converter arrays accept canonical names and legacy objects with
        ``class_name`` or ``__type__``; harm arrays accept strings. JSON null
        members represent missing metadata.
        Empty harm arrays are missing, while an empty converter array is explicitly
        no-converters. Wrong array shapes or outer member types receive an invalid
        kind that the reader rejects. Legacy object properties are expected to
        satisfy the identifier contract (a string or missing class_name).

        Args:
            source (_Source): Array rules and case-folding policy.
            raw (ColumnElement[Any]): Original serialized array, needed even for an
                outer-join row with no member.
            items (FromClause): Correlated expansion exposing key, value, and type.
                JSON1 uses textual type names; OPENJSON uses numeric type codes.

        Returns:
            tuple[ColumnElement[Any], ColumnElement[Any], ColumnElement[Any]]: Kind,
                binary key text, and binary-collated display text. Absence keys use
                empty text internally but remain disjoint from real blank values.
        """
        object_type = "object" if self.dialect == "sqlite" else 5
        string_type = "text" if self.dialect == "sqlite" else 1
        label = cast(items.c.value, UnicodeText())
        if source.converters:
            document = case((items.c.type == object_type, items.c.value), else_="{}")
            label = case(
                (items.c.type == object_type, self._identifier_property(document=document, path="class_name")),
                else_=label,
            )
        valid_types = [string_type, object_type] if source.converters else [string_type]
        absent = or_(raw.is_(None), raw == "null")
        kind = case(
            (absent, "missing"),
            (JsonArrayEmpty(raw) == true(), "no_converters" if source.converters else "missing"),
            (JsonIsArray(raw) != true(), "invalid"),
            (label.is_(None), "missing"),
            (~items.c.type.in_(valid_types), "invalid"),
            else_="value",
        )
        value = func.lower(label) if source.insensitive else label
        value = func.coalesce(value, "")
        return kind, self._collate(value), self._collate(label)

    def _axis(self, *, profiles: CTE, sources: list[_Source], index: int, limit: int) -> CTE:
        """
        Discover one axis independently before bounding high-cardinality cell combinations.

        Returns:
            CTE: Distinct typed keys in binary order, normally including one extra key
                for truncation detection. Axis selection does not rank by outcome counts.
        """
        members = self._memberships(profiles=profiles, sources=sources, dimensions=[index], name=f"axis{index}_members")
        return (
            select(
                members.c[f"kind{index}"],
                members.c[f"value{index}"],
                func.min(members.c[f"label{index}"]).label(f"label{index}"),
            )
            .group_by(members.c[f"kind{index}"], members.c[f"value{index}"])
            .order_by(members.c[f"kind{index}"], members.c[f"value{index}"])
            .limit(limit)
            .cte(f"axis{index}_candidates")
        )

    @staticmethod
    def _aggregate(members: CTE, *, dimensions: list[int]) -> CTE:
        """
        Sum deduplicated membership weights by key and outcome, then serialize counts.

        Returns:
            CTE: One row per group or cell with a raw outcome/count JSON object and
                total weight. The intermediate outcome grouping gives the JSON object
                unique keys; no success-rate or decided-denominator calculation occurs here.
        """
        keys = [members.c[f"{field}{index}"] for index in dimensions for field in ("kind", "value")]
        labels = [func.min(members.c[f"label{index}"]).label(f"label{index}") for index in dimensions]
        buckets = (
            select(*keys, *labels, members.c.outcome, func.sum(members.c.weight).label("count"))
            .group_by(*keys, members.c.outcome)
            .cte(f"{members.name}_outcomes")
        )
        keys = [buckets.c[f"{field}{index}"] for index in dimensions for field in ("kind", "value")]
        return (
            select(
                *keys,
                *[func.min(buckets.c[f"label{index}"]).label(f"label{index}") for index in dimensions],
                JsonObjectAggregate(buckets.c.outcome, buckets.c.count).label("counts"),
                func.sum(buckets.c.count).label("total"),
            )
            .group_by(*keys)
            .cte(f"{members.name}_counts")
        )

    @staticmethod
    def _same_key(*, left: FromClause, right: FromClause, index: int) -> ColumnElement[bool]:
        """
        Join on a typed key without NULL equality or display-label comparisons.

        Returns:
            ColumnElement[bool]: Equality on kind and non-NULL value text. The kind
                prevents a missing member from joining a real empty-string member.
        """
        return and_(
            left.c[f"kind{index}"] == right.c[f"kind{index}"],
            left.c[f"value{index}"] == right.c[f"value{index}"],
        )

    @staticmethod
    def _axis_record(axis: CTE, *, index: int, truncated: ColumnElement[Any]) -> Select[Any]:
        """
        Project an axis into the shared matrix row shape, filling the other axis with NULL.

        Returns:
            Select[Any]: A row/column-tagged record with no counts. Explicit text types
                keep SQL Server's UNION from inferring incompatible types for absent fields.
        """
        empty = cast(null(), UnicodeText())
        return select(
            literal("row" if index == 0 else "column").label("record"),
            *[
                (axis.c[f"{field}{dimension}"] if dimension == index else empty).label(f"{field}{dimension}")
                for dimension in range(2)
                for field in ("kind", "value", "label")
            ],
            empty.label("counts"),
            truncated,
        )
