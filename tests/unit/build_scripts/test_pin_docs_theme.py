# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for build_scripts/pin_docs_theme.py."""

from pathlib import Path

import pytest

from build_scripts import pin_docs_theme

PINNED_TEMPLATE = "https://github.com/myst-templates/book-theme/archive/23f2df5493e23dfb1d9433c8aa64c87beb4232a3.zip"


def test_pin_docs_theme_replaces_only_site_template(tmp_path: Path) -> None:
    config = tmp_path / "myst.yml"
    config.write_text(
        "project:\n  exports:\n    - format: pdf\n      template: plain_latex_book\nsite:\n  template: book-theme\n",
        encoding="utf-8",
    )

    pin_docs_theme.pin_docs_theme(config_path=config, template=PINNED_TEMPLATE)

    assert config.read_text(encoding="utf-8") == (
        "project:\n"
        "  exports:\n"
        "    - format: pdf\n"
        "      template: plain_latex_book\n"
        "site:\n"
        f"  template: {PINNED_TEMPLATE}\n"
    )


@pytest.mark.parametrize(
    "template",
    [
        "book-theme",
        "https://github.com/myst-templates/book-theme/archive/main.zip",
        "https://github.com/myst-templates/book-theme/archive/23f2df5.zip",
    ],
)
def test_pin_docs_theme_rejects_mutable_or_abbreviated_template(*, tmp_path: Path, template: str) -> None:
    config = tmp_path / "myst.yml"
    config.write_text("site:\n  template: book-theme\n", encoding="utf-8")

    with pytest.raises(ValueError, match="pinned to a full commit SHA"):
        pin_docs_theme.pin_docs_theme(config_path=config, template=template)


def test_pin_docs_theme_requires_one_unpinned_site_template(tmp_path: Path) -> None:
    config = tmp_path / "myst.yml"
    config.write_text(f"site:\n  template: {PINNED_TEMPLATE}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected exactly one unpinned"):
        pin_docs_theme.pin_docs_theme(config_path=config, template=PINNED_TEMPLATE)
