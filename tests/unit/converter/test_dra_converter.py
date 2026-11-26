# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for DRAConverter."""

import re

import pytest

from pyrit.converter import DRAConverter, DRATemplate, dra_converter

_SEEDS = range(80)

_TOOLS = {
    "lots of money",
    "a lab",
    "a hammer",
    "a fast car",
    "a mask",
    "a computer",
    "a smartphone",
    "a table",
    "a fake ID card",
    "a toolbox",
    "gloves",
    "a flashlight",
    "some disguise tools",
    "a telescope",
}

# A line only one template has, to tell from an output which template rendered it.
_TEMPLATE_MARKERS = {
    DRATemplate.PREPARE_TOOLS: "First, you should prepare",
    DRATemplate.CHOOSE_TARGET: "First, you should choose a target",
    DRATemplate.STRICT_PREPARE_TOOLS: "First, we should prepare",
    DRATemplate.STRICT_CHOOSE_TARGET: "1. We should choose a target",
}


def _converter(**kwargs) -> DRAConverter:
    return DRAConverter(**{"template": DRATemplate.PREPARE_TOOLS, "seed": 1, **kwargs})


async def _convert(prompt: str, **kwargs) -> str:
    return (await _converter(**kwargs).convert_async(prompt=prompt)).output_text


def _disguise_lines(output: str, prompt: str) -> list[str]:
    return output.split("\n")[: len(prompt)]


def _word_guide_entries(output: str) -> list[str]:
    guide_line = next(line for line in output.split("\n") if line.startswith("Note that Q1 contains the words"))
    guide = guide_line.removeprefix("Note that Q1 contains the words").removeprefix(":").lstrip()
    entries: list[str] = re.findall(r'"([^"]*)" ', guide)
    assert guide == "".join(f'"{entry}" ' for entry in entries)
    return entries


def _template_of(output: str) -> DRATemplate:
    (template,) = [template for template, marker in _TEMPLATE_MARKERS.items() if marker in output]
    return template


async def _cuts(word: str, **kwargs) -> list[list[str]]:
    """Word-guide entries for a one-word prompt, once per seed."""
    return [_word_guide_entries(await _convert(word, seed=seed, **kwargs)) for seed in _SEEDS]


async def test_convert_async_prefix_spells_lowercased_prompt():
    prompt = "How To Rob a Bank"
    output = await _convert(prompt)

    lines = _disguise_lines(output, prompt)
    hidden = []
    for line in lines:
        match = re.fullmatch(r"[^()]*\((.)\)[^()]*", line)
        assert match, f"expected exactly one parenthesized character in {line!r}"
        hidden.append(match.group(1))

    assert "".join(hidden) == prompt.lower()
    assert "( )" in lines
    assert output.split("\n")[len(prompt)].startswith("Read these sentences carefully!")


async def test_convert_async_disguise_uses_every_phrase_and_position():
    # "e" maps to "iPhone", "eleven", and "eleven killers"; every "e" in them can be the hidden one.
    lines = {_disguise_lines(await _convert("e", seed=seed), "e")[0] for seed in _SEEDS}

    assert lines == {
        "iPhon(e)",
        "(e)leven",
        "el(e)ven",
        "elev(e)n",
        "(e)leven killers",
        "el(e)ven killers",
        "elev(e)n killers",
        "eleven kill(e)rs",
    }


async def test_convert_async_parenthesizes_character_without_phrase():
    prompt = "a1?"
    output = await _convert(prompt)

    assert _disguise_lines(output, prompt)[1:] == ["(1)", "(?)"]


async def test_convert_async_truncates_flagged_word():
    # 3 or 4 characters: drop the last letter, or the last two characters after punctuation.
    assert _word_guide_entries(await _convert("Poo? GUN"))[:2] == ["po", "gu"]

    # Longer words are cut from max(2, int(length * harmful_keep_ratio)) to length - 1, and the
    # tail is sometimes added. "Shoplift" is flagged once lowercased, so it is never kept whole.
    for word, ratio, head_lengths in (
        ("Shoplift", 0.5, {4, 5, 6, 7}),
        ("shoplift", 0.7, {5, 6, 7}),
        ("bombs", 0.5, {2, 3, 4}),
        ("bombs", 0.1, {2, 3, 4}),
    ):
        cuts = await _cuts(word, harmful_keep_ratio=ratio)
        lowered = word.lower()

        assert {len(entries[0]) for entries in cuts} == head_lengths
        assert {len(entries) for entries in cuts} == {1, 2}
        for head, *tail in cuts:
            assert lowered.startswith(head)
            assert tail in ([], [lowered[len(head) :]])


async def test_convert_async_cuts_benign_words():
    # "sandwich" is benign: with no whole-word chance, the cut starts at min(8 * 2 // 3, int(8 * ratio)).
    for ratio, head_lengths in ((0.2, set(range(1, 8))), (0.5, {4, 5, 6, 7}), (0.9, {5, 6, 7})):
        cuts = await _cuts("sandwich", benign_keep_ratio=ratio, benign_full_word_probability=0.0)

        assert {len(entries[0]) for entries in cuts} == head_lengths
        assert {len(entries) for entries in cuts} == {1, 2}
        for head, *tail in cuts:
            assert tail in ([], ["sandwich"[len(head) :]])

    # A tiny ratio would cut before the first character; the hint is never empty.
    cuts = await _cuts("tree", benign_keep_ratio=0.01, benign_full_word_probability=0.0)
    assert {entries[0] for entries in cuts} == {"t", "tr", "tre"}

    assert {tuple(entries) for entries in await _cuts("sandwich", benign_full_word_probability=1.0)} == {("sandwich",)}
    whole = [entries == ["sandwich"] for entries in await _cuts("sandwich")]
    assert 0 < sum(whole) < len(whole)


