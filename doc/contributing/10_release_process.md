# 10. Releasing PyRIT

This section is for maintainers only.
If you don't know who the maintainers are but you need to reach them
please file an issue or (if it needs to remain private) contact the
email address listed in pyproject.toml

Follow the instructions according to the order provided.

## 1. Release Readiness

Before starting the release process, verify the codebase is in a healthy state.

- **Check for pending changes.** Ask other PyRIT maintainers whether they have any in-flight changes that should land before the release.
- **Verify build pipelines.** Confirm that all integration tests and end-to-end tests are passing in the CI pipelines. If any tests are failing, fix them before proceeding.
  - **Partner integration tests.** Ensure the partner integration tests are also passing. These tests validate that we are not breaking contracts with partner teams (e.g., Foundry). If any are failing, coordinate with the affected partner teams before proceeding with the release.
- **Update scorer metrics.** Run `python .\build_scripts\evaluate_scorers.py` and commit the results so that scorer evaluation metrics are up to date.

## 2. Decide the Next Version

First, decide what the next release version is going to be.
We follow semantic versioning for Python projects; see
https://semver.org/ for more details.
Below, we refer to the version as `x.y.z`.
`x` is the major version, `y` the minor version, and `z` the patch version.
Every Python project starts at `0.1.0`.
Backwards compatible bug fixes increase the patch version.
Importantly, they are backward compatible, so upgrading from `0.1.0` to
`0.1.1` (or higher ones like `0.1.38`) should not break your code.
More significant changes, such as major features, require at least a new
minor version.
They should still be backwards compatible, so if you're upgrading from
`1.1.0` to `1.2.0` your code shouldn't break.
The major version `1.0.0` is the first "stable" release.
Anything before (i.e., leading with major version `0`) indicates that it is
not stable and anything may change at any time.
For that reason, the minor version may indicate breaking changes, too,
at least until we hit major version `1`.

We will always have a .dev version in main, but there are circumstances when
we might want to release versions that aren't final; consult
https://packaging.python.org/en/latest/discussions/versioning/ to determine whether
there should be any postfixes on the release version.

With that in mind, the reason for the release and the set of changes
that happened since the last release will influence the new version number.

## 3. Remove deprecated functionality

If you are incrementing the minor version, search the entire codebase for the new minor
version, e.g., "0.11.0" (no leading "v"),
to find occurrences where we deprecated functionality and announced that it will be
removed in the new version. Typically, functionality is deprecated and then stays for
two minor versions before getting removed.

If you find functionality to remove make sure to merge the PR to `main` before proceeding.

In some cases, we know that the next version will not be a patch upgrade (e.g.,
0.10.0 to 0.10.1) but rather a minor upgrade (0.10.3 to 0.11.0) and we can remove
functionality that is to be deprecated beforehand.
This is preferable but it may not be clear if a release is patch or minor until the full
scope of the release is known (see step 2).

## 4. Update the version

### __init__.py, pyproject.toml, and package.json

Set the version in `pyrit/__init__.py`, `pyproject.toml`, and `package.json` to the version
established in step 2.

### Update README File

The README file is published to PyPI and also needs to be updated so the
links work properly. Note: There may not be any links to update, but it is
good practice to check in case our README changes.

Replace all "main" links like
"https://github.com/microsoft/PyRIT/blob/main/doc/index.md" with "raw" links that have
the correct version number, i.e.,
"https://raw.githubusercontent.com/microsoft/PyRIT/releases/vx.y.z/doc/index.md".

For images, update using the "raw" link, e.g.,
"https://raw.githubusercontent.com/microsoft/PyRIT/releases/vx.y.z/assets/pyrit_architecture.png".

For directories, update using the "tree" link, e.g.,
"https://github.com/microsoft/PyRIT/tree/releases/vx.y.z/doc/code"

This is required for the release branch because PyPI does not pick up
other files besides the README, which results in local links breaking.

## 5. Publish to GitHub

Commit your changes and push them to the repository on a branch called
`releases/vx.y.z`, then run

```bash
git checkout -b "releases/vx.y.z"
git commit -m "release vx.y.z"
git push origin releases/vx.y.z
git tag -a vx.y.z -m "vx.y.z release"
git push --tags
```

After pushing the branch to remote, check the release branch to make sure it looks as intended (e.g. check the links in the README work properly).

## 6. Build Package

