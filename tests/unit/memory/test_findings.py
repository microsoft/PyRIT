# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, insert, select
from sqlalchemy.dialects import mssql
from sqlalchemy.engine import ScalarResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pyrit.memory import SQLiteMemory
from pyrit.memory.memory_models import (
    AttackResultEntry,
    FindingEntry,
    FindingEvidenceEntry,
    OperationEntry,
    PromptMemoryEntry,
    ScoreEntry,
)
from pyrit.models import Finding, FindingCreate, FindingEvidence, FindingSeverity, Operation
from pyrit.models.harm_category import HarmCategory

pytestmark = pytest.mark.usefixtures("patch_central_database")


async def test_operations_are_unique_by_trimmed_case_folded_name(sqlite_instance: SQLiteMemory) -> None:
    saved = await sqlite_instance.add_operation_async(Operation(name=" Operation A "))
    await sqlite_instance.add_operation_async(Operation(name="Beta"))
    assert await sqlite_instance.add_operation_async(Operation(name="operation a")) == saved
    assert [operation.name for operation in await sqlite_instance.get_operations_async()] == ["Beta", "Operation A"]
    assert await sqlite_instance.get_operations_async(name="  OPERATION a ") == [saved]
    assert await sqlite_instance.require_operation_async(operation_id=saved.id) == saved


async def test_update_operation_renames_and_replaces_the_description(sqlite_instance: SQLiteMemory) -> None:
    saved = await sqlite_instance.add_operation_async(Operation(name="Scoped", description="First draft"))
    described = await sqlite_instance.update_operation_async(operation_id=saved.id, description="Final scope")
    assert described == saved.model_copy(update={"description": "Final scope"})
    recased = await sqlite_instance.update_operation_async(operation_id=saved.id, name="  SCOPED ")
    assert recased == described.model_copy(update={"name": "SCOPED"})
    renamed = await sqlite_instance.update_operation_async(operation_id=saved.id, name="Engagement", description="")
    assert renamed == saved.model_copy(update={"name": "Engagement", "description": ""})
    assert await sqlite_instance.get_operations_async(name="scoped") == []
    assert await sqlite_instance.get_operations_async(name="engagement") == [renamed]


async def test_update_operation_keeps_names_unique_and_valid(sqlite_instance: SQLiteMemory) -> None:
    saved = await sqlite_instance.add_operation_async(Operation(name="Scoped"))
    other = await sqlite_instance.add_operation_async(Operation(name="Other"))
    taken = await sqlite_instance.update_operation_async(operation_id=saved.id, name=" OTHER ", description="Lost")
    assert taken == other
    assert await sqlite_instance.require_operation_async(operation_id=saved.id) == saved
    with pytest.raises(ValidationError):
        await sqlite_instance.update_operation_async(operation_id=saved.id, name=" ")
    with pytest.raises(LookupError):
        await sqlite_instance.update_operation_async(operation_id=uuid4(), description="Missing")
    assert await sqlite_instance.get_operations_async() == [other, saved]


async def test_add_finding_rejects_a_missing_operation(sqlite_instance: SQLiteMemory) -> None:
    with pytest.raises(LookupError, match="Operation"):
        await sqlite_instance.add_finding_async(finding=Finding(operation_id=uuid4(), title="Orphan", severity="low"))
    assert await sqlite_instance.get_findings_async() == []


async def test_add_finding_maps_a_foreign_key_failure_to_a_missing_operation(sqlite_instance: SQLiteMemory) -> None:
    # SQL Server rejects the insert at flush when the Operation was deleted after the service checked it.
    failure = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed")))
    saved = await sqlite_instance.add_operation_async(Operation(name="Saved"))
    with patch.object(AsyncSession, "flush", failure), pytest.raises(LookupError, match="Operation"):
        await sqlite_instance.add_finding_async(finding=Finding(operation_id=uuid4(), title="Orphan", severity="low"))
    with patch.object(AsyncSession, "flush", failure), pytest.raises(IntegrityError):
        await sqlite_instance.add_finding_async(finding=Finding(operation_id=saved.id, title="Other", severity="low"))


