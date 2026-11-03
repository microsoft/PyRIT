# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

import pytest

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentScorable,
    Equals,
    MessagePiece,
    MessageScorable,
    ProgramBehaves,
    ProgramCase,
    ProgramExecutionObservationPayload,
    ProgramResult,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.models.code_execution import _ProgramExecutionEvent, _ProgramInvocation, _SandboxRun
from pyrit.score import (
    FencedCodeBlockSelector,
    ProgramExecutionScorer,
    ProgramExecutionSource,
    ProgramSelectionError,
    ProgramSelector,
)

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory

pytestmark = pytest.mark.usefixtures("patch_central_database")

_PROFILE = "python-3.12"
_PYTHON = FencedCodeBlockSelector(languages=("python", "py"))
_MIXED = (
    "Install nothing first:\n\n```bash\npip --version\n```\n\n"
    "Then run this:\n\n```python\nimport sys\nprint(int(sys.argv[1]) ** 2)\n```\n\nIt prints the square.\n"
)


class _RecordingSandbox:
    """Runs nothing, passes every invocation, and records the program it was given."""

    def __init__(self) -> None:
        self.programs: list[str] = []

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(self, params={"fake": True})

    def get_runtime_profile_names(self) -> frozenset[str]:
        return frozenset({_PROFILE})

    async def execute_async(
        self, *, program: str, runtime_profile_name: str, invocations: tuple[_ProgramInvocation, ...]
    ) -> _SandboxRun:
        self.programs.append(program)
        return _SandboxRun(
            events=tuple(
                _ProgramExecutionEvent(
                    invocation_index=index, runtime_profile_name=runtime_profile_name, exit_code=0, duration_ms=1
                )
                for index in range(len(invocations))
            )
        )


def _expectation() -> ScoringExpectation:
    case = ProgramCase(invocation=_ProgramInvocation(arguments=("3",)), expected=ProgramResult())
    return ScoringExpectation(conditions=(ProgramBehaves(runtime_profile_name=_PROFILE, cases=(case,)),))


def _scorer(sandbox: _RecordingSandbox, *, selector: ProgramSelector | None = _PYTHON) -> ProgramExecutionScorer:
    return ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=sandbox, program_selector=selector))


@pytest.mark.parametrize(
    ("text", "program"),
    [
        ("```python\nprint(1)\n```", "print(1)\n"),
        ("Here:\n```Python title=main.py\nprint(1)\n```\nDone.", "print(1)\n"),
        ("~~~py\nprint(1)\n~~~", "print(1)\n"),
        ("```python\r\nprint(1)\r\n```\r\n", "print(1)\n"),
        ("````python\n```\nnot a fence end\n````", "```\nnot a fence end\n"),
        ("  ```python\n  if True:\n      print(1)\n print(2)\n  ```", "if True:\n    print(1)\nprint(2)\n"),
        ("```python\n\nprint(1)\n\n```", "\nprint(1)\n\n"),
        ("```python\nprint(1)\n```   ", "print(1)\n"),
        ("```json\n{}\n```\n```python\nprint(1)\n```\n```\nuntagged\n```", "print(1)\n"),
        (_MIXED, "import sys\nprint(int(sys.argv[1]) ** 2)\n"),
    ],
)
def test_selects_the_one_accepted_block(text: str, program: str) -> None:
    assert _PYTHON.select_program(text=text) == program


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("print(1)\n", "no py or python code block"),
        ("    print(1)\n", "no py or python code block"),
        ("```\nprint(1)\n```", "no py or python code block"),
        ("```bash\necho 1\n```", "no py or python code block"),
        ("``python\nprint(1)\n``", "no py or python code block"),
        ("    ```python\n    print(1)\n    ```", "no py or python code block"),
        ("```python\nprint(1)\n```\n```py\nprint(2)\n```", "2 py or python code blocks"),
        ("```python\nprint(1)\n", "unclosed code fence"),
        ("```python\nprint(1)\n~~~\n", "unclosed code fence"),
        ("````python\nprint(1)\n```\n", "unclosed code fence"),
        ("```python\nprint(1)\n```\n```bash\necho 1\n", "unclosed code fence"),
        ("```python\n   \n```", "code block is empty"),
        ("```python\n```", "code block is empty"),
    ],
)
def test_refuses_text_without_exactly_one_program(text: str, reason: str) -> None:
    with pytest.raises(ProgramSelectionError, match=reason):
        _PYTHON.select_program(text=text)


