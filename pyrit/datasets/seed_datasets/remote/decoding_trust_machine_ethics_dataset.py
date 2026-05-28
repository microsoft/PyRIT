# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import re
from typing import Literal

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


# Pinned commit SHA of AI-secure/DecodingTrust `main` (2024-09-16). Defined locally
# rather than shared with the Toxicity loader so each perspective can be re-pinned
# independently if upstream regenerates one subset but not the others.
_DECODING_TRUST_COMMIT = "161ae8321ced62f45fcd9ceb412e05b47c603cd4"
_BASE_URL = f"https://raw.githubusercontent.com/AI-secure/DecodingTrust/{_DECODING_TRUST_COMMIT}/data/machine_ethics/"
_CSV_URL = f"{_BASE_URL}jiminy_subset.csv"

# Source Morality field uses the pattern "{good|bad}, {self|others}, {1|2|3}".
# Multiple labels in one row are joined with "\n" (e.g. "bad, others, 2\nbad, self, 2").
_MORALITY_LABEL_RE = re.compile(r"^(bad|good)\s*,\s*(self|others)\s*,\s*([123])\s*$")

# Required CSV columns. The source file ships extra trailing junk columns
# ("Unnamed: 8/9/10", "Annotation completed by Dan and Mantas") that we ignore.
_REQUIRED_COLUMNS = ("Description", "Morality")


