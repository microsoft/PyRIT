# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlparse

from typing_extensions import override

from pyrit.common.net_utility import make_request_and_raise_if_error_async
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.memory import data_serializer_factory
from pyrit.models import SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class _MSTSDataset(_RemoteDatasetLoader):
    """
    Loader for the MSTS (Multimodal Safety Test Suite) dataset.

    MSTS contains 400 English test prompts across 40 fine-grained hazard categories,
    translated into 10 additional languages (11 total). Each test case consists of a
    text question paired with an image — only their combination reveals the unsafe
    meaning (e.g., text "Should I spray people with this?" + image of a rifle).

    Each test case comes in two response framings:

    - "assistance": e.g. "Should I X?"
    - "intention": e.g. "I am going to X."

    Per language, MSTS has 200 unique cases × 2 framings = 400 rows, with the same
    image shared between the two framing rows for a given case.

    Supported language ISO codes: en, de, ru, zh, hi, es, it, fr, ko, ar, fa.
    The default loads English only. Pass ``["all"]`` to load every language.

    Note: The first call may be slow as images need to be downloaded. Subsequent
    calls reuse cached images.

    License: CC-BY-4.0.

    Reference: https://huggingface.co/datasets/felfri/MSTS
    Paper: [@rottger2025msts]
    """

    _HF_REPO_ID: ClassVar[str] = "felfri/MSTS"

    _LANGUAGE_TO_SPLIT: ClassVar[dict[str, str]] = {
        "en": "english",
        "de": "german",
        "ru": "russian",
        "zh": "chinese",
        "hi": "hindi",
        "es": "spanish",
        "it": "italian",
        "fr": "french",
        "ko": "korean",
        "ar": "arabic",
        "fa": "farsi",
    }

    _VALID_TEXT_MODIFIERS: ClassVar[frozenset[str]] = frozenset({"assistance", "intention"})

    _PIL_FORMAT_TO_EXTENSION: ClassVar[dict[str, str]] = {
        "JPEG": "jpg",
        "JPG": "jpg",
        "PNG": "png",
        "GIF": "gif",
        "BMP": "bmp",
        "TIFF": "tiff",
        "WEBP": "webp",
    }

    _AUTHORS: ClassVar[list[str]] = [
        "Paul Röttger",
        "Giuseppe Attanasio",
        "Felix Friedrich",
        "Janis Goldzycher",
        "Alicia Parrish",
        "Rishabh Bhardwaj",
        "Chiara Di Bonaventura",
        "Roman Eng",
        "Gaia El Khoury Geagea",
        "Sujata Goswami",
        "Jieun Han",
        "Dirk Hovy",
        "Seogyeong Jeong",
        "Paloma Jeretič",
        "Flor Miriam Plaza-del-Arco",
        "Donya Rooein",
        "Patrick Schramowski",
        "Anastassia Shaitarova",
        "Xudong Shen",
        "Richard Willats",
        "Andrea Zugarini",
        "Bertie Vidgen",
    ]

    _GROUPS: ClassVar[list[str]] = [
        "Bocconi University",
        "Instituto de Telecomunicações",
        "TU Darmstadt",
        "hessian.AI",
        "University of Zurich",
        "Google DeepMind",
        "Walled AI",
        "King's College London",
        "Imperial College London",
        "Clarkson University",
        "Lawrence Berkeley National Laboratory",
        "KAIST",
        "University of Pennsylvania",
        "DFKI",
        "CERTAIN",
        "National University of Singapore",
        "Contextual AI",
        "expert.ai",
    ]

    # Metadata
    harm_categories: list[str] = [
        "Violent Crimes",
        "Non-Violent Crimes",
        "Sex-Related Crimes",
        "Suicide & Self-Harm",
        "Other",
    ]
    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "violent crimes": [HarmCategory.VIOLENT_CONTENT],
        "non-violent crimes": [HarmCategory.COORDINATION_HARM],
        "sex-related crimes": [HarmCategory.SEXUAL_CONTENT],
        "suicide & self-harm": [HarmCategory.SUICIDE, HarmCategory.SELF_HARM],
        "other": [HarmCategory.OTHER],
    }
    # The 21 fine-grained hazard_subcategory values resolve far more precisely than
    # the 5 top-level hazard_category buckets (e.g. the "Other" bucket splits into
    # "Conspiracy theories" -> INFO_INTEGRITY and "Gross behaviour" -> OTHER). When a
    # row's subcategory is present and recognized it takes precedence; otherwise the
    # loader falls back to the coarse hazard_category mapping above.
    HAZARD_SUBCATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "property damage": [HarmCategory.COORDINATION_HARM],
        "sale, purchase or ownership of illegal, regulated or unregistered items": [
            HarmCategory.REGULATED_GOODS,
            HarmCategory.COORDINATION_HARM,
        ],
        "environmental pollution": [HarmCategory.OTHER],
        "production of illegal, regulated or unregistered items": [
            HarmCategory.REGULATED_GOODS,
            HarmCategory.COORDINATION_HARM,
        ],
        "reckless endangerment": [HarmCategory.DANGEROUS_SITUATIONS],
        "dangerous challenges": [HarmCategory.DANGEROUS_SITUATIONS],
        "scams & fraud": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
        "self-harm": [HarmCategory.SELF_HARM],
        "physical assault": [HarmCategory.VIOLENT_CONTENT],
        "murder": [HarmCategory.VIOLENT_CONTENT],
        "gross behaviour": [HarmCategory.OTHER],
        "sexual harassment": [HarmCategory.HARASSMENT, HarmCategory.SEXUAL_SOLICITATION],
        "trespassing": [HarmCategory.COORDINATION_HARM],
        "terror": [HarmCategory.VIOLENT_EXTREMISM, HarmCategory.COORDINATION_HARM],
        "conspiracy theories": [HarmCategory.INFO_INTEGRITY],
        "eating disorders": [HarmCategory.EATING_DISORDERS, HarmCategory.SELF_HARM],
        "suicide": [HarmCategory.SUICIDE, HarmCategory.SELF_HARM],
        "sexual assault": [HarmCategory.SEXUAL_CONTENT, HarmCategory.VIOLENT_CONTENT],
        "indecent exposure": [HarmCategory.SEXUAL_CONTENT],
        "theft": [HarmCategory.COORDINATION_HARM],
        "mass violence": [HarmCategory.VIOLENT_CONTENT, HarmCategory.VIOLENT_EXTREMISM],
    }
    modalities: list[str] = ["text", "image"]
    size: str = "large"
    tags: set[str] = {"default", "safety", "multimodal", "multilingual"}

    def __init__(
        self,
        *,
        languages: list[str] | None = None,
        text_modifiers: list[str] | None = None,
        token: str | None = None,
    ) -> None:
        """
        Initialize the MSTS dataset loader.

        Args:
            languages (list[str] | None): List of ISO language codes to fetch. Supported codes:
                en, de, ru, zh, hi, es, it, fr, ko, ar, fa. Pass ``["all"]`` to load every
                language. Defaults to ``["en"]``.
            text_modifiers (list[str] | None): Subset of {"assistance", "intention"} to include.
                Defaults to both.
            token (str | None): Optional HuggingFace authentication token.

        Raises:
            ValueError: If any language code is unsupported, or any text modifier is invalid.
        """
        self.languages = self._resolve_languages(languages)
        self.text_modifiers = self._resolve_text_modifiers(text_modifiers)
        self.token = token
        self.source = f"https://huggingface.co/datasets/{self._HF_REPO_ID}"

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "msts"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch MSTS examples and return as a SeedDataset.

        Each test row produces a pair of SeedPrompts that share a ``prompt_group_id``
        and both have ``sequence=0``, so they are delivered together as a single
        multimodal user message (image + text) rather than as two separate turns.

        Args:
            cache (bool): Whether to cache the fetched dataset and images. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the multimodal MSTS examples.
        """
        logger.info(f"Loading MSTS dataset (languages={self.languages}, text_modifiers={self.text_modifiers})")

        prompts: list[SeedUnion] = []
        failed_image_count = 0

        for language in self.languages:
            split_name = self._LANGUAGE_TO_SPLIT[language]
            split_data = await self._fetch_from_huggingface_async(
                dataset_name=self._HF_REPO_ID,
                split=split_name,
                cache=cache,
                token=self.token,
            )

            for row in split_data:
                if row.get("prompt_type") not in self.text_modifiers:
                    continue

                try:
                    pair = await self._build_prompt_pair_async(row=row, language=language)
                except Exception as e:
                    failed_image_count += 1
                    logger.warning(
                        f"[MSTS] Failed to fetch image for case "
                        f"{row.get('case_id', '<unknown>')} ({language}): {e}. Skipping example."
                    )
                    continue

                prompts.extend(pair)

        if failed_image_count > 0:
            logger.warning(f"[MSTS] Skipped {failed_image_count} example(s) due to image fetch failures")

        logger.info(f"Successfully loaded {len(prompts)} prompts from MSTS dataset")

        return SeedDataset(seeds=prompts, dataset_name=self.dataset_name)

    @staticmethod
    def _resolve_languages(languages: list[str] | None) -> list[str]:
        """
        Validate and normalize the requested list of language codes.

        Args:
            languages (list[str] | None): User-supplied language codes, or ``["all"]``.

        Returns:
            list[str]: A normalized list of ISO language codes.

        Raises:
            ValueError: If any code is unsupported.
        """
        if languages is None:
            return ["en"]

        if not languages:
            raise ValueError(
                "MSTS languages must not be empty. Pass None to use the default ['en'] "
                "or ['all'] to load every supported language."
            )

        if languages == ["all"]:
            return list(_MSTSDataset._LANGUAGE_TO_SPLIT.keys())

        invalid = [lang for lang in languages if lang not in _MSTSDataset._LANGUAGE_TO_SPLIT]
        if invalid:
            valid = ", ".join(sorted(_MSTSDataset._LANGUAGE_TO_SPLIT.keys()))
            raise ValueError(
                f"Unsupported MSTS language(s): {invalid}. Valid ISO codes: {valid}. "
                f"Pass ['all'] to load every language."
            )

        return list(languages)

    @staticmethod
    def _resolve_text_modifiers(text_modifiers: list[str] | None) -> list[str]:
        """
        Validate and normalize the requested list of text modifiers.

        Args:
            text_modifiers (list[str] | None): User-supplied subset of {"assistance", "intention"}.

        Returns:
            list[str]: Normalized list of text modifiers.

        Raises:
            ValueError: If any modifier is invalid.
        """
        if text_modifiers is None:
            return ["assistance", "intention"]

        if not text_modifiers:
            raise ValueError(
                "MSTS text_modifiers must not be empty. Pass None to use the default ['assistance', 'intention']."
            )

        invalid = [m for m in text_modifiers if m not in _MSTSDataset._VALID_TEXT_MODIFIERS]
        if invalid:
            valid = ", ".join(sorted(_MSTSDataset._VALID_TEXT_MODIFIERS))
            raise ValueError(f"Invalid MSTS text_modifiers: {invalid}. Valid values: {valid}.")

        return list(text_modifiers)

    async def _build_prompt_pair_async(self, *, row: dict[str, Any], language: str) -> list["SeedUnion"]:
        """
        Build an image+text SeedPrompt pair for a single MSTS row.

        Args:
            row (dict[str, Any]): A single row from the HuggingFace dataset.
            language (str): The ISO language code the row was loaded for.

        Returns:
            list[SeedPrompt]: A two-element list containing the image and text prompts.

        Raises:
            Exception: If the image cannot be fetched or saved.
        """
        case_id = str(row.get("case_id", ""))
        image_id = str(row.get("unsafe_image_id", ""))
        prompt_text = str(row.get("prompt_text", ""))
        text_modifier = str(row.get("prompt_type", ""))
        image_url = str(row.get("unsafe_image_url", ""))
        hazard_category = str(row.get("hazard_category", ""))
        image_license = str(row.get("unsafe_image_license") or "")
        image_description = str(row.get("unsafe_image_description") or "")
        hazard_subcategory = str(row.get("hazard_subcategory") or "")
        hazard_subsubcategory = str(row.get("hazard_subsubcategory") or "")
        pil_image = row.get("unsafe_image")

        extension = self._infer_image_extension(image_url=image_url, pil_image=pil_image)
        local_image_path = await self._fetch_and_save_image_async(
            pil_image=pil_image,
            image_url=image_url,
            image_id=image_id,
            extension=extension,
        )

        group_id = uuid.uuid4()
        subcategory_key = hazard_subcategory.strip().lower()
        if subcategory_key in self.HAZARD_SUBCATEGORY_ALIAS_OVERRIDES:
            harm_categories = self._standardize_harm_categories(
                hazard_subcategory,
                alias_overrides=self.HAZARD_SUBCATEGORY_ALIAS_OVERRIDES,
            )
        else:
            harm_categories = self._standardize_harm_categories(
                hazard_category,
                alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
            )
        metadata: dict[str, str | int] = {
            "case_id": case_id,
            "image_id": image_id,
            "text_modifier": text_modifier,
            "image_description": image_description,
            "category": hazard_category,
            "subcategory": hazard_subcategory,
            "subsubcategory": hazard_subsubcategory,
            "language": language,
            "image_license": image_license,
            "original_image_url": image_url,
        }

        image_prompt = SeedPrompt(
            value=local_image_path,
            data_type="image_path",
            name=f"MSTS Image - {case_id} ({language}/{text_modifier})",
            dataset_name=self.dataset_name,
            harm_categories=harm_categories,
            description=(
                "Image component of an MSTS multimodal safety test prompt. "
                "The unsafe meaning emerges only from the combination of image and text."
            ),
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.source,
            prompt_group_id=group_id,
            sequence=0,
            metadata=metadata,
        )

        text_prompt = SeedPrompt(
            value=prompt_text,
            data_type="text",
            name=f"MSTS Text - {case_id} ({language}/{text_modifier})",
            dataset_name=self.dataset_name,
            harm_categories=harm_categories,
            description=(
                "Text component of an MSTS multimodal safety test prompt. "
                "The unsafe meaning emerges only from the combination of image and text."
            ),
            authors=self._AUTHORS,
            groups=self._GROUPS,
            source=self.source,
            prompt_group_id=group_id,
            sequence=0,
            metadata=metadata,
        )

        return [image_prompt, text_prompt]

    @staticmethod
    def _infer_image_extension(*, image_url: str, pil_image: "PILImage | None") -> str:
        """
        Infer the file extension to use when saving an MSTS image.

        Preference order: extension from URL path → PIL image format → "jpg".

        Args:
            image_url (str): The original image URL.
            pil_image (PIL.Image.Image | None): The loaded PIL image, if available.

        Returns:
            str: A lowercase file extension without leading dot (e.g. "jpg", "png").
        """
        url_path = urlparse(image_url).path
        url_ext = url_path.rsplit(".", 1)[-1].lower() if "." in url_path else ""
        if url_ext in {"jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp"}:
            return "jpg" if url_ext == "jpeg" else url_ext

        pil_format = getattr(pil_image, "format", None) if pil_image is not None else None
        if isinstance(pil_format, str):
            mapped = _MSTSDataset._PIL_FORMAT_TO_EXTENSION.get(pil_format.upper())
            if mapped:
                return mapped

        return "jpg"

    async def _fetch_and_save_image_async(
        self,
        *,
        pil_image: "PILImage | None",
        image_url: str,
        image_id: str,
        extension: str,
    ) -> str:
        """
        Save an MSTS image to local storage, preferring PIL bytes over a URL fetch.

        Args:
            pil_image (PIL.Image.Image | None): The PIL image bundled with the row,
                if present.
            image_url (str): Fallback URL used if the PIL image is unavailable.
            image_id (str): Stable identifier used to name the cached file (so the
                same image is reused across languages and text modifiers).
            extension (str): File extension to save under (e.g. "jpg").

        Returns:
            str: Local path to the saved image.

        Raises:
            RuntimeError: If the serializer memory is not properly configured.
            Exception: If neither PIL bytes nor a URL fetch can produce image data.
        """
        filename = f"msts_{image_id}.{extension}"
        serializer = data_serializer_factory(
            category="seed-prompt-entries",
            data_type="image_path",
            extension=extension,
        )

        results_path = serializer._memory.results_path
        results_storage_io = serializer._memory.results_storage_io
        if not results_path or results_storage_io is None:
            raise RuntimeError(
                "[MSTS] Serializer memory is not properly configured: results_path and results_storage_io must be set."
            )
        serializer.value = str(Path(str(results_path) + serializer.data_sub_directory, filename))
        try:
            if await results_storage_io.path_exists_async(serializer.value):
                return serializer.value
        except Exception as e:
            logger.warning(f"[MSTS] Failed to check if image {image_id} exists in cache: {e}")

        image_bytes = self._encode_pil_image(pil_image=pil_image, extension=extension)
        if image_bytes is None:
            response = await make_request_and_raise_if_error_async(endpoint_uri=image_url, method="GET")
            image_bytes = response.content

        await serializer.save_data_async(data=image_bytes, output_filename=filename.rsplit(".", 1)[0])

        return str(serializer.value)

    @staticmethod
    def _encode_pil_image(*, pil_image: "PILImage | None", extension: str) -> bytes | None:
        """
        Encode a PIL image to raw bytes in the requested format.

        Args:
            pil_image (PIL.Image.Image | None): The PIL image, or None.
            extension (str): The target file extension (used to derive PIL format).

        Returns:
            bytes | None: Encoded image bytes, or None if no PIL image was provided
                or encoding failed.
        """
        if pil_image is None:
            return None

        pil_format = (getattr(pil_image, "format", None) or extension).upper()
        if pil_format == "JPG":
            pil_format = "JPEG"

        try:
            buf = io.BytesIO()
            save_image = pil_image
            if pil_format == "JPEG" and getattr(pil_image, "mode", None) not in ("RGB", "L"):
                save_image = pil_image.convert("RGB")
            save_image.save(buf, format=pil_format)
            return buf.getvalue()
        except Exception as e:
            logger.warning(f"[MSTS] Failed to encode PIL image as {pil_format}: {e}. Will fall back to URL fetch.")
            return None
