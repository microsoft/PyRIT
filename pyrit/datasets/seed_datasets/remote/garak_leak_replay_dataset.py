# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Cloze passages used by garak's ``leakreplay`` probes, fetched from the garak repository.

Each corpus is a headerless TSV in which every line holds a proper name and a passage
that replaces that name with ``[MASK]``. The passages are excerpts of copyrighted works,
so PyRIT does not bundle them; these loaders download them from a pinned garak commit.
Each line becomes one ``SeedPrompt`` whose value is the passage, kept verbatim, with the
masked name in ``metadata["trigger"]``.

Reference: [@chang2023speak; @derczynski2024garak]
"""

import asyncio
import csv
import logging
from collections.abc import Callable
from typing import IO, Any, ClassVar, Literal

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import _RemoteDatasetLoader
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


def _read_cloze_tsv(file: IO[Any]) -> list[dict[str, str]]:
    """
    Read a headerless trigger/passage TSV without treating quotes as field delimiters.

    Returns:
        list[dict[str, str]]: One ``trigger``/``passage`` row per line.

    Raises:
        ValueError: If a line does not have exactly two tab-separated fields.
    """
    rows: list[dict[str, str]] = []
    for line_number, row in enumerate(csv.reader(file, delimiter="\t", quoting=csv.QUOTE_NONE), start=1):
        if len(row) != 2:
            raise ValueError(
                f"Leak replay line {line_number} has {len(row)} tab-separated fields; expected trigger and passage."
            )
        rows.append({"trigger": row[0], "passage": row[1]})
    return rows


def _write_cloze_tsv(file: IO[Any], examples: list[dict[str, str]]) -> None:
    """Write trigger/passage rows in the same headerless TSV format."""
    file.writelines(f"{example['trigger']}\t{example['passage']}\n" for example in examples)


class _GarakLeakReplayDataset(_RemoteDatasetLoader):
    """
    Base loader for garak's leak replay cloze corpora.

    Subclasses set ``SOURCE_FILE``, ``CORPUS`` and ``_DATASET_NAME``. Rows are parsed with
    ``csv.QUOTE_NONE`` so that each line is one passage. garak's own reader uses the default
    quoting, which treats quotation marks as field delimiters, merges 185 lines of
    ``book_cloze.tsv`` into 5 rows, and loads 1,239 passages instead of 1,419.

    Reference: [@chang2023speak; @derczynski2024garak]
    """

    should_register = False

    COMMIT: ClassVar[str] = "3f50ea5ff9cd7050099940647c15c39b07a93392"
    MASK: ClassVar[str] = "[MASK]"
    SOURCE_FILE: ClassVar[str]
    CORPUS: ClassVar[str]
    _DATASET_NAME: ClassVar[str]
    FILE_TYPE_HANDLERS: ClassVar[dict[str, dict[str, Callable[..., Any]]]] = {
        **_RemoteDatasetLoader.FILE_TYPE_HANDLERS,
        "tsv": {"read": _read_cloze_tsv, "write": _write_cloze_tsv},
    }
    SOURCE_AUTHORS: ClassVar[list[str]] = [
        "Leon Derczynski",
        "Erick Galinkin",
        "Jeffrey Martin",
        "Subho Majumdar",
        "Nanna Inie",
    ]
    SOURCE_GROUPS: ClassVar[list[str]] = [
        "NVIDIA",
        "IT University of Copenhagen",
        "University of Washington",
        "Vijil.ai",
    ]

    harm_categories: list[str] = ["copyright", "training_data_leakage"]
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    tags: set[str] = {"safety"}

    def __init__(
        self,
        *,
        source: str | None = None,
        source_type: Literal["public_url", "file"] = "public_url",
    ) -> None:
        """
        Initialize the loader.

        Args:
            source: URL or local path of the TSV. Defaults to the file at the pinned garak commit.
            source_type: ``"public_url"`` or ``"file"``.
        """
        self.source = source or self.dataset_url()
        self.source_type: Literal["public_url", "file"] = source_type

    @classmethod
    def dataset_url(cls) -> str:
        """
        Return the raw URL of this corpus at the pinned garak commit.

        Returns:
            str: The pinned raw GitHub URL.
        """
        return f"https://raw.githubusercontent.com/NVIDIA/garak/{cls.COMMIT}/garak/data/{cls.SOURCE_FILE}"

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return self._DATASET_NAME

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the corpus and return one passage seed per line.

        Args:
            cache: Whether to cache the downloaded TSV. Defaults to True.

        Returns:
            SeedDataset: The passages in file order.

        Raises:
            ValueError: If a line has an empty trigger or a passage without exactly one mask.
        """
        logger.info(f"Loading garak leak replay corpus {self.CORPUS} from {self.source}")
        rows = await asyncio.to_thread(
            self._fetch_from_url, source=self.source, source_type=self.source_type, cache=cache
        )
        seeds: list[SeedUnion] = []
        for line_number, row in enumerate(rows, start=1):
            trigger, passage = row["trigger"], row["passage"]
            if not trigger.strip():
                raise ValueError(f"Leak replay {self.CORPUS} line {line_number} has an empty trigger.")
            if passage.count(self.MASK) != 1:
                raise ValueError(f"Leak replay {self.CORPUS} line {line_number} must contain exactly one {self.MASK}.")
            seeds.append(self._build_seed(line_number=line_number, trigger=trigger, passage=passage))
        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")
        logger.info(f"Successfully loaded {len(seeds)} passages from garak leak replay corpus {self.CORPUS}")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    def _build_seed(self, *, line_number: int, trigger: str, passage: str) -> SeedPrompt:
        """
        Build the seed for one line.

        Args:
            line_number: The 1-based line in the source file.
            trigger: The masked proper name.
            passage: The passage containing ``[MASK]``.

        Returns:
            SeedPrompt: The passage with its trigger, corpus and line in metadata.
        """
        return SeedPrompt(
            value=passage,
            data_type="text",
            dataset_name=self.dataset_name,
            harm_categories=list(self.harm_categories),
            source=self.dataset_url(),
            authors=list(self.SOURCE_AUTHORS),
            groups=list(self.SOURCE_GROUPS),
            metadata={"trigger": trigger, "corpus": self.CORPUS, "line": line_number},
        )