class _DecodingTrustMachineEthicsDataset(_RemoteDatasetLoader):
    """
    Loader for the Machine Ethics perspective of the DecodingTrust benchmark.

    DecodingTrust [1] evaluates LLM trustworthiness across eight perspectives.
    For Machine Ethics it bundles the human-annotated Jiminy Cricket [2] subset:
    2091 actions extracted from interactive-fiction games, each labelled by
    humans with a structured ``Morality`` field of the form
    ``"{good|bad}, {self|others}, {1|2|3}"`` (with multi-label rows joining
    multiple labels via ``\\n``). Some rows carry no morality label and
    represent a neutral baseline.

    The two JSON splits shipped alongside the CSV (``jiminy_train.json``,
    ``jiminy_test.json``) encode a label vector whose schema is not documented
    in the DecodingTrust repository and so are intentionally **not** loaded
    here; they can be added in a follow-up loader once the schema is
    confirmed against the paper.

    The loader fetches the CSV at runtime from ``raw.githubusercontent.com``
    (no redistribution) at a pinned commit SHA.

    Per-prompt ``harm_categories`` are derived from each row's ``Morality``
    label(s) using the source's terminology: ``bad_to_self``, ``bad_to_others``,
    ``good_to_self``, ``good_to_others``. The intensity (1-3) is not folded
    into the category name but is preserved per-seed in ``metadata`` so callers
    can filter or rank by severity.

    References:
        - Wang et al., 2023. *DecodingTrust: A Comprehensive Assessment of
          Trustworthiness in GPT Models.* https://arxiv.org/abs/2306.11698
        - Hendrycks et al., 2021. *What Would Jiminy Cricket Do? Towards
          Agents That Behave Morally.* https://arxiv.org/abs/2110.13136
        - https://github.com/AI-secure/DecodingTrust

    License:
        DecodingTrust is distributed under CC BY-SA 4.0. PyRIT fetches the
        annotations at runtime and does not redistribute them. Full attribution
        to both the DecodingTrust authors and the Jiminy Cricket authors is
        recorded on every ``SeedPrompt`` produced.

    Warning:
        The dataset describes harmful actions (self-harm, violence, theft,
        deception) extracted from text-adventure games. Use only for
        authorised safety and ethics evaluation.
    """

    # Class-level metadata picked up by _RemoteDatasetLoader._parse_metadata.
    modalities: list[str] = ["text"]
    size: str = "large"  # 2091 raw rows; ~1.5k after default morality="bad" filter
    tags: set[str] = {"default", "safety", "machine_ethics"}

    _AUTHORS: tuple[str, ...] = (
        # DecodingTrust authors
        "Boxin Wang",
        "Weixin Chen",
        "Hengzhi Pei",
        "Chulin Xie",
        "Mintong Kang",
        "Chenhui Zhang",
        "Chejian Xu",
        "Zidi Xiong",
        "Ritik Dutta",
        "Rylan Schaeffer",
        "Sang T. Truong",
        "Simran Arora",
        "Mantas Mazeika",
        "Dan Hendrycks",
        "Zinan Lin",
        "Yu Cheng",
        "Sanmi Koyejo",
        "Dawn Song",
        "Bo Li",
        # Jiminy Cricket authors (whose annotations DecodingTrust uses)
        "Andy Zou",
        "Tristan Xiao",
        "Ryan Jia",
        "John Kwon",
        "Jacob Steinhardt",
    )

    _GROUPS: tuple[str, ...] = (
        "University of Illinois Urbana-Champaign",
        "Stanford University",
        "University of California, Berkeley",
        "Center for AI Safety",
        "Microsoft Research",
        "Allen Institute for Artificial Intelligence",
        "University of Washington",
    )

    _DESCRIPTION = (
        "Machine Ethics perspective of the DecodingTrust benchmark (Wang et al., 2023). "
        "Loads the human-annotated Jiminy Cricket subset (Hendrycks et al., 2021): "
        "2091 actions extracted from text-adventure games, each labelled with a "
        "structured `Morality` field of the form `{good|bad}, {self|others}, {1|2|3}`. "
        "Per-prompt `harm_categories` are derived from these labels at load time; "
        "the intensity (1-3) is preserved in `metadata['max_intensity']`."
    )

    def __init__(
        self,
        *,
        morality: Literal["bad", "good", "neutral", "all"] = "bad",
        min_intensity: int = 1,
    ) -> None:
        """
        Initialize the DecodingTrust Machine Ethics dataset loader.

        Args:
            morality: Which rows to retain. ``"bad"`` (default) keeps only rows
                whose ``Morality`` field contains at least one ``bad, *, *``
                label — matches the red-teaming use case. ``"good"`` keeps only
                rows with a ``good, *, *`` label. ``"neutral"`` keeps only
                unlabelled rows. ``"all"`` retains every row, including
                unlabelled ones.
            min_intensity: Minimum max-intensity (1, 2 or 3) to retain a row.
                Defaults to 1 (no severity filtering). Unlabelled rows have
                effective intensity 0 and are dropped when ``min_intensity >= 1``
                under ``morality="all"``.
        """
        self.morality: Literal["bad", "good", "neutral", "all"] = morality
        self.min_intensity = min_intensity

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "decoding_trust_machine_ethics"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the DecodingTrust Machine Ethics prompts and return them as a SeedDataset.

        Args:
            cache: Whether to cache the fetched CSV locally. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset whose seeds are the selected Jiminy actions.

        Raises:
            ValueError: If the source CSV is missing the required columns.
        """
        logger.info(
            f"Loading DecodingTrust Machine Ethics (Jiminy subset) "
            f"morality={self.morality!r} min_intensity={self.min_intensity} from {_CSV_URL}"
        )

        rows = self._fetch_from_url(source=_CSV_URL, source_type="public_url", cache=cache)
        seed_prompts = self._rows_to_seed_prompts(rows=rows)
        logger.info(f"Loaded {len(seed_prompts)} prompts from DecodingTrust Machine Ethics")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _rows_to_seed_prompts(self, *, rows: list[dict[str, str]]) -> list[SeedPrompt]:
        """
        Convert raw CSV rows into SeedPrompt instances, applying morality and intensity filters.

        Args:
            rows: List of CSV rows as dicts (``csv.DictReader`` output from
                ``_fetch_from_url``).

        Returns:
            List of SeedPrompt objects, one per row that passes filters and has
            a non-empty ``Description``.

        Raises:
            ValueError: If the first row is missing any required column. We
                only check the first row because the CSV has fixed columns.
        """
        if rows:
            missing = [c for c in _REQUIRED_COLUMNS if c not in rows[0]]
            if missing:
                raise ValueError(f"DecodingTrust Machine Ethics CSV is missing required columns: {', '.join(missing)}")

        seed_prompts: list[SeedPrompt] = []
        for row in rows:
            description = (row.get("Description") or "").strip()
            if not description:
                logger.warning("Skipping row with empty 'Description'")
                continue

            categories, max_intensity = self._parse_morality(raw=row.get("Morality") or "")

            if not self._passes_morality_filter(categories=categories):
                continue
            if max_intensity < self.min_intensity:
                continue

            seed_prompts.append(
                SeedPrompt(
                    value=description,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=categories,
                    description=self._DESCRIPTION,
                    source=_CSV_URL,
                    authors=list(self._AUTHORS),
                    groups=list(self._GROUPS),
                    metadata={
                        "source_file": row.get("File", ""),
                        "source_line": self._coerce_int(row.get("Line", "")),
                        "morality_raw": row.get("Morality", ""),
                        "max_intensity": max_intensity,
                        "neighboring_text": row.get("Neighboring text", ""),
                    },
                )
            )
        return seed_prompts

    def _parse_morality(self, *, raw: str) -> tuple[list[str], int]:
        """
        Parse a Morality cell into a list of harm categories and the max intensity.

        Args:
            raw: Raw Morality cell value. May be empty (neutral), a single
                label, or multiple labels joined by ``\\n``.

        Returns:
            A tuple ``(categories, max_intensity)``. ``categories`` is the
            de-duplicated list of harm category strings (e.g. ``["bad_to_self",
            "bad_to_others"]``) in stable insertion order, with intensity
            stripped. ``max_intensity`` is 0 for empty input, otherwise the
            maximum intensity across recognised labels (1-3). Malformed labels
            are logged and skipped — a row with at least one valid label still
            yields a SeedPrompt.
        """
        categories: list[str] = []
        seen: set[str] = set()
        max_intensity = 0
        for line in raw.split("\n"):
            label = line.strip()
            if not label:
                continue
            match = _MORALITY_LABEL_RE.match(label)
            if not match:
                logger.warning(f"Skipping malformed Morality label: {label!r}")
                continue
            polarity, target, intensity_str = match.groups()
            category = f"{polarity}_to_{target}"
            if category not in seen:
                seen.add(category)
                categories.append(category)
            max_intensity = max(max_intensity, int(intensity_str))
        return categories, max_intensity

    def _passes_morality_filter(self, *, categories: list[str]) -> bool:
        """
        Return True if a row's parsed categories satisfy ``self.morality``.

        Args:
            categories: Parsed harm category list for the row (possibly empty).

        Returns:
            True if the row should be included given the current ``morality``
            filter setting.
        """
        if self.morality == "all":
            return True
        if self.morality == "neutral":
            return not categories
        prefix = f"{self.morality}_to_"  # "bad_to_" or "good_to_"
        return any(c.startswith(prefix) for c in categories)

    @staticmethod
    def _coerce_int(value: str) -> int:
        """
        Best-effort int coercion for the CSV ``Line`` column.

        Args:
            value: Raw cell value.

        Returns:
            Parsed integer, or 0 if the cell is empty or non-numeric. We don't
            fail the row on a missing line number — it's metadata, not data.
        """
        try:
            return int(value.strip())
        except (ValueError, AttributeError):
            return 0
