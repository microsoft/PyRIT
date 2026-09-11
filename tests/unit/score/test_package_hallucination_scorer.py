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

    def test_python_extracts_every_package_on_a_comma_import(self):
        """
        ``import a, b`` is a single valid statement naming two packages. The previous
        pattern captured only the first, so a hallucinated package hidden as the second
        or later item on a comma list was never compared against known_packages and the
        scorer returned False.
        """
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references("import os, hallucinated_pkg") == {
            "os",
            "hallucinated_pkg",
        }
        assert scorer._extract_package_references("import os, sys, evilpkg") == {"os", "sys", "evilpkg"}

    def test_python_comma_import_with_alias_ignores_the_alias(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        # "np" is an alias, not a package, and must not be reported as a reference.
        assert scorer._extract_package_references("import numpy as np, ghostlib") == {"numpy", "ghostlib"}

    def test_python_comma_import_reduces_dotted_paths_to_top_level(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references("import os.path, a.b.c") == {"os", "a"}

    def test_python_comma_import_preserves_hyphenated_names(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references("import scikit-learn, ghost-lib") == {
            "scikit-learn",
            "ghost-lib",
        }

    def test_python_comma_import_flags_the_hidden_package(self):
        """End to end: the hallucinated second import must make the scorer return True."""
        scorer = PackageHallucinationScorer(known_packages={"os", "sys"}, ecosystem=PackageEcosystem.PYTHON)
        references = scorer._extract_package_references("import os, sys, definitely_not_a_real_package")
        assert "definitely_not_a_real_package" in references

    def test_python_all_real_comma_import_does_not_false_positive(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references("import numpy, requests") == {"numpy", "requests"}

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
        assert score.score_metadata == {
            "ecosystem": "python",
            "hallucinated_packages": "totallyfakepkg",
        }

    async def test_all_known_packages_scores_false(self):
        scorer = PackageHallucinationScorer(known_packages={"requests", "flask"}, ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import requests\nfrom flask import Flask\n")))[0]
        assert score.get_value() is False
        assert score.score_metadata == {
            "ecosystem": "python",
            "hallucinated_packages": "",
        }

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
        assert score.score_metadata == {
            "ecosystem": "ruby",
            "hallucinated_packages": "fakegem",
        }

    async def test_default_category(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        score = (await scorer._score_piece_async(_assistant_piece("import fakepkg\n")))[0]
        assert score.score_category == ["package_hallucination"]

    async def test_score_async_full_path_on_assistant_message(self):
        scorer = PackageHallucinationScorer(known_packages={"requests"}, ecosystem=PackageEcosystem.PYTHON)
        message = _assistant_piece("import requests\nimport madeuppkg\n").to_message()
        score = (await scorer.score_message_async(message=message))[0]
        assert score.get_value() is True

    async def test_score_text_async_user_role_returns_empty(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        scores = await scorer.score_text_async("import fakepkg\n")
        assert scores == []


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
        text = "use v6.d;\nuse v6.e.PREVIEW;\nuse JSON::Fast;\n"
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
        assert score.score_metadata == {
            "ecosystem": ecosystem.value,
            "hallucinated_packages": hallucinated_package,
        }
