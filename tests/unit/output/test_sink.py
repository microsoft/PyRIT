# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS, drain_tasks_async, wait_for_event_async

from pyrit.output.sink import FileSink, IPythonMarkdownSink, Sink, StdoutSink, get_default_sink


def test_sink_is_abstract():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        Sink()  # type: ignore[abstract]


async def test_stdout_sink_writes_to_stdout(capsys):
    sink = StdoutSink()
    await sink.write_async("hello world")
    captured = capsys.readouterr()
    assert captured.out == "hello world"


async def test_stdout_sink_no_trailing_newline(capsys):
    sink = StdoutSink()
    await sink.write_async("line1")
    await sink.write_async("line2")
    captured = capsys.readouterr()
    assert captured.out == "line1line2"


async def test_stdout_sink_replaces_unsupported_unicode():
    stdout = MagicMock()
    stdout.encoding = "cp1252"
    sink = StdoutSink()

    with patch("pyrit.output.sink.sys.stdout", stdout):
        await sink.write_async("📊─")

    stdout.write.assert_called_once_with("??")


async def test_file_sink_writes_to_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        path = Path(f.name)

    try:
        sink = FileSink(path=path, mode="w")
        await sink.write_async("hello file")
        assert path.read_text(encoding="utf-8") == "hello file"
    finally:
        path.unlink(missing_ok=True)


async def test_file_sink_append_mode():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        path = Path(f.name)

    try:
        sink = FileSink(path=path, mode="w")
        await sink.write_async("first")

        append_sink = FileSink(path=path, mode="a")
        await append_sink.write_async(" second")

        assert path.read_text(encoding="utf-8") == "first second"
    finally:
        path.unlink(missing_ok=True)


def test_file_sink_rejects_invalid_mode():
    with pytest.raises(ValueError, match="mode must be 'w' or 'a'"):
        FileSink(path=Path("test.txt"), mode="wb")


@pytest.mark.timeout(7 * TEST_WATCHDOG_SECONDS)
def test_file_sink_serializes_contended_writes_across_event_loops(tmp_path: Path) -> None:
    path = tmp_path / "cross-loop.txt"
    sink = FileSink(path=path, mode="a")
    original_write_unlocked_sync = sink._write_unlocked_sync
    events: list[str] = []

    def run_contended_pair(*, loop_name: str) -> None:
        release_first = threading.Event()

        async def write_pair_async() -> None:
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
            first_started = asyncio.Event()
            second_contended = asyncio.Event()
            real_lock = sink._lock

            def acquire_lock() -> None:
                if not real_lock.acquire(blocking=False):
                    loop.call_soon_threadsafe(second_contended.set)
                    real_lock.acquire()

            def release_lock(*_args: object) -> None:
                real_lock.release()

            observed_lock = MagicMock(spec=real_lock)
            observed_lock.__enter__.side_effect = acquire_lock
            observed_lock.__exit__.side_effect = release_lock

            def controlled_write_unlocked_sync(data: str) -> None:
                events.append(f"{data}:start")
                if data.endswith("first"):
                    loop.call_soon_threadsafe(first_started.set)
                    release_first.wait()
                original_write_unlocked_sync(data)
                events.append(f"{data}:end")

            with (
                patch.object(sink, "_write_unlocked_sync", side_effect=controlled_write_unlocked_sync),
                patch.object(sink, "_lock", observed_lock),
            ):
                tasks = [asyncio.create_task(sink.write_async(f"{loop_name}-first"))]
                try:
                    await wait_for_event_async(event=first_started, tasks=tasks)
                    if tasks[0].done():
                        tasks[0].result()
                        pytest.fail("First writer completed before its release gate opened")
                    tasks.append(asyncio.create_task(sink.write_async(f"{loop_name}-second")))
                    await wait_for_event_async(event=second_contended, tasks=[tasks[1]])

                    if tasks[1].done():
                        tasks[1].result()
                        pytest.fail("Second writer completed while the first writer held the lock")
                    assert events[-1] == f"{loop_name}-first:start"
                finally:
                    release_first.set()
                    await drain_tasks_async(*tasks)
                assert [task.result() for task in tasks] == [None, None]

        asyncio.run(write_pair_async())

    run_contended_pair(loop_name="loop-a")
    run_contended_pair(loop_name="loop-b")

    assert events == [
        "loop-a-first:start",
        "loop-a-first:end",
        "loop-a-second:start",
        "loop-a-second:end",
        "loop-b-first:start",
        "loop-b-first:end",
        "loop-b-second:start",
        "loop-b-second:end",
    ]
    assert path.read_text(encoding="utf-8") == "loop-a-firstloop-a-secondloop-b-firstloop-b-second"
    assert sink._lock.acquire(blocking=False)
    sink._lock.release()


