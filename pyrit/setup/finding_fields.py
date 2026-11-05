# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Hold the finding fields that the current setup pass configured."""

from pyrit.models import DEFAULT_FINDING_FIELDS, FindingFields

_active: tuple[FindingFields, str] | None = None


def get_active_finding_fields() -> FindingFields:
    """
    Return the finding fields of the current setup pass.

    Returns:
        FindingFields: The fields set in this pass, or ``DEFAULT_FINDING_FIELDS`` when none were set.
    """
    return _active[0] if _active is not None else DEFAULT_FINDING_FIELDS


def set_active_finding_fields(*, finding_fields: FindingFields, source: str) -> None:
    """
    Set the finding fields for the current setup pass.

    Args:
        finding_fields (FindingFields): The deployment's finding fields.
        source (str): The setter's name, which the error names when two setters run in one pass.

    Raises:
        TypeError: If ``finding_fields`` is not a ``FindingFields``.
        ValueError: If finding fields were already set in this setup pass.
    """
    global _active
    if not isinstance(finding_fields, FindingFields):
        raise TypeError(f"{source} must set FindingFields, not {type(finding_fields).__name__}.")
    if _active is not None:
        raise ValueError(
            f"{source} cannot set the finding fields: {_active[1]} already set them in this setup pass. "
            "Use one FindingsInitializer per deployment."
        )
    _active = (finding_fields, source)


def reset_active_finding_fields() -> None:
    """Restore the default finding fields at the start of a setup pass."""
    global _active
    _active = None
