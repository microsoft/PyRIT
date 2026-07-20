# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Focused tests for the TrustAIRLab in-the-wild static jailbreak templates.

These templates live in ``pyrit/datasets/jailbreak/templates/in_the_wild`` and are
generated from the TrustAIRLab "In-The-Wild Jailbreak Prompts" dataset. The tests here
verify the subfolder's attribution, globally unique filenames/names, the exact
single-``{{ prompt }}`` parameter contract, provenance metadata, faithful rendering
(including preservation of literal Jinja-like source syntax), and the absence of
residual PII / user handles.
"""

import re
from pathlib import Path

import pytest

from pyrit.common.path import JAILBREAK_TEMPLATES_PATH
from pyrit.datasets import TextJailBreak
from pyrit.models import SeedPrompt

IN_THE_WILD_DIR = JAILBREAK_TEMPLATES_PATH / "in_the_wild"

FILENAME_RE = re.compile(r"^trustairlab_[0-9a-f]{16}\.yaml$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESIDUAL_TEMPLATE_VARIABLE_RE = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}")
KNOWN_COMMIT = "a10aab8eff1c73165a442d4464dce192bd28b9c5"
VALID_PLATFORMS = {"website", "discord", "reddit", "open_source"}
VALID_CONVERSION_PATHS = {"A", "B"}

# --- Stable curation anchors, keyed by the SHA-256 of the normalized *source* prompt. -------
# These lock in specific semantic decisions independently of the content-addressed output
# filenames (which change if a template value changes).

# A slot-less "director" persona wrapper converted via Path B by appending the slot.
PATH_B_APPEND_SOURCE_SHA = "cd1bb3de55b2227e630e54560fc6ae4d3303ef85232b35a09e42c3be32e63bf9"
# A slot-less wrapper whose obvious trailing sample request is replaced in place (Path B).
PATH_B_REPLACE_SOURCE_SHA = "0cb6305955ac667d0b37ff8762728d3da71c2605017ec06b76394226795bdfd9"

# Prompts with generic narrative "attribution" (e.g. "created by OpenAI", "made by BISH",
# "created by something unknown") that name no real user and therefore must NOT be dropped as
# PII; each carries a genuine request placeholder and ships via Path A.
PATH_A_NARRATIVE_ATTRIBUTION_SOURCE_SHAS = {
    "2afb68ca314883cc62d1d422737f46920fe542759435bac942de954f37323a7d",  # "created by OpenAI"
    "e6841f28d72d5414026e066a013a844178bdafe699a3d2aa41507f5ce25be2b1",  # "predictions made by BISH"
    "f0adc723444397b387e11c102f16038242bdab4b8e22a00bba453514ce2958bb",  # "created by something unknown"
}

# Reviewed source prompts that must never ship: a multi-field fill-in template mistaken for a
# single-slot wrapper, a mid-text example token, a baked multi-part one-shot prompt, and a
# targeted-hate persona.
EXCLUDED_SOURCE_SHAS = {
    "58e72304652bb1d214edfc70e61fc2c4a57919c686877a19bbc215ad39345c55",  # [Role]/[Topic]/[Specify ...] fill-in form
    "c6540c8bfe02cd40f0ccb50636fc41c3d5b854de9b49e1b17e61f5b63aed9720",  # mid-text "[question]" example
    "617352830aa2fc7792c388f054f17c411f45959e0dcfa1b5922b6f484847cb86",  # baked multi-part one-shot prompt
    "056d76cf278ea1187f7bba464c55262d520c2a58e04eef1b2f3c91d3851531cb",  # targeted-hate persona
    "c22b96a199e22d46909c5e2ffe2eac20f87c1cf225345f2d77e72f079d3e1d0e",  # required ON/OFF and language fields
    "20bbf005dee74bdbe5c4b8201c296e6dfbd351230dcef954e8ae37f043297834",  # unresolved topic field
    "9443f40870b8b4381c49f03f3149f387482ddf1ff76258e6b52f496cec1a301f",  # undeclared {{var_0}} input
    "a342e9bece3b93fd9cc2a56c0d794e8265b074f5e351033411056cdcf3928263",  # three-message roleplay setup
    "055aec75072b5935f24b1921da1a397b4604bf63a24d422d59fd70dcdfad620d",  # marker before baked keylogger request
}

# Audited unprefixed contributor aliases and credits. These are personal identifiers even
# though they do not use conventional @handle, u/name, email, or Discord-tag syntax.
UNPREFIXED_ATTRIBUTION_SOURCE_SHAS = {
    "1d3a003828911e366aa90df8f2e6941e1f573be63bd8a434942b790a7050b74e",
    "5bd2aadd545280b06f7791d361112ef6d2e8f85e855e8113dbc51b443c9e3c59",
    "b2d0e0eacbd1412d3b737b67718d5b65a89d7594de8e7372cf589c0656bc5813",
    "5f16116fa8515d72cb233172de09b69df6d66261dbe9eef876b44fd54b1004e4",
    "82a52c6637a50b996e71ef4d324598168612c321bdf840dc536e35913c1191b9",
    "699e84c11aac7c9e93b4951c4e62b32ae9cb003d735c8df2f9c19ae26e767f5f",
    "7400bad527bb7510aa8f37d8b2caf0c6e3a2f810bc692fe5bbc733264628d216",
    "a71bdd08e42739c3eebcad03c518b03be40c2b296121c49a11af85877b35ff3c",
    "bb42e281473a78493c16e4b0de61b9c5b16e5f9bd6b4dac431769995ca773714",
    "d981f48cda621f2e453e5fd1007b18c6cc8e3a3034f5b956614c8b8c4a3d8128",
    "02bb99106da4cab85dc23cf59f95fd4754058c5b868ddeb24ae4111ebc73ff8c",
    "d95269d8c840734c67431c3a5076790083e84673d4b4a3e2f03d60dcfb9bba16",
    "1acb81cf75816d8ef3ee8da7e92e6decf202bcd499c17d342ea95fbd24931657",
    "c976fc5c5b63b1c69c60133547d8f43928193e47cd4b7400a7dfc73c1db51d10",
    "aabd82eac4e8ed009766603331fb9326921d11ba48262afa74e474eedce15670",
    "60e689144cd5f5c49c9d66f9ed7aa2cc99f6f4f01a9e07d044acac3abf039e3a",
    "74342ece497623e462dde2e21096462e4fff51d7565bc94fa1157d81fed7fea6",
    "7990cc6e5fe064eae39c2102215a0d90d752b88db413ae0f95023c7e245c2b34",
    "f695333f6610bfcb9c76e286d9f0a1eb45686d42ff3a4122c07342c939ef24c1",
    "98327d9f95f11d44e44a89c0f4a4466f23e22b4a99e6202e2996435e0d4fd8fe",
    "b278120d0b105a92256b31dc230283a021674608c250a878dd120ca280e56eb1",
    "c2161496a7ffdc17d9b870d00112bfcc9a86653628172f45065f630f1b302843",
    "0bfded3dfe36239739024ded444a3f24b5097f77bd31588e75c459e2a647fdb2",
    "67217bd476e1f18de8d8a16d12335e8d118823c2dc783a7bb037473699e7a6ed",
    "a1f054b9a0ce9b7eccf29f5b74b62fe9144387480e7e98a5006e6b3c9e0f2207",
    "5f5ec65708c8f6e659ddc5ec9645f38b1ae79b58981a5a0343c0fdbf2cf08cbd",
    "b2afc43e908eda68a961cd87adaf84f8e4e1878714890cc177874a0e97e33b43",
    "2b7df3bd4845650415c42dc05d1a0754b512cd668cc4b196c30d03fb0aa5707f",
}

NONSTANDARD_PLACEHOLDER_SOURCE_SHAS = {
    "d610ceaa89fafbf7336961ff3137a3d210d828b32675f2d7bc2a2f7ee1e33bfb",  # YOUR PROPMPT HERE
    "74b56520f9779a47dd5b1725e315adbccd761fb11f35c11e3a64f468205756bd",  # encoded <PROMPT_HERE>
    "d76e5ec0ce7fdb6949a10ec5ed3fa20c2a13a5a18d871f28361fe21820f4132c",  # [user_prompt]
    "cde9aabbc42845540d750c221191d7accc5595fc00690e2806582cd5b632d397",  # prompt example phrase
}

TRAILING_SAMPLE_SOURCE_SHAS = {
    "eb8818673ecae75f35246452f623084d94375c1d3ef97660b7d6f2e7d75c10c3",
    "009ed8bc860732b09ba1a259b1584933581f42b1758eb1bd1a75a23f04c5658c",
    "aed2b635f0219c84e1e5fdfbb1eda33c8aed217721a25b7c611dca4fada9e4fa",
    "8f1f6acb27d43f8fc75adb7355164ebee7eaec3b63d1727fec28e6ea7a041673",
    "0cff25d9b0223585f543b1d31418f6bc85d6fb1c85bbf64595cbf2acd60ff73c",
    "98a945c6fe80261eb1bce89eeabc158118d6bc985d1e30a30d53e54f9417e22c",
    "191f16df07cb9583331a32a488a54d14de4b6c79d75903bad0131754e67024a0",
}

# Conservative PII / user-handle signals that must never appear in a shipped template.
PII_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "discord_tag": re.compile(r"[A-Za-z0-9_.]{2,}#\d{4}\b"),
    "discord_mention": re.compile(r"<@!?\d+>"),
    "at_handle": re.compile(r"(?<![\w`/])@[A-Za-z][A-Za-z0-9_]{2,}\b"),
    "reddit_user": re.compile(r"\bu/[A-Za-z0-9_\-]{3,}"),
}


def _yaml_files() -> list[Path]:
    return sorted(IN_THE_WILD_DIR.glob("*.yaml"))


def _yaml_ids() -> list[str]:
    return [p.name for p in _yaml_files()]


def _shipped_by_source_sha() -> dict[str, SeedPrompt]:
    """Map each shipped template's ``source_prompt_sha256`` to its loaded ``SeedPrompt``."""
    index: dict[str, SeedPrompt] = {}
    for path in _yaml_files():
        seed = SeedPrompt.from_yaml_file(path)
        sha = str((seed.metadata or {}).get("source_prompt_sha256", ""))
        index[sha] = seed
    return index