async def test_update_operation_deleted_after_it_was_read_is_not_found(tmp_path: Path) -> None:
    db_path = tmp_path / "update-race.db"
    # A second connection needs a real file, and SQLiteMemory(...) would return the fixture's singleton.
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=db_path, silent=True)
    try:
        operation = await memory.add_operation_async(Operation(name="Racing"))
        read = OperationEntry.get_operation

        def read_then_delete(entry: OperationEntry) -> Operation:
            other = sqlite3.connect(db_path, timeout=5)
            try:
                other.execute("DELETE FROM OperationEntries")
                other.commit()
            finally:
                other.close()
            return read(entry)

        with patch.object(OperationEntry, "get_operation", read_then_delete), pytest.raises(LookupError):
            await memory.update_operation_async(operation_id=operation.id, name="Renamed")
        assert await memory.get_operations_async() == []
    finally:
        await memory.dispose_engine_async()


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("Café", "Cafe", False),
        ("ＡＢＣ", "abc", False),
        ("STRASSE", "straße", True),
        (" Ǆemal ", "ǆemal", True),
    ],
)
async def test_operation_identity_follows_casefold_not_database_collation(
    sqlite_instance: SQLiteMemory, first: str, second: str, same: bool
) -> None:
    saved = await sqlite_instance.add_operation_async(Operation(name=first))
    assert (await sqlite_instance.add_operation_async(Operation(name=second)) == saved) is same
    assert len(await sqlite_instance.get_operations_async()) == (1 if same else 2)


@pytest.mark.parametrize(
    ("stored", "harm_type", "harm_type_other"),
    [("Malwar", HarmCategory.OTHER, "Malwar"), ("malware", HarmCategory.MALWARE, None)],
)
async def test_unknown_stored_harm_type_stays_readable(
    sqlite_instance: SQLiteMemory,
    caplog: pytest.LogCaptureFixture,
    stored: str,
    harm_type: HarmCategory,
    harm_type_other: str | None,
) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Written elsewhere"))
    with sqlite_instance.engine.begin() as connection:
        connection.execute(
            insert(FindingEntry).values(
                id=uuid4(),
                operation_id=operation.id,
                title="Stored outside PyRIT",
                description="",
                severity="low",
                harm_type=stored,
                created_at=datetime.now(UTC),
            )
        )
    with caplog.at_level("WARNING"):
        [finding] = await sqlite_instance.get_findings_async(operation_id=operation.id)
    assert (finding.harm_type, finding.harm_type_other) == (harm_type, harm_type_other)
    assert ("Unknown harm category 'Malwar'" in caplog.text) is (harm_type_other is not None)