You'll need the build package to build the project. If it’s not already installed, install it `pip install build`.

### Build the Frontend

The PyRIT package includes a web-based frontend that must be built before packaging. This requires Node.js and npm to be installed.

Run the prepare script to build the frontend and copy it into the package structure:

```bash
python build_scripts/prepare_package.py
```

This will:

1. Run `npm install` and `npm run build` in the `frontend/` directory
2. Copy the built assets from `frontend/dist/` to `pyrit/backend/frontend/`. Double check to make sure the files exist after running the `prepare_package.py` script. This should at least include index.html, an `assets` folder with `js` and `css` files.

### Build the Python Package

To build the package wheel and archive for PyPI run

```bash
python -m build
```

This should print

> Successfully built pyrit-x.y.z.tar.gz and pyrit-x.y.z-py3-none-any.whl

## 7. Test Built Package

This step is crucial to ensure that the new package works out of the box.

Create a new environment with the equivalent of `uv venv --python 3.11`. You do not need to test with multiple versions of python or environments, but this manual process can detect issues with the package. Install the built wheel file `uv pip install dist/pyrit-x.y.z-py3-none-any.whl[all]`.

Once the package is successfully installed in the new environment, run `uv pip show pyrit`. Ensure that the version matches the release `vx.y.z` and that the package is found under the site-packages directory of the environment, like `..\venv\Lib\site-packages`.

Make sure to set up the Jupyter kernel as described in our [Jupyter setup](../getting_started/troubleshooting/jupyter_setup.md) guide.

To test the demos outside the PyRIT repository, copy the `doc`, `assets`, and `.env` files to a new folder created outside the PyRIT directory. For better organization, you could create a main folder called `releases` and a subfolder named `releasevx.y.z`, and then place the copied folders within this structure.

Before running the demos, execute `az login` or `az login --use-device-code`, as some demos require Azure authentication and use delegation SAS.

Additionally, verify that your environment file includes all the test secrets needed to run the demos. If not, update your .env file using the secrets from the key vault.

In the new location, run all notebooks that are currently skipped by integration tests (there are less than 10) in VS Code. These are listed in `skipped_files` in each `tests/integration/<folder>/test_notebooks_*.py` file and are located in the doc folder that you copied into your new `releases\releasevx.y.z` folder. Note that some of these notebooks have known issues and it may make sense to skip testing them until those are fixed. Check with the last person to deploy or look for the relevant release work item for more information. In running the notebooks, you may also see exceptions. If this happens, make sure to look for existing bugs open on the ADO board or create a new one if it does not exist! If it is easy to fix, we prefer to fix the issue before the release continues.

A reminder that you should ensure that the integration tests pass in the version you are releasing in addition to the skipped files.

Note: copying the `doc` folder elsewhere is essential since we store data files
in the repository that should be shipped with the package.
If we run inside the repository, we may not face errors that users encounter
with a clean installation and no locally cloned repository.

If at any point you need to make changes to fix bugs discovered while testing, or there is another change to include with the release, follow the steps below after the item has been merged into `main`.

```bash
git checkout main
git fetch main
git log main # to identify the commit hash of the change you want to cherry-pick
git checkout releases/vx.y.z
git cherry-pick <commit-hash>
git push origin releases/vx.y.z
git tag -a vx.y.z -m "vx.y.z release" --force # to update the tag to the correct commit
```

Note: You may need to build the package again if those changes modify any dependencies, and consider retesting the notebooks if the changes affect them. If you reuse the same environment, it is best to `uv pip uninstall pyrit` to force the reinstall.