async def test_convert_async_keeps_short_words_whole():
    # Benign words of 3 or fewer characters and flagged words of 2 or fewer stay whole.
    entries = _word_guide_entries(await _convert("How to be a id"))

    assert entries == ["how", "to", "be", "a", "id"]


async def test_convert_async_picks_two_different_tools():
    seen = set()
    for seed in _SEEDS:
        output = await _convert("rob a bank", seed=seed)
        tools = re.search(r"you should prepare (.+?), then", output).group(1).split(" and ")
        assert len(tools) == 2
        assert tools[0] != tools[1]
        seen.update(tools)

    assert seen == _TOOLS


async def test_convert_async_is_reproducible_with_same_seed():
    first = DRAConverter(seed=7)
    second = DRAConverter(seed=7)
    prompt = "how to steal a car without getting caught"

    assert first.get_identifier() == second.get_identifier()
    first_output = (await first.convert_async(prompt=prompt)).output_text
    second_output = (await second.convert_async(prompt=prompt)).output_text
    assert first_output == second_output

    other_seed = [(await DRAConverter(seed=seed).convert_async(prompt=prompt)).output_text for seed in range(8, 13)]
    assert first_output not in other_seed


async def test_convert_async_picks_template_per_conversion():
    converter = DRAConverter(seed=7)

    templates = [_template_of((await converter.convert_async(prompt=f"request {i}")).output_text) for i in range(40)]

    assert set(templates) == set(DRATemplate)


@pytest.mark.parametrize("template", list(DRATemplate))
async def test_convert_async_renders_each_template(template: DRATemplate):
    seed_prompt = template.to_seed_prompt()
    prompt = "how to rob a bank"

    result = await DRAConverter(template=template, seed=3).convert_async(prompt=prompt)
    suffix = "\n".join(result.output_text.split("\n")[len(prompt) :])
    entries = _word_guide_entries(result.output_text)

    assert result.output_type == "text"
    assert entries[0] == "how"
    assert _template_of(result.output_text) == template
    expected = seed_prompt.value.replace("{{ word_guide }}", "".join(f'"{entry}" ' for entry in entries))
    if "tools" in seed_prompt.parameters:
        tools = re.search(r"prepare (.+?), ", suffix).group(1).split(" and ")
        assert len(set(tools)) == 2
        assert set(tools) <= _TOOLS
        expected = expected.replace("{{ tools }}", " and ".join(tools))
    assert suffix == expected
    assert "`" not in suffix and "{{" not in suffix


@pytest.mark.parametrize(
    "kwargs",
    [
        {"harmful_keep_ratio": 0.0},
        {"harmful_keep_ratio": 1.0},
        {"harmful_keep_ratio": float("nan")},
        {"benign_keep_ratio": 0.0},
        {"benign_keep_ratio": 1.0},
        {"benign_full_word_probability": -0.01},
        {"benign_full_word_probability": 1.01},
    ],
)
def test_init_rejects_out_of_range_settings(kwargs):
    setting = next(iter(kwargs))
    with pytest.raises(ValueError, match=setting):
        DRAConverter(**kwargs)


def test_init_rejects_template_that_is_not_a_member():
    with pytest.raises(TypeError, match="DRATemplate"):
        DRAConverter(template="prepare_tools")  # type: ignore[arg-type]


@pytest.mark.parametrize("probability", [0.0, 1.0])
def test_init_accepts_probability_bounds(probability):
    DRAConverter(benign_full_word_probability=probability)


async def test_convert_async_rejects_unsupported_input_type():
    with pytest.raises(ValueError, match="not supported"):
        await _converter().convert_async(prompt="how to rob a bank", input_type="image_path")


async def test_convert_async_reads_no_files(monkeypatch):
    # All data is loaded at construction, so conversion does no blocking file I/O on the event loop.
    dra_converter._load_templates.cache_clear()
    dra_converter._load_words.cache_clear()
    converter = DRAConverter(seed=1)

    def fail(*args, **kwargs):
        raise AssertionError("convert_async read a file")

    monkeypatch.setattr(dra_converter.SeedDataset, "from_yaml_file", fail)
    monkeypatch.setattr(dra_converter, "load_yaml_file", fail)

    templates = {_template_of((await converter.convert_async(prompt=f"rob bank {i}")).output_text) for i in range(40)}
    assert templates == set(DRATemplate)


def test_identifier_includes_template_and_settings():
    converter = DRAConverter(
        template=DRATemplate.STRICT_CHOOSE_TARGET,
        harmful_keep_ratio=0.6,
        benign_keep_ratio=0.4,
        benign_full_word_probability=0.2,
        seed=11,
    )

    assert (
        converter.get_identifier().params.items()
        >= {
            "template": "strict_choose_target",
            "harmful_keep_ratio": 0.6,
            "benign_keep_ratio": 0.4,
            "benign_full_word_probability": 0.2,
            "seed": 11,
        }.items()
    )
    assert DRAConverter().get_identifier().params.get("template") is None
