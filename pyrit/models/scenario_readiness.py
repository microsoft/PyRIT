# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration readiness shared by scenario entry points."""

from pydantic import BaseModel, Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier

SCENARIO_ROLE_REFERENCES_METADATA_KEY = "scenario_role_references_v1"


class ScenarioConfigurationIssue(BaseModel):
    """An actionable configuration problem, without connection credentials."""

    role: str
    component: str
    reason: str
    remediation: str


class ScenarioRoleBinding(BaseModel):
    """An effective execution dependency."""

    role: str
    component: str
    identifier: ComponentIdentifier


class ScenarioReadiness(BaseModel):
    """Resolved dependencies and all discoverable configuration issues."""

    bindings: list[ScenarioRoleBinding] = Field(default_factory=list)
    issues: list[ScenarioConfigurationIssue] = Field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Whether every required role is configured."""
        return not self.issues


class ScenarioConfigurationError(ValueError):
    """A scan cannot be admitted with unresolved execution roles."""

    def __init__(self, *, issues: list[ScenarioConfigurationIssue]) -> None:
        """Retain all role-specific issues for callers and diagnostics."""
        self.issues = issues
        super().__init__(
            "\n".join(f"{issue.role} ({issue.component}): {issue.reason} {issue.remediation}" for issue in issues)
        )
