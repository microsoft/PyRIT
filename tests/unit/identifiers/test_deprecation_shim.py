# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the ``pyrit.identifiers`` deprecation shim.

The shim was installed when ``pyrit.identifiers`` was renamed to
``pyrit.models.identifiers`` (Phase 2 of the models refactor). These tests
ensure the shim correctly forwards every public symbol to the new location,
emits a ``DeprecationWarning`` exactly once per name per process, and raises
``AttributeError`` for unknown attributes — matching the behavior contract
documented in ``pyrit/identifiers/__init__.py``.
"""

from __future__ import annotations

import importlib
import re
import warnings
from pathlib import Path

import pytest

import pyrit.identifiers as shim
import pyrit.identifiers.atomic_attack_identifier as shim_atomic
import pyrit.identifiers.class_name_utils as shim_class_name
import pyrit.identifiers.component_identifier as shim_component
import pyrit.identifiers.evaluation_identifier as shim_eval
import pyrit.identifiers.identifier_filters as shim_filters
import pyrit.models as models_pkg
import pyrit.models.identifiers as new
import pyrit.models.identifiers.atomic_attack_identifier as new_atomic
import pyrit.models.identifiers.class_name_utils as new_class_name
import pyrit.models.identifiers.component_identifier as new_component
import pyrit.models.identifiers.evaluation_identifier as new_eval
import pyrit.models.identifiers.identifier_filters as new_filters

SUBMODULE_PAIRS = [
    (shim_component, new_component, "component_identifier"),
    (shim_atomic, new_atomic, "atomic_attack_identifier"),
    (shim_eval, new_eval, "evaluation_identifier"),
    (shim_class_name, new_class_name, "class_name_utils"),
    (shim_filters, new_filters, "identifier_filters"),
]

# Names that are deprecated at BOTH the pyrit.identifiers shim path AND the new
# pyrit.models.identifiers canonical path (because the underlying class was itself
# renamed). The shim's __getattr__ suppresses its standard path-migration warning
# for these names so a single access produces a single, more informative warning
# pointing at the actual replacement class. Tested separately in
# ``test_scorer_identifier_*`` below.
NAMES_DEPRECATED_AT_NEW_PATH = {"ScorerIdentifier"}
FORWARD_ONLY_NAMES = [n for n in shim.__all__ if n not in NAMES_DEPRECATED_AT_NEW_PATH]


@pytest.fixture(autouse=True)
def _reset_warning_caches():
    """Reset every shim's per-process `_warned` set so each test starts clean."""
    saved = {}
    modules = [shim, new, models_pkg] + [m for m, _, _ in SUBMODULE_PAIRS]
    for mod in modules:
        saved[mod] = set(mod._warned)
        mod._warned.clear()
    try:
        yield
    finally:
        for mod, original in saved.items():
            mod._warned.clear()
            mod._warned.update(original)