async def test_findings_paginate_within_one_operation(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Red team / α%"))
    other = await sqlite_instance.add_operation_async(Operation(name="Elsewhere"))
    stamp = datetime(2026, 10, 6, tzinfo=UTC)
    for number, severity in [(1, "low"), (2, "critical"), (3, "critical"), (4, "informational")]:
        await sqlite_instance.add_finding_async(
            finding=Finding(
                id=UUID(int=number), created_at=stamp, operation_id=operation.id, title=str(number), severity=severity
            )
        )
    await sqlite_instance.add_finding_async(finding=Finding(operation_id=other.id, title="Other", severity="critical"))
    first = await sqlite_instance.get_findings_async(operation_id=operation.id, limit=2)
    second = await sqlite_instance.get_findings_async(operation_id=operation.id, limit=2, offset=2)
    assert [finding.title for finding in first + second] == ["3", "2", "1", "4"]
    async with await sqlite_instance.get_session_async() as session:
        for table in [AttackResultEntry, PromptMemoryEntry, ScoreEntry]:
            assert await session.scalar(select(func.count()).select_from(table)) == 0


async def test_duplicate_id_returns_the_saved_finding_unchanged(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Case"))
    finding = Finding(operation_id=operation.id, title="Original", severity="low")
    assert await sqlite_instance.add_finding_async(finding=finding) == finding
    assert await sqlite_instance.add_finding_async(finding=finding.model_copy(update={"title": "Duplicate"})) == finding
    assert [f.title for f in await sqlite_instance.get_findings_async()] == ["Original"]
    async with await sqlite_instance.get_session_async() as session:
        assert await session.scalar(select(func.count()).select_from(FindingEntry)) == 1


async def test_finding_and_evidence_save_together_or_not_at_all(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Case"))
    finding = Finding(operation_id=operation.id, title="With evidence", severity="low")
    evidence = FindingEvidence(finding_id=finding.id, attack_result_id=uuid4())
    assert await sqlite_instance.add_finding_async(finding=finding, evidence=evidence) == finding
    assert (
        await sqlite_instance.get_finding_evidence_by_source_async(
            finding_id=finding.id, attack_result_id=evidence.attack_result_id
        )
        == evidence
    )
    rejected = Finding(operation_id=operation.id, title="Evidence clash", severity="low")
    clash = evidence.model_copy(update={"finding_id": rejected.id})
    with pytest.raises(IntegrityError):
        await sqlite_instance.add_finding_async(finding=rejected, evidence=clash)
    assert [f.title for f in await sqlite_instance.get_findings_async()] == ["With evidence"]


async def test_update_finding_preserves_identity_and_reorders_severity(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Case"))
    original = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Original", severity="low")
    )
    await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Other", severity="moderate")
    )
    updated = await sqlite_instance.update_finding_async(
        operation_id=operation.id,
        finding_id=original.id,
        request=FindingCreate(title="Edited", severity="critical", description="Notes"),
    )
    assert updated.id == original.id
    assert updated.operation_id == original.operation_id
    assert updated.created_at == original.created_at
    assert updated.title == "Edited"
    assert updated.description == "Notes"
    assert [finding.title for finding in await sqlite_instance.get_findings_async()] == ["Edited", "Other"]


async def test_finding_classifications_persist_and_clear_without_losing_evidence(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Classifications"))
    original = Finding.model_validate(
        {
            "operation_id": operation.id,
            "title": "Custom",
            "severity": "other",
            "severity_other": "  Review needed  ",
            "harm_type": "Other",
            "harm_type_other": "  Team harm  ",
        }
    )
    saved = await sqlite_instance.add_finding_async(finding=original)
    assert saved == original
    evidence = FindingEvidence(finding_id=saved.id, attack_result_id=uuid4())
    await sqlite_instance.add_finding_evidence_async(evidence=evidence)
    read = await sqlite_instance.get_finding_async(operation_id=operation.id, finding_id=saved.id)
    assert read == original
    updated = await sqlite_instance.update_finding_async(
        operation_id=operation.id,
        finding_id=saved.id,
        request=FindingCreate(title="Canonical", severity="important", harm_type="Malware"),
    )
    assert updated.id == saved.id
    assert updated.created_at == saved.created_at
    assert updated.severity_other is None
    assert updated.harm_type.value == "Malware"
    assert updated.harm_type_other is None
    cleared = await sqlite_instance.update_finding_async(
        operation_id=operation.id, finding_id=saved.id, request=FindingCreate(title="Optional", severity="low")
    )
    assert cleared.harm_type is None
    assert await sqlite_instance.get_finding_evidence_async(finding_id=saved.id, limit=20, offset=0) == [evidence]


async def test_custom_severity_sorts_after_every_preset(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Order"))
    stamp = datetime(2026, 10, 9, tzinfo=UTC)
    for number, severity in enumerate(["other", "informational", "low", "moderate", "important", "critical"], start=1):
        await sqlite_instance.add_finding_async(
            finding=Finding.model_validate(
                {
                    "id": UUID(int=number),
                    "created_at": stamp,
                    "operation_id": operation.id,
                    "title": severity,
                    "severity": severity,
                    "severity_other": "Critical" if severity == "other" else None,
                }
            )
        )
    assert [finding.title for finding in await sqlite_instance.get_findings_async()] == [
        "critical",
        "important",
        "moderate",
        "low",
        "informational",
        "other",
    ]


async def test_finding_mutations_are_scoped_and_delete_only_the_finding(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Case"))
    other = await sqlite_instance.add_operation_async(Operation(name="Other"))
    original = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Original", severity="low")
    )
    with pytest.raises(LookupError, match="not found"):
        await sqlite_instance.update_finding_async(
            operation_id=other.id,
            finding_id=original.id,
            request=FindingCreate(title="Wrong operation", severity="critical"),
        )
    with pytest.raises(LookupError, match="not found"):
        await sqlite_instance.delete_finding_async(operation_id=other.id, finding_id=original.id)
    assert await sqlite_instance.get_findings_async() == [original]
    await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=original.id)
    assert await sqlite_instance.get_findings_async() == []
    assert len(await sqlite_instance.get_operations_async()) == 2
    with pytest.raises(LookupError, match="not found"):
        await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=original.id)


async def test_update_finding_validates_before_mutation(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Case"))
    original = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Original", severity="low")
    )
    invalid = FindingCreate(title="Valid", severity="low").model_copy(update={"title": " "})
    with pytest.raises(ValueError):
        await sqlite_instance.update_finding_async(operation_id=operation.id, finding_id=original.id, request=invalid)
    assert await sqlite_instance.get_findings_async() == [original]


async def test_add_finding_evidence_and_get_page(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Evidence"))
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="A", severity="low")
    )
    stamp = datetime(2026, 10, 8, tzinfo=UTC)
    items = [
        FindingEvidence(
            id=UUID(int=i),
            finding_id=finding.id,
            attack_result_id=uuid4(),
            attached_at=stamp,
        )
        for i in [1, 2, 3]
    ]
    for evidence in items:
        assert await sqlite_instance.add_finding_evidence_async(evidence=evidence) == evidence
    assert await sqlite_instance.get_finding_evidence_async(finding_id=finding.id, limit=2, offset=0) == items[:0:-1]
    assert await sqlite_instance.get_finding_evidence_async(finding_id=finding.id, limit=2, offset=2) == items[:1]
    assert (
        await sqlite_instance.get_finding_evidence_by_source_async(
            finding_id=finding.id, attack_result_id=items[0].attack_result_id
        )
        == items[0]
    )
    assert (
        await sqlite_instance.get_finding_evidence_by_source_async(
            finding_id=uuid4(), attack_result_id=items[0].attack_result_id
        )
        is None
    )
    assert await sqlite_instance.get_finding_async(operation_id=operation.id, finding_id=finding.id) == finding
    with pytest.raises(ValueError):
        await sqlite_instance.get_finding_evidence_async(finding_id=finding.id, limit=0, offset=0)


async def test_finding_evidence_requires_an_existing_finding(sqlite_instance: SQLiteMemory) -> None:
    evidence = FindingEvidence(finding_id=uuid4(), attack_result_id=uuid4())
    with pytest.raises(LookupError):
        await sqlite_instance.add_finding_evidence_async(evidence=evidence)
    async with await sqlite_instance.get_session_async() as session:
        assert await session.scalar(select(func.count()).select_from(FindingEvidenceEntry)) == 0


async def test_finding_evidence_foreign_key_failure_is_lookup_error(sqlite_instance: SQLiteMemory) -> None:
    evidence = FindingEvidence(finding_id=uuid4(), attack_result_id=uuid4())
    failure = IntegrityError("insert", {}, RuntimeError("foreign key"))
    with patch.object(AsyncSession, "flush", side_effect=failure), pytest.raises(LookupError):
        await sqlite_instance.add_finding_evidence_async(evidence=evidence)


async def test_finding_evidence_unique_per_finding_attack_result(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Evidence"))
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="A", severity="low")
    )
    evidence = FindingEvidence(finding_id=finding.id, attack_result_id=uuid4())
    await sqlite_instance.add_finding_evidence_async(evidence=evidence)
    duplicate = evidence.model_copy(update={"id": uuid4()})
    assert await sqlite_instance.add_finding_evidence_async(evidence=duplicate) == evidence
    assert await sqlite_instance.get_finding_evidence_async(finding_id=finding.id, limit=20, offset=0) == [evidence]


