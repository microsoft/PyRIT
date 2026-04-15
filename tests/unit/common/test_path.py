# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
from unittest.mock import patch

from pyrit.common.path import (
    CONFIGURATION_DIRECTORY_PATH,
    DATASETS_PATH,
    DB_DATA_PATH,
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_CONFIG_PATH,
    DOCS_CODE_PATH,
    DOCS_PATH,
    HOME_PATH,
    LOG_PATH,
    PATHS_DICT,
    PYRIT_PATH,
    get_default_data_path,
    in_git_repo,
)


def test_pyrit_path_is_absolute():
    assert PYRIT_PATH.is_absolute()


def test_home_path_is_parent_of_pyrit_path():
    assert PYRIT_PATH.parent == HOME_PATH


def test_docs_path_relative_to_home():
    assert (HOME_PATH / "doc").resolve() == DOCS_PATH


def test_docs_code_path_relative_to_home():
    assert (HOME_PATH / "doc" / "code").resolve() == DOCS_CODE_PATH


def test_datasets_path_inside_pyrit():
    assert (PYRIT_PATH / "datasets").resolve() == DATASETS_PATH


def test_configuration_directory_is_in_home():
    assert pathlib.Path.home() / ".pyrit" == CONFIGURATION_DIRECTORY_PATH


def test_default_config_filename():
    assert DEFAULT_CONFIG_FILENAME == ".pyrit_conf"


def test_default_config_path():
    assert DEFAULT_CONFIG_PATH == CONFIGURATION_DIRECTORY_PATH / DEFAULT_CONFIG_FILENAME


def test_db_data_path_is_absolute():
    assert DB_DATA_PATH.is_absolute()


def test_log_path_is_absolute():
    assert LOG_PATH.is_absolute()


def test_paths_dict_contains_expected_keys():
    expected = {"pyrit_path", "datasets_path", "db_data_path", "log_path", "docs_path"}
    assert expected.issubset(set(PATHS_DICT.keys()))


def test_in_git_repo_returns_bool():
    result = in_git_repo()
    assert isinstance(result, bool)


def test_get_default_data_path_in_git_repo():
    with patch("pyrit.common.path.in_git_repo", return_value=True):
        result = get_default_data_path("testdir")
    assert result == pathlib.Path(PYRIT_PATH, "..", "testdir").resolve()


def test_get_default_data_path_not_in_git_repo():
    with patch("pyrit.common.path.in_git_repo", return_value=False):
        result = get_default_data_path("testdir")
    assert "testdir" in str(result)
    assert result.is_absolute()
