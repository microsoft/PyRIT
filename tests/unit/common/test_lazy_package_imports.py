# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import importlib
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest

_REPOSITORY_ROOT = Path(__file__).parents[3]
_PACKAGE_ROOT = _REPOSITORY_ROOT / "pyrit"
# Remove an entry when its package adopts the standard lazy export contract.
# The inventory test rejects both unlisted eager packages and stale exceptions.
_EAGER_PACKAGE_EXCEPTIONS = frozenset(
    {
        "pyrit.executor.promptgen.gcg",
    }
)

_LAZY_IMPORT_SPOT_CHECKS = [
    (
        "pyrit.models",
        "Message",
        "pyrit.models.messages.message",
        "pyrit.models.question_answering",
    ),
    (
        "pyrit.models.catalog",
        "RegisteredInitializer",
        "pyrit.models.catalog.initializer",
        "pyrit.models.catalog.scenario",
    ),
    (
        "pyrit.models.identifiers",
        "validate_registry_name",
        "pyrit.models.identifiers.class_name_utils",
        "pyrit.models.identifiers.evaluation_identifier",
    ),
    (
        "pyrit.models.messages",
        "Message",
        "pyrit.models.messages.message",
        "pyrit.models.messages.chat_message",
    ),
    (
        "pyrit.models.results",
        "StrategyResult",
        "pyrit.models.results.strategy_result",
        "pyrit.models.results.scenario_result",
    ),
    (
        "pyrit.models.score",
        "Condition",
        "pyrit.models.score.condition",
        "pyrit.models.score.score",
    ),
    (
        "pyrit.models.seeds",
        "Seed",
        "pyrit.models.seeds.seed",
        "pyrit.models.seeds.yaml_seed_loader",
    ),
    (
        "pyrit.models.target",
        "TokenUsage",
        "pyrit.models.target.token_usage",
        "pyrit.models.target.json_schema_definition",
    ),
    (
        "pyrit.converter",
        "Base64Converter",
        "pyrit.converter.base64_converter",
        "pyrit.converter.audio_echo_converter",
    ),
    (
        "pyrit.datasets",
        "SeedDatasetProvider",
        "pyrit.datasets.seed_datasets.seed_dataset_provider",
        "pyrit.datasets.seed_datasets.remote",
    ),
    (
        "pyrit.memory",
        "CentralMemory",
        "pyrit.memory.central_memory",
        "pyrit.memory.sqlite_memory",
    ),
    (
        "pyrit.prompt_target",
        "OpenAIChatTarget",
        "pyrit.prompt_target.openai.openai_chat_target",
        "pyrit.prompt_target.hugging_face.hugging_face_chat_target",
    ),
    (
        "pyrit.scenario",
        "Scenario",
        "pyrit.scenario.core.scenario",
        "pyrit.scenario.scenarios.airt",
    ),
    (
        "pyrit.score",
        "SubStringScorer",
        "pyrit.score.true_false.substring_scorer",
        "pyrit.score.true_false.audio_true_false_scorer",
    ),
]


def _is_type_checking_guard(test: ast.expr) -> bool:
    """Return whether an expression is a ``TYPE_CHECKING`` guard."""
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
        and test.attr == "TYPE_CHECKING"
    )


class _RuntimeImportCollector(ast.NodeVisitor):
    """Collect non-standard imports that execute when a package is imported."""

    def __init__(self) -> None:
        self.imports: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_If(self, node: ast.If) -> None:
        if not _is_type_checking_guard(node.test):
            for statement in node.body:
                self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "pyrit.common.lazy_imports":
                continue
            if alias.name.split(".", maxsplit=1)[0] not in sys.stdlib_module_names:
                self.imports.append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = node.module or ""
        if node.level == 0 and module_name == "pyrit.common.lazy_imports":
            return
        if node.level or module_name.split(".", maxsplit=1)[0] not in sys.stdlib_module_names:
            self.imports.append(f"{'.' * node.level}{module_name}")


def _initializer_paths() -> tuple[Path, ...]:
    """Return every PyRIT package initializer."""
    return tuple(sorted(_PACKAGE_ROOT.rglob("__init__.py")))


def _package_name(init_path: Path) -> str:
    """Return the dotted package name for an initializer."""
    relative_parts = init_path.parent.relative_to(_PACKAGE_ROOT).parts
    return ".".join(("pyrit", *relative_parts))