async def test_unrelated_integrity_failures_propagate(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Evidence"))
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="A", severity="low")
    )
    evidence = FindingEvidence(finding_id=finding.id, attack_result_id=uuid4())
    await sqlite_instance.add_finding_evidence_async(evidence=evidence)
    with pytest.raises(IntegrityError):
        await sqlite_instance.add_finding_evidence_async(
            evidence=evidence.model_copy(update={"attack_result_id": uuid4()})
        )
    with pytest.raises(IntegrityError):
        await sqlite_instance.add_operation_async(operation.model_copy(update={"name": "Different"}))


async def test_one_attack_result_can_support_multiple_findings_and_counts(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Evidence"))
    findings = [
        await sqlite_instance.add_finding_async(finding=Finding(operation_id=operation.id, title=title, severity="low"))
        for title in ["A", "B", "C"]
    ]
    attack_result_id = uuid4()
    for finding in findings[:2]:
        await sqlite_instance.add_finding_evidence_async(
            evidence=FindingEvidence(finding_id=finding.id, attack_result_id=attack_result_id)
        )
    assert await sqlite_instance.get_finding_evidence_counts_async(finding_ids=[f.id for f in findings]) == {
        findings[0].id: 1,
        findings[1].id: 1,
    }
    assert await sqlite_instance.get_finding_evidence_counts_async(finding_ids=[]) == {}


async def test_get_findings_by_ids_follows_dashboard_order_and_scope(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Selected"))
    other = await sqlite_instance.add_operation_async(Operation(name="Other"))
    stamp = datetime(2026, 10, 6, tzinfo=UTC)
    saved: dict[str, Finding] = {}
    for title, severity, created_at in [
        ("Low", "low", stamp),
        ("Older critical", "critical", stamp),
        ("Moderate", "moderate", stamp),
        ("Newer critical", "critical", datetime(2026, 10, 7, tzinfo=UTC)),
    ]:
        saved[title] = await sqlite_instance.add_finding_async(
            finding=Finding(operation_id=operation.id, title=title, severity=severity, created_at=created_at)
        )
    foreign = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=other.id, title="Foreign", severity="critical")
    )
    requested = [saved["Moderate"].id, foreign.id, saved["Low"].id, uuid4(), saved["Newer critical"].id]
    requested += [saved["Older critical"].id, saved["Low"].id]
    findings = await sqlite_instance.get_findings_by_ids_async(operation_id=operation.id, finding_ids=requested)
    assert [finding.title for finding in findings] == ["Newer critical", "Older critical", "Moderate", "Low"]


