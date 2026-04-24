# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib

from appdirs import user_data_dir


def get_default_data_path(dir: str) -> pathlib.Path:  # noqa: A002
    """
    Retrieve the default data path for PyRIT.

    Returns:
        pathlib.Path: The resolved absolute path to the data directory.
    """
    if in_git_repo():
        return pathlib.Path(PYRIT_PATH, "..", dir).resolve()
    return pathlib.Path(user_data_dir(dir, "pyrit")).resolve()


def in_git_repo() -> bool:
    """
    Check if the current PyRIT installation is running from a git repository.

    Returns:
        bool: True if in a git repository, False otherwise.
    """
    return pathlib.Path(HOME_PATH, ".git").exists()


PYRIT_PATH = pathlib.Path(__file__, "..", "..").resolve()

CONFIGURATION_DIRECTORY_PATH = pathlib.Path.home() / ".pyrit"

# Default configuration file name and path
DEFAULT_CONFIG_FILENAME = ".pyrit_conf"
DEFAULT_CONFIG_PATH = CONFIGURATION_DIRECTORY_PATH / DEFAULT_CONFIG_FILENAME

# Points to the root of the project
HOME_PATH = pathlib.Path(PYRIT_PATH, "..").resolve()

DOCS_PATH = pathlib.Path(PYRIT_PATH, "..", "doc").resolve()
DOCS_CODE_PATH = pathlib.Path(PYRIT_PATH, "..", "doc", "code").resolve()

# Path to where all the seed prompt entry and prompt memory entry files and database file will be stored
DB_DATA_PATH = get_default_data_path("dbdata")
DB_DATA_PATH.mkdir(parents=True, exist_ok=True)

# Path to where cache files are stored, i.e. token cache, etc.
PYRIT_CACHE_PATH = get_default_data_path(".pyrit_cache")
PYRIT_CACHE_PATH.mkdir(parents=True, exist_ok=True)

# Path to where the logs are located
LOG_PATH = pathlib.Path(DB_DATA_PATH, "logs.txt").resolve()
LOG_PATH.touch(exist_ok=True)

DATASETS_PATH = pathlib.Path(PYRIT_PATH, "datasets").resolve()

EXECUTOR_SEED_PROMPT_PATH = pathlib.Path(DATASETS_PATH, "executors").resolve()
EXECUTOR_RED_TEAM_PATH = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH, "red_teaming").resolve()
EXECUTOR_SIMULATED_TARGET_PATH = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH, "simulated_target").resolve()
CONVERTER_SEED_PROMPT_PATH = pathlib.Path(DATASETS_PATH, "prompt_converters").resolve()
SCORER_SEED_PROMPT_PATH = pathlib.Path(DATASETS_PATH, "score").resolve()
SCORER_CONTENT_CLASSIFIERS_PATH = pathlib.Path(SCORER_SEED_PROMPT_PATH, "content_classifiers").resolve()
SCORER_LIKERT_PATH = pathlib.Path(SCORER_SEED_PROMPT_PATH, "likert").resolve()
SCORER_SCALES_PATH = pathlib.Path(SCORER_SEED_PROMPT_PATH, "scales").resolve()
HARM_DEFINITION_PATH = pathlib.Path(DATASETS_PATH, "harm_definition").resolve()

JAILBREAK_TEMPLATES_PATH = pathlib.Path(DATASETS_PATH, "jailbreak", "templates").resolve()

SCORER_EVALS_PATH = pathlib.Path(DATASETS_PATH, "scorer_evals").resolve()
SCORER_EVALS_HARM_PATH = pathlib.Path(SCORER_EVALS_PATH, "harm").resolve()
SCORER_EVALS_OBJECTIVE_PATH = pathlib.Path(SCORER_EVALS_PATH, "objective").resolve()
SCORER_EVALS_REFUSAL_SCORER_PATH = pathlib.Path(SCORER_EVALS_PATH, "refusal_scorer").resolve()
SCORER_EVALS_TRUE_FALSE_PATH = pathlib.Path(SCORER_EVALS_PATH, "true_false").resolve()
SCORER_EVALS_LIKERT_PATH = pathlib.Path(SCORER_EVALS_PATH, "likert").resolve()


# Dictionary of default PyRIT paths used primarily for rendering jinja templates
PATHS_DICT = {
    "content_classifiers_path": SCORER_CONTENT_CLASSIFIERS_PATH,
    "datasets_path": DATASETS_PATH,
    "db_data_path": DB_DATA_PATH,
    "docs_code_path": DOCS_CODE_PATH,
    "docs_path": DOCS_PATH,
    "harm_definition_path": HARM_DEFINITION_PATH,
    "jailbreak_templates_path": JAILBREAK_TEMPLATES_PATH,
    "likert_path": SCORER_LIKERT_PATH,
    "log_path": LOG_PATH,
    "pyrit_home_path": HOME_PATH,
    "pyrit_path": PYRIT_PATH,
    "red_team_executor_path": EXECUTOR_RED_TEAM_PATH,
    "scales_path": SCORER_SCALES_PATH,
    "scorer_evals_path": SCORER_EVALS_PATH,
    "scorer_evals_harm_path": SCORER_EVALS_HARM_PATH,
    "scorer_evals_objective_path": SCORER_EVALS_OBJECTIVE_PATH,
    "simulated_target_path": EXECUTOR_SIMULATED_TARGET_PATH,
}
