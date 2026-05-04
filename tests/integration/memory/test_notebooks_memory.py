# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib

import nbformat
import pytest
from nbconvert.preprocessors import ExecutePreprocessor

from pyrit.common import path

nb_directory_path = pathlib.Path(path.DOCS_CODE_PATH, "memory").resolve()

skipped_files = [
    "6_azure_sql_memory.ipynb",  # todo: requires Azure SQL setup, remove following completion of #4001
    "7_azure_sql_memory_attacks.ipynb",  # todo: remove following completion of #4001
    "embeddings.ipynb",  # requires OpenAI embedding API key
]


@pytest.mark.parametrize(
    "file_name",
    [file for file in os.listdir(nb_directory_path) if file.endswith(".ipynb") and file not in skipped_files],
)
def test_execute_notebooks(file_name):
    nb_path = pathlib.Path(nb_directory_path, file_name).resolve()
    with open(nb_path, encoding="utf-8") as f:
        nb = nbformat.read(f, as_version=4)

    ep = ExecutePreprocessor(timeout=600)

    # Execute notebook, test will throw exception if any cell fails
    ep.preprocess(nb, {"metadata": {"path": nb_path.parent}})
