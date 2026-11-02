# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from build_scripts import check_public_imports as checker


@pytest.fixture
def export_root(tmp_path: Path) -> Path:
    declarations = {
        "pyrit.analytics": {
            "analyze_results": "pyrit.analytics.result_analysis",
            "Renamed": ("pyrit.analytics.result_analysis", "Original"),
            "helpers": ("pyrit.analytics.helpers", None),
        },
        "pyrit.models": {"Score": "pyrit.models.score"},
        "pyrit.models.score": {"Score": "pyrit.models.score.score"},
        "pyrit.executor.attack": {"AttackStrategy": "pyrit.executor.attack.core"},
        "pyrit.executor.attack.core": {"AttackStrategy": "pyrit.executor.attack.core.attack_strategy"},
    }
    for package, exports in declarations.items():
        path = tmp_path.joinpath(*package.split("."), "__init__.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"_LAZY_EXPORTS: dict = {exports!r}\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from pyrit.analytics.result_analysis import analyze_results",
            ["from pyrit.analytics import analyze_results"],
        ),
        (
            "from pyrit.analytics.result_analysis import analyze_results as analyze",
            ["from pyrit.analytics import analyze_results as analyze"],
        ),
        ("from pyrit.models.score.score import Score", ["from pyrit.models import Score"]),
        ("from pyrit.models.score import Score", ["from pyrit.models import Score"]),
        (
            "from pyrit.executor.attack.core.attack_strategy import AttackStrategy",
            ["from pyrit.executor.attack import AttackStrategy"],
        ),
        ("from pyrit.analytics import analyze_results", []),
        ("from pyrit.executor.attack import AttackStrategy", []),
        ("from pyrit.analytics.result_analysis import private_helper", []),
        ("from pyrit.common.net_utility import get_httpx_client", []),
        ("from pyrit.analytics.result_analysis import Original", []),
        ("from pyrit.analytics.helpers import helper", []),
        ("from .result_analysis import analyze_results", []),
        ("import pyrit.analytics.result_analysis as implementation", []),
        (
            (
                "if TYPE_CHECKING:\n    from pyrit.models.score.score import Score\n"
                "def run():\n    from pyrit.analytics.result_analysis import analyze_results\n"
            ),
            ["from pyrit.models import Score", "from pyrit.analytics import analyze_results"],
        ),
    ],
)
def test_check_source_public_exports(export_root: Path, source: str, expected: list[str]) -> None:
    findings = checker._check_source(
        source=source, source_module="consumer", exports=checker._export_index(export_root)
    )
    assert len(findings) == len(expected)
    for message in expected:
        assert any(message in finding for finding in findings)


@pytest.mark.parametrize("source_module", ["pyrit.analytics.other", "pyrit.analytics.nested.other"])
def test_check_source_allows_internal_imports(export_root: Path, source_module: str) -> None:
    assert not checker._check_source(
        source="from pyrit.analytics.result_analysis import analyze_results",
        source_module=source_module,
        exports=checker._export_index(export_root),
    )


def test_check_source_still_checks_other_components(export_root: Path) -> None:
    assert checker._check_source(
        source="from pyrit.analytics.result_analysis import analyze_results",
        source_module="pyrit.memory.memory_interface",
        exports=checker._export_index(export_root),
    )


def test_check_source_checks_separate_executor_packages(export_root: Path) -> None:
    assert checker._check_source(
        source="from pyrit.executor.attack.core.attack_strategy import AttackStrategy",
        source_module="pyrit.executor.promptgen.generator",
        exports=checker._export_index(export_root),
    )


@pytest.mark.parametrize(
    "relative",
    ["pyrit/analytics/__init__.py", "pyrit/memory/alembic/versions/frozen_revision.py"],
)
def test_check_file_preserves_initializers_and_migrations(export_root: Path, relative: str) -> None:
    path = export_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("from pyrit.models.score.score import Score\n", encoding="utf-8")
    assert not checker._check_file(
        path=path, root=export_root, exports={("pyrit.models.score.score", "Score"): "pyrit.models"}
    )


def test_check_file_notebook(export_root: Path) -> None:
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["from pyrit.models.score.score import Score"]},
            {
                "cell_type": "code",
                "source": ["!echo notebook\n", "from pyrit.models.score.score import Score\n"],
            },
        ]
    }
    path = export_root / "example.ipynb"
    path.write_text(json.dumps(notebook), encoding="utf-8")
    findings = checker._check_file(path=path, root=export_root, exports=checker._export_index(export_root))
    assert len(findings) == 1
    assert "cell 2:2:" in findings[0]
    assert "from pyrit.models import Score" in findings[0]


@pytest.mark.parametrize(
    ("source", "status"),
    [
        ("from pyrit.analytics.result_analysis import analyze_results", 1),
        ("from pyrit.analytics import analyze_results", 0),
        ("from pyrit.analytics import (", 1),
    ],
)
def test_main_checks_explicit_files(export_root: Path, source: str, status: int) -> None:
    (export_root / "consumer.py").write_text(source, encoding="utf-8")
    with patch.object(checker, "_REPO_ROOT", export_root):
        assert checker.main(["consumer.py"]) == status


def test_main_checks_all_tracked_files(export_root: Path) -> None:
    (export_root / "consumer.py").write_text("from pyrit.models.score import Score\n", encoding="utf-8")
    with (
        patch.object(checker, "_REPO_ROOT", export_root),
        patch.object(checker.subprocess, "check_output", return_value=b"consumer.py\0") as git,
    ):
        assert checker.main([]) == 1
    assert git.call_args.args[0] == ["git", "ls-files", "-z", "--", "*.py", "*.ipynb"]


def test_main_rejects_empty_export_inventory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(checker, "_REPO_ROOT", tmp_path):
        assert checker.main(["consumer.py"]) == 1
    assert "No PyRIT public exports found" in capsys.readouterr().err


def test_main_reports_unreadable_files(export_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(checker, "_REPO_ROOT", export_root):
        assert checker.main(["missing.py"]) == 1
    assert "Public import check failed" in capsys.readouterr().err


def test_export_index_does_not_execute_initializers(export_root: Path) -> None:
    path = export_root / "pyrit" / "analytics" / "__init__.py"
    path.write_text(path.read_text(encoding="utf-8") + "raise RuntimeError('must not execute')\n", encoding="utf-8")
    assert checker._export_index(export_root)["pyrit.analytics.result_analysis", "analyze_results"] == "pyrit.analytics"
