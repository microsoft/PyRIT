# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``ShieldGemmaGuideline`` and ``ShieldGemmaPolicy``."""

import pytest

from pyrit.score import ShieldGemmaGuideline, ShieldGemmaMessageRole, ShieldGemmaPolicy


def _guideline(name: str = "No Dangerous Content", description: str = "content that is bad.") -> ShieldGemmaGuideline:
    return ShieldGemmaGuideline(name=name, description=description)


def test_default_policy_loads_documented_guidelines() -> None:
    policy = ShieldGemmaPolicy.default()

    assert policy.guideline_names == (
        "No Dangerous Content",
        "No Harassment",
        "No Hate Speech",
        "No Sexually Explicit Information",
    )


def test_default_hate_speech_guideline_is_not_truncated() -> None:
    """The bundled text is Google's in full, including the clauses after "protected groups"."""
    description = ShieldGemmaPolicy.default().get("No Hate Speech").description

    assert description.endswith(
        "or dehumanizing/belittling/vilifying on the bases of race, ethnicity, religion, "
        "disability, age, nationality, veteran status, sexual orientation, gender, gender "
        "identity, caste, or any other protected status. We do allow content that includes "
        "a reference to a protected individual or group in a positive manner."
    )


def test_guideline_rendered_quotes_the_name() -> None:
    rendered = _guideline(name="No Harassment", description="content that is abusive.").rendered(
        ShieldGemmaMessageRole.USER
    )

    assert rendered == '"No Harassment": The prompt shall not contain or seek generation of content that is abusive.'


@pytest.mark.parametrize("name", ["Ends in colon:", "Ends in period.", "Ends in question?"])
def test_guideline_rendered_quotes_names_ending_in_punctuation(name: str) -> None:
    """Quoting is unconditional, so a name ending in punctuation cannot double it up."""
    rendered = _guideline(name=name).rendered(ShieldGemmaMessageRole.USER)

    assert rendered.startswith(f'"{name}": ')


def test_get_is_case_insensitive() -> None:
    policy = ShieldGemmaPolicy.default()

    assert policy.get("no dangerous content").name == "No Dangerous Content"


def test_get_unknown_guideline_raises_with_available_names() -> None:
    policy = ShieldGemmaPolicy.default()

    with pytest.raises(KeyError) as excinfo:
        policy.get("No Such Guideline")

    assert "No Dangerous Content" in str(excinfo.value)


@pytest.mark.parametrize(
    ("name", "description"),
    [
        (" leading space", "valid"),
        ("valid", "trailing space "),
    ],
)
def test_guideline_rejects_surrounding_whitespace(name: str, description: str) -> None:
    with pytest.raises(ValueError):
        ShieldGemmaGuideline(name=name, description=description)


def test_policy_rejects_duplicate_guideline_names() -> None:
    with pytest.raises(ValueError):
        ShieldGemmaPolicy(
            name="dupes",
            version="1",
            guidelines=(_guideline(name="Same"), _guideline(name="Same")),
        )


def test_policy_rejects_guideline_names_differing_only_in_case() -> None:
    """Uniqueness matches how get() matches, so no guideline can become unreachable."""
    with pytest.raises(ValueError):
        ShieldGemmaPolicy(
            name="case dupes",
            version="1",
            guidelines=(_guideline(name="Harm"), _guideline(name="harm")),
        )


def test_policy_rejects_empty_guidelines() -> None:
    with pytest.raises(ValueError):
        ShieldGemmaPolicy(name="empty", version="1", guidelines=())


def test_policy_from_yaml_rejects_non_mapping(tmp_path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("- not\n- a mapping\n", encoding="utf-8")

    with pytest.raises(ValueError):
        ShieldGemmaPolicy.from_yaml(path)