async def test_get_findings_by_ids_reads_in_batches(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Batched"))
    for number, severity in enumerate(["low", "critical", "moderate", "critical", "informational"]):
        await sqlite_instance.add_finding_async(
            finding=Finding(operation_id=operation.id, title=str(number), severity=severity)
        )
    expected = await sqlite_instance.get_findings_async(operation_id=operation.id, limit=10)
    with patch.object(sqlite_instance, "_MAX_BIND_VARS", 2):
        findings = await sqlite_instance.get_findings_by_ids_async(
            operation_id=operation.id, finding_ids=[finding.id for finding in reversed(expected)]
        )
    assert len(expected) == 5
    assert findings == expected


async def test_get_finding_evidence_for_findings_groups_newest_first(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Evidence"))
    findings = [
        await sqlite_instance.add_finding_async(finding=Finding(operation_id=operation.id, title=title, severity="low"))
        for title in ["Two links", "One link", "No links"]
    ]
    older = FindingEvidence(
        finding_id=findings[0].id, attack_result_id=uuid4(), attached_at=datetime(2026, 10, 8, tzinfo=UTC)
    )
    newer = FindingEvidence(
        finding_id=findings[0].id, attack_result_id=uuid4(), attached_at=datetime(2026, 10, 9, tzinfo=UTC)
    )
    only = FindingEvidence(finding_id=findings[1].id, attack_result_id=uuid4())
    for evidence in [older, newer, only]:
        await sqlite_instance.add_finding_evidence_async(evidence=evidence)
    expected = {
        finding.id: await sqlite_instance.get_finding_evidence_async(finding_id=finding.id, limit=10, offset=0)
        for finding in findings[:2]
    }
    assert expected[findings[0].id] == [newer, older]
    finding_ids = [finding.id for finding in findings]
    assert await sqlite_instance.get_finding_evidence_for_findings_async(finding_ids=finding_ids) == expected
    with patch.object(sqlite_instance, "_MAX_BIND_VARS", 1):
        batched = await sqlite_instance.get_finding_evidence_for_findings_async(
            finding_ids=[*finding_ids, findings[0].id]
        )
    assert batched == expected


async def test_batched_finding_reads_accept_empty_input(sqlite_instance: SQLiteMemory) -> None:
    with patch.object(sqlite_instance, "get_session_async", new_callable=AsyncMock) as get_session:
        assert await sqlite_instance.get_findings_by_ids_async(operation_id=uuid4(), finding_ids=[]) == []
        assert await sqlite_instance.get_finding_evidence_for_findings_async(finding_ids=[]) == {}
    get_session.assert_not_called()


async def test_finding_severity_counts_group_by_operation(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Counted"))
    other = await sqlite_instance.add_operation_async(Operation(name="Other"))
    await sqlite_instance.add_operation_async(Operation(name="Empty"))
    for severity in ["critical", "critical", "low"]:
        await sqlite_instance.add_finding_async(
            finding=Finding(operation_id=operation.id, title="A", severity=severity)
        )
    await sqlite_instance.add_finding_async(finding=Finding(operation_id=other.id, title="B", severity="informational"))
    assert await sqlite_instance.get_finding_severity_counts_async() == {
        operation.id: {FindingSeverity.CRITICAL: 2, FindingSeverity.LOW: 1},
        other.id: {FindingSeverity.INFORMATIONAL: 1},
    }


async def test_delete_finding_removes_evidence_without_deleting_source(sqlite_instance: SQLiteMemory) -> None:
    from pyrit.models import Message, MessagePiece

    operation = await sqlite_instance.add_operation_async(Operation(name="Evidence"))
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="A", severity="low")
    )
    await sqlite_instance.add_message_to_memory_async(
        request=Message(
            message_pieces=[MessagePiece(role="user", original_value="Retain me", conversation_id="source")]
        )
    )
    evidence = await sqlite_instance.add_finding_evidence_async(
        evidence=FindingEvidence(finding_id=finding.id, attack_result_id=uuid4())
    )
    with pytest.raises(LookupError):
        await sqlite_instance.delete_finding_async(operation_id=uuid4(), finding_id=finding.id)
    assert await sqlite_instance.get_finding_evidence_async(finding_id=finding.id, limit=20, offset=0) == [evidence]
    await sqlite_instance.delete_finding_evidence_async(finding_id=finding.id, evidence_id=evidence.id)
    assert await sqlite_instance.get_finding_evidence_counts_async(finding_ids=[finding.id]) == {}
    await sqlite_instance.add_finding_evidence_async(evidence=evidence)
    await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=finding.id)
    assert await sqlite_instance.get_finding_evidence_async(finding_id=finding.id, limit=20, offset=0) == []
    assert (await sqlite_instance.get_conversation_stats_async(conversation_ids=["source"]))[
        "source"
    ].message_count == 1


async def test_finding_search_escapes_sql_server_bracket_wildcards(sqlite_instance: SQLiteMemory) -> None:
    session = MagicMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    result = MagicMock(spec=ScalarResult)
    result.all.return_value = []
    session.scalars.return_value = result
    with patch.object(sqlite_instance, "get_session_async", return_value=session):
        await sqlite_instance.get_findings_async(title_query="[Proof]")
    statement = session.scalars.call_args.args[0]
    compiled = statement.compile(dialect=mssql.dialect())
    assert compiled.params["title_1"] == "%/[Proof]%"
    assert "ESCAPE '/'" in str(compiled)


@pytest.mark.parametrize("query", ["[Proof]", "100%_", "a/b", "a//b"])
async def test_finding_search_treats_wildcards_and_escape_characters_literally(
    sqlite_instance: SQLiteMemory,
    query: str,
) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Literal search"))
    for title in ["[Proof]", "100%_", "a/b", "a//b", "Proof", "100XX", "ab"]:
        await sqlite_instance.add_finding_async(finding=Finding(operation_id=operation.id, title=title, severity="low"))
    findings = await sqlite_instance.get_findings_async(operation_id=operation.id, title_query=query)
    assert [finding.title for finding in findings] == [query]