Lastly, **Verify pyrit-internal is up to date.** Follow the instructions at [aka.ms/internal-release](https://aka.ms/internal-release) to ensure the internal package is current.


## 8. Publish to PyPI

Create an account on pypi.org if you don't have one yet.
Ask one of the other maintainers to add you to the `pyrit` project on PyPI.

Note: Before publishing to PyPI, have your API token for scope 'Project: pyrit' handy. You can create one by going to the Settings in the pyrit project and "Create a token for pyrit" under API tokens. This token will be used to publish the release.

```bash
uv pip install twine
twine upload dist/* # this should be the same as dist/pyrit-x.y.z-py3-none-any.whl
```

If successful, it will print

> View at:
> https://pypi.org/project/pyrit/x.y.z/

## 9. Update main

After the release is on PyPI, make sure to create a PR for the `main` branch
where the only changes are:

- the version increase in `__init__.py` (while keeping suffix `.dev0`).
- Search for the previous release version in the codebase and replace any occurrences with the new version
  (without `.dev0`). For example, some installation pages refer to the latest release.

The PR should be made from your fork and should be a different branch than the releases branch you created earlier.
This should be something like `x.y.z+1.dev0`.

## 10. Create GitHub Release

Finally, go to the [releases page](https://github.com/microsoft/PyRIT/releases), select "Draft a new release" and the "tag"
for which you want to create the release notes. It should match the version that you just released
to PyPI. Hit "Generate release notes". This will pre-populate the text field with all changes.
Make sure that it starts where the last release left off.
Sometimes this tool adds too many changes, or leaves a few out, so it's best to check.
Be sure to check and update the new contributors as well.
Add a header "## Full list of changes" below "## What's changed?".
In addition to the full notes, we also want a shorter section with just the relevant
changes that users should be aware of. The shorter section will be under "## What's changed"
while the full list of changes will be right below.
Maintenance changes, build pipeline updates, and documentation fixes are not really important for users.
However, important bug fixes, new features, and breaking changes are good candidates to include.
If you are unsure about whether to include certain changes please consult with your fellow
maintainers.
When you're done, hit "Publish release" and mark it as the latest release.

## Appendix: Patch Releases (Cherry-Pick Process)

A patch release (e.g., `0.12.0` → `0.12.1`) ships a targeted fix — typically a security
patch or critical bug fix — without including other in-flight changes from `main`.
The process follows the same steps as a regular release with a few key differences.

### When to use a patch release

- A security vulnerability fix needs to be shipped urgently.
- A critical bug was found in the latest release that blocks users.
- The fix is already merged to `main`, but `main` also contains other changes
  that are not ready for release.

### Abbreviated steps

**1. Create a release branch from the previous tag, not from `main`:**

```bash
git fetch origin
git checkout -b release/vx.y.z vx.y.(z-1)
```

For example, to create `0.12.1` from `0.12.0`:

```bash
git checkout -b release/v0.12.1 v0.12.0
```

**2. Cherry-pick the fix from `main`:**

Identify the merge commit SHA on `main` (e.g., from the merged PR) and cherry-pick it:

```bash
git cherry-pick <commit-sha>
```

If the cherry-pick has conflicts, resolve them manually. Since this is a patch release
the fix should apply cleanly in most cases.

**3. Bump the version:**

Update the version in all three files (`pyproject.toml`, `pyrit/__init__.py`, `frontend/package.json`)
to the new patch version (e.g., `0.12.1`). Also update any version-pinned links in `README.md`
(e.g., image URLs pointing to `releases/v0.12.0` → `releases/v0.12.1`).

Commit the version bump:

```bash
git add pyproject.toml pyrit/__init__.py frontend/package.json README.md
git commit -m "Bump version to x.y.z"
```

**4. Push and tag:**

Push the release branch with the `releases/` prefix and create the tag:

```bash
git push origin release/vx.y.z:releases/vx.y.z
git tag -a vx.y.z -m "vx.y.z release"
git push origin vx.y.z
```

**5. Follow the regular release process from step 6 onward:**

- Build the package (step 6)
- Test the built package in a clean environment (step 7)
- Run integration tests (step 7)
- Publish to PyPI (step 8)
- Update `main` with the next dev version (step 9) — for a patch release after `x.y.z`,
  the next version on `main` may be either `x.y.(z+1).dev0` or `x.(y+1).0.dev0`
  depending on what the next planned release is.
- Create the GitHub release (step 10) — for patch releases the release notes should
  clearly state the reason for the patch (e.g., "Security fix for …" or "Critical bug fix
  for …"). Because a patch release contains only cherry-picked changes, the "What's
  changed?" summary and the full changelog will be much shorter than a regular release.
  Make sure to call out the specific issue or vulnerability that prompted the patch so
  users can quickly assess whether they need to upgrade.

### Key differences from a regular release

| Aspect | Regular release | Patch release |
|---|---|---|
| Branch base | `main` | Previous release tag (e.g., `v0.12.0`) |
| Changes included | Everything on `main` | Only cherry-picked fix(es) |
| Deprecated code removal | Yes (if minor bump) | No |
| Integration test scope | Full | Focused on affected areas |
| Release notes | Full changelog with curated summary | Short, focused on the reason for the patch |
