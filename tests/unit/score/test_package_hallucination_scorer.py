# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the PackageHallucinationScorer."""

import pytest  # type: ignore

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
        assert "totallyfakepkg" in score.score_metadata["hallucinated_packages"]  # type: ignore

    async def test_all_known_packages_scores_false(self):
        scorer = PackageHallucinationScorer(known_packages={"requests", "flask"}, ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import requests\nfrom flask import Flask\n")))[0]
        assert score.get_value() is False
        assert score.score_metadata["hallucinated_packages"] == ""  # type: ignore

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
        assert score.score_metadata["ecosystem"] == "ruby"  # type: ignore

    async def test_default_category(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import fakepkg\n")))[0]
        assert score.score_category == ["package_hallucination"]

    async def test_score_async_full_path_on_assistant_message(self):
        scorer = PackageHallucinationScorer(known_packages={"requests"}, ecosystem=PackageEcosystem.PYTHON)
        message = _assistant_piece("import requests\nimport madeuppkg\n").to_message()
        score = (await scorer.score_message_async(message=message))[0]
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


@pytest.mark.usefixtures("patch_central_database")
class TestAdditionalPackageEcosystems:
    """Verify package hallucination detection for Dart, Perl, and Raku."""

    def test_dart_extracts_package_imports(self):
        scorer = PackageHallucinationScorer(
            known_packages=set(),
            ecosystem=PackageEcosystem.DART,
        )
        text = "import 'package:http/http.dart';\nimport 'package:provider/provider.dart';\n"
        assert scorer._extract_package_references(text) == {"http", "provider"}

    def test_dart_normalizes_package_names(self):
        scorer = PackageHallucinationScorer(
            known_packages={"HTTP"},
            ecosystem=PackageEcosystem.DART,
        )
        references = scorer._extract_package_references("import 'package:Http/http.dart';")
        assert references == {"http"}
        assert "http" in scorer._known_packages

    def test_perl_extracts_module_imports(self):
        scorer = PackageHallucinationScorer(
            known_packages=set(),
            ecosystem=PackageEcosystem.PERL,
        )
        text = "use JSON::MaybeXS;\nuse Fake::Module;\n"
        assert scorer._extract_package_references(text) == {"JSON::MaybeXS", "Fake::Module"}

    def test_raku_extracts_module_references(self):
        scorer = PackageHallucinationScorer(
            known_packages=set(),
            ecosystem=PackageEcosystem.RAKU,
        )
        text = "use JSON::Fast;\nneed Fake::Module;\n"
        assert scorer._extract_package_references(text) == {"JSON::Fast", "Fake::Module"}

    def test_raku_ignores_version_declarations(self):
        scorer = PackageHallucinationScorer(
            known_packages=set(),
            ecosystem=PackageEcosystem.RAKU,
        )
        text = "use v6.d;\nuse JSON::Fast;\n"
        assert scorer._extract_package_references(text) == {"JSON::Fast"}

    @pytest.mark.parametrize(
        ("ecosystem", "code", "hallucinated_package"),
        [
            (
                PackageEcosystem.DART,
                "import 'package:fake_dart_package/main.dart';",
                "fake_dart_package",
            ),
            (
                PackageEcosystem.PERL,
                "use Fake::PerlModule;",
                "Fake::PerlModule",
            ),
            (
                PackageEcosystem.RAKU,
                "use Fake::RakuModule;",
                "Fake::RakuModule",
            ),
        ],
    )
    async def test_hallucinated_packages_are_detected(
        self,
        ecosystem,
        code,
        hallucinated_package,
    ):
        scorer = PackageHallucinationScorer(
            known_packages=set(),
            ecosystem=ecosystem,
        )
        score = (await scorer._score_piece_async(_assistant_piece(code)))[0]
        assert score.get_value() is True
        assert hallucinated_package in score.score_metadata["hallucinated_packages"]  # type: ignore
        assert score.score_metadata["ecosystem"] == ecosystem.value  # type: ignore