class _GarakLeakReplayLiteratureDataset(_GarakLeakReplayDataset):
    """
    Literature passages from garak's ``book_cloze.tsv`` (1,419 lines).

    Reference: [@chang2023speak; @derczynski2024garak]
    """

    should_register = True
    SOURCE_FILE: ClassVar[str] = "book_cloze.tsv"
    CORPUS: ClassVar[str] = "literature"
    _DATASET_NAME: ClassVar[str] = "garak_leak_replay_literature"
    size: str = "large"


class _GarakLeakReplayPotterDataset(_GarakLeakReplayDataset):
    """
    Harry Potter passages from garak's ``potter_cloze.tsv`` (30 lines).

    Reference: [@chang2023speak; @derczynski2024garak]
    """

    should_register = True
    SOURCE_FILE: ClassVar[str] = "potter_cloze.tsv"
    CORPUS: ClassVar[str] = "potter"
    _DATASET_NAME: ClassVar[str] = "garak_leak_replay_potter"
    size: str = "small"


class _GarakLeakReplayNYTDataset(_GarakLeakReplayDataset):
    """
    New York Times passages from garak's ``nyt_cloze.tsv`` (32 lines).

    Reference: [@chang2023speak; @derczynski2024garak]
    """

    should_register = True
    SOURCE_FILE: ClassVar[str] = "nyt_cloze.tsv"
    CORPUS: ClassVar[str] = "nyt"
    _DATASET_NAME: ClassVar[str] = "garak_leak_replay_nyt"
    size: str = "small"


class _GarakLeakReplayGuardianDataset(_GarakLeakReplayDataset):
    """
    Guardian passages from garak's ``guardian_cloze.tsv`` (9 lines).

    Reference: [@chang2023speak; @derczynski2024garak]
    """

    should_register = True
    SOURCE_FILE: ClassVar[str] = "guardian_cloze.tsv"
    CORPUS: ClassVar[str] = "guardian"
    _DATASET_NAME: ClassVar[str] = "garak_leak_replay_guardian"
    size: str = "tiny"
