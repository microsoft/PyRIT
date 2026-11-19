# Finding Fields

A finding in CoPyRIT has a title, a severity and a set of fields. Each deployment chooses those fields. The finding dialog, the finding cards and the exports all follow them.

## Default Fields

Without any configuration, findings use these fields:

- **Severity**: Critical, Important, Moderate, Low or Informational. People can also choose **Other** and type their own text. The dialog preselects Moderate.
- **Harm-type**: optional. Its list is searchable, and it also takes **Other** text.
- **Description**: optional multi-line text.

## Defining Your Own Fields

To use other fields, subclass `FindingsInitializer` and return a `FindingFields` from `get_finding_fields`. The `severity` slot is always a required choice field. The fields in `fields` follow it in the dialog.

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
                    key="area",
                    label="Area",
                    type=FindingFieldType.CHOICE,
                    required=True,
                    options={
                        ("content", "Content safety"): [("hate", "Hate"), ("violence", "Violence")],
                        ("security", "Security"): [("prompt_injection", "Prompt injection")],
                    },
                    levels=[("subarea", "Subarea")],
                ),
                FindingField(key="repro_steps", label="Repro steps", type=FindingFieldType.MULTILINE),
                FindingField(key="contoso_ticket", label="Contoso ticket", type=FindingFieldType.TEXT),
            ],
        )
```

A few things to note in this example:

- Options are `(key, label)` pairs. The finding stores the `key`, and people see the `label`. Field and level keys use lowercase letters, digits and underscores, up to 256 characters each, and the fields and their levels can use at most 100 keys besides `severity`. Option keys can be any text that isn't blank and has no surrounding whitespace.
- `Area` is a cascade. Each of its options maps to the options of a second dropdown. `levels` names that dropdown, and its value is stored under `subarea`.
- `colors` sets the badge color of a severity option.

`FindingField` also takes `hint`, `section` and `allow_other`. Its docstring describes each setting.

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

- Use one `FindingsInitializer` per deployment. If the configuration lists two, or the fields are invalid, setup fails when the backend starts. The backend still runs, but it blocks runtime operations, including findings, until a process restart. Configuration repair stays available. A live apply reports invalid configuration instead, and the running fields don't change.
- Don't make `get_finding_fields` depend on environment variables. A live apply checks the fields before it applies the new environment.
- Change a field's `label` to rename what people see.
- A changed `key` makes a new field. Values saved under the old key show under **Retired fields**, where they are read-only. Edits keep them. **Remove** deletes one when you save.
- Other text that matches an option's key or label is rejected, ignoring case and surrounding spaces. The message names the option to choose instead.
- On the Operations list, severities that match no option share one **Other** count.
- Exports put multi-line fields in the Finding column, and choice and text fields in the Assessment column. Retired values follow in the Finding column, labelled by their keys.

## Upgrading

One database migration moves findings saved before finding fields existed. It stores Other severity text as the `severity` itself, and moves the harm type and description into field values. Finding IDs and evidence don't change. Downgrading that migration refuses while any finding exists.

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