@pytest.mark.parametrize("name", shim.__all__)
def test_top_level_shim_forwards_to_new_module(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim_obj = getattr(shim, name)
        new_obj = getattr(new, name)
    assert shim_obj is new_obj


@pytest.mark.parametrize("name", FORWARD_ONLY_NAMES)
def test_top_level_shim_emits_one_warning_per_name(name):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        getattr(shim, name)
        getattr(shim, name)
        getattr(shim, name)

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1, f"Expected 1 DeprecationWarning for {name!r}, got {len(dep)}"
    message = str(dep[0].message)
    assert f"pyrit.identifiers.{name}" in message
    assert f"pyrit.models.identifiers.{name}" in message
    assert "0.16.0" in message


def test_scorer_identifier_via_shim_emits_single_rename_warning():
    """`from pyrit.identifiers import ScorerIdentifier` produces ONE warning that points at the
    actual replacement (ComponentIdentifier), not at the deprecated pyrit.models.identifiers path.

    The shim's standard path-migration warning is suppressed for this name so the partner sees a
    single actionable signal in one step.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = shim.ScorerIdentifier
        _ = shim.ScorerIdentifier
        _ = shim.ScorerIdentifier

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1, f"Expected 1 DeprecationWarning, got {len(dep)}: {[str(w.message) for w in dep]}"
    message = str(dep[0].message)
    assert "pyrit.models.identifiers.ScorerIdentifier" in message
    assert "ComponentIdentifier" in message
    assert "0.16.0" in message
    assert result is new.ComponentIdentifier


def test_scorer_identifier_via_canonical_path_emits_single_warning():
    """`from pyrit.models.identifiers import ScorerIdentifier` warns once per process."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = new.ScorerIdentifier
        _ = new.ScorerIdentifier

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1, f"Expected 1 DeprecationWarning, got {len(dep)}"
    message = str(dep[0].message)
    assert "pyrit.models.identifiers.ScorerIdentifier" in message
    assert "ComponentIdentifier" in message
    assert "0.16.0" in message
    assert result is new.ComponentIdentifier


def test_scorer_identifier_via_models_package_emits_single_warning():
    """`from pyrit.models import ScorerIdentifier` warns once per process."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = models_pkg.ScorerIdentifier
        _ = models_pkg.ScorerIdentifier

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1, f"Expected 1 DeprecationWarning, got {len(dep)}"
    message = str(dep[0].message)
    assert "pyrit.models.ScorerIdentifier" in message
    assert "ComponentIdentifier" in message
    assert "0.16.0" in message
    assert result is models_pkg.ComponentIdentifier


def test_top_level_shim_attribute_error_for_unknown_name():
    with pytest.raises(AttributeError, match="has no attribute 'definitely_not_a_real_name'"):
        _ = shim.definitely_not_a_real_name


def test_top_level_shim_dir_returns_all_public_names():
    assert dir(shim) == sorted(shim.__all__)


@pytest.mark.parametrize("shim_mod, new_mod, label", SUBMODULE_PAIRS)
def test_submodule_shim_forwards_every_name(shim_mod, new_mod, label):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in shim_mod.__all__:
            assert getattr(shim_mod, name) is getattr(new_mod, name), f"{label}.{name} did not forward to new module"


@pytest.mark.parametrize("shim_mod, _new_mod, label", SUBMODULE_PAIRS)
def test_submodule_shim_warns_once_per_name(shim_mod, _new_mod, label):
    for name in shim_mod.__all__:
        shim_mod._warned.clear()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            getattr(shim_mod, name)
            getattr(shim_mod, name)

        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) == 1, f"Expected 1 DeprecationWarning for {label}.{name}, got {len(dep)}"
        message = str(dep[0].message)
        assert f"pyrit.identifiers.{label}.{name}" in message
        assert f"pyrit.models.identifiers.{label}.{name}" in message
        assert "0.16.0" in message


@pytest.mark.parametrize("shim_mod, _new_mod, label", SUBMODULE_PAIRS)
def test_submodule_shim_attribute_error_for_unknown_name(shim_mod, _new_mod, label):
    with pytest.raises(AttributeError, match=f"'pyrit.identifiers.{label}'"):
        _ = shim_mod.definitely_not_a_real_name


def test_submodule_shim_from_import_style_returns_new_class():
    """`from pyrit.identifiers.component_identifier import ComponentIdentifier` works."""
    # Force re-import via importlib to confirm the from-import codepath fires __getattr__.
    importlib.reload(shim_component)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from pyrit.identifiers.component_identifier import ComponentIdentifier as ShimCI

    from pyrit.models.identifiers.component_identifier import ComponentIdentifier as NewCI

    assert ShimCI is NewCI


def test_submodule_shim_attribute_access_style_returns_new_class():
    """`import pyrit.identifiers.X; X.ComponentIdentifier` works."""
    import pyrit.identifiers.component_identifier as mod

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        cls = mod.ComponentIdentifier

    from pyrit.models.identifiers.component_identifier import ComponentIdentifier as NewCI

    assert cls is NewCI


def test_warning_stacklevel_attributes_to_caller():
    """`stacklevel=3` should attribute the warning to the test file, not the shim."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        getattr(shim, "ComponentIdentifier")  # noqa: B009  (intentional attribute access)

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep) == 1
    assert dep[0].filename.endswith("test_deprecation_shim.py"), (
        f"Expected warning attributed to this test file, got {dep[0].filename}"
    )


