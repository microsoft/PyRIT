# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Package-name reference lists from the garak ``garak-llm`` HuggingFace org.

garak's ``packagehallucination`` detector checks model-generated code for
imports/requires of packages that do not exist in the relevant registry. These
loaders expose the same per-language package registries as ``SeedDataset``s:
every package name is a ``SeedPrompt`` (``data_type="text"``), so the set can be
used both as the hallucination-reference lookup (``{s.value for s in
dataset.seeds}``) and as candidate prompts. The ``package_first_seen`` column,
when present, is preserved in ``SeedPrompt.metadata`` so garak's cutoff-date
filtering remains reproducible.

Each loader pins the same dated snapshot garak currently defaults to per
language.

Reference: [@derczynski2024garak]
"""

from typing import Any, ClassVar

from pyrit.datasets.seed_datasets.remote.garak_dataset import _GarakRemoteDataset
from pyrit.models import Modality


class _GarakPackageHallucinationDataset(_GarakRemoteDataset):
    """
    Base for garak per-language package-name registries.

    The text column is always ``text``; subclasses set ``HF_DATASET_NAME``,
    ``_DATASET_NAME``, and ``_LANGUAGE``. The ``package_first_seen`` metadata
    column name drifts across registries (``package_first_seen`` vs
    ``package first seen``), so both spellings are accepted.
    """

    TEXT_COLUMN: ClassVar[str] = "text"
    METADATA_COLUMNS: ClassVar[dict[str, tuple[str, ...]]] = {
        "package_first_seen": ("package_first_seen", "package first seen"),
    }

    # Programming-language label recorded on each seed's metadata.
    _LANGUAGE: ClassVar[str] = ""

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    tags: frozenset[str] = frozenset({"cybersecurity"})

    def _extract_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """
        Add the programming-language label alongside the package-date metadata.

        Args:
            item: A single raw HuggingFace row.

        Returns:
            dict: Metadata including ``language`` and (when present)
                ``package_first_seen``.
        """
        metadata = super()._extract_metadata(item)
        metadata["language"] = self._LANGUAGE
        return metadata


class _GarakPypiDataset(_GarakPackageHallucinationDataset):
    """
    garak PyPI (Python) package registry.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/pypi-20241031"
    _DATASET_NAME: ClassVar[str] = "garak_pypi_packages"
    _LANGUAGE: ClassVar[str] = "python"
    size: str = "huge"  # ~555k packages


class _GarakNpmDataset(_GarakPackageHallucinationDataset):
    """
    garak npm (JavaScript) package registry.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/npm-20241031"
    _DATASET_NAME: ClassVar[str] = "garak_npm_packages"
    _LANGUAGE: ClassVar[str] = "javascript"
    size: str = "huge"  # ~3.3M packages


class _GarakCratesDataset(_GarakPackageHallucinationDataset):
    """
    garak crates.io (Rust) package registry.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/crates-20250307"
    _DATASET_NAME: ClassVar[str] = "garak_crates_packages"
    _LANGUAGE: ClassVar[str] = "rust"
    size: str = "huge"  # ~156k crates


class _GarakRubyGemsDataset(_GarakPackageHallucinationDataset):
    """
    garak RubyGems (Ruby) package registry.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/rubygems-20241031"
    _DATASET_NAME: ClassVar[str] = "garak_rubygems_packages"
    _LANGUAGE: ClassVar[str] = "ruby"
    size: str = "huge"  # ~181k gems


class _GarakDartDataset(_GarakPackageHallucinationDataset):
    """
    garak pub.dev (Dart) package registry.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/dart-20250811"
    _DATASET_NAME: ClassVar[str] = "garak_dart_packages"
    _LANGUAGE: ClassVar[str] = "dart"
    size: str = "huge"  # ~67k packages


class _GarakPerlDataset(_GarakPackageHallucinationDataset):
    """
    garak CPAN (Perl) package registry.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/perl-20250811"
    _DATASET_NAME: ClassVar[str] = "garak_perl_packages"
    _LANGUAGE: ClassVar[str] = "perl"
    size: str = "huge"  # ~56k modules


class _GarakRakuDataset(_GarakPackageHallucinationDataset):
    """
    garak Raku package registry.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/raku-20250811"
    _DATASET_NAME: ClassVar[str] = "garak_raku_packages"
    _LANGUAGE: ClassVar[str] = "raku"
    size: str = "large"  # ~2.1k modules
