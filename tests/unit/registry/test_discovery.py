# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for shared registry discovery utilities."""

import abc
from types import ModuleType

from pyrit.registry.discovery import discover_exported_subclasses


class _Base:
    pass


class _ConcreteA(_Base):
    pass


class _ConcreteB(_Base):
    pass


class _AbstractChild(_Base, abc.ABC):
    @abc.abstractmethod
    def do(self) -> None: ...


class _Unrelated:
    pass


def _make_module(*, all_names: list[str] | None, **attrs: object) -> ModuleType:
    module = ModuleType("fake_discovery_module")
    for name, value in attrs.items():
        setattr(module, name, value)
    if all_names is not None:
        module.__all__ = all_names  # type: ignore[attr-defined]
    return module


def test_discover_exported_subclasses_yields_concrete_subclasses() -> None:
    module = _make_module(
        all_names=["_ConcreteA", "_ConcreteB", "_Unrelated", "_Base"],
        _ConcreteA=_ConcreteA,
        _ConcreteB=_ConcreteB,
        _Unrelated=_Unrelated,
        _Base=_Base,
    )

    discovered = set(discover_exported_subclasses(module=module, base_class=_Base))

    assert discovered == {_ConcreteA, _ConcreteB}


def test_discover_exported_subclasses_excludes_abstract_and_base() -> None:
    module = _make_module(
        all_names=["_Base", "_AbstractChild", "_ConcreteA"],
        _Base=_Base,
        _AbstractChild=_AbstractChild,
        _ConcreteA=_ConcreteA,
    )

    discovered = set(discover_exported_subclasses(module=module, base_class=_Base))

    assert discovered == {_ConcreteA}


def test_discover_exported_subclasses_honors_all_export_list() -> None:
    # _ConcreteB is an attribute but not exported via __all__, so it is skipped.
    module = _make_module(
        all_names=["_ConcreteA"],
        _ConcreteA=_ConcreteA,
        _ConcreteB=_ConcreteB,
    )

    discovered = list(discover_exported_subclasses(module=module, base_class=_Base))

    assert discovered == [_ConcreteA]


def test_discover_exported_subclasses_ignores_non_class_and_missing_names() -> None:
    module = _make_module(
        all_names=["_ConcreteA", "not_a_class", "missing_attr"],
        _ConcreteA=_ConcreteA,
        not_a_class="just a string",
    )

    discovered = list(discover_exported_subclasses(module=module, base_class=_Base))

    assert discovered == [_ConcreteA]


def test_discover_exported_subclasses_falls_back_to_dir_without_all() -> None:
    module = _make_module(all_names=None, _ConcreteA=_ConcreteA, _Unrelated=_Unrelated)

    discovered = set(discover_exported_subclasses(module=module, base_class=_Base))

    assert discovered == {_ConcreteA}