def test_in_the_wild_directory_is_populated() -> None:
    assert IN_THE_WILD_DIR.is_dir(), f"missing directory: {IN_THE_WILD_DIR}"
    files = _yaml_files()
    # The collection covers both explicit-placeholder wrappers (Path A) and the much larger
    # set of slot-less persona/override wrappers (Path B); guard against a silent regression
    # that would strip the Path-B expansion back down to the original ~100 templates.
    assert len(files) == 488, f"expected exactly 488 TrustAIRLab templates, found {len(files)}"


@pytest.mark.parametrize("yaml_file", _yaml_files(), ids=_yaml_ids())
def test_filename_follows_stable_hash_convention(yaml_file: Path) -> None:
    assert FILENAME_RE.match(yaml_file.name), f"{yaml_file.name} does not match trustairlab_<16 hex>.yaml"


def test_filenames_are_globally_unique_across_all_templates() -> None:
    """No in-the-wild filename may collide with any other template in the tree.

    ``TextJailBreak._resolve_template_by_name`` requires globally unique filenames.
    """
    all_names = [p.name for p in JAILBREAK_TEMPLATES_PATH.rglob("*.yaml")]
    duplicates = {name for name in _yaml_ids() if all_names.count(name) > 1}
    assert not duplicates, f"filenames collide with other templates: {sorted(duplicates)}"


