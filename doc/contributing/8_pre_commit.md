# 8. Pre-Commit Hooks

There are a number of pre-commit hooks available to run on files within the repo. Run these once you have code that you'd like to submit in a pull request to make sure they pass. These are meant to enforce style within the code base.

```bash
### Make sure all files are added with `git add` before running pre-commit

# run hooks on all files
uv run pre-commit run --all-files

# run hooks on a specific file
uv run pre-commit run --files <file_name>

```
Note: if you did not install PyRIT using uv, then omit uv run from the above commands

## Hook Environments

The dev dependencies include [pre-commit-uv](https://github.com/tox-dev/pre-commit-uv), so after `uv sync`, `uv run pre-commit` creates the Python hook environments with `uv venv` and installs the hooks with `uv pip install` instead of virtualenv and pip. It uses the uv from the dev dependencies, at the version selected in `uv.lock`. Project CI jobs read that version directly with setup-uv's `version-file: uv.lock`, so updating uv in the lockfile does not require matching version edits in those workflows. The versioned documentation pipeline keeps an independent bootstrap version because it also builds older releases whose lockfiles do not contain uv. Under `uv run` or in an activated virtual environment, the locked uv comes before your machine-wide uv on PATH, so it is the `uv` you run there and the one the `ty-check` hook calls. Hook versions and hook selection still come from `.pre-commit-config.yaml`.

To check that pre-commit-uv is active, run `uv run pre-commit --version`. The output should end with the pre-commit-uv and uv versions, such as `(pre-commit-uv=4.3.0, uv=0.9.17)`.

pre-commit-uv does not reuse hook environments that were created with virtualenv and pip. It rebuilds them on the next run, which needs network access. To rebuild them ahead of time, run this once while online:

```bash
uv run pre-commit install-hooks
```

Setting `DISABLE_PRE_COMMIT_UV_PATCH=1` turns pre-commit-uv off. pre-commit then rebuilds the hook environments with pip, and the next run without it rebuilds them with uv.

### Running the Hooks Offline

Preparation needs network access and is a separate step from the run. It installs the project environment, which the `ty-check` hook runs in, then clones the hook repositories and creates every hook environment:

```bash
uv sync --extra all
uv run pre-commit install-hooks
```

Then run the hooks without network access:

```bash
UV_OFFLINE=1 GIT_ALLOW_PROTOCOL=file uv run pre-commit run --all-files
```

`UV_OFFLINE=1` makes uv use only its cache, so a package that was not prepared fails the run instead of being downloaded. It does not cover git: if a hook repository was not prepared, pre-commit tries to clone it, and `GIT_ALLOW_PROTOCOL=file` makes that clone fail instead of reaching the network. In PowerShell, set `$env:UV_OFFLINE = "1"` and `$env:GIT_ALLOW_PROTOCOL = "file"` before the command instead, and clear them afterwards with `Remove-Item Env:UV_OFFLINE, Env:GIT_ALLOW_PROTOCOL`, since they stay set for the rest of the session.

The environments are stored in the pre-commit cache (`~/.cache/pre-commit`, or `PRE_COMMIT_HOME` if set) and their packages in the uv cache. To prepare from cold caches, point `PRE_COMMIT_HOME` and `UV_CACHE_DIR` at empty directories and keep the same values for the run.
