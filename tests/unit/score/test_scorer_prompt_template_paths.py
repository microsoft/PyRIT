# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests that a scorer's caller-supplied prompt template cannot use PyRIT's path constants to touch the filesystem."""

import pathlib
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.common.path import DATASETS_PATH
from pyrit.score import LlamaGuardScorer, Scorer, ShieldGemmaGuideline, ShieldGemmaScorer, WildGuardScorer

# Each scorer renders a caller-supplied string template with PyRIT's path constants bound,
# so constructing the scorer is enough to evaluate the template.
SCORER_CASES = [
    pytest.param(LlamaGuardScorer, {}, "{{ message_role }} {{ categories }} {{ conversation }}", id="llamaguard"),
    pytest.param(WildGuardScorer, {}, "{{ user_prompt }} {{ response }}", id="wildguard"),
    pytest.param(
        ShieldGemmaScorer,
        {"guideline": ShieldGemmaGuideline(name="Custom harm", description="content that is bad.")},
        "{{ response }} {{ guideline }}",
        id="shieldgemma",
    ),
]


def _build_scorer(
    *, scorer_class: Callable[..., Scorer], scorer_kwargs: dict[str, Any], base_template: str, payload: str
) -> Scorer:
    return scorer_class(chat_target=MockPromptTarget(), prompt_template=f"{base_template} {payload}", **scorer_kwargs)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("scorer_class", "scorer_kwargs", "base_template"), SCORER_CASES)
@pytest.mark.parametrize(
    "payload",
    [
        "{{ (datasets_path / 'secret.txt').read_text() }}",
        "{{ (datasets_path / 'secret.txt').open().read() }}",
        "{% for entry in datasets_path.iterdir() %}{{ entry.name }}{% endfor %}",
    ],
)
def test_scorer_prompt_template_cannot_read_files_through_path_constants(
    *,
    scorer_class: Callable[..., Scorer],
    scorer_kwargs: dict[str, Any],
    base_template: str,
    payload: str,
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")

    with (
        patch("pyrit.models.seeds.seed_prompt.PATHS_DICT", {"datasets_path": tmp_path}),
        pytest.raises(ValueError, match="Error rendering template") as error,
    ):
        _build_scorer(
            scorer_class=scorer_class, scorer_kwargs=scorer_kwargs, base_template=base_template, payload=payload
        )

    assert "TOP-SECRET" not in str(error.value)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("scorer_class", "scorer_kwargs", "base_template"), SCORER_CASES)
@pytest.mark.parametrize(
    "payload",
    [
        "{{ (datasets_path / 'pwned.txt').write_text('owned') }}",
        "{{ (datasets_path / 'pwned.txt').touch() }}",
        "{{ (datasets_path / 'secret.txt').unlink() }}",
    ],
)
def test_scorer_prompt_template_cannot_modify_files_through_path_constants(
    *,
    scorer_class: Callable[..., Scorer],
    scorer_kwargs: dict[str, Any],
    base_template: str,
    payload: str,
    tmp_path: pathlib.Path,
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")

    with (
        patch("pyrit.models.seeds.seed_prompt.PATHS_DICT", {"datasets_path": tmp_path}),
        pytest.raises(ValueError, match="Error rendering template"),
    ):
        _build_scorer(
            scorer_class=scorer_class, scorer_kwargs=scorer_kwargs, base_template=base_template, payload=payload
        )

    assert list(tmp_path.iterdir()) == [secret]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("scorer_class", "scorer_kwargs", "base_template"), SCORER_CASES)
def test_scorer_prompt_template_cannot_reach_the_os_module_through_path_constants(
    *, scorer_class: Callable[..., Scorer], scorer_kwargs: dict[str, Any], base_template: str
) -> None:
    with (
        patch.dict("os.environ", {"PYRIT_TEST_SECRET": "leaked-env-value"}),
        pytest.raises(ValueError, match="Error rendering template") as error,
    ):
        _build_scorer(
            scorer_class=scorer_class,
            scorer_kwargs=scorer_kwargs,
            base_template=base_template,
            payload="{{ datasets_path.parser.os.environ['PYRIT_TEST_SECRET'] }}",
        )

    assert "leaked-env-value" not in str(error.value)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("scorer_class", "scorer_kwargs", "base_template"), SCORER_CASES)
def test_scorer_prompt_template_formats_path_constants_unchanged(
    *, scorer_class: Callable[..., Scorer], scorer_kwargs: dict[str, Any], base_template: str
) -> None:
    scorer = _build_scorer(
        scorer_class=scorer_class,
        scorer_kwargs=scorer_kwargs,
        base_template=base_template,
        payload="{{ datasets_path }}/seed_datasets",
    )

    assert scorer.get_identifier().params["prompt_template"].endswith(f"{DATASETS_PATH}/seed_datasets")