@pytest.mark.timeout(7 * TEST_WATCHDOG_SECONDS)
@pytest.mark.parametrize(
    ("failure", "cancel_during_drain"),
    [(AssertionError, False), (TimeoutError, False), (asyncio.CancelledError, False), (asyncio.CancelledError, True)],
)
def test_file_sink_contention_drains_worker_on_early_failure(
    *, tmp_path: Path, failure: type[BaseException], cancel_during_drain: bool
) -> None:
    observe = wait_for_event_async
    drain = drain_tasks_async

    async def fail_after_readiness_async(*, event: asyncio.Event, tasks: list[asyncio.Task[None]]) -> None:
        await observe(event=event, tasks=tasks)
        raise failure("injected failure after worker readiness")

    async def interrupt_drain_async(*tasks: asyncio.Task[None]) -> None:
        if cancel_during_drain:
            task = asyncio.current_task()
            assert task is not None
            asyncio.get_running_loop().call_soon(task.cancel, "cleanup cancellation")
        await drain(*tasks)

    with (
        patch(f"{__name__}.wait_for_event_async", side_effect=fail_after_readiness_async),
        patch(f"{__name__}.drain_tasks_async", side_effect=interrupt_drain_async),
    ):
        with pytest.raises(failure, match="injected failure"):
            test_file_sink_serializes_contended_writes_across_event_loops(tmp_path)

    assert (tmp_path / "cross-loop.txt").read_text(encoding="utf-8") == "loop-a-first"


@pytest.mark.timeout(7 * TEST_WATCHDOG_SECONDS)
def test_file_sink_contention_rejects_bypassed_lock(tmp_path: Path) -> None:
    with patch.object(FileSink, "_write_sync", FileSink._write_unlocked_sync):
        with pytest.raises(AssertionError, match="completed without signalling readiness"):
            test_file_sink_serializes_contended_writes_across_event_loops(tmp_path)


@pytest.mark.timeout(7 * TEST_WATCHDOG_SECONDS)
def test_file_sink_reports_failure_before_readiness(tmp_path: Path) -> None:
    failure = RuntimeError("write failed before readiness")
    with patch.object(FileSink, "_write_sync", side_effect=failure):
        with pytest.raises(RuntimeError, match="write failed before readiness") as error:
            test_file_sink_serializes_contended_writes_across_event_loops(tmp_path)
    assert error.value is failure


# --- IPythonMarkdownSink tests ---


async def test_ipython_markdown_sink_fallback_to_print(capsys):
    """When IPython is not available, falls back to print()."""
    sink = IPythonMarkdownSink()
    with patch.dict(sys.modules, {"IPython": None, "IPython.display": None}):
        await sink.write_async("# Hello")
    captured = capsys.readouterr()
    assert "# Hello" in captured.out


async def test_ipython_markdown_sink_with_ipython_available():
    """When IPython is available, uses display(Markdown(...))."""
    mock_display = MagicMock()
    mock_markdown_cls = MagicMock()

    ipython_display_module = MagicMock()
    ipython_display_module.display = mock_display
    ipython_display_module.Markdown = mock_markdown_cls

    sink = IPythonMarkdownSink()
    with patch.dict(sys.modules, {"IPython": MagicMock(), "IPython.display": ipython_display_module}):
        await sink.write_async("**bold**")

    mock_markdown_cls.assert_called_once_with("**bold**")
    mock_display.assert_called_once()


# --- get_default_sink tests ---


def test_get_default_sink_no_default_returns_stdout_outside_notebook():
    result = get_default_sink()
    assert isinstance(result, StdoutSink)


def test_get_default_sink_explicit_ipython():
    result = get_default_sink(IPythonMarkdownSink)
    assert isinstance(result, IPythonMarkdownSink)


def test_get_default_sink_explicit_stdout():
    result = get_default_sink(StdoutSink)
    assert isinstance(result, StdoutSink)
