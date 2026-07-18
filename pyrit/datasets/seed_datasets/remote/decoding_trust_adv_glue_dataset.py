# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from collections.abc import Sequence
from enum import Enum
from typing import Any, cast

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


# Pinned commit SHA of AI-secure/DecodingTrust `main` (2024-09-16); the same
# revision the DecodingTrust Toxicity loader pins. Pinning prevents silent
# upstream changes from altering the prompt set.
_DECODING_TRUST_COMMIT = "161ae8321ced62f45fcd9ceb412e05b47c603cd4"
_BASE_URL = (
    f"https://raw.githubusercontent.com/AI-secure/DecodingTrust/{_DECODING_TRUST_COMMIT}/data/adv-glue-plus-plus/data/"
)

# The six GLUE tasks published in each AdvGLUE++ file. Each maps to the key used
# in the source JSON objects.
_TASK_KEYS: tuple[str, ...] = ("sst2", "qqp", "mnli", "mnli-mm", "qnli", "rte")

# How to compose the adversarial input text for the two-/dual-field tasks when a
# record stores its parts separately instead of a pre-joined ``sentence``. Each
# entry maps a task to ``(trigger_field, [(label, source_field), ...])``: a record
# is in "component form" when ``trigger_field`` is present, and the text is built
# as ``"label1: value1 label2: value2"`` — the exact format DecodingTrust uses for
# its pre-composed ``sentence`` values. sst2 is single-field and always ships a
# pre-composed ``sentence``, so it is intentionally absent here.
_COMPONENT_TEMPLATES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "mnli": ("premise", (("premise", "premise"), ("hypothesis", "hypothesis"))),
    "mnli-mm": ("premise", (("premise", "premise"), ("hypothesis", "hypothesis"))),
    "rte": ("sentence1", (("premise", "sentence1"), ("hypothesis", "sentence2"))),
    "qqp": ("question1", (("question1", "question1"), ("question2", "question2"))),
    "qnli": ("question", (("question", "question"), ("sentence", "sentence"))),
}

# Per-task human-readable label maps (the `label` field is the *gold* label of
# the clean example; the adversarial `sentence` tries to flip a model away from
# it). Kept for provenance in per-seed metadata; not required to build a prompt.
_LABEL_NAMES: dict[str, dict[int, str]] = {
    "sst2": {0: "negative", 1: "positive"},
    "qqp": {0: "not_duplicate", 1: "duplicate"},
    "mnli": {0: "entailment", 1: "neutral", 2: "contradiction"},
    "mnli-mm": {0: "entailment", 1: "neutral", 2: "contradiction"},
    "qnli": {0: "entailment", 1: "not_entailment"},
    "rte": {0: "entailment", 1: "not_entailment"},
}


class DecodingTrustAdvGLUEModel(Enum):
    """
    Which surrogate model's AdvGLUE++ adversarial set to load.

    AdvGLUE++ crafts word-level adversarial perturbations against three
    open-source models and studies their transfer to GPT-3.5/GPT-4. Each value
    selects the corresponding source file; ``ALL`` fetches and concatenates all
    three.
    """

    ALPACA = "alpaca"
    VICUNA = "vicuna"
    STABLE_VICUNA = "stable-vicuna"
    ALL = "all"


class DecodingTrustAdvGLUETask(Enum):
    """A GLUE task within the AdvGLUE++ adversarial set."""

    SST2 = "sst2"
    QQP = "qqp"
    MNLI = "mnli"
    MNLI_MM = "mnli-mm"
    QNLI = "qnli"
    RTE = "rte"