def _assigned_value(*, tree: ast.Module, name: str) -> ast.expr | None:
    """Return the module-level value assigned to a name."""
    for statement in tree.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            return statement.value
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            return statement.value
    return None


def _is_empty_all(value: ast.expr | None) -> bool:
    """Return whether ``__all__`` is an empty literal."""
    return isinstance(value, (ast.List, ast.Tuple)) and not value.elts


def _lazy_export_names(tree: ast.Module) -> tuple[str, ...] | None:
    """Return names from a literal ``_LAZY_EXPORTS`` map."""
    value = _assigned_value(tree=tree, name="_LAZY_EXPORTS")
    if not isinstance(value, ast.Dict):
        return None

    names: list[str] = []
    for key in value.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        names.append(key.value)
    return tuple(names)


def _all_derives_from_lazy_exports(tree: ast.Module) -> bool:
    """Return whether ``__all__`` is exactly ``list(_LAZY_EXPORTS)``."""
    value = _assigned_value(tree=tree, name="__all__")
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "list"
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Name)
        and value.args[0].id == "_LAZY_EXPORTS"
        and not value.keywords
    )


def _type_checking_import_names(tree: ast.Module) -> set[str]:
    """Return names imported below module-level ``TYPE_CHECKING`` guards."""
    names: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.If) or not _is_type_checking_guard(statement.test):
            continue
        for node in ast.walk(ast.Module(body=statement.body, type_ignores=[])):
            if isinstance(node, ast.Import):
                names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.asname or alias.name for alias in node.names)
    return names