def test_top_level_shim_does_not_warn_on_internal_attribute_access():
    """Accessing module-level internals (e.g., the helper alias `_new`) must NOT warn."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        _ = shim._new
        _ = shim.__all__
        _ = shim._warned

    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert dep == [], f"Internal-attribute access should not warn, got: {[str(w.message) for w in dep]}"


# Matches statements that import from the deprecated ``pyrit.identifiers``
# package, at module level OR indented inside a function/class body. Both
# ``from <pkg> ...`` and ``import <pkg> ...`` forms are recognised, with or
# without a submodule suffix and with or without an ``as`` alias. Strings
# and comments containing the package name are NOT matched because the regex
# anchors to the start of a logical line and requires the leading token
# (``from`` or ``import``) to be the first non-whitespace text.
_DEPRECATED_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+pyrit\.identifiers(?:\.|\s)|import\s+pyrit\.identifiers(?:\.|\s|$|,))",
    re.MULTILINE,
)


def _shim_package_files(repo_root: Path) -> set[Path]:
    """Return resolved paths of the six shim files inside ``pyrit/identifiers/``.

    These files legitimately reference their own package path (in module
    docstrings, ``AttributeError`` messages, and the deprecation-message
    string formatting), so the scan must skip them.
    """
    shim_dir = repo_root / "pyrit" / "identifiers"
    return {p.resolve() for p in shim_dir.rglob("*.py")}


def test_no_internal_callers_of_deprecated_pyrit_identifiers_path():
    """Production and test code must not import from the deprecated shim path.

    Internal code should import from ``pyrit.models.identifiers`` directly. The
    ``pyrit.identifiers`` package exists only as a backwards-compatibility shim
    for external users and will be removed in 0.16.0. Letting internal callers
    rely on it would:

    * Drown the test suite in ``DeprecationWarning`` noise.
    * Make the eventual 0.16.0 shim removal a much bigger churn.
    * Hide bugs caused by the shim path having weaker static typing (PEP 562
      ``__getattr__`` returns ``Any``).

    A regex-based static scan beats a runtime ``-W error`` filter here because
    it catches files that aren't exercised by any test (e.g. optional backend
    modules) and produces a clear, file-and-line error message — no special
    pytest command to remember.
    """
    repo_root = Path(__file__).resolve().parents[3]
    pyrit_dir = repo_root / "pyrit"
    tests_dir = repo_root / "tests"

    allowed = _shim_package_files(repo_root) | {Path(__file__).resolve()}

    offenders: list[str] = []
    for root in (pyrit_dir, tests_dir):
        for path in root.rglob("*.py"):
            if path.resolve() in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _DEPRECATED_IMPORT_RE.match(line):
                    rel = path.relative_to(repo_root)
                    offenders.append(f"  {rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Found internal imports from the deprecated `pyrit.identifiers` path. "
        "Replace each with the equivalent `pyrit.models.identifiers...` import:\n" + "\n".join(offenders)
    )


def test_regression_guard_detects_a_deliberate_offender():
    """Meta-test: the regression-guard scanner above must actually flag offenders.

    Without this test, the scanner could silently regress (e.g. a typo in the
    regex) and we wouldn't notice — the guard would pass vacuously on a clean
    tree. Here we hand the scanner a synthetic offender file and confirm the
    regex matches every legitimate import form.
    """
    samples = [
        "from pyrit.identifiers import ComponentIdentifier",
        "from pyrit.identifiers.component_identifier import ComponentIdentifier",
        "import pyrit.identifiers",
        "import pyrit.identifiers.component_identifier",
        "import pyrit.identifiers as ident",
        "    from pyrit.identifiers import ComponentIdentifier",  # indented (lazy import)
    ]
    for source_line in samples:
        assert _DEPRECATED_IMPORT_RE.match(source_line), (
            f"Regression guard regex failed to match a legitimate offender: {source_line!r}"
        )

    # And confirm it does NOT match strings/comments/docstrings that merely
    # mention the deprecated path. Otherwise the shim's own deprecation message
    # text and this test file would create false positives.
    non_offenders = [
        "# from pyrit.identifiers import ComponentIdentifier",
        '"""See pyrit.identifiers for the legacy path."""',
        'old_item = "pyrit.identifiers.ComponentIdentifier"',
        "from pyrit.models.identifiers import ComponentIdentifier",
        "import pyrit.models.identifiers",
    ]
    for source_line in non_offenders:
        assert not _DEPRECATED_IMPORT_RE.match(source_line), (
            f"Regression guard regex produced a false positive on: {source_line!r}"
        )
