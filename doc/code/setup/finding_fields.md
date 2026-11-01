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
                FindingField(key="contoso_ticket", label="Contoso ticket", type=FindingFieldType.TEXT, internal=True),
            ],
        )
```

A few things to note in this example:

- Options are `(key, label)` pairs. The finding stores the `key`, and people see the `label`. Field and level keys use lowercase letters, digits and underscores. Option keys can be any text that isn't blank.
- `Area` is a cascade. Each of its options maps to the options of a second dropdown. `levels` names that dropdown, and its value is stored under `subarea`.
- `colors` sets the badge color of a severity option.
- `internal=True` keeps `Contoso ticket` out of the Word and Markdown exports. The dashboard still shows it.

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
- Exports put multi-line fields in the Finding column, and choice and text fields in the Assessment column. They leave out internal fields and retired values. The export dialog warns when selected findings have retired values.

## Upgrading

One database migration moves findings saved before finding fields existed. It stores Other severity text as the `severity` itself, and moves the harm type and description into field values. Finding IDs and evidence don't change. Downgrading that migration refuses while any finding exists.
