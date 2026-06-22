# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Audio jailbreak dataset from the garak ``garak-llm`` HuggingFace org.

garak's ``audio`` probe uses the ``audio_achilles_heel`` set of spoken
adversarial prompts. This loader exposes each clip as a ``SeedPrompt`` with
``data_type="audio_path"`` pointing at a locally-cached ``.wav`` file. The
upstream filenames encode a harm category (e.g. ``Malware_Generation_12.wav``),
which is preserved in ``SeedPrompt.harm_categories`` and ``metadata``.

The audio column is loaded with ``Audio(decode=False)`` so the original WAV
bytes are persisted verbatim, avoiding any audio-decoding dependency.

Reference: [@derczynski2024garak]
"""

import logging
import re
from typing import Any, ClassVar

from datasets import Audio
from typing_extensions import override

from pyrit.datasets.seed_datasets.remote._audio_cache import cache_audio_bytes_async
from pyrit.datasets.seed_datasets.remote.garak_dataset import _GarakRemoteDataset
from pyrit.models import Modality, PromptDataType, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)

# Strips a trailing "_<number>" index from a filename stem to recover the
# harm-category prefix (e.g. "Malware_Generation_12" -> "Malware_Generation").
_INDEX_SUFFIX = re.compile(r"_\d+$")


class _GarakAudioAchillesHeelDataset(_GarakRemoteDataset):
    """
    garak ``audio_achilles_heel`` spoken-jailbreak dataset.

    Reference: [@derczynski2024garak]
    """

    should_register = True
    HF_DATASET_NAME: ClassVar[str] = "garak-llm/audio_achilles_heel"
    _DATASET_NAME: ClassVar[str] = "garak_audio_achilles_heel"
    DATA_TYPE: ClassVar[PromptDataType] = "audio_path"

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.AUDIO,)
    size: str = "medium"  # ~350 audio clips
    tags: frozenset[str] = frozenset({"safety", "multimodal", "jailbreak"})

    @staticmethod
    def _harm_category_from_path(path: str | None) -> str | None:
        """
        Derive a harm category from an upstream filename.

        Args:
            path: The upstream audio filename (e.g. ``"Malware_Generation_12.wav"``).

        Returns:
            str | None: The category prefix (e.g. ``"Malware_Generation"``), or
                None if no usable name is present.
        """
        if not path:
            return None
        stem = re.sub(r"\.[^.]+$", "", path)
        category = _INDEX_SUFFIX.sub("", stem)
        return category or None

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the garak audio dataset and return it as a ``SeedDataset``.

        Each clip's raw WAV bytes are cached locally and referenced by an
        ``audio_path`` ``SeedPrompt``.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: The audio dataset.

        Raises:
            ValueError: If no usable seeds remain after processing.
        """
        logger.info(f"Loading garak dataset {self.HF_DATASET_NAME}")
        data = await self._fetch_rows_async(cache=cache)
        # Disable decoding so the original WAV bytes are persisted verbatim
        # (no soundfile/librosa dependency required).
        data = data.cast_column("audio", Audio(decode=False))

        seeds: list[SeedUnion] = []
        for index, item in enumerate(data):
            audio = item.get("audio") or {}
            audio_bytes = audio.get("bytes")
            if not audio_bytes:
                continue

            upstream_name = audio.get("path") or f"clip_{index}.wav"
            stem = re.sub(r"\.[^.]+$", "", upstream_name)
            safe_stem = re.sub(r"[^0-9A-Za-z_-]", "_", stem)
            cached_path = await cache_audio_bytes_async(
                filename=f"{self._DATASET_NAME}_{safe_stem}.wav",
                audio_bytes=audio_bytes,
                log_prefix="Garak-Audio",
            )

            category = self._harm_category_from_path(upstream_name)
            metadata: dict[str, Any] = {"original_filename": upstream_name}
            if category:
                metadata["category"] = category

            seeds.append(
                SeedPrompt(
                    value=cached_path,
                    data_type=self.DATA_TYPE,
                    dataset_name=self.dataset_name,
                    harm_categories=[category] if category else [],
                    source=self._source_url,
                    authors=list(self.SOURCE_AUTHORS),
                    groups=list(self.SOURCE_GROUPS),
                    metadata=metadata,
                )
            )

            if self._max_examples is not None and len(seeds) >= self._max_examples:
                break

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seeds)} audio seeds from {self.HF_DATASET_NAME}")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
