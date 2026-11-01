# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models import (
    DEFAULT_FINDING_FIELDS,
    FindingField,
    FindingFields,
    FindingValuesError,
)
from pyrit.models.finding_fields import FindingFieldOption, FindingValueRow
from pyrit.models.harm_category import HarmCategory

CONTOSO = FindingFields(
    severity=FindingField(
        key="severity",
        label="Severity",
        type="choice",
        section="Severity",
        colors={"sev1": "red", "sev2": "yellow", "sev3": "blue"},
        levels=[("severity_rationale", "Severity rationale")],
        options={
            ("sev1", "Sev 1"): [("remote_no_auth", "Remote, no sign-in needed")],
            ("sev2", "Sev 2"): [("signed_in_user", "Needs a signed-in user")],
            ("sev3", "Sev 3"): [("admin_only", "Needs admin access")],
        },
    ),
    fields=[
        FindingField(
            key="category",
            label="Category",
            type="choice",
            required=True,
            section="Classification",
            levels=[("subcategory", "Subcategory")],
            options={
                ("model_security", "Model security"): [
                    ("system_prompt_leak", "System prompt leak"),
                    ("tool_misuse", "Tool misuse"),
                ],
                ("content_safety", "Content safety"): [("hate_speech", "Hate speech"), ("self_harm", "Self-harm")],
            },
        ),
        FindingField(
            key="technique",
            label="Technique",
            type="choice",
            section="Reproduction",
            allow_other=True,
            options=[("role_play", "Role play"), ("multi_turn", "Multi-turn escalation")],
        ),
        FindingField(
            key="steps",
            label="Steps to reproduce",
            type="multiline",
            required=True,
            section="Reproduction",
            hint="List the prompts in the order you sent them.",
        ),
        FindingField(key="owner", label="Owner", type="text", section="Tracking", internal=True),
    ],
)


def test_finding_field_normalizes_pairs_and_nested_options() -> None:
    assert CONTOSO.severity.options[0] == FindingFieldOption(
        key="sev1",
        label="Sev 1",
        children=[FindingFieldOption(key="remote_no_auth", label="Remote, no sign-in needed")],
    )
    technique = CONTOSO.fields[1]
    assert [(option.key, option.label, option.children) for option in technique.options] == [
        ("role_play", "Role play", []),
        ("multi_turn", "Multi-turn escalation", []),
    ]


def test_finding_fields_round_trip_through_json() -> None:
    assert FindingFields.model_validate_json(CONTOSO.model_dump_json()) == CONTOSO


def test_severity_slot_is_required_and_ordered() -> None:
    assert CONTOSO.severity.required is True
    assert CONTOSO.severity_order == ["sev1", "sev2", "sev3"]


def test_field_keys_lists_every_field_and_level_key_except_severity() -> None:
    assert CONTOSO.field_keys == frozenset(
        {"severity_rationale", "category", "subcategory", "technique", "steps", "owner"}
    )


def test_default_finding_fields_define_severity_harm_type_and_description() -> None:
    severity, (harm, description) = DEFAULT_FINDING_FIELDS.severity, DEFAULT_FINDING_FIELDS.fields
    assert DEFAULT_FINDING_FIELDS.severity_order == ["critical", "important", "moderate", "low", "informational"]
    assert [option.label for option in severity.options] == [
        "Critical",
        "Important",
        "Moderate",
        "Low",
        "Informational",
    ]
    assert (severity.default, severity.allow_other) == ("moderate", True)
    assert severity.colors == {"critical": "red", "important": "yellow", "moderate": "yellow", "low": "gray"}
    assert (harm.key, harm.label, harm.type, harm.required, harm.allow_other) == (
        "harm_type",
        "Harm-type",
        "choice",
        False,
        True,
    )
    assert [option.key for option in harm.options] == [c.value for c in HarmCategory if c is not HarmCategory.OTHER]
    assert harm.hint == "Optional. Select a category, or choose Other for custom text."
    assert (description.key, description.label, description.type, description.hint) == (
        "description",
        "Description",
        "multiline",
        "Optional",
    )


SEVERITY = {"key": "severity", "label": "Severity", "type": "choice", "options": [("high", "High")]}


