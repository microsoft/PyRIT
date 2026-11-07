# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Synthetic analytics workload and fixtures shared by local SQLite tests."""

from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import random
import sqlite3
import tempfile
import time
import uuid
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import patch

import pytest

from test_support.fixtures import use_sqlite_memory

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator, Mapping, Sequence

    import httpx

    from pyrit.backend.authentication_policy import AuthenticationPolicy
    from pyrit.backend.middleware.compatibility import CompatibilityAPI
    from pyrit.memory import SQLiteMemory
    from pyrit.models import (
        AtomicAttackIdentifier,
        AttackAnalyticsFacets,
        AttackAnalyticsReport,
        AttackAnalyticsResults,
    )


@dataclass
class RequestSample:
    """One attempted operation, including errors and operations that could not be sent."""

    seconds: float
    status: int | None
    error: str | None


@pytest.fixture
def benchmark_directory(tmp_path: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="analytics-benchmark-", dir=tmp_path) as temporary:
        directory = Path(temporary)
        (directory / "config.yaml").write_text(
            json.dumps(
                {
                    "memory_db_type": "sqlite",
                    "env_files": [],
                    "env_akv_ref": [],
                    "initializers": ["technique"],
                    "initialization_scripts": [],
                    "custom_initializers_source": str(directory / "initializers"),
                    "silent": True,
                }
            ),
            encoding="utf-8",
        )
        yield directory


@pytest.fixture
async def sqlite_instance(benchmark_directory: Path) -> AsyncGenerator[SQLiteMemory, None]:
    """Use file-backed SQLite for concurrent WAL readers; all files remain fixture-owned."""
    from pyrit.memory import SQLiteMemory
    from pyrit.memory.memory_models import Base

    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=benchmark_directory / "analytics.sqlite", skip_schema_migration=True)
    memory.results_path = str(benchmark_directory)
    memory.disable_embedding()
    try:
        async with await memory.get_session_async() as session:
            connection = await session.connection()
            assert (await connection.exec_driver_sql("PRAGMA journal_mode=WAL")).scalar_one() == "wal"
            await connection.run_sync(Base.metadata.create_all)
            await session.commit()
        with use_sqlite_memory(memory):
            yield memory
    finally:
        await memory.dispose_engine_async()


@pytest.fixture
async def benchmark_app(
    *,
    benchmark_directory: Path,
    sqlite_instance: SQLiteMemory,
    compatibility_id: str,
    authentication_policy: AuthenticationPolicy,
) -> AsyncGenerator[CompatibilityAPI, None]:
    """Exercise production routes, middleware, startup and shutdown with isolated test configuration."""
    from pyrit.backend.main import app as backend_app
    from pyrit.backend.middleware.compatibility import CompatibilityAPI
    from pyrit.backend.services.analytics_service import get_analytics_service
    from pyrit.backend.services.configuration_file_service import ConfigurationFileService
    from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle

    assert get_analytics_service.cache_info().currsize == 0
    with (
        patch.dict(
            os.environ,
            {"ENTRA_CLIENT_ID": "", "ENTRA_TENANT_ID": "", "ENTRA_ALLOWED_GROUP_IDS": "", "ENTRA_ADMIN_GROUP_ID": ""},
        ),
        patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH", benchmark_directory / "missing-default.yaml"),
    ):
        app = CompatibilityAPI(routes=list(backend_app.router.routes), middleware=backend_app.user_middleware)
        app.exception_handlers.update(backend_app.exception_handlers)
        app.state.compatibility_id = compatibility_id
        app.state.authentication_policy = authentication_policy
        source = ConfigurationFileService(config_file_value=str(benchmark_directory / "config.yaml"))
        app.state.configuration_file_service = source
        runtime = RuntimeLifecycle(app=app, source=source)
        app.state.runtime_lifecycle = runtime
        try:
            await runtime.startup_async()
            assert runtime.state == "ready", runtime.message
            yield app
        finally:
            await runtime.shutdown_async()
        assert get_analytics_service.cache_info().currsize == 0


def percentile95(values: Sequence[float]) -> float:
    """Return the nearest-rank p95 without rounding away a threshold failure."""
    if not values:
        raise ValueError("A required workload produced no samples")
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