def test_seedprompt_names_are_unique() -> None:
    names = [SeedPrompt.from_yaml_file(p).name for p in _yaml_files()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate SeedPrompt names: {sorted(dupes)}"


@pytest.mark.parametrize("yaml_file", _yaml_files(), ids=_yaml_ids())
def test_parameter_and_type_contract(yaml_file: Path) -> None:
    seed = SeedPrompt.from_yaml_file(yaml_file)
    assert seed.parameters == ["prompt"], f"{yaml_file.name}: parameters={seed.parameters}"
    assert seed.data_type == "text", f"{yaml_file.name}: data_type={seed.data_type}"
    assert seed.is_general_technique is True, f"{yaml_file.name}: is_general_technique must be true"
    assert seed.name == yaml_file.stem, f"{yaml_file.name}: name should match file stem"


@pytest.mark.parametrize("yaml_file", _yaml_files(), ids=_yaml_ids())
def test_exactly_one_prompt_slot_in_template_value(yaml_file: Path) -> None:
    """The loaded template value must expose exactly one usable ``{{ prompt }}`` placeholder."""
    seed = SeedPrompt.from_yaml_file(yaml_file)
    assert seed.value.count("{{ prompt }}") == 1, f"{yaml_file.name}: expected one usable {{{{ prompt }}}} slot"


@pytest.mark.parametrize("yaml_file", _yaml_files(), ids=_yaml_ids())
def test_attribution_and_provenance(yaml_file: Path) -> None:
    seed = SeedPrompt.from_yaml_file(yaml_file)

    # Do not attribute user-posted prompts to the dataset collectors.
    assert seed.authors == [], f"{yaml_file.name}: authors must be empty (no user attribution)"
    assert seed.groups == ["TrustAIRLab"], f"{yaml_file.name}: groups must be [TrustAIRLab]"
    assert seed.source == "https://huggingface.co/datasets/TrustAIRLab/in-the-wild-jailbreak-prompts"

    description = (seed.description or "").lower()
    assert "trustairlab" in description
    assert "collected" in description
    # Must not claim the collectors authored the prompt.
    assert "written by trustairlab" not in description
    assert "authored by" not in description

    metadata = seed.metadata or {}
    assert metadata.get("source_platform") in VALID_PLATFORMS, f"{yaml_file.name}: bad platform"
    assert "source_collection" in metadata
    assert metadata.get("source_dataset_commit") == KNOWN_COMMIT
    assert metadata.get("source_dataset_config") == "jailbreak_2023_12_25"
    assert metadata.get("source_dataset_split") == "train"
    assert SHA256_RE.match(str(metadata.get("source_prompt_sha256", ""))), f"{yaml_file.name}: bad source hash"
    assert metadata.get("conversion_path") in VALID_CONVERSION_PATHS, f"{yaml_file.name}: bad conversion_path"
    assert metadata.get("conversion_method"), f"{yaml_file.name}: missing conversion_method"
    assert seed.dataset_name == "trustairlab_in_the_wild_jailbreak_2023_12_25"


@pytest.mark.parametrize("yaml_file", _yaml_files(), ids=_yaml_ids())
def test_renders_without_residual_placeholders(yaml_file: Path) -> None:
    jailbreak = TextJailBreak(template_path=str(yaml_file))
    sentinel = "SENTINEL_USER_REQUEST_XYZ"
    rendered = jailbreak.get_jailbreak(sentinel)
    assert sentinel in rendered, f"{yaml_file.name}: prompt not inserted"
    for residual in ("{{ prompt }}", "{% raw %}", "{% endraw %}"):
        assert residual not in rendered, f"{yaml_file.name}: residual {residual!r} after render"
    assert not RESIDUAL_TEMPLATE_VARIABLE_RE.search(rendered), (
        f"{yaml_file.name}: unresolved template variable remains after render"
    )


@pytest.mark.parametrize("yaml_file", _yaml_files(), ids=_yaml_ids())
def test_prompt_value_is_not_re_evaluated_as_jinja(yaml_file: Path) -> None:
    """A user prompt containing Jinja-like syntax must be inserted literally, never executed."""
    jailbreak = TextJailBreak(template_path=str(yaml_file))
    injected = "{{ 7 * 7 }} {% raw %}pwned"
    rendered = jailbreak.get_jailbreak(injected)
    # If the substituted value were re-evaluated, "{{ 7 * 7 }}" would collapse to "49".
    assert injected in rendered, f"{yaml_file.name}: user-supplied Jinja was not inserted literally"


@pytest.mark.parametrize("yaml_file", _yaml_files(), ids=_yaml_ids())
def test_no_pii_or_user_handles_in_rendered_template(yaml_file: Path) -> None:
    jailbreak = TextJailBreak(template_path=str(yaml_file))
    text = jailbreak.get_jailbreak_system_prompt()
    for label, pattern in PII_PATTERNS.items():
        match = pattern.search(text)
        assert match is None, f"{yaml_file.name}: residual {label} {match.group(0)!r}"


def test_both_conversion_paths_are_represented() -> None:
    """The collection must ship both explicit-placeholder (A) and slot-less-wrapper (B) templates."""
    paths = {(SeedPrompt.from_yaml_file(p).metadata or {}).get("conversion_path") for p in _yaml_files()}
    assert {"A", "B"} <= paths, f"expected both conversion paths, found {paths}"


def test_representative_path_b_append_wrapper_is_included() -> None:
    """A slot-less persona/override wrapper must be converted by appending a final ``{{ prompt }}``."""
    seed = _shipped_by_source_sha().get(PATH_B_APPEND_SOURCE_SHA)
    assert seed is not None, "expected the slot-less persona wrapper to be converted via Path B (append)"
    metadata = seed.metadata or {}
    assert metadata.get("conversion_path") == "B"
    assert "appended" in str(metadata.get("conversion_method", ""))
    # The single active slot sits at the natural final user-request position (end of the wrapper).
    assert seed.value.rstrip().endswith("{{ prompt }}"), "append conversion should end with the slot"


def test_representative_path_b_replace_wrapper_is_included() -> None:
    """A slot-less wrapper with an obvious trailing sample request must have it replaced in place."""
    seed = _shipped_by_source_sha().get(PATH_B_REPLACE_SOURCE_SHA)
    assert seed is not None, "expected the trailing-sample wrapper to be converted via Path B (replace)"
    metadata = seed.metadata or {}
    assert metadata.get("conversion_path") == "B"
    assert "embedded" in str(metadata.get("conversion_method", ""))


def test_narrative_attribution_prompts_are_not_dropped_as_pii() -> None:
    """Generic narrative like "created by OpenAI" is not user PII and must not exclude a prompt."""
    index = _shipped_by_source_sha()
    for sha in PATH_A_NARRATIVE_ATTRIBUTION_SOURCE_SHAS:
        seed = index.get(sha)
        assert seed is not None, f"narrative-attribution prompt {sha} must be included, not PII-excluded"
        assert (seed.metadata or {}).get("conversion_path") == "A"


def test_nonstandard_request_placeholders_are_replaced_in_place() -> None:
    """Reviewed placeholder variants must use their natural request slot, not Path-B append."""
    index = _shipped_by_source_sha()
    for sha in NONSTANDARD_PLACEHOLDER_SOURCE_SHAS:
        seed = index.get(sha)
        assert seed is not None, f"nonstandard request placeholder {sha} must be included"
        assert (seed.metadata or {}).get("conversion_path") == "A"


def test_trailing_sample_requests_are_replaced_in_place() -> None:
    """Audited source samples must be replaced rather than retained beside an appended slot."""
    index = _shipped_by_source_sha()
    for sha in TRAILING_SAMPLE_SOURCE_SHAS:
        seed = index.get(sha)
        assert seed is not None, f"trailing-sample wrapper {sha} must be included"
        metadata = seed.metadata or {}
        assert metadata.get("conversion_path") == "B"
        assert "replaced audited trailing sample" in str(metadata.get("conversion_method", ""))


def test_known_false_positive_sources_are_absent() -> None:
    """Reviewed non-wrapper / unsafe source prompts must never ship."""
    leaked = EXCLUDED_SOURCE_SHAS & set(_shipped_by_source_sha())
    assert not leaked, f"excluded source prompts leaked into shipped templates: {sorted(leaked)}"


def test_unprefixed_user_attributions_are_absent() -> None:
    """Reviewed plain-text contributor aliases must not be redistributed."""
    leaked = UNPREFIXED_ATTRIBUTION_SOURCE_SHAS & set(_shipped_by_source_sha())
    assert not leaked, f"credited user aliases leaked into shipped templates: {sorted(leaked)}"
