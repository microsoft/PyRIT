# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Guard notebook identifiers consumed by MyST's project-wide reference index."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_notebook_cleanup_preserves_ids_and_outputs() -> None:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "nbstripout")
    assert "--keep-id" in hook["args"]
    assert "--keep-output" in hook["args"]
