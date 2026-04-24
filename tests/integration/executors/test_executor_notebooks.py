# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib

import nbformat
import pytest
from nbconvert.preprocessors import ExecutePreprocessor

from pyrit.common import path

nb_directory_path = pathlib.Path(path.DOCS_CODE_PATH, "executor").resolve()
folder_paths = [d for d in nb_directory_path.iterdir() if d.is_dir()]

skipped_files = [
    "1_xpia_website.ipynb",  # requires publicly accessible Azure Storage Account
    "2_xpia_ai_recruiter.ipynb",  # requires AI recruiter service running locally
]


@pytest.mark.parametrize(
    "file_path",
    [
        os.path.join(dir_path, file)
        for dir_path in folder_paths
        for file in os.listdir(dir_path)
        if file.endswith(".ipynb") and file not in skipped_files
    ],
)
def test_execute_notebooks(file_path):
    nb_path = pathlib.Path(file_path).resolve()
    print(nb_path)
    with open(nb_path, encoding="utf-8") as f:
        nb = nbformat.read(f, as_version=4)

    ep = ExecutePreprocessor(timeout=900)

    # Execute notebook, test will throw exception if any cell fails
    ep.preprocess(nb, {"metadata": {"path": nb_path.parent}})