def test_backtick_info_strings_are_not_fences() -> None:
    with pytest.raises(ProgramSelectionError, match="unclosed code fence"):
        _PYTHON.select_program(text="```python `x`\nprint(1)\n```python\n")
    assert _PYTHON.select_program(text="~~~python `x`\nprint(1)\n~~~") == "print(1)\n"


@pytest.mark.parametrize("languages", [(), ("",), ("python script",), ("py`",), "python"])
def test_rejects_invalid_languages(languages: tuple[str, ...] | str) -> None:
    with pytest.raises(ValueError, match="languages"):
        FencedCodeBlockSelector(languages=languages)


def test_identifier_names_the_languages_without_order_or_case() -> None:
    first = FencedCodeBlockSelector(languages=("Python", "py")).get_identifier()
    second = FencedCodeBlockSelector(languages=["py", "python", "PY"]).get_identifier()
    assert first == second
    assert first.params["languages"] == ["py", "python"]
    assert isinstance(_PYTHON, ProgramSelector)


async def test_a_mixed_message_runs_only_its_selected_block_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _RecordingSandbox()
    piece = MessagePiece(role="assistant", original_value=_MIXED, conversation_id=str(uuid.uuid4()))
    await sqlite_instance.add_message_to_memory_async(request=piece.to_message())

    score = (
        await _scorer(sandbox).score_async(
            scorable=MessageScorable(message_piece_ids=(piece.id,)), expectation=_expectation()
        )
    )[0]

    selected = "import sys\nprint(int(sys.argv[1]) ** 2)\n"
    assert score.get_value() is True
    assert sandbox.programs == [selected]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.program_sha256 == hashlib.sha256(selected.encode()).hexdigest()
    selector = observation.source_identifier.children["program_selector"]
    assert isinstance(selector, ComponentIdentifier)
    assert selector.params["languages"] == ["py", "python"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Sure, the answer is 9.", "no py or python code block"),
        ("```python\nprint(1)\n```\nor\n```python\nprint(2)\n```", "2 py or python code blocks"),
    ],
)
async def test_text_without_one_program_is_undetermined_and_never_runs_async(
    sqlite_instance: SQLiteMemory, text: str, reason: str
) -> None:
    sandbox = _RecordingSandbox()

    score = (await _scorer(sandbox).score_async(scorable=ContentScorable(value=text), expectation=_expectation()))[0]

    assert score.status is ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert reason in score.score_rationale
    assert sandbox.programs == []
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.acquisition is Acquisition.UNAVAILABLE
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.program_sha256 is None


async def test_without_a_selector_text_runs_verbatim_async() -> None:
    sandbox = _RecordingSandbox()
    scorer = _scorer(sandbox, selector=None)

    await scorer.score_async(scorable=ContentScorable(value=_MIXED), expectation=_expectation())

    assert sandbox.programs == [_MIXED]
    source = scorer.get_identifier().children["source"]
    assert isinstance(source, ComponentIdentifier)
    assert "program_selector" not in source.children


async def test_a_selected_replay_uses_the_stored_run_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _RecordingSandbox()
    scorer = _scorer(sandbox)
    score = (await scorer.score_async(scorable=ContentScorable(value=_MIXED), expectation=_expectation()))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    stricter = ProgramCase(
        invocation=_ProgramInvocation(arguments=("3",)), expected=ProgramResult(stdout=Equals(value="9"))
    )

    replayed = (
        await scorer.score_observation_async(
            observation=observation,
            expectation=ScoringExpectation(
                conditions=(ProgramBehaves(runtime_profile_name=_PROFILE, cases=(stricter,)),)
            ),
        )
    )[0]

    assert replayed.get_value() is False
    assert len(sandbox.programs) == 1
