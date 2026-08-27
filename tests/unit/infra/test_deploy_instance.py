# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for CoPyRIT deployment authentication configuration."""

import json
from unittest.mock import patch

from infra import deploy_instance


def test_post_deploy_enables_spa_and_device_code_authentication() -> None:
    with patch.object(deploy_instance, "run_az") as run_az:
        deploy_instance.post_deploy(
            app_object_id="app-object-id",
            fqdn="copyrit.example.com",
        )

    args = run_az.call_args.kwargs["args"]
    body = json.loads(args[args.index("--body") + 1])
    assert body == {
        "spa": {"redirectUris": ["https://copyrit.example.com"]},
        "isFallbackPublicClient": True,
    }
