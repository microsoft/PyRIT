# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib

import nbformat
import pytest
from nbconvert.preprocessors import ExecutePreprocessor

from pyrit.common import path

nb_directory_path = pathlib.Path(path.DOCS_CODE_PATH, "targets").resolve()

skipped_files = [
    "4_openai_video_target.ipynb",  # requires OpenAI video API key
    "8_non_llm_targets.ipynb",  # requires Azure Blob Storage data-plane credentials
    "10_1_playwright_target.ipynb",  # Playwright installation takes too long
    "10_2_playwright_target_copilot.ipynb",  # Playwright installation takes too long, plus requires M365 account
    "10_3_websocket_copilot_target.ipynb",  # WebSocket Copilot target requires manual pasting tokens
]

_AZURE_KEY_AUTH_DISABLED_REASON = "Azure key-based (local) auth is disabled in our tenant."

# Notebooks whose targets use Azure key-based (local) auth, which is disabled in our tenant.
_azure_key_auth_notebooks = {
    "10_http_target.ipynb",
    "5_openai_tts_target.ipynb",
    "6_custom_targets.ipynb",
    "9_rate_limiting.ipynb",
    "round_robin_target.ipynb",
}


def _notebook_params():
    params = []
    for file in os.listdir(nb_directory_path):
        if not file.endswith(".ipynb") or file in skipped_files:
            continue
        if file in _azure_key_auth_notebooks:
            params.append(pytest.param(file, marks=pytest.mark.skip(reason=_AZURE_KEY_AUTH_DISABLED_REASON)))
        else:
            params.append(file)
    return params


@pytest.mark.parametrize("file_name", _notebook_params())
def test_execute_notebooks(file_name):
    nb_path = pathlib.Path(nb_directory_path, file_name).resolve()
    with open(nb_path, encoding="utf-8") as f:
        nb = nbformat.read(f, as_version=4)

    ep = ExecutePreprocessor(timeout=600)

    # Execute notebook, test will throw exception if any cell fails
    ep.preprocess(nb, {"metadata": {"path": nb_path.parent}})