def _module_function_names(tree: ast.Module) -> set[str]:
    """Return function names defined directly in a module."""
    return {statement.name for statement in tree.body if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _lazy_helper_import_names(tree: ast.Module) -> set[str]:
    """Return names imported from the shared lazy import helper."""
    names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "pyrit.common.lazy_imports":
            names.update(alias.asname or alias.name for alias in statement.names)
    return names


def _runtime_non_standard_imports(tree: ast.Module) -> list[str]:
    """Return non-standard imports that execute while importing a package."""
    collector = _RuntimeImportCollector()
    collector.visit(tree)
    return collector.imports


def _non_exempt_public_initializers() -> tuple[Path, ...]:
    """Return public package initializers that must use the lazy contract."""
    paths: list[Path] = []
    for init_path in _initializer_paths():
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
        all_value = _assigned_value(tree=tree, name="__all__")
        lazy_exports_value = _assigned_value(tree=tree, name="_LAZY_EXPORTS")
        if lazy_exports_value is not None or (all_value is not None and not _is_empty_all(all_value)):
            paths.append(init_path)
    return tuple(paths)


def _assert_subprocess_succeeds(code: str) -> None:
    """Run Python code in a clean interpreter and require success."""
    result = subprocess.run(
        [sys.executable, "-c", dedent(code)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_all_package_initializers_are_lazy_or_exempt() -> None:
    eager_imports: dict[str, list[str]] = {}
    for init_path in _initializer_paths():
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
        imports = _runtime_non_standard_imports(tree)
        if imports:
            eager_imports[_package_name(init_path)] = imports

    eager_packages = set(eager_imports)
    unexpected = sorted(eager_packages - _EAGER_PACKAGE_EXCEPTIONS)
    stale = sorted(_EAGER_PACKAGE_EXCEPTIONS - eager_packages)
    unexpected_details = [f"{package}: {eager_imports[package]}" for package in unexpected]

    assert not unexpected and not stale, (
        f"Unlisted eager packages: {unexpected_details}\n"
        f"Stale eager-package exceptions: {stale}\n"
        "Convert unlisted packages to the standard lazy export contract. "
        "Remove an exception when its package no longer imports PyRIT modules eagerly."
    )


@pytest.mark.parametrize(
    "init_path",
    _non_exempt_public_initializers(),
    ids=lambda path: _package_name(path),
)
def test_non_exempt_public_package_uses_standard_lazy_contract(init_path: Path) -> None:
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    lazy_export_names = _lazy_export_names(tree)
    function_names = _module_function_names(tree)
    helper_import_names = _lazy_helper_import_names(tree)

    assert lazy_export_names is not None, f"{_package_name(init_path)} must define a literal _LAZY_EXPORTS map"
    assert _all_derives_from_lazy_exports(tree), f"{_package_name(init_path)} must set __all__ = list(_LAZY_EXPORTS)"
    assert {"__getattr__", "__dir__"} <= function_names
    assert {"get_lazy_dir", "resolve_lazy_export"} <= helper_import_names

    missing_type_imports = set(lazy_export_names) - _type_checking_import_names(tree)
    assert not missing_type_imports, (
        f"{_package_name(init_path)} lacks TYPE_CHECKING imports for: {sorted(missing_type_imports)}"
    )


def test_lazy_packages_do_not_load_child_modules() -> None:
    package_names = [_package_name(path) for path in _non_exempt_public_initializers()]
    _assert_subprocess_succeeds(
        f"""
        import importlib
        import sys

        package_names = {package_names!r}
        for package_name in sorted(package_names, key=lambda name: name.count(".")):
            package = importlib.import_module(package_name)
            descendants = [name for name in sys.modules if name.startswith(f"{{package_name}}.")]
            if package_name == "pyrit":
                descendants = [
                    name
                    for name in descendants
                    if name not in {{"pyrit.common", "pyrit.common.lazy_imports"}}
                ]
            elif package_name == "pyrit.common":
                descendants = [name for name in descendants if name != "pyrit.common.lazy_imports"]

            assert not descendants, (package_name, descendants)
            assert package.__all__ == list(package._LAZY_EXPORTS)
            assert set(package._LAZY_EXPORTS) <= set(dir(package))
        """
    )


@pytest.mark.parametrize(
    "package_name",
    [_package_name(path) for path in _non_exempt_public_initializers()],
)
def test_lazy_package_runtime_contract_in_process(package_name: str) -> None:
    package = importlib.import_module(package_name)

    assert set(package.__all__) <= set(dir(package))
    with pytest.raises(AttributeError, match="has no attribute '_missing_lazy_export'"):
        package.__getattr__("_missing_lazy_export")


@pytest.mark.parametrize(
    ("package_name", "export_name", "declaring_module"),
    [(package, export, declaring) for package, export, declaring, _ in _LAZY_IMPORT_SPOT_CHECKS],
    ids=[case[0] for case in _LAZY_IMPORT_SPOT_CHECKS],
)
def test_lazy_import_resolves_and_caches_export_in_process(
    package_name: str,
    export_name: str,
    declaring_module: str,
) -> None:
    package = importlib.import_module(package_name)
    package.__dict__.pop(export_name, None)

    exported_value = package.__getattr__(export_name)
    direct_value = getattr(importlib.import_module(declaring_module), export_name)

    assert exported_value is direct_value
    assert getattr(package, export_name) is direct_value


@pytest.mark.parametrize(
    ("package_name", "export_name", "declaring_module", "unrelated_module"),
    _LAZY_IMPORT_SPOT_CHECKS,
    ids=[case[0] for case in _LAZY_IMPORT_SPOT_CHECKS],
)
def test_lazy_import_spot_check(
    package_name: str,
    export_name: str,
    declaring_module: str,
    unrelated_module: str,
) -> None:
    _assert_subprocess_succeeds(
        f"""
        import importlib
        import sys

        package = importlib.import_module({package_name!r})

        assert {declaring_module!r} not in sys.modules
        assert {unrelated_module!r} not in sys.modules
        assert {export_name!r} in dir(package)

        exported_value = getattr(package, {export_name!r})
        direct_value = getattr(importlib.import_module({declaring_module!r}), {export_name!r})

        assert exported_value is direct_value
        assert getattr(package, {export_name!r}) is exported_value
        assert {unrelated_module!r} not in sys.modules
        """
    )


def test_dataset_catalog_materializes_only_for_complete_discovery() -> None:
    _assert_subprocess_succeeds(
        """
        import sys

        from pyrit.datasets import SeedDatasetProvider

        assert "pyrit.datasets.seed_datasets.local" not in sys.modules
        assert "pyrit.datasets.seed_datasets.remote" not in sys.modules

        providers = SeedDatasetProvider.get_all_providers()

        assert "_JailbreakTemplatesDataset" in providers
        assert "_HarmBenchDataset" in providers
        assert "pyrit.datasets.seed_datasets.local" in sys.modules
        assert "pyrit.datasets.seed_datasets.remote" in sys.modules
        """
    )


def test_dataset_catalog_materializes_for_complete_discovery_in_process() -> None:
    from pyrit.datasets import SeedDatasetProvider

    providers = SeedDatasetProvider.get_all_providers()

    assert "_JailbreakTemplatesDataset" in providers
    assert "_HarmBenchDataset" in providers


@pytest.mark.parametrize("method_name", ["get_all_dataset_names_async", "fetch_datasets_async"])
async def test_dataset_discovery_entrypoint_materializes_builtin_providers(method_name: str) -> None:
    from pyrit.datasets import SeedDatasetProvider

    with (
        patch.object(
            SeedDatasetProvider,
            "_materialize_builtin_providers",
            side_effect=RuntimeError("materialized"),
        ) as materialize,
        pytest.raises(RuntimeError, match="materialized"),
    ):
        await getattr(SeedDatasetProvider, method_name)()

    materialize.assert_called_once_with()


def test_scenario_short_imports_preserve_canonical_identity() -> None:
    _assert_subprocess_succeeds(
        """
        import importlib
        import sys

        import pyrit.scenario

        assert "pyrit.scenario.scenarios.airt" not in sys.modules

        alias_package = importlib.import_module("pyrit.scenario.airt")
        canonical_package = importlib.import_module("pyrit.scenario.scenarios.airt")
        alias_module = importlib.import_module("pyrit.scenario.airt.leakage")
        canonical_module = importlib.import_module("pyrit.scenario.scenarios.airt.leakage")

        assert alias_package is canonical_package
        assert alias_module is canonical_module
        assert alias_package.Leakage is canonical_module.Leakage
        """
    )


def test_scenario_short_imports_preserve_canonical_identity_in_process() -> None:
    alias_package = importlib.import_module("pyrit.scenario.airt")
    canonical_package = importlib.import_module("pyrit.scenario.scenarios.airt")

    assert alias_package is canonical_package


def test_missing_scenario_short_import_raises() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pyrit.scenario.airt.missing_scenario")


def test_dynamic_scenario_techniques_reject_unknown_export() -> None:
    dynamic_techniques = importlib.import_module("pyrit.scenario.scenarios._dynamic_techniques")

    with pytest.raises(AttributeError, match="has no attribute 'UnknownTechnique'"):
        dynamic_techniques.__getattr__("UnknownTechnique")


def test_dynamic_scenario_technique_resolves_and_caches() -> None:
    dynamic_techniques = importlib.import_module("pyrit.scenario.scenarios._dynamic_techniques")
    builder = MagicMock(return_value=object())
    builder_module = MagicMock()
    builder_module.build = builder

    with (
        patch.dict(
            dynamic_techniques._TECHNIQUE_BUILDERS,
            {"TestTechnique": ("test.builder", "build")},
        ),
        patch.object(dynamic_techniques, "import_module", return_value=builder_module),
    ):
        technique = dynamic_techniques.__getattr__("TestTechnique")

    assert dynamic_techniques.TestTechnique is technique
    builder.assert_called_once_with()
    dynamic_techniques.__dict__.pop("TestTechnique")


def test_scenario_registry_materializes_builtin_catalog() -> None:
    _assert_subprocess_succeeds(
        """
        import sys

        from pyrit.registry import ScenarioRegistry

        assert "pyrit.scenario.scenarios.airt.cyber" not in sys.modules

        names = ScenarioRegistry().get_class_names()

        assert "airt.cyber" in names
        assert "garak.encoding" in names
        assert "pyrit.scenario.scenarios.airt.cyber" in sys.modules
        """
    )


def test_scenario_registry_materializes_builtin_catalog_in_process() -> None:
    from pyrit.registry import ScenarioRegistry

    names = ScenarioRegistry().get_class_names()

    assert "airt.cyber" in names
    assert "garak.encoding" in names


def test_function_exports_override_same_named_child_modules() -> None:
    _assert_subprocess_succeeds(
        """
        import importlib

        import pyrit
        import pyrit.common

        show_versions_module = importlib.import_module("pyrit.show_versions")
        apply_defaults_module = importlib.import_module("pyrit.common.apply_defaults")

        assert pyrit.show_versions is show_versions_module.show_versions
        assert pyrit.common.apply_defaults is apply_defaults_module.apply_defaults
        """
    )


def test_function_exports_override_same_named_child_modules_in_process() -> None:
    import pyrit
    import pyrit.common

    show_versions_module = importlib.import_module("pyrit.show_versions")
    apply_defaults_module = importlib.import_module("pyrit.common.apply_defaults")

    assert pyrit.show_versions is show_versions_module.show_versions
    assert pyrit.common.apply_defaults is apply_defaults_module.apply_defaults
