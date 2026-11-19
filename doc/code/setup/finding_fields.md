# Finding Fields

A finding in CoPyRIT has a title, a severity and a set of fields. Each deployment chooses those fields. The finding dialog, the finding cards and the exports all follow them.

## Default Fields

Without any configuration, findings use these fields:

- **Severity**: Critical, Important, Moderate, Low or Informational. People can also choose **Other** and type their own text. The dialog preselects Moderate.
- **Harm-type**: optional. Its options are PyRIT's harm categories, such as Hate Speech, Malware and Scams. The list is searchable, and it also takes **Other** text.
- **Description**: optional multi-line text.

## Defining Your Own Fields

To use other fields, subclass `FindingsInitializer` and return a `FindingFields` from `get_finding_fields`. Every deployment has a `severity` slot. It's always a required choice field, but its options are your own: the example below uses Sev 1–3 instead of the defaults. The fields in `fields` follow it in the dialog.

```python
from pyrit.models import FindingField, FindingFields, FindingFieldType, FindingSeverityColor
from pyrit.setup.initializers import FindingsInitializer


class ContosoFindingsInitializer(FindingsInitializer):
    """Finding fields for the Contoso red team."""

    def get_finding_fields(self) -> FindingFields:
        return FindingFields(
            severity=FindingField(
                key="severity",
                label="Severity",
                type=FindingFieldType.CHOICE,
                options=[("sev1", "Sev 1"), ("sev2", "Sev 2"), ("sev3", "Sev 3")],
                default="sev2",
                colors={"sev1": FindingSeverityColor.RED, "sev2": FindingSeverityColor.ORANGE},
            ),
            fields=[
                FindingField(
                    key="harm_category",
                    label="Harm category",
                    type=FindingFieldType.CHOICE,
                    required=True,
                    options={
                        ("content", "Content safety"): [("hate", "Hate"), ("violence", "Violence")],
                        ("security", "Security"): [("prompt_injection", "Prompt injection")],
                    },
                    levels=[("subcategory", "Subcategory")],
                ),
                FindingField(key="repro_steps", label="Repro steps", type=FindingFieldType.MULTILINE),
                FindingField(key="cvss_score", label="CVSS score", type=FindingFieldType.NUMBER),
                FindingField(key="contoso_ticket", label="Contoso ticket", type=FindingFieldType.TEXT),
            ],
        )
```

A few things to note in this example:

- Options are `(key, label)` pairs. The finding stores the `key`, and people see the `label`. Field and level keys use lowercase letters, digits and underscores, up to 256 characters each, and the fields and their levels can use at most 100 keys besides `severity`. Option keys can be any text that isn't blank and has no surrounding whitespace.
- `Harm category` is a cascade. Each of its options maps to the options of a second dropdown. `levels` names that dropdown, and its value is stored under `subcategory`.
- The severity options are listed from highest to lowest. Findings sort in that order on the operation page, in exports and in an attack's findings.
- `CVSS score` is a `number` field. It takes a plain decimal, such as `9.8` or `-2`, without exponents.
- `colors` sets the badge color of a severity option.

`FindingField` also takes `hint`, `section` and `allow_other`. `section` puts a heading, such as "Reproduction", above a field whose section differs from the field before it. The `FindingField` docstring describes each setting.

Save the script as `contoso_findings.py` in your custom initializers source. The file name is the initializer's name. Then turn it on in `.pyrit_conf`:

```yaml
allow_custom_initializers: true
custom_initializers_source: /srv/contoso/pyrit_initializers
initializers:
  - name: target
  - name: technique
  - name: contoso_findings
```

With `allow_custom_initializers: true`, the backend runs every script in that source. Only trusted administrators should be able to write to it. See [the configuration file guide](../../getting_started/pyrit_conf.md) for details.

## Rules

- Use one `FindingsInitializer` per deployment. If the configuration lists two, or the fields are invalid, setup fails when the backend starts. The backend still runs, but it blocks runtime operations, including findings, until a process restart. Configuration repair stays available. **Reinitialize PyRIT** reports invalid configuration instead, and the running fields don't change. See [Reinitializing a running backend](0_setup.md#reinitializing-a-running-backend).
- `get_finding_fields` should not depend on environment variables. **Reinitialize PyRIT** checks the fields before it applies the new environment, so it would check fields built from the old values.
- Other text that matches an option's key or label is rejected, ignoring case and surrounding spaces. The message names the option to choose instead.
- On the Operations list, severities that match no option share one **Other** count.
- Exports put multi-line fields in the Finding column, and choice, text and number fields in the Assessment column. Retired values follow in the Finding column, labelled by their keys.

## Changing the Fields

A deployment has one set of finding fields, and every operation uses it, including operations from before a change. Changing the initializer changes how findings show in all of them:

- A new `label` renames what people see. Stored values don't change.
- A new `key` makes a new field. Values saved under the old key show under **Retired fields**, where they are read-only. Edits keep them, **Remove** deletes one when you save, and exports include them.
- A removed option stays on the findings that use it, shown as stored. Edits keep it until someone picks another value.
- A field changed to `number` keeps older values that aren't numbers until someone changes them.
- A newly required field must be filled the next time someone edits an older finding.
- Reordering the severity options re-sorts the findings in every operation. Findings whose severity is no longer an option sort after the rest.

## Upgrading

One database migration moves findings saved before finding fields existed. It stores Other severity text as the `severity` itself, and moves the harm type and description into field values. Finding IDs and evidence don't change. Downgrading that migration moves those values back: a severity or harm type that PyRIT 1.2 doesn't list becomes Other with its text. It refuses, without changing anything, while any finding has a value under another key, and names those keys so you can remove the values first.

### Code written for PyRIT 1.2

PyRIT 1.2 had fixed finding fields. Code that uses them still works, with a deprecation warning, until PyRIT 1.5.0:

| PyRIT 1.2 | Use instead |
|---|---|
| `FindingSeverity` | The option keys of `FindingFields.severity`, such as `"critical"` |
| `description` | `field_values["description"]` |
| `harm_type` and `harm_type_other` | `field_values["harm_type"]`, which holds the category or its Other text |
| `severity_other` | `severity`, which holds Other text itself |
| `finding.severity.value` | `finding.severity` |
| `GET /api/operations/finding-options` | `GET /api/operations/{operation_id}/finding-fields` |

Creating or updating a finding with the old fields moves their values the way the migration does, so they fill the default fields' keys. A deployment whose fields don't use those keys rejects them. API responses still include the old fields, marked deprecated in the API schema.

Two checks can't keep working. A severity with Other text is that text, so it no longer equals `FindingSeverity.OTHER`. A severity is now a string, so it isn't an instance of `FindingSeverity`.
