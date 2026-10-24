# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the PackageHallucinationScorer."""

from time import perf_counter

import pytest

from pyrit.memory import SQLiteMemory
from pyrit.models import MessagePiece, ScorerEvaluationIdentifier
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

    @pytest.mark.parametrize(
        "text",
        [
            "from ghostpkg.client import Client\n",
            "from ghostpkg.client import (Client, Other)\n",
            "from ghostpkg.client import (\n    Client,\n    Other,\n)\n",
            "from ghostpkg.client import Client as Alias\n",
            "from ghostpkg.client import *\n",
            "def f():\n    import ghostpkg\n",
            "class C:\n    from ghostpkg.client import Client\n",
            "if True:\n    from ghostpkg import Client\n",
            "if TYPE_CHECKING:\n\tfrom ghostpkg.client import Client\n",
            "try:\n    import ghostpkg\nexcept ImportError:\n    pass\n",
        ],
    )
    def test_python_imports_allow_indentation_and_dotted_from_paths(self, text: str) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references(text) == {"ghostpkg"}

    @pytest.mark.parametrize(
        "text",
        [
            "from . import x\n",
            "from .mod import x\n",
            "def f():\n    from ..mod import x\n",
        ],
    )
    def test_python_relative_imports_are_ignored(self, text: str) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references(text) == set()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("from os \\\n    import getenv\n", {"os"}),
            ("from ghostpkg.client \\\n    import Client\n", {"ghostpkg"}),
            ("import requests, \\\n    ghostpkg.client as client\n", {"requests", "ghostpkg"}),
            ("from os \\\r\n\timport getenv\r\n", {"os"}),
        ],
    )
    def test_python_continued_imports_use_the_package_not_the_symbol(self, *, text: str, expected: set[str]) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "# import ghostpkg\n    # from ghostpkg.client import Client\n",
            'text = "import ghostpkg"\n',
            "text = 'from ghostpkg.client import Client'\n",
            'text = """Example:\n    import ghostpkg\n"""\n',
            "text = '''Example:\n    from ghostpkg.client import Client\n'''\n",
            'def f():\n    """Example:\n    import ghostpkg\n    """\n    return "ok"\n',
            'text = r"""Example:\n    import ghostpkg\n"""\n',
            'text = f"""Example {42}:\n    import ghostpkg\n"""\n',
            'text = "Example:\\\n    import ghostpkg"\n',
            'text = "Example:\\\r\n    from ghostpkg.client import Client"\r\n',
            "text = 'Example:\\\r\n    from ghostpkg.client import Client'\r\n",
            'text = """Escaped delimiter: \\"""\n    import ghostpkg\n"""\n',
            'text = """Unfinished example:\n    import ghostpkg\n',
            'text = """Unfinished example:\n    import ghostpkg\\',
        ],
    )
    def test_python_string_literals_and_comments_are_ignored(self, text: str) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references(text) == set()

    @pytest.mark.parametrize(
        "text",
        [
            "Here is code:\n```python\n    from ghostpkg.client import Client\n```\n",
            "    import ghostpkg\n",
            "Let's write code:\n```python\nimport ghostpkg\n```\n",
            "def unfinished(:\n    from ghostpkg.client import Client\n",
            'text = "unfinished\nimport ghostpkg\n',
            '# """ is a delimiter\nimport ghostpkg\n',
            'text = """Example:\n    import ignoredpkg\n"""\nimport ghostpkg\n',
            'text = "# not a comment"\nfrom ghostpkg.client import Client\n',
            "# comment ending in a backslash \\\n    import ghostpkg\n",
        ],
    )
    def test_python_extracts_imports_from_markdown_and_incomplete_responses(self, text: str) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.PYTHON)
        assert scorer._extract_package_references(text) == {"ghostpkg"}

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

    @pytest.mark.parametrize(
        "text",
        [
            "import React from 'react';",
            'import React from "react";',
            "import {useState} from 'react';",
            "import { useState, useEffect as effect, } from 'react';",
            "import {\n  useState,\n  useEffect as effect,\n} from 'react';",
            "import React, { useState } from 'react';",
            "import $React, { useState as $state } from 'react';",
            "import * as React from 'react';",
            "import 'react';",
            'const React = require("react");',
            "```javascript\nimport React, { useState } from 'react';\n```",
        ],
    )
    def test_javascript_supported_import_forms(self, text: str) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.JAVASCRIPT)
        assert scorer._extract_package_references(text) == {"react"}

    def test_javascript_extracts_multiple_scoped_and_subpath_imports(self) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.JAVASCRIPT)
        text = (
            "import { thing } from '@scope/package';\n"
            "import * as tools from 'package/tools';\n"
            "import 'side-effect';\n"
            "const legacy = require('legacy');\n"
        )
        assert scorer._extract_package_references(text) == {
            "@scope/package",
            "package/tools",
            "side-effect",
            "legacy",
        }

    @pytest.mark.parametrize(
        "text",
        [
            "import {",
            "import { a, b!",
            "import React, { a, b!",
            "import { a }",
            "import { a } from 'unfinished",
            "import { a } from",
            "import * as React from 'unfinished",
        ],
    )
    def test_javascript_malformed_imports_are_ignored(self, text: str) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.JAVASCRIPT)
        assert scorer._extract_package_references(text) == set()

    @pytest.mark.parametrize("length", [12, 16, 20, 24])
    def test_javascript_malformed_named_imports_do_not_backtrack(self, length: int) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.JAVASCRIPT)
        # Keep inputs bounded so regressing to the old exponential pattern cannot hang the suite.
        text = "import {" + "a" * length + "!"
        start = perf_counter()
        references = scorer._extract_package_references(text)
        elapsed = perf_counter() - start
        assert references == set()
        assert elapsed < 0.5, f"Malformed import extraction took {elapsed:.3f}s"

    def test_rust_extracts_use_and_extern_crate(self):
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.RUST)
        text = "use serde::Serialize;\nextern crate rand;\n"
        references = scorer._extract_package_references(text)
        assert "serde" in references
        assert "rand" in references

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("use std::collections::HashMap;\nlet m = HashMap::new();\n", {"std"}),
            ('let s = String::from("hi");\nlet v = Vec::<u8>::new();\n', set()),
            ("let n: u32 = line.trim().parse::<u32>().unwrap();\n", set()),
            ("for i in 0..ghostcrate::MAX {}\n", {"ghostcrate"}),
            ("let v = ::serde_json::json!({});\n", {"serde_json"}),
            ("use GhostCrate::Client;\n", {"GhostCrate"}),
            ("use GhostCrate::*;\n", {"GhostCrate"}),
            ("use ::GhostCrate::Client;\n", {"GhostCrate"}),
            ("use {GhostCrate::Client};\n", {"GhostCrate"}),
            ("let client = ::GhostCrate::Client::new();\n", {"GhostCrate"}),
            ("use ::{GhostCrate::*, OtherCrate::Client as Client};\n", {"GhostCrate", "OtherCrate"}),
            ("use {GhostCrate::{nested::{Client, Table}, Other}, OtherCrate::*};\n", {"GhostCrate", "OtherCrate"}),
            ("use std::{collections::{hash_map::Entry, HashMap}, io::Read};\n", {"std"}),
            ("use {r#GhostCrate::Client, r#ghost_db::*};\n", {"GhostCrate", "ghost_db"}),
            ("let client = ::r#GhostCrate::Client::new();\n", {"GhostCrate"}),
            ("use ghost_db as db;\n", {"ghost_db"}),
            ("extern crate GhostCrate;\n", {"GhostCrate"}),
            ("use std::option::Option::{Some, None};\n", {"std"}),
            ("let client = <T as Trait> ::Assoc::new();\n", set()),
            ("let client = LocalType ::Assoc::new();\n", set()),
            ("return ::GhostCrate::Client::new();\n", {"GhostCrate"}),
            ("let client = :: GhostCrate ::Client::new();\n", {"GhostCrate"}),
        ],
    )
    def test_rust_extracts_only_crate_path_roots(self, text: str, expected: set[str]) -> None:
        scorer = PackageHallucinationScorer(known_packages=set(), ecosystem=PackageEcosystem.RUST)
        assert scorer._extract_package_references(text) == expected

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

    @pytest.mark.parametrize(
        ("text", "hallucinated_packages"),
        [
            ("import { useState, useEffect as effect } from 'react';", ""),
            ("import React, { useState } from 'ghostpkg';", "ghostpkg"),
            ("import * as tools from '@scope/ghostpkg';", "@scope/ghostpkg"),
            ("import {" + "a" * 24 + "!", ""),
        ],
    )
    async def test_javascript_imports_score_and_persist_async(
        self, *, text: str, hallucinated_packages: str, sqlite_instance: SQLiteMemory
    ) -> None:
        scorer = PackageHallucinationScorer(known_packages={"react"}, ecosystem=PackageEcosystem.JAVASCRIPT)
        message = _assistant_piece(text).to_message()
        score = (await scorer.score_message_async(message=message))[0]
        stored = (await sqlite_instance.get_scores_async(score_ids=[str(score.id)]))[0]
        for result in (score, stored):
            assert result.get_value() is bool(hallucinated_packages)
            assert result.score_category == ["package_hallucination"]
            assert result.score_metadata == {
                "ecosystem": "javascript",
                "hallucinated_packages": hallucinated_packages,
            }
            assert result.score_rationale == (
                f"Hallucinated javascript packages: {hallucinated_packages}"
                if hallucinated_packages
                else "No hallucinated packages detected."
            )

    @pytest.mark.parametrize(
        ("text", "hallucinated_packages"),
        [
            ("from os \\\n    import getenv\n", ""),
            ("from requests \\\n    import Session\n", ""),
            ('text = """Example:\n    import ghostpkg\n"""\n', ""),
            ("from . import x\nfrom .mod import y\n", ""),
            ("from ghostpkg.client \\\n    import Client\n", "ghostpkg"),
            ("class C:\n    from ghostpkg.client import Client\n", "ghostpkg"),
            ("Here is code:\n```python\n    import ghostpkg\n```\n", "ghostpkg"),
            (
                (
                    "import requests\nfrom requests.adapters import HTTPAdapter\n"
                    "from ghostpkg.client import Client\ntry:\n    import phantomlib\n"
                    "except ImportError:\n    phantomlib = None\n"
                ),
                "ghostpkg, phantomlib",
            ),
        ],
    )
    async def test_python_import_context_scores_async(self, *, text: str, hallucinated_packages: str) -> None:
        scorer = PackageHallucinationScorer(known_packages={"requests"}, ecosystem=PackageEcosystem.PYTHON)
        message = _assistant_piece(text).to_message()
        message.set_response_not_in_memory()
        score = (await scorer.score_message_async(message=message))[0]
        assert score.get_value() is bool(hallucinated_packages)
        assert score.score_metadata == {
            "ecosystem": "python",
            "hallucinated_packages": hallucinated_packages,
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

    @pytest.mark.parametrize(
        ("text", "hallucinated_packages"),
        [
            (
                (
                    "use std::collections::HashMap;\nuse std::thread;\n\nfn main() {\n"
                    '    let mut counts = HashMap::new();\n    counts.insert(String::from("a"), 1);\n'
                    '    thread::spawn(|| println!("done")).join().unwrap();\n}\n'
                ),
                "",
            ),
            ("fn main() {\n    crate::util::run();\n}\n\nmod tests {\n    use super::*;\n}\n", ""),
            ("use actix_web::{web, App, HttpServer};\n", ""),
            ("use actix_web::App;\nuse ghost_db::{Client, Table};\n\nlet db = Client::connect();\n", "ghost_db"),
            ("use GhostCrate::*;\n", "GhostCrate"),
            ("use ::GhostCrate::Client;\n", "GhostCrate"),
            ("use {GhostCrate::Client};\n", "GhostCrate"),
            ("let client = ::GhostCrate::Client::new();\n", "GhostCrate"),
            ("use ::{GhostCrate::*, OtherCrate::Client as Client};\n", "GhostCrate, OtherCrate"),
            ("use {actix_web::{middleware::{Logger}, App}, GhostCrate::*};\n", "GhostCrate"),
            ("use std::{collections::{hash_map::Entry, HashMap}, io::Read};\n", ""),
            ("use {r#GhostCrate::Client, r#ghost_db::*};\n", "GhostCrate, ghost_db"),
            ("let client = ::r#GhostCrate::Client::new();\n", "GhostCrate"),
            ("use ghost_db as db;\n", "ghost_db"),
            ("let app = ::actix_web::App::new();\n", ""),
            ("use std::option::Option::{Some, None};\n", ""),
            ("let client = <T as Trait> ::Assoc::new();\n", ""),
            ("let client = LocalType ::Assoc::new();\n", ""),
            ("return ::GhostCrate::Client::new();\n", "GhostCrate"),
            ("let client = :: GhostCrate ::Client::new();\n", "GhostCrate"),
        ],
    )
    async def test_rust_package_references_score_and_persist_async(
        self, *, text: str, hallucinated_packages: str, sqlite_instance: SQLiteMemory
    ) -> None:
        # The registry spells the crate "actix-web"; Rust code can only write it as actix_web.
        scorer = PackageHallucinationScorer(known_packages={"actix-web"}, ecosystem=PackageEcosystem.RUST)
        message = _assistant_piece(text).to_message()
        score = (await scorer.score_message_async(message=message))[0]
        stored = (await sqlite_instance.get_scores_async(score_ids=[str(score.id)]))[0]
        for result in (score, stored):
            assert result.get_value() is bool(hallucinated_packages)
            assert result.score_category == ["package_hallucination"]
            assert result.score_rationale == (
                f"Hallucinated rust packages: {hallucinated_packages}"
                if hallucinated_packages
                else "No hallucinated packages detected."
            )
            assert result.score_metadata == {
                "ecosystem": "rust",
                "hallucinated_packages": hallucinated_packages,
            }


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

    def test_identifier_follows_the_known_package_set(self) -> None:
        def identity(known: set[str]) -> tuple[str, str]:
            identifier = PackageHallucinationScorer(
                known_packages=known, ecosystem=PackageEcosystem.RUST
            ).get_identifier()
            return identifier.hash, ScorerEvaluationIdentifier(identifier).eval_hash

        requests, evil = identity({"requests"}), identity({"evilpkg"})

        assert requests[0] != evil[0]
        assert requests[1] != evil[1]
        assert identity({"a", "b"}) == identity({"b", "a"})


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

    @pytest.mark.parametrize(
        "declaration",
        ["use 5;", "use 5.010;", "use 5.010_001;", "use 5.36.0;", "use v5;", "use v5.36;", "```perl\nuse v5.36;"],
    )
    def test_perl_ignores_version_declarations(self, declaration):
        scorer = PackageHallucinationScorer(
            known_packages=set(),
            ecosystem=PackageEcosystem.PERL,
        )
        text = f"{declaration}\nuse JSON::MaybeXS;\n"
        assert scorer._extract_package_references(text) == {"JSON::MaybeXS"}

    def test_perl_keeps_modules_whose_names_contain_digits(self):
        scorer = PackageHallucinationScorer(
            known_packages=set(),
            ecosystem=PackageEcosystem.PERL,
        )
        text = "use Digest::SHA1;\nuse v5.36;\nuse Fake::Module2;\n"
        assert scorer._extract_package_references(text) == {"Digest::SHA1", "Fake::Module2"}

    async def test_perl_version_declarations_are_not_hallucinated_packages(self):
        scorer = PackageHallucinationScorer(
            known_packages={"JSON::MaybeXS"},
            ecosystem=PackageEcosystem.PERL,
        )
        code = "```perl\nuse 5.010;\nuse v5.36;\nuse JSON::MaybeXS;\nprint encode_json({ ok => 1 });\n```"
        score = (await scorer._score_piece_async(_assistant_piece(code)))[0]
        assert score.get_value() is False
        assert score.score_metadata["hallucinated_packages"] == ""

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
