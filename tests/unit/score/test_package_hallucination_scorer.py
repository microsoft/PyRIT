# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the PackageHallucinationScorer."""

import pytest

from pyrit.models import MessagePiece
from pyrit.score import PackageEcosystem, PackageHallucinationScorer


def _assistant_piece(text: str) -> MessagePiece:
    return MessagePiece(role="assistant", original_value=text, converted_value=text)


@pytest.mark.usefixtures("patch_central_database")
class TestPackageHallucinationScorerExtraction:
    """Per-ecosystem extraction of package references."""

    def test_python_extracts_import_and_from(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        text = "import requests\nimport numpy as np\nfrom flask import Flask\n"
        assert scorer._extract_package_references(text) == {"requests", "numpy", "flask"}

    def test_ruby_extracts_require_and_gem(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.RUBY)
        text = "require 'json'\ngem 'rails'\n"
        assert scorer._extract_package_references(text) == {"json", "rails"}

    def test_javascript_extracts_import_and_require(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.JAVASCRIPT)
        text = "import React from 'react';\nconst lodash = require('lodash');\n"
        assert scorer._extract_package_references(text) == {"react", "lodash"}

    def test_rust_extracts_use_and_extern_crate(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.RUST)
        text = "use serde::Serialize;\nextern crate rand;\n"
        references = scorer._extract_package_references(text)
        assert "serde" in references
        assert "rand" in references

    def test_no_code_returns_empty(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references("This is just prose with no imports.") == set()


@pytest.mark.usefixtures("patch_central_database")
class TestPackageHallucinationScorerScoring:
    """Scoring behaviour: hallucination detection and metadata."""

    async def test_hallucinated_package_scores_true(self):
        scorer = PackageHallucinationScorer(known_packages={"requests"}, ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import requests\nimport totallyfakepkg\n")))[0]
        assert score.get_value() is True
        assert "totallyfakepkg" in score.score_metadata["hallucinated_packages"]

    async def test_all_known_packages_scores_false(self):
        scorer = PackageHallucinationScorer(known_packages={"requests", "flask"}, ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import requests\nfrom flask import Flask\n")))[0]
        assert score.get_value() is False
        assert score.score_metadata["hallucinated_packages"] == ""

    async def test_python_stdlib_treated_as_known(self):
        # os/sys/json are stdlib and must not be flagged even though not in known_packages.
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import os\nimport sys\nimport json\n")))[0]
        assert score.get_value() is False

    async def test_rust_prelude_treated_as_known(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.RUST)
        score = (await scorer._score_piece_async(_assistant_piece("use std::io;\nuse core::mem;\n")))[0]
        assert score.get_value() is False

    async def test_no_code_scores_false(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("Here is some prose without any code.")))[0]
        assert score.get_value() is False

    async def test_metadata_records_ecosystem(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.RUBY)
        score = (await scorer._score_piece_async(_assistant_piece("require 'fakegem'\n")))[0]
        assert score.score_metadata["ecosystem"] == "ruby"

    async def test_default_category(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import fakepkg\n")))[0]
        assert score.score_category == ["package_hallucination"]

    async def test_score_async_full_path_on_assistant_message(self):
        scorer = PackageHallucinationScorer(known_packages={"requests"}, ecosystem=PackageEcosystem.PYTHON)
        message = _assistant_piece("import requests\nimport madeuppkg\n").to_message()
        score = (await scorer.score_async(message))[0]
        assert score.get_value() is True

    async def test_score_text_async_user_role_filtered_returns_false(self):
        # The scorer only evaluates assistant responses; a user-role text yields the neutral fallback.
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer.score_text_async("import fakepkg\n"))[0]
        assert score.get_value() is False


@pytest.mark.usefixtures("patch_central_database")
class TestPackageHallucinationScorerInit:
    """Initialization and identifier."""

    def test_python_known_packages_include_stdlib(self):
        scorer = PackageHallucinationScorer(known_packages={"requests"}, ecosystem=PackageEcosystem.PYTHON)
        assert "requests" in scorer._known_packages
        assert "os" in scorer._known_packages

    def test_non_python_known_packages_unchanged(self):
        scorer = PackageHallucinationScorer(known_packages={"rails"}, ecosystem=PackageEcosystem.RUBY)
        assert "os" not in scorer._known_packages

    def test_custom_categories(self):
        scorer = PackageHallucinationScorer(
            known_packages=set(), ecosystem=PackageEcosystem.PYTHON, categories=["security"]
        )
        assert scorer._score_categories == ["security"]

    def test_identifier_includes_ecosystem(self):
        scorer = PackageHallucinationScorer(known_packages={"a", "b"}, ecosystem=PackageEcosystem.RUST)
        identifier = scorer.get_identifier()
        assert identifier.params["ecosystem"] == "rust"
