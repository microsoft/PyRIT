---
applyTo: "pyrit/datasets/seed_datasets/**"
---

# Seed Dataset Loader Guidelines

These rules apply when adding or modifying loaders under `pyrit/datasets/seed_datasets/`.
Style rules from `style-guide.instructions.md` (async `_async` suffix, keyword-only args, type hints, enums-over-Literals) still apply and are not repeated here.

The keyword-only `__init__` rule is **enforced at class-definition time** by
`SeedDatasetProvider.__init_subclass__` calling `enforce_keyword_only_init` (see
`pyrit/common/brick_contract.py`). Loaders with positional `__init__` params raise
`TypeError` at import time; existing offenders may set `_brick_legacy_init = True`
to opt into a one-release grace period that downgrades the error to a
`DeprecationWarning(removed_in="0.16.0")`.

## Use SeedObjective for behavior/goal rows; SeedPrompt for literal messages

This is the most consequential modelling decision and must be made before writing the loader:

- **`SeedObjective`** — each row describes a *behavior/goal the attacker wants the model to perform* ("write malware that…", "explain how to…"). Used by HarmBench-style behavior datasets and any "do X" dataset that downstream attacks will pursue via converters/multi-turn strategies.
- **`SeedPrompt`** — each row is *the literal message to send to the target* (jailbreak strings, single-shot prompt collections, templates). Used by XSTest, SimpleSafetyTests, jailbreak template datasets.

When in doubt: if the row reads as "an instruction the red-teamer wants the model to follow", it is an objective; if it reads as "the text I would copy/paste into the chat box", it is a prompt.

## Subclass `_RemoteDatasetLoader` for HF or URL-backed datasets

Concrete loader classes are private (leading underscore, e.g. `_HarmBenchDataset`) and must implement:

- a `dataset_name` property returning the short snake_case name used by `CentralMemory`,
- `async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset`.

Use the inherited helpers — do not re-implement them:

- `self._fetch_from_url(source=..., source_type=..., cache=...)` for raw CSV/JSON/JSONL/TXT URLs,
- `await self._fetch_from_huggingface(dataset_name=..., split=..., cache=..., token=...)` for HF Hub,
- `self._validate_enum(value, EnumCls, "label")` / `self._validate_enums(values, EnumCls, "label")` for enum filter validation.

Local YAML-backed datasets subclass `_LocalDatasetLoader` instead; the conventions below about metadata, enums, and tests still apply.

## Document HuggingFace gating and accept a `token`

For HF-gated datasets the constructor must accept `token: str | None = None`, fall back to `os.environ.get("HUGGINGFACE_TOKEN")`, and forward the resolved value to `_fetch_from_huggingface`. The class docstring must state that the dataset is gated and that the user needs to accept the terms on HF and supply a token. See `_SorryBenchDataset`, `_VLGuardDataset` for the canonical pattern.

```python
self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")
```

## Expose filters as module-level Enums

When the dataset has meaningful subsets (label, category, subset, severity, prompt-style, …), define a module-level `Enum` per axis and accept it on the constructor — never raw strings or `Literal[...]`. Validate with the inherited `_validate_enum` / `_validate_enums` helpers; do not roll your own `if value not in …` checks. Re-export every new public enum from `pyrit/datasets/seed_datasets/remote/__init__.py`. See `VLGuardSubset`, `PromptIntelSeverity` for the pattern.

Pick a default that is most useful for red teaming (e.g. `VLGuardSubset.UNSAFES`).

## Preserve source metadata per seed

Each `SeedPrompt` / `SeedObjective` must carry:

- `dataset_name` set to `self.dataset_name`,
- `source` pointing to the canonical dataset URL,
- `authors` and (where applicable) `groups` from the paper,
- `harm_categories=[item["category"]]` — preserve the source's original casing,
- `metadata={...}` for distinguishing per-row fields the loader filters on (label, subcategory, source-side IDs) so downstream users can post-filter without re-fetching.