def _choice(**overrides: object) -> dict[str, object]:
    return {"key": "area", "label": "Area", "type": "choice", "options": [("chat", "Chat")], **overrides}


NESTED = {("chat", "Chat"): [("history", "History")]}


@pytest.mark.parametrize(
    ("severity", "fields", "match"),
    [
        (SEVERITY, [_choice(key="Bad-Key")], "lowercase letters, digits and underscores"),
        (
            SEVERITY,
            [{"key": "notes", "label": "Notes", "type": "text", "options": [("a", "A")]}],
            "cannot have options",
        ),
        (SEVERITY, [_choice(options=[])], "needs at least one option"),
        (SEVERITY, [_choice(options=[("chat", "Chat"), ("chat", "Chat again")])], "repeats the option key 'chat'"),
        (SEVERITY, [_choice(options=NESTED)], "one entry in levels"),
        (SEVERITY, [_choice(levels=[("feature", "Feature")])], "one entry in levels"),
        (SEVERITY, [_choice(default="search")], "'search' is not a top-level option of 'area'"),
        (
            SEVERITY,
            [_choice(allow_other=True, levels=[("feature", "Feature")], options=NESTED)],
            "Other text only without levels",
        ),
        (SEVERITY, [_choice(colors={"chat": "red"})], "Only the severity field can set colors"),
        ({**SEVERITY, "colors": {"low": "red"}}, [], "'low' is not a top-level option of 'severity'"),
        ({**SEVERITY, "key": "sev"}, [], "key must be 'severity'"),
        ({**SEVERITY, "type": "text", "options": []}, [], "must be a choice field"),
        ({**SEVERITY, "required": False}, [], "always required"),
        (SEVERITY, [_choice(), _choice(label="Area again")], "'area' is used more than once"),
        (SEVERITY, [_choice(levels=[("severity", "Severity")], options=NESTED)], "'severity' is used more than once"),
        (SEVERITY, [_choice(key="title")], "'title' is reserved"),
        (SEVERITY, [_choice(label=" ")], "must not be blank"),
    ],
)
def test_finding_fields_rejects_invalid_definitions(severity: dict, fields: list, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        FindingFields.model_validate({"severity": severity, "fields": fields})


@pytest.mark.parametrize(
    ("field", "match"),
    [
        (_choice(levels=[("Feature", "Feature")], options=NESTED), "lowercase letters, digits and underscores"),
        (_choice(key="__proto__"), "'__proto__' is reserved"),
        (_choice(levels=[("__proto__", "Feature")], options=NESTED), "'__proto__' is reserved"),
        (_choice(options=[("chat", " ")]), "must not be blank"),
        (_choice(levels=[("feature", "Feature")], options={("chat", "Chat"): [(" ", "History")]}), "must not be blank"),
        ({"key": "notes", "label": "Notes", "type": "text", "default": "a"}, "cannot have default"),
        (_choice(options={"chat": []}), r"\(key, label\) pair"),
    ],
)
def test_finding_field_rejects_invalid_parts(field: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        FindingField.model_validate(field)


def test_severity_slot_accepts_explicit_required() -> None:
    assert FindingFields.model_validate({"severity": {**SEVERITY, "required": True}}).severity.required is True


def test_internal_defaults_to_false_and_is_rejected_on_the_severity_slot() -> None:
    assert FindingField.model_validate(_choice()).internal is False
    with pytest.raises(ValidationError, match="The severity slot can't be internal."):
        FindingFields.model_validate({"severity": {**SEVERITY, "internal": True}})


def test_describe_values_lists_filled_values_in_configured_order_and_retired_keys() -> None:
    description = CONTOSO.describe_values(
        severity="sev1",
        field_values={
            "owner": "dana",
            "description": "Old text",
            "category": "model_security",
            "subcategory": "system_prompt_leak",
            "technique": "Many-shot priming",
            "steps": "   ",
            "severity_rationale": "remote_no_auth",
            "repro": "Older text",
        },
    )
    assert description.severity_label == "Sev 1"
    assert [(row.label, row.value) for row in description.rows] == [
        ("Severity rationale", "Remote, no sign-in needed"),
        ("Category", "Model security"),
        ("Subcategory", "System prompt leak"),
        ("Technique", "Many-shot priming"),
        ("Owner", "dana"),
    ]
    assert description.retired == [("description", "Old text"), ("repro", "Older text")]


def test_describe_values_shows_a_cascade_child_not_under_its_stored_parent_as_typed() -> None:
    description = CONTOSO.describe_values(
        severity="sev1", field_values={"category": "model_security", "subcategory": "hate_speech"}
    )
    assert [(row.label, row.value) for row in description.rows] == [
        ("Category", "Model security"),
        ("Subcategory", "hate_speech"),
    ]


def test_describe_values_shows_an_unmatched_severity_as_typed() -> None:
    description = CONTOSO.describe_values(severity="Sev 2.5", field_values={"severity_rationale": "remote_no_auth"})
    assert description.severity_label == "Sev 2.5"
    assert [(row.label, row.value) for row in description.rows] == [("Severity rationale", "remote_no_auth")]


def test_describe_values_carries_multiline_and_internal_on_each_row() -> None:
    description = CONTOSO.describe_values(severity="sev2", field_values={"owner": "dana", "steps": "1. Open\n2. Ask"})
    assert description.rows == [
        FindingValueRow(
            key="steps", label="Steps to reproduce", value="1. Open\n2. Ask", multiline=True, internal=False
        ),
        FindingValueRow(key="owner", label="Owner", value="dana", multiline=False, internal=True),
    ]
    assert description.retired == []


def test_describe_values_labels_the_default_fields() -> None:
    description = DEFAULT_FINDING_FIELDS.describe_values(
        severity="critical", field_values={"description": "Synthetic text", "harm_type": "Hate Speech"}
    )
    assert description.severity_label == "Critical"
    assert description.rows == [
        FindingValueRow(key="harm_type", label="Harm-type", value="Hate Speech", multiline=False, internal=False),
        FindingValueRow(key="description", label="Description", value="Synthetic text", multiline=True, internal=False),
    ]


VALID = {
    "severity_rationale": "remote_no_auth",
    "category": "model_security",
    "subcategory": "system_prompt_leak",
    "steps": "1. Open a new chat",
}


def _without(*keys: str) -> dict[str, str]:
    return {key: value for key, value in VALID.items() if key not in keys}


@pytest.mark.parametrize(
    ("severity", "values", "previous_severity", "previous_values"),
    [
        pytest.param("sev1", VALID, None, None, id="complete"),
        pytest.param("sev1", {**VALID, "technique": "Many-shot priming", "owner": "dana"}, None, None, id="other text"),
        pytest.param("sev4", _without("severity_rationale"), "sev4", {}, id="unchanged unmatched severity"),
        pytest.param("sev1", {**VALID, "repro": "old"}, "sev1", {"repro": "old"}, id="unchanged retired field"),
        pytest.param(
            "sev1",
            {**VALID, "category": "gone", "subcategory": "old"},
            "sev1",
            {"category": "gone", "subcategory": "old"},
            id="unchanged unmatched path",
        ),
    ],
)
def test_check_values_accepts(
    severity: str, values: dict, previous_severity: str | None, previous_values: dict | None
) -> None:
    CONTOSO.check_values(
        severity=severity, field_values=values, previous_severity=previous_severity, previous_values=previous_values
    )


@pytest.mark.parametrize(
    ("severity", "values", "previous_severity", "previous_values", "message"),
    [
        pytest.param("", VALID, None, None, "Severity is required.", id="blank severity"),
        pytest.param("sev9", VALID, None, None, "Severity has no option 'sev9'.", id="unknown severity"),
        pytest.param("sev4", VALID, "sev1", VALID, "Severity has no option 'sev4'.", id="changed to unmatched"),
        pytest.param(
            "sev1", _without("severity_rationale"), None, None, "Severity rationale is required.", id="missing level"
        ),
        pytest.param(
            "sev1", _without("category", "subcategory"), None, None, "Category is required.", id="missing field"
        ),
        pytest.param(
            "sev1",
            {**VALID, "subcategory": "hate_speech"},
            None,
            None,
            "Subcategory has no option 'hate_speech'.",
            id="child under wrong parent",
        ),
        pytest.param(
            "sev1",
            {**VALID, "category": "content_safety"},
            "sev1",
            VALID,
            "Subcategory has no option 'system_prompt_leak'.",
            id="stale child",
        ),
        pytest.param("sev1", {**VALID, "steps": "   "}, None, None, "Steps to reproduce is required.", id="blank text"),
        pytest.param(
            "sev1",
            _without("steps"),
            "sev1",
            _without("steps"),
            "Steps to reproduce is required.",
            id="required field added after saving",
        ),
        pytest.param(
            "sev1", {**VALID, "reviewer": "kim"}, None, None, "Unknown finding field 'reviewer'.", id="unknown key"
        ),
        pytest.param(
            "sev1",
            {**VALID, "reviewer": "kim"},
            "sev1",
            VALID,
            "Unknown finding field 'reviewer'.",
            id="unknown key the stored finding never had",
        ),
        pytest.param(
            "sev1",
            {**VALID, "severity": "sev1"},
            None,
            None,
            "Unknown finding field 'severity'.",
            id="severity in values",
        ),
        pytest.param(
            "sev1",
            {**VALID, "repro": "new"},
            "sev1",
            {**VALID, "repro": "old"},
            "Finding field 'repro' is no longer configured, so its saved value can't change.",
            id="changed retired field",
        ),
    ],
)
def test_check_values_rejects(
    severity: str, values: dict, previous_severity: str | None, previous_values: dict | None, message: str
) -> None:
    with pytest.raises(FindingValuesError) as error:
        CONTOSO.check_values(
            severity=severity, field_values=values, previous_severity=previous_severity, previous_values=previous_values
        )
    assert str(error.value) == message


@pytest.mark.parametrize(
    ("severity", "values", "message"),
    [
        pytest.param(
            "Critical",
            {},
            "Severity 'Critical' matches the option 'Critical' (key 'critical'). Choose that option instead.",
            id="severity label",
        ),
        pytest.param(
            "CRITICAL",
            {},
            "Severity 'CRITICAL' matches the option 'Critical' (key 'critical'). Choose that option instead.",
            id="severity key in capitals",
        ),
        pytest.param(
            "low ",
            {},
            "Severity 'low ' matches the option 'Low' (key 'low'). Choose that option instead.",
            id="severity key with a space",
        ),
        pytest.param(
            "low",
            {"harm_type": "malware"},
            "Harm-type 'malware' matches the option 'Malware' (key 'Malware'). Choose that option instead.",
            id="harm type key in lowercase",
        ),
    ],
)
def test_check_values_rejects_other_text_that_matches_an_option(
    severity: str, values: dict[str, str], message: str
) -> None:
    with pytest.raises(FindingValuesError) as error:
        DEFAULT_FINDING_FIELDS.check_values(severity=severity, field_values=values)
    assert str(error.value) == message


@pytest.mark.parametrize("severity", ["Sev 2.5", "critcal"])
def test_check_values_accepts_other_text_that_matches_no_option(severity: str) -> None:
    DEFAULT_FINDING_FIELDS.check_values(severity=severity, field_values={})


def test_check_values_accepts_unchanged_other_text_that_matches_an_option() -> None:
    DEFAULT_FINDING_FIELDS.check_values(severity="Critical", field_values={}, previous_severity="Critical")


AREA = _choice(
    levels=[("feature", "Feature")],
    options={("chat", "Chat"): [("history", "History")], ("search", "Search"): []},
)
AREAS = FindingFields.model_validate({"severity": SEVERITY, "fields": [AREA]})
REQUIRED_AREAS = FindingFields.model_validate({"severity": SEVERITY, "fields": [{**AREA, "required": True}]})


@pytest.mark.parametrize("values", [{"area": "chat"}, {}])
def test_check_values_optional_cascade_allows_a_partial_path(values: dict) -> None:
    AREAS.check_values(severity="high", field_values=values)


def test_check_values_rejects_a_child_without_its_parent() -> None:
    with pytest.raises(FindingValuesError) as error:
        AREAS.check_values(severity="high", field_values={"feature": "history"})
    assert str(error.value) == "Feature has no option 'history'."


def test_check_values_required_cascade_skips_a_level_with_no_options() -> None:
    REQUIRED_AREAS.check_values(severity="high", field_values={"area": "search"})
    with pytest.raises(FindingValuesError) as error:
        REQUIRED_AREAS.check_values(severity="high", field_values={"area": "chat"})
    assert str(error.value) == "Feature is required."