class _DecodingTrustAdvGLUEDataset(_RemoteDatasetLoader):
    """
    Loader for the Adversarial Robustness (AdvGLUE++) perspective of DecodingTrust.

    DecodingTrust [1] evaluates LLM trustworthiness across eight perspectives; the
    Adversarial Robustness perspective ships AdvGLUE++ [1], a set of word-level
    adversarial perturbations of GLUE [2] / AdvGLUE [3] inputs. The perturbations
    are generated with five attack strategies (``bertattack``, ``semattack``,
    ``sememepso``, ``textbugger``, ``textfooler``) against three surrogate models
    (Alpaca-7B, Vicuna-13B, StableVicuna-13B) and are studied for transfer to
    GPT-3.5 and GPT-4.

    The published data lives in three files under
    ``AI-secure/DecodingTrust/data/adv-glue-plus-plus/data/`` — ``alpaca.json``,
    ``vicuna.json`` and ``stable-vicuna.json``. Each file is a JSON object keyed
    by the six GLUE tasks (``sst2``, ``qqp``, ``mnli``, ``mnli-mm``, ``qnli``,
    ``rte``). Records come in two shapes depending on the attack that produced
    them: some carry a pre-composed adversarial ``sentence`` while others store
    the perturbed parts separately (e.g. ``premise``/``hypothesis`` for NLI,
    ``question1``/``question2`` for QQP). The loader builds the prompt from the
    pre-composed ``sentence`` when present and otherwise joins the component
    fields using the same ``"premise: ... hypothesis: ..."`` layout DecodingTrust
    uses, so no records are dropped. Each record's gold ``label``, attack
    ``method``, ``data_construction`` flag and clean ``original_*`` counterparts
    are preserved in metadata. The loader fetches the source files at runtime
    from ``raw.githubusercontent.com`` (no redistribution) at a pinned commit SHA.

    References:
        [1] [@wang2023decodingtrust] https://github.com/AI-secure/DecodingTrust
        [2] GLUE (Wang et al., 2019)
        [3] AdvGLUE (Wang et al., 2021)

    License:
        DecodingTrust is distributed under CC BY-SA 4.0. PyRIT fetches the
        prompts at runtime and does not redistribute them. Attribution to the
        DecodingTrust authors is recorded on every ``SeedPrompt`` produced.

    Note:
        AdvGLUE++ inputs are perturbed but otherwise benign natural-language
        understanding sentences — they probe model robustness, not harmful
        content — so no ``harm_categories`` are assigned.
    """

    # Class-level metadata picked up by _RemoteDatasetLoader._parse_metadata_async.
    # See pyrit/datasets/seed_datasets/seed_metadata.py for the schema. No
    # harm_categories: these are robustness probes, not harmful prompts. "synthetic"
    # reflects that the perturbations are algorithmically generated.
    modalities: list[str] = ["text"]
    size: str = "huge"  # ~11k (alpaca) up to ~40k (source_model=ALL)
    tags: set[str] = {"safety", "synthetic"}

    _AUTHORS: tuple[str, ...] = (
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
    )

    _GROUPS: tuple[str, ...] = (
        "University of Illinois Urbana-Champaign",
        "Stanford University",
        "University of California, Berkeley",
        "Center for AI Safety",
        "Microsoft Research",
    )

    _DESCRIPTION = (
        "Adversarial Robustness (AdvGLUE++) perspective of the DecodingTrust benchmark "
        "(Wang et al., 2023). Word-level adversarial perturbations of GLUE/AdvGLUE inputs "
        "generated with five attack strategies against three surrogate models "
        "(Alpaca-7B, Vicuna-13B, StableVicuna-13B). Each prompt is the perturbed "
        "adversarial sentence; the clean original, gold label, attack method and task are "
        "preserved in metadata."
    )

    def __init__(
        self,
        *,
        source_model: DecodingTrustAdvGLUEModel = DecodingTrustAdvGLUEModel.ALPACA,
        tasks: Sequence[DecodingTrustAdvGLUETask] | None = None,
    ) -> None:
        """
        Initialize the DecodingTrust AdvGLUE++ dataset loader.

        Args:
            source_model: Which surrogate model's adversarial set to load.
                Defaults to ``DecodingTrustAdvGLUEModel.ALPACA`` (one file);
                ``ALL`` concatenates all three surrogate files.
            tasks: Restrict to these GLUE tasks. ``None`` (default) keeps all six.

        Raises:
            ValueError: If ``source_model`` is not a ``DecodingTrustAdvGLUEModel``,
                or if ``tasks`` contains a value that is not a
                ``DecodingTrustAdvGLUETask``.
        """
        self._validate_enum(source_model, DecodingTrustAdvGLUEModel, "source_model")
        if tasks is not None:
            self._validate_enums(tasks, DecodingTrustAdvGLUETask, "tasks")
        self.source_model = source_model
        # Store the selected task keys as a set of source-JSON keys; None => all.
        self.task_keys: set[str] = {t.value for t in tasks} if tasks else set(_TASK_KEYS)

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "decoding_trust_adv_glue_plus_plus"

    def _model_urls(self) -> list[str]:
        """Return the source file URLs implied by ``self.source_model``."""
        if self.source_model is DecodingTrustAdvGLUEModel.ALL:
            models = [m for m in DecodingTrustAdvGLUEModel if m is not DecodingTrustAdvGLUEModel.ALL]
        else:
            models = [self.source_model]
        return [f"{_BASE_URL}{model.value}.json" for model in models]

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the DecodingTrust AdvGLUE++ prompts and return them as a SeedDataset.

        Args:
            cache: Whether to cache the fetched JSON files locally. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset whose seeds are the selected adversarial prompts.

        Raises:
            ValueError: If a source file is not a JSON object keyed by task, or if
                the chosen filter combination leaves zero seeds.
        """
        logger.info(f"Loading DecodingTrust AdvGLUE++ source_model={self.source_model.value!r} from {_BASE_URL}")

        seed_prompts: list[SeedUnion] = []
        for url in self._model_urls():
            raw = self._fetch_from_url(source=url, source_type="public_url", cache=cache)
            # AdvGLUE++ files are a JSON object {task: [record, ...]}, unlike the
            # list-shaped JSONL the base type hints assume.
            by_task = cast("dict[str, Any]", raw)
            if not isinstance(by_task, dict):
                raise ValueError(
                    f"Expected AdvGLUE++ file {url!r} to be a JSON object keyed by task, got {type(by_task).__name__}"
                )
            model_name = url.rsplit("/", 1)[-1].removesuffix(".json")
            seed_prompts.extend(self._records_to_seed_prompts(by_task=by_task, source_url=url, model_name=model_name))

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")
        logger.info(f"Loaded {len(seed_prompts)} prompts from DecodingTrust AdvGLUE++")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _records_to_seed_prompts(
        self,
        *,
        by_task: dict[str, Any],
        source_url: str,
        model_name: str,
    ) -> list[SeedUnion]:
        """
        Convert one surrogate file's task->records mapping into SeedPrompt instances.

        Args:
            by_task: The parsed ``{task: [record, ...]}`` object from a source file.
            source_url: The file URL; becomes each prompt's ``source``.
            model_name: The surrogate model name (e.g. ``"alpaca"``) for metadata.

        Returns:
            List of SeedPrompt objects, one per record that passes the task filter
            and yields non-empty adversarial text.

        Raises:
            ValueError: If a task's value is not a list of records.
        """
        seed_prompts: list[SeedUnion] = []
        for task_key, records in by_task.items():
            if task_key not in self.task_keys:
                continue
            if not isinstance(records, list):
                raise ValueError(
                    f"Expected task {task_key!r} in {source_url!r} to map to a list, got {type(records).__name__}"
                )
            for item in records:
                if not isinstance(item, dict):
                    logger.warning(f"Skipping non-dict record in task {task_key!r} (type={type(item).__name__})")
                    continue

                text = self._compose_text(task_key=task_key, item=item)
                if not text:
                    logger.warning(f"Skipping record with no usable adversarial text in task {task_key!r}")
                    continue

                seed_prompts.append(
                    SeedPrompt(
                        value=text,
                        data_type="text",
                        dataset_name=self.dataset_name,
                        harm_categories=[],
                        description=self._DESCRIPTION,
                        source=source_url,
                        authors=list(self._AUTHORS),
                        groups=list(self._GROUPS),
                        metadata=self._build_metadata(item=item, task_key=task_key, model_name=model_name),
                    )
                )
        return seed_prompts

    def _compose_text(self, *, task_key: str, item: dict[str, Any]) -> str | None:
        """
        Build the adversarial input text for one record.

        Records ship in two shapes: some carry a pre-composed ``sentence``, others
        store the perturbed parts separately (see ``_COMPONENT_TEMPLATES``). When a
        record is in component form its parts are joined as
        ``"label1: value1 label2: value2"`` — the layout DecodingTrust uses for its
        own pre-composed ``sentence`` — so both shapes yield equivalent prompts.

        Args:
            task_key: The GLUE task the record belongs to.
            item: A single AdvGLUE++ record.

        Returns:
            The composed adversarial text, or None if the record has neither a
            usable ``sentence`` nor a complete set of component fields.
        """
        template = _COMPONENT_TEMPLATES.get(task_key)
        if template is not None:
            trigger_field, parts = template
            if trigger_field in item:
                segments: list[str] = []
                for label, source_field in parts:
                    value = item.get(source_field)
                    if not isinstance(value, str) or not value:
                        return None  # incomplete component record — skip
                    segments.append(f"{label}: {value}")
                return " ".join(segments)

        # Pre-composed shape (always the case for sst2, and for the pre-joined
        # records of the other tasks): the `sentence` field is the full input.
        sentence = item.get("sentence")
        if isinstance(sentence, str) and sentence:
            return sentence
        return None

    def _build_metadata(self, *, item: dict[str, Any], task_key: str, model_name: str) -> dict[str, str]:
        """
        Assemble per-seed metadata (provenance) for one AdvGLUE++ record.

        Args:
            item: A single AdvGLUE++ record.
            task_key: The GLUE task the record belongs to.
            model_name: The surrogate model the adversarial example was crafted on.

        Returns:
            A flat ``dict[str, str]`` of provenance fields. Optional source fields
            are included only when present.
        """
        metadata: dict[str, str] = {"task": task_key, "source_model": model_name}

        label = item.get("label")
        if isinstance(label, int):
            metadata["label"] = str(label)
            label_name = _LABEL_NAMES.get(task_key, {}).get(label)
            if label_name is not None:
                metadata["label_name"] = label_name

        for key in ("method", "data_construction"):
            value = item.get(key)
            if isinstance(value, str) and value:
                metadata[key] = value

        # Preserve every clean counterpart the source provides. Which `original_*`
        # fields exist depends on the task and record shape (e.g. original_sentence,
        # original_premise/hypothesis, original_sentence1/2, original_question1/2).
        metadata.update(
            {
                key: value
                for key, value in item.items()
                if key.startswith("original_") and isinstance(value, str) and value
            }
        )

        idx = item.get("idx")
        if isinstance(idx, int):
            metadata["idx"] = str(idx)

        return metadata