## Set class-level dataset metadata when known

`_parse_metadata_async` on `_RemoteDatasetLoader` reads class attributes matching `SeedDatasetMetadata` fields. Declare what you can know statically as class-level constants so dataset discovery/filtering works:

```python
class _MyDataset(_RemoteDatasetLoader):
    HF_DATASET_NAME: str = "owner/my-dataset"
    harm_categories: list[str] = ["harassment", "violence"]
    modalities: list[str] = ["text"]
    size: str = "medium"   # tiny <10, small 10-99, medium 100-499, large 500-4999, huge 5000+
    tags: set[str] = {"default", "safety"}
```

The class-level `harm_categories` lists the unique set the source data exposes and is lowercased to match PyRIT's tag normalization. Per-seed `harm_categories` may keep the source's original casing.

## Raise on empty results

If filter arguments produce zero seeds, raise `ValueError("SeedDataset cannot be empty. Check your filter criteria.")` — do not return an empty `SeedDataset`. See `_SorryBenchDataset`, `_VLGuardDataset`.

## Register in `__init__.py`

Add the loader and any new public enums to `pyrit/datasets/seed_datasets/remote/__init__.py` (or `local/__init__.py`):

- import block: alphabetical by module name,
- `__all__`: alphabetical, with public enums grouped above the underscore-prefixed loader classes (matching the existing ordering).

## Cite the paper

- Add a BibTeX entry to `doc/references.bib` in alphabetical position by cite key. Match the surrounding format (`@article{` or `@misc{`, fields ordered title/author/journal/year/url, optional `note` for venue).
- Add the new cite key to the hidden-citations block in `doc/bibliography.md` in alphabetical position.
- Reference the cite key from the loader's class docstring as `Reference: [@citekey]`.

## Update and regenerate `doc/code/datasets/1_loading_datasets`

The rendered datasets notebook drives the public list of built-in datasets on the docs site, so every new loader must touch it:

- Add the new dataset and its cite key to the prose paragraph at the top of `doc/code/datasets/1_loading_datasets.py` (alphabetical with the rest), and add the matching entry to `doc/code/datasets/1_loading_datasets.ipynb`.
- Regenerate the notebook so the `SeedDatasetProvider.get_all_dataset_names_async()` output cell picks up the new loader: `jupytext --to ipynb --execute doc/code/datasets/1_loading_datasets.py`. Inline edits to both files are also acceptable per `docs.instructions.md`, but executed regeneration is the only way the rendered dataset-name list stays in sync.

## Test loaders against mocked HF data

Place tests in `tests/unit/datasets/test_<dataset>_dataset.py`. Mock `_fetch_from_huggingface` (or `_fetch_from_url`) — never make a live call. Cover:

- `dataset_name` property,
- happy-path fetch with a small fixture matching the real HF row schema,
- each filter mode (per enum value) including the empty-after-filter case raising `ValueError`,
- invalid-enum raises `ValueError`,
- token forwarding (explicit kwarg, `HUGGINGFACE_TOKEN` env fallback, explicit overrides env).

```python
with patch.object(loader, "_fetch_from_huggingface", new_callable=AsyncMock, return_value=mock_rows):
    dataset = await loader.fetch_dataset_async()
```

`asyncio_mode = "auto"` is set project-wide, so do not decorate async tests with `@pytest.mark.asyncio`. Use `class TestXxxDataset:` to mirror neighboring files when grouping helps; standalone test functions are also fine.

## Sanity-check against the real dataset before opening the PR

Markdown/HF schemas drift. Once per new loader, run it for real against the HF dataset — typically via `initialize_pyrit_async()` to pick up the token from `~/.pyrit/.env` — and confirm that row keys, category values, and counts match what the loader expects. This is not enforced in CI but catches the bugs unit tests with mocked rows cannot.
