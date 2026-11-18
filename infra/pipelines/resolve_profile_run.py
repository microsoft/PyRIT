# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pin one successful protected-main build in the current Azure DevOps project."""

import json
import os
import re
from http.client import HTTPSConnection
from urllib.parse import urlencode, urlsplit
from uuid import UUID


def resolve_run(*, collection: str, project: str, definition: str, selector: str, token: str) -> tuple[int, str]:
    """Resolve and verify build metadata before allowing the artifact download."""
    project = str(UUID(project))
    origin = urlsplit(collection)
    if not (
        origin.scheme == "https"
        and not origin.query
        and not origin.fragment
        and (
            (origin.netloc == "dev.azure.com" and re.fullmatch(r"/[a-zA-Z0-9-]+/?", origin.path))
            or (re.fullmatch(r"[a-zA-Z0-9-]+\.visualstudio\.com", origin.netloc) and origin.path in ("", "/"))
        )
    ):
        raise ValueError("Expected the team's Azure DevOps Services collection URI.")
    if not re.fullmatch(r"[1-9][0-9]*", definition) or not token or token.startswith("$("):
        raise ValueError("A private CI definition and project-scoped job token are required.")
    path = f"{origin.path.rstrip('/')}/{project}/_apis/build/builds"
    query = {"api-version": "7.1"}
    if selector == "latest":
        query.update(
            definitions=definition,
            branchName="refs/heads/main",
            statusFilter="completed",
            resultFilter="succeeded",
            queryOrder="finishTimeDescending",
            **{"$top": "1"},
        )
    elif re.fullmatch(r"[1-9][0-9]*", selector):
        path += f"/{selector}"
    else:
        raise ValueError("profileRun must be latest or a positive decimal CI run ID.")

    # Send the token only to this origin. Never follow redirects or print the response body.
    connection = HTTPSConnection(origin.netloc, timeout=30)
    try:
        connection.request("GET", f"{path}?{urlencode(query)}", headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Private CI build lookup failed (HTTP {response.status}).")
        build = json.load(response)
    finally:
        connection.close()

    if selector == "latest":
        builds = build.get("value")
        if not isinstance(builds, list) or len(builds) != 1:
            raise ValueError("No successful protected-main profile run found.")
        build = builds[0]
    run, commit = build["id"], build["sourceVersion"]
    if not (
        type(run) is int
        and run > 0
        and (selector == "latest" or str(run) == selector)
        and build["project"]["id"].lower() == project
        and build["definition"]["id"] == int(definition)
        and build["sourceBranch"] == "refs/heads/main"
        and build["reason"] != "pullRequest"
        and build["status"] == "completed"
        and build["result"] == "succeeded"
        and isinstance(commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", commit)
    ):
        raise ValueError("Private CI build is not a successful protected-main run of the expected definition.")
    return run, commit


if __name__ == "__main__":
    run, commit = resolve_run(
        collection=os.environ["SYSTEM_COLLECTIONURI"],
        project=os.environ["SYSTEM_TEAMPROJECTID"],
        definition=os.environ["PROFILE_DEFINITION"],
        selector=os.environ["PROFILE_RUN"],
        token=os.environ["SYSTEM_ACCESSTOKEN"],
    )
    print(f"Selected private CI run {run} at {commit}")
    print(f"##vso[task.setvariable variable=runId;isOutput=true]{run}")
    print(f"##vso[task.setvariable variable=commit;isOutput=true]{commit}")