class AnalyticsBenchmark:
    """Run independent HTTP callers, native async queries, and concurrent native writes."""

    LIMITS: ClassVar[dict[str, float]] = {
        "report_attack": 2.0,
        "report_converter": 2.0,
        "facet": 0.5,
        "page": 0.5,
        "continuation": 0.5,
    }
    PAGE_SIZE: ClassVar[int] = 25
    RANDOM_SEED: ClassVar[int] = 20260914

    def __init__(
        self,
        *,
        memory: SQLiteMemory,
        app: CompatibilityAPI,
        headers: Mapping[str, str],
        count: int,
    ) -> None:
        """Retain fixture-owned resources without creating, replacing, or disposing memory."""
        from importlib.metadata import version

        from pyrit import _compatibility

        self.environment = {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "processor": platform.processor(),
            "recorded_at": datetime.now(UTC).isoformat(),
            "packages": {
                name: version(name) for name in ("fastapi", "starlette", "pydantic", "SQLAlchemy", "aiosqlite", "httpx")
            },
            "journal_mode": "wal (explicitly set only on the new scratch database)",
            "compatibility_id": headers.get(_compatibility.COMPATIBILITY_HEADER),
        }
        self.count = count
        self.memory = memory
        self.app = app
        self.headers = dict(headers)
        self.samples: dict[str, list[RequestSample]] = {kind: [] for kind in self.LIMITS}
        self.cold: dict[str, RequestSample] = {}
        self.failures: list[str] = []
        self.write_samples: list[float] = []
        self.write_errors: list[str] = []
        self.verification: list[RequestSample] = []
        self.inserted = 0
        self.pending_insert = 0
        self.updates = 0
        self.expected_outcomes: Counter[str] = Counter()
        self.saved_outcomes: list[str] = []
        self.stop_writer = asyncio.Event()
        self.writer_started = asyncio.Event()
        self.active_requests = 0
        self.peak_requests = 0
        self.sql_statements = 0

    async def run_async(self, *, analysts: int, iterations: int) -> dict[str, Any]:
        """Seed and measure the workload; pytest fixtures own runtime and database cleanup."""
        from sqlalchemy import event

        await self._seed_async()
        engine = self.memory._get_async_engine().sync_engine
        event.listen(engine, "before_cursor_execute", self._record_statement)
        try:
            await self._cold_async()
            started = time.perf_counter()
            writer = asyncio.create_task(self._write_async())
            try:
                await self.writer_started.wait()
                await asyncio.gather(
                    *(self._analyst_async(index=index, iterations=iterations) for index in range(analysts))
                )
            finally:
                self.stop_writer.set()
                await writer
            elapsed = time.perf_counter() - started
            await self._verify_final_async()
            return self._measurements(analysts=analysts, iterations=iterations, elapsed=elapsed)
        finally:
            event.remove(engine, "before_cursor_execute", self._record_statement)

    async def _seed_async(self) -> None:
        from sqlalchemy import insert

        from pyrit.memory.analytics_identity_v1 import ObjectiveTargetAnalyticsIdentityV1
        from pyrit.memory.memory_models import AttackResultEntry
        from pyrit.models import AttackResultMetadata, AttackResultRole, Operation

        memory = self.memory
        self.operation_id = (await memory.add_operation_async(Operation(name="operation-000"))).id
        identifiers = self._identifiers()
        async with await self.memory.get_session_async() as session:
            for identifier in identifiers:
                await session.run_sync(
                    lambda sync_session, item=identifier: memory._persist_identifier(
                        session=sync_session, identifier=item
                    )
                )
            await session.commit()
        documents = [identifier.model_dump(mode="json") for identifier in identifiers]
        legacy = [self._legacy_document(document) for document in documents]
        frozen_keys = {
            id(document): ObjectiveTargetAnalyticsIdentityV1.from_atomic_document(document=document)
            for document in [*documents, *legacy]
        }
        roles = [AttackResultMetadata(result_role=role).to_metadata() for role in AttackResultRole]
        generator = random.Random(self.RANDOM_SEED)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        outcomes = ("success", "failure", "error", "undetermined")
        for offset in range(0, self.count, 2500):
            rows = []
            for index in range(offset, min(offset + 2500, self.count)):
                configuration = generator.randrange(len(identifiers))
                category = generator.randrange(40)
                outcome = outcomes[generator.randrange(4)]
                self.saved_outcomes.append(outcome)
                self.expected_outcomes[outcome] += 1
                document = (
                    None if index % 100 == 0 else legacy[configuration] if index % 10 == 0 else documents[configuration]
                )
                categories = [f"category-{(category + value) % 40:02}" for value in range(3)]
                if index % 11 == 0:
                    categories.append(categories[0])
                saved_operation = index < self.count * 0.6
                rows.append(
                    {
                        "id": uuid.UUID(int=index + 1),
                        "conversation_id": str(uuid.UUID(int=index // 2 + 1)),
                        "objective": f"Synthetic objective {index}. " + "Safe evaluation metadata. " * 6,
                        "outcome": outcome,
                        "timestamp": start + timedelta(seconds=index),
                        "operation": None if saved_operation else f"operation-{index % 100:03}",
                        "operation_id": self.operation_id if saved_operation else None,
                        "operator": f"operator-{index % 50:03}",
                        "labels": {"team": f"team-{index % 10}", "case": f"case-{index:06}"},
                        "targeted_harm_categories": categories,
                        "atomic_attack_identifier_hash": identifiers[configuration].hash if index % 10 else None,
                        "atomic_attack_identifier": document,
                        "objective_target_eval_hash_v1": frozen_keys[id(document)] if document is not None else None,
                        "attribution_data": roles[index % len(roles)],
                        "executed_turns": 0,
                        "execution_time_ms": 0,
                    }
                )
            async with await self.memory.get_session_async() as session:
                connection = await session.connection()
                await connection.execute(insert(AttackResultEntry), rows)
                await session.commit()

    @staticmethod
    def _identifiers() -> list[AtomicAttackIdentifier]:
        from pyrit.models import AtomicAttackIdentifier, AttackIdentifier, ConverterIdentifier, TargetIdentifier

        return [
            AtomicAttackIdentifier.build(
                attack_identifier=AttackIdentifier(
                    class_name=f"BenchmarkAttack{index % 16:02}",
                    class_module="benchmark",
                    params={"configuration": index},
                    objective_target=TargetIdentifier(
                        class_name="MockTarget", class_module="benchmark", model_name=f"model-{index % 8}"
                    ),
                    request_converters=[
                        ConverterIdentifier(
                            class_name="\u00d6mega" if value % 16 == 0 else f"BenchmarkConverter{value % 16:02}",
                            class_module="benchmark",
                        )
                        for value in (() if index % 16 == 0 else (index, index + 1))
                    ],
                    response_converters=[ConverterIdentifier(class_name="ResponseConverter", class_module="benchmark")],
                )
            )
            for index in range(64)
        ]

    @staticmethod
    def _legacy_document(document: dict[str, Any]) -> dict[str, Any]:
        """Keep supported historical names without a normalized identifier lookup."""
        attack = document["children"]["attack_technique"]["children"]["attack"]
        children = attack["children"]
        target = children["objective_target"]
        return {
            "children": {
                "attack": {
                    "__type__": attack["class_name"],
                    "children": {
                        "objective_target": {
                            "__type__": target["class_name"],
                            "model_name": target["model_name"],
                        },
                        "request_converters": [
                            {"__type__": converter["class_name"]} for converter in children["request_converters"]
                        ],
                        "response_converters": [
                            {"class_name": converter["class_name"], "__type__": "IgnoredLegacyName"}
                            for converter in children["response_converters"]
                        ],
                    },
                }
            }
        }

    def _client(self, analyst: str) -> httpx.AsyncClient:
        import httpx

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://benchmark",
            headers={**self.headers, "X-Request-ID": f"benchmark-{analyst}"},
        )

    async def _cold_async(self) -> None:
        async with self._client("cold") as client:
            previous = None
            for kind in self.LIMITS:
                sample, data = await self._request_async(client=client, kind=kind, previous=previous)
                self.cold[kind] = sample
                if kind == "page":
                    previous = data

    async def _analyst_async(self, *, index: int, iterations: int) -> None:
        async with self._client(str(index)) as client:
            for _ in range(iterations):
                previous = None
                for kind in self.LIMITS:
                    sample, data = await self._request_async(client=client, kind=kind, previous=previous)
                    self.samples[kind].append(sample)
                    if kind == "page":
                        previous = data

    async def _request_async(
        self, *, client: httpx.AsyncClient, kind: str, previous: Any = None
    ) -> tuple[RequestSample, Any]:
        import httpx

        from pyrit.models import AttackAnalyticsFacets, AttackAnalyticsReport, AttackAnalyticsResults

        started = time.perf_counter()
        status: int | None = None
        lower_count = self.count + self.inserted
        self.active_requests += 1
        self.peak_requests = max(self.peak_requests, self.active_requests)
        try:
            endpoint, body = self._request(kind=kind, previous=previous)
            response = await client.post(f"/api/analytics/attacks/{endpoint}", json=body)
            status = response.status_code
            if status != 200:
                raise ValueError(f"HTTP {status}: {response.text[:500]}")
            model = (
                AttackAnalyticsReport
                if endpoint == "query"
                else AttackAnalyticsFacets
                if endpoint == "facets"
                else AttackAnalyticsResults
            )
            wire = response.json()
            data = model.model_validate(wire)
            if endpoint == "query":
                statistics = [wire["summary"], *(item["statistics"] for item in wire["cells"])]
                if any(
                    "success_rate_decided" not in item or item["success_rate_decided"] != item["success_rate"]
                    for item in statistics
                ):
                    raise ValueError("The HTTP response must preserve the shared success-rate alias")
            self._validate(kind=kind, data=data, previous=previous, lower_count=lower_count)
            return RequestSample(time.perf_counter() - started, status, None), data
        except (httpx.HTTPError, ValueError, AssertionError, OSError) as error:
            return RequestSample(time.perf_counter() - started, status, f"{type(error).__name__}: {error}"), None
        finally:
            self.active_requests -= 1

    def _request(self, *, kind: str, previous: Any) -> tuple[str, dict[str, Any]]:
        if kind.startswith("report_"):
            return "query", {
                "group_by": {"name": "targeted_harm_category"},
                "compare_by": {"name": "attack_type" if kind == "report_attack" else "converter_type"},
                "result_limit": self.PAGE_SIZE,
            }
        if kind == "facet":
            return "facets", {
                "dimension": {"name": "label", "label_key": "case"},
                "search": "case-0",
                "limit": 50,
                "filters": {
                    "dimensions": [{"dimension": {"name": "operation"}, "values": [{"value": str(self.operation_id)}]}]
                },
            }
        body: dict[str, Any] = {"limit": self.PAGE_SIZE}
        if kind == "continuation":
            if previous is None or not previous.next_cursor:
                raise ValueError("A continuation must follow a successful first page's actual next_cursor")
            body["cursor"] = previous.next_cursor
        return "results", body

    def _validate(
        self,
        *,
        kind: str,
        data: AttackAnalyticsReport | AttackAnalyticsResults | AttackAnalyticsFacets,
        previous: Any,
        lower_count: int,
    ) -> None:
        from pyrit.models import AttackAnalyticsFacets, AttackAnalyticsReport

        if isinstance(data, AttackAnalyticsReport):
            if not lower_count <= data.summary.total_results <= self.count + self.inserted + self.pending_insert:
                raise ValueError("Report must count every saved result ID in a possible concurrent read view")
            if data.summary.success_rate_all is None or data.computed_at != data.results.computed_at:
                raise ValueError("Report must include both rates and a coherent initial page")
            if len(data.rows) > 20 or len(data.columns) > 20:
                raise ValueError("Report axes exceed their requested bounds")
            if len(data.cells) != len(data.rows) * len(data.columns) or len(data.cells) > 400:
                raise ValueError("Report must contain exactly the bounded axis cross product")
            if not data.groups_overlap or data.warnings:
                raise ValueError(f"Unexpected report metadata: overlap={data.groups_overlap}, warnings={data.warnings}")
            data = data.results
        if isinstance(data, AttackAnalyticsFacets):
            if len(data.items) != 50 or not data.has_more or data.next_offset != 50:
                raise ValueError("Facet must return a bounded, full page and its next offset")
            if len({item.key.value for item in data.items}) != 50:
                raise ValueError("Facet options must remain distinct")
        else:
            if len(data.items) != self.PAGE_SIZE or not data.has_more or not data.next_cursor:
                raise ValueError("Results must return a full page and a continuation cursor")
            ids = {item.attack_result_id for item in data.items}
            keys = [(item.updated_at, item.attack_result_id) for item in data.items]
            if len(ids) != self.PAGE_SIZE or keys != sorted(keys, reverse=True):
                raise ValueError("Result IDs must be distinct and in recency/UUID order")
            if kind == "continuation":
                if previous is None or not ids.isdisjoint(item.attack_result_id for item in previous.items):
                    raise ValueError("Continuation must be disjoint from its actual first page")
                anchor = previous.items[-1]
                if not all(key < (anchor.updated_at, anchor.attack_result_id) for key in keys):
                    raise ValueError("Continuation did not seek past the previous page's anchor")

    async def _write_async(self) -> None:
        from sqlalchemy import insert, update

        from pyrit.memory.memory_models import AttackResultEntry

        transaction = 0
        try:
            while not self.stop_writer.is_set():
                started = time.perf_counter()
                transaction += 1
                index = (transaction - 1) % self.count
                outcome = "success" if transaction % 2 else "failure"
                now = datetime.now(UTC)
                self.pending_insert = int(transaction % 2 == 0)
                async with await self.memory.get_session_async() as session:
                    connection = await session.connection()
                    updated = await connection.execute(
                        update(AttackResultEntry)
                        .where(AttackResultEntry.id == uuid.UUID(int=index + 1))
                        .values(outcome=outcome, timestamp=now)
                    )
                    if updated.rowcount != 1:
                        raise ValueError("The writer must update exactly one existing result")
                    if self.pending_insert:
                        await connection.execute(
                            insert(AttackResultEntry),
                            {
                                "id": uuid.UUID(int=self.count + self.inserted + 1),
                                "conversation_id": str(uuid.UUID(int=1)),
                                "objective": "Synthetic concurrent insert sharing a saved conversation",
                                "outcome": "undetermined",
                                "timestamp": now,
                                "executed_turns": 0,
                                "execution_time_ms": 0,
                            },
                        )
                    await session.commit()
                self.expected_outcomes[self.saved_outcomes[index]] -= 1
                self.expected_outcomes[outcome] += 1
                self.saved_outcomes[index] = outcome
                self.expected_outcomes["undetermined"] += self.pending_insert
                self.inserted += self.pending_insert
                self.pending_insert = 0
                self.updates += 1
                self.write_samples.append(time.perf_counter() - started)
                self.writer_started.set()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.stop_writer.wait(), timeout=0.1)
        except (Exception, asyncio.CancelledError) as error:
            self.write_errors.append(f"{type(error).__name__}: {error}")
            self.writer_started.set()
            if isinstance(error, asyncio.CancelledError):
                raise

    async def _verify_final_async(self) -> None:
        from sqlalchemy import func, select

        from pyrit.memory.memory_models import AttackResultEntry

        async with await self.memory.get_session_async() as session:
            rows = await session.execute(
                select(AttackResultEntry.outcome, func.count()).group_by(AttackResultEntry.outcome)
            )
            counts = dict(rows.all())
            conversations = (
                await session.execute(select(func.count(func.distinct(AttackResultEntry.conversation_id))))
            ).scalar_one()
        if counts != dict(self.expected_outcomes) or sum(counts.values()) != self.count + self.inserted:
            self.failures.append("Final SQL counts disagree with the exact seeded and committed write ledger")
        if conversations != math.ceil(self.count / 2):
            self.failures.append("The workload did not retain its distinct IDs sharing conversations")
        async with self._client("verification") as client:
            for kind in ("report_attack", "report_converter"):
                sample, report = await self._request_async(client=client, kind=kind)
                self.verification.append(sample)
                if sample.error:
                    self.failures.append(f"Final {kind}: {sample.error}")
                elif report is not None:
                    summary = report.summary
                    observed = {
                        "success": summary.successes,
                        "failure": summary.failures,
                        "error": summary.errors,
                        "undetermined": summary.undetermined,
                    }
                    if observed != counts:
                        self.failures.append(f"Final {kind} does not match exact SQL counts")

    def _measurements(self, *, analysts: int, iterations: int, elapsed: float) -> dict[str, Any]:
        measurements: dict[str, Any] = {}
        failures = [*self.failures, *self.write_errors]
        for kind, threshold in self.LIMITS.items():
            samples = self.samples[kind]
            if len(samples) != analysts * iterations:
                failures.append(f"{kind}: expected {analysts * iterations} samples, received {len(samples)}")
            p95 = percentile95([sample.seconds for sample in samples])
            failed = [sample for sample in [self.cold[kind], *samples] if sample.error]
            failures.extend(f"{kind}: {sample.error}" for sample in failed)
            if p95 > threshold:
                failures.append(f"{kind}: p95 {p95:.6f}s exceeds {threshold:.3f}s")
            measurements[kind] = {
                "cold_seconds": self.cold[kind].seconds,
                "p95_seconds": p95,
                "maximum_seconds": max(sample.seconds for sample in samples),
                "threshold_seconds": threshold,
                "requests": len(samples) + 1,
                "successful_requests": sum(
                    sample.status == 200 and sample.error is None for sample in [self.cold[kind], *samples]
                ),
                "error_requests": len(failed),
                "not_sent": sum(sample.status is None for sample in [self.cold[kind], *samples]),
                "samples_seconds": [sample.seconds for sample in samples],
            }
        if not self.updates or not self.inserted or not self.write_samples:
            failures.append("Concurrent updates and inserts must both commit during the workload")
        if self.peak_requests != analysts:
            failures.append(f"Expected {analysts} overlapping independent callers, observed {self.peak_requests}")
        return {
            "environment": self.environment,
            "timing": {
                "included": (
                    "HTTPX ASGI request encoding, full production middleware, admission/queueing, "
                    "native SDK/SQL/session cleanup, shared response serialization and client model validation"
                ),
                "excluded": (
                    "schema creation/seeding, runtime startup/shutdown, external Graph authentication, "
                    "TCP/TLS/network, frontend/static serving and final count verification"
                ),
                "cold": "first request per class after seeding/startup; OS and SQLite caches are not flushed",
                "p95": "nearest rank per workload, errors included; no averaging across report shapes",
                "pagination": (
                    "every continuation follows its own analyst's first-page next_cursor "
                    "and must be disjoint and past its anchor"
                ),
            },
            "initial_results": self.count,
            "final_results": self.count + self.inserted,
            "analysts": analysts,
            "iterations_per_analyst": iterations,
            "representative_workload": self.count >= 100_000 and analysts >= 5 and iterations >= 6,
            "data_shape": {
                "random_seed": self.RANDOM_SEED,
                "operations": 101,
                "largest_operation_share": "exactly 60% (one saved Operation ID; the rest use 100 legacy names)",
                "operators": 50,
                "harm_categories": 40,
                "categories_per_result": "3 overlapping, with duplicate members every 11th result",
                "identifier_configurations": 64,
                "target_models": 8,
                "converter_types": "16 request types, including a Unicode name, plus one response type",
                "request_converters": "overlapping pairs; every 16th configuration has a known empty pipeline",
                "legacy_embedded_share": 0.09,
                "missing_identifier_share": 0.01,
                "high_cardinality_case_labels": self.count,
                "results_per_conversation": "2 initially; concurrent inserts share the first conversation",
                "outcomes": "all four, independently distributed",
                "result_roles": "all supported roles, all counted",
            },
            "workloads": measurements,
            "elapsed_seconds": elapsed,
            "peak_concurrent_requests": self.peak_requests,
            "sql_statements": self.sql_statements,
            "sql_statement_scope": (
                "SQLAlchemy cursor statements after seeding, including cold requests and final verification; "
                "driver-direct pragmas are excluded"
            ),
            "verification_requests": {
                "successful": sum(sample.status == 200 and sample.error is None for sample in self.verification),
                "errors": sum(sample.error is not None for sample in self.verification),
            },
            "writes": {
                "transactions": len(self.write_samples),
                "updates": self.updates,
                "inserts": self.inserted,
                "p95_seconds": percentile95(self.write_samples) if self.write_samples else None,
                "maximum_seconds": max(self.write_samples, default=None),
                "errors": self.write_errors,
                "cadence": (
                    "one outcome update per transaction; an insert every second transaction; 100ms between commits"
                ),
            },
            "failures": failures,
            "meets_latency_targets": not failures,
        }

    def _record_statement(
        self, connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        self.sql_statements += 1
