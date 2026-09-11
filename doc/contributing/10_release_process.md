# 10. Releasing PyRIT

This section is for maintainers only.
If you don't know who the maintainers are but you need to reach them
please file an issue or (if it needs to remain private) contact the
email address listed in pyproject.toml

Follow the instructions according to the order provided.

## 1. Release Readiness

Before starting the release process, verify the codebase is in a healthy state.

### Customer-Facing Review

Use an existing release work item, or create one if none exists, to track release
readiness. The release owner reviews the customer-facing release materials: draft
GitHub release notes, changed public documentation, and changed CoPyRIT UI content.
Review the changed documentation and UI content for technical accuracy, security and
compatibility disclosures, content clarity, and usability. Ensure the release notes
include:

- A summary of the important bug fixes and features being shipped.
- Security fixes and known high-priority security issues, including affected versions
  and recommended actions. If there are none, write
  `No known high-priority security issues.`
- Breaking changes and backward-compatibility issues, including clear migration or
  mitigation steps. If there are none, write
  `No known breaking changes or backward-compatibility issues.`

Link the draft release notes in the release work item. Record
`Initial review completed by @owner on YYYY-MM-DD: no findings` or link the issues or
pull requests that resolved the findings.

#### Before Publishing

A PyRIT maintainer other than the release owner reviews the final materials and:

1. Compares the final release notes with the changes included in the release and
   confirms that the required content is complete and accurate.
2. Opens the changed documentation and applicable CoPyRIT UI to confirm that the
   content is clear, security information is accessible, and the final materials match
   the release notes.
3. Confirms that high-priority security bugs and technical-review findings from the
   initial review are resolved.
4. Records either
   `Approved by @reviewer on YYYY-MM-DD for development, security, content, and UX`
   or the remaining changes required before approval in the release work item.

Do not include details about security vulnerabilities that have not yet been publicly
disclosed in the release work item. Follow [the security policy](../../SECURITY.md)
for private vulnerability reporting.

- **Check for pending changes.** Ask other PyRIT maintainers whether they have any in-flight changes that should land before the release.
- **Verify build pipelines.** Confirm that all integration tests and end-to-end tests are passing in the CI pipelines. If any tests are failing, fix them before proceeding.
  - **Partner integration tests.** Ensure the partner integration tests are also passing. These tests validate that we are not breaking contracts with partner teams (e.g., Foundry). If any are failing, coordinate with the affected partner teams before proceeding with the release.
  - **Azure key-based auth is disabled in our tenant.** Our Azure subscription has API-key (local) auth turned off, so Azure target integration tests authenticate with Microsoft Entra ID. Tests that run notebooks requiring Azure API keys are deliberately skipped; otherwise they fail with HTTP 403 `AuthenticationTypeDisabled` ("Key based authentication is disabled for this resource"). Do not re-enable key auth for our tenant. When validating a release manually, authenticate Azure targets with Entra (`az login`) rather than API keys.
- **Update scorer metrics.** Run `python -m build_scripts.evaluate_scorers` and commit the results so that scorer evaluation metrics are up to date.

## 2. Decide the Next Version

First, decide what the next release version is going to be.
We follow semantic versioning for Python projects; see
https://semver.org/ for more details.
Below, we refer to the version as `x.y.z`.
`x` is the major version, `y` the minor version, and `z` the patch version.

PyRIT is past `1.0.0`, its first "stable" release, so in practice:

- **Regular releases increment the minor version** (e.g., `1.0.1` to `1.1.0`).
  This is the default for a normal release, whatever mix of features and fixes it contains.
  Minor releases are broadly backward compatible: the main exception is that functionality
  which was deprecated in an earlier release may be removed once its announced removal version
  arrives, so check step 3 and document any such removal as a breaking change per step 1.
- **Patch releases are reserved for targeted fixes** shipped outside the normal cadence, such
  as a security patch or a critical bug fix cherry-picked onto the previous release
  (e.g., `1.0.0` to `1.0.1`). See the patch release appendix at the end of this document.
- **Major releases** are for deliberate, wide-reaching breaking changes, and are planned with
  the maintainers rather than chosen at release time. Some functionality is already scheduled
  for removal in a future major version, so treat step 3 as applying to whichever version
  number you are incrementing.

`main` always carries a `.dev0` version for the next planned release (e.g., `1.2.0.dev0` while
`1.1.0` is the latest published release). There are circumstances when we might want to release
versions that aren't final; consult
https://packaging.python.org/en/latest/discussions/versioning/ to determine whether
there should be any postfixes on the release version.

Confirm the choice with the other maintainers if this release is anything other than the next
minor version.

## 3. Remove deprecated functionality

If you are incrementing the minor version, search the entire codebase for the new version,
e.g., "1.2.0" (no leading "v"),
to find occurrences where we deprecated functionality and announced that it will be
removed in the new version. Typically, functionality is deprecated and then stays for
two minor versions before getting removed.

Deprecations are usually declared with a `removed_in=` argument to the helpers in
`pyrit/common/deprecation.py`, so searching for `removed_in=` is a quick way to find
everything that is scheduled for removal:

```bash
grep -rn "removed_in=" --include=*.py pyrit/
```

If you find functionality to remove make sure to merge the PR to `main` before proceeding.
If nothing is scheduled for removal in this version, record that in the release work item so
it is not re-investigated later.

Because regular releases are always a minor bump (step 2), functionality scheduled for removal
in the next minor version can be removed as soon as that version is the next planned release,
rather than waiting for the release branch to be cut. That is preferable, because it keeps the
removal and its announcement in separate pull requests.

## 4. Update the version

### Version files

Set the version established in step 2 in every one of these files. The runtime version lives in
`pyrit/_version.py`, not `pyrit/__init__.py`, which only re-exports it lazily.

| File | Format | Notes |
|---|---|---|
| `pyrit/_version.py` | `__version__ = "x.y.z"` | Canonical runtime version |
| `pyproject.toml` | `version = "x.y.z"` | Packaging metadata |
| `frontend/package.json` | `"version": "x.y.z"` | npm form: `x.y.z-dev.0`, not `x.y.z.dev0` |
| `frontend/package-lock.json` | `"version": "x.y.z"` | Two occurrences near the top of the file |
| `uv.lock` | `version = "x.y.z"` | Under the `[[package]]` entry named `pyrit` |

Do not edit `.github/docs-versions.yml` here; that is updated on `main` after the release
in step 10.

Bump `pyrit/_version.py` first. It is the source of truth that the frontend development server
syncs `frontend/package.json` from, so if the two disagree the next dev-server run silently
rewrites `package.json` back, and it never updates `frontend/package-lock.json` to match.

Take care not to modify dependency versions that happen to resemble the PyRIT version. Examples
that exist today are `starlette` and `python-docx` in `pyproject.toml`, `rfc3987-syntax` in
`uv.lock`, and several packages in `frontend/package-lock.json`. In the lock files, only the
root `pyrit` / `pyrit-frontend` entries are the project version; every other `[[package]]`
stanza or `node_modules/**` entry belongs to a dependency.

Confirm that no development suffix survives in the files you just changed:

```bash
grep -rn 'x\.y\.z\.dev0\|x\.y\.z-dev\.0' pyrit/ pyproject.toml frontend/package.json \
  frontend/package-lock.json uv.lock
```

Then confirm the runtime version resolves correctly:

```bash
python -c "import pyrit; assert pyrit.__version__ == 'x.y.z', pyrit.__version__"
```

### Update README File

The README file is published to PyPI, where relative links do not resolve, so any
repository-relative link has to be rewritten to point at the release branch. Check the
current README, since the set of links changes over time.

Rewrite relative links, such as `./doc/roakey.png` or `./CITATION.cff`, to absolute
links pinned to this release. For files and images use the "raw" form, e.g.
`https://raw.githubusercontent.com/microsoft/PyRIT/releases/vx.y.z/doc/roakey.png`. For
directories use the "tree" form, e.g.
`https://github.com/microsoft/PyRIT/tree/releases/vx.y.z/doc/code`. Links that already
point at a stable external URL, such as the documentation site or the security policy,
stay as they are.

Make this change only on the release branch. `main` keeps the relative links so that
they resolve while browsing the repository.

## 5. Publish to GitHub

Commit your changes and push them to the repository on a branch called
`releases/vx.y.z`, then run

```bash
git checkout -b "releases/vx.y.z"
git add pyrit/_version.py pyproject.toml frontend/package.json \
  frontend/package-lock.json uv.lock README.md
git commit -m "release vx.y.z"
git push origin releases/vx.y.z
git tag -a vx.y.z -m "vx.y.z release"
git push origin vx.y.z
```

After pushing the branch to remote, check the release branch to make sure it looks as intended (e.g. check the links in the README work properly).

Confirm the GitHub Actions checks ran and passed on the release branch itself, and re-run them
after any cherry-pick, rather than relying only on the checks that ran on `main` before the
branch was cut.

A patch release branch is cut from an older tag (see the appendix), so it carries that
release's workflow files. If the tag predates these release-branch triggers, nothing will run
on push; cherry-pick the workflow change onto the branch, or start each workflow manually from
the Actions tab.

## 6. Build Package

You'll need the build package to build the project. If it’s not already installed, install it `pip install build`.

### Build the Frontend

The PyRIT package includes a web-based frontend that must be built before packaging. This requires Node.js and npm to be installed.

Run the prepare script to build the frontend and copy it into the package structure:

```bash
python -m build_scripts.prepare_package
```

This will:

1. Run `npm install` and `npm run build` in the `frontend/` directory
2. Copy the built assets from `frontend/dist/` to `pyrit/backend/frontend/`. Double check to make sure the files exist after running the `prepare_package.py` script. This should at least include index.html, an `assets` folder with `js` and `css` files.

### Build the Python Package

`dist/` is not cleaned automatically and may still hold wheels from an earlier release. Remove
them so the `python -m build` output is unambiguous and no stale artifact can reach PyPI:

```bash
rm -rf dist/
```

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

Also confirm the *runtime* version, which is what users actually see. Two things can shadow the
installed package: a `PYTHONPATH` that points at the repository, and the current directory, which
Python puts on `sys.path` for `python -c`. Running the check from inside the checkout therefore
tests the working tree rather than the wheel, and it passes even when the wheel is wrong. Run it
from a directory outside the repository, with `PYTHONPATH` unset, and assert both the location
and the version:

```bash
cd ..
env -u PYTHONPATH python -c "import pyrit; assert 'site-packages' in pyrit.__file__, pyrit.__file__; assert pyrit.__version__ == 'x.y.z', pyrit.__version__"
```

In PowerShell, change to a directory outside the repository, clear the variable with
`Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue`, and then run the same `python -c`
command.

Make sure to set up the Jupyter kernel as described in our [Jupyter setup](../getting_started/troubleshooting/jupyter_setup.md) guide.

To test the demos outside the PyRIT repository, copy the `doc`, `assets`, and `.env` files to a new folder created outside the PyRIT directory. For better organization, you could create a main folder called `releases` and a subfolder named `releasevx.y.z`, and then place the copied folders within this structure.

Before running the demos, execute `az login` or `az login --use-device-code`, as some demos require Azure authentication and use delegation SAS.

Additionally, verify that your environment file includes all the test secrets needed to run the demos. If not, update your .env file using the secrets from the key vault.

In the new location, run all notebooks that are currently skipped by integration tests in VS Code. To find them, search for `skipped_files` under `tests/integration/`, and also check the separate `_azure_key_auth_notebooks` set described below; the lists live in several differently-named test modules, so search rather than relying on a fixed filename pattern or a remembered count. The notebooks themselves are in the doc folder that you copied into your new `releases\releasevx.y.z` folder. Note that some of these notebooks have known issues and it may make sense to skip testing them until those are fixed. Check with the last person to deploy or look for the relevant release work item for more information. In running the notebooks, you may also see exceptions. If this happens, make sure to look for existing bugs open on the ADO board or create a new one if it does not exist! If it is easy to fix, we prefer to fix the issue before the release continues.

Some notebooks that use Azure targets with API-key (local) auth are skipped by the integration tests for a separate reason: key-based auth is disabled in our Azure subscription (see the note under *Release Readiness* above). These are tracked in the `_azure_key_auth_notebooks` set in `tests/integration/targets/test_notebooks_targets.py` (distinct from the long-standing `skipped_files` list). Validate them with Entra auth (`az login`) rather than API keys, or rely on the equivalent Entra-auth integration tests; running them with API keys fails with HTTP 403 `AuthenticationTypeDisabled`.

A reminder that you should ensure that the integration tests pass in the version you are releasing in addition to the skipped files.

The Azure DevOps test pipelines do not run themselves on a release branch. `integration-tests`,
`end-to-end-tests` and `partner-integration-tests` all declare `trigger: none` and schedule only
`main`, so cutting the release branch produces no automatic run and the absence of a result is
easy to mistake for a passing one. Queue each pipeline manually against `releases/vx.y.z` and
wait for the results before continuing; the previous release was validated this way.

Note: copying the `doc` folder elsewhere is essential since we store data files
in the repository that should be shipped with the package.
If we run inside the repository, we may not face errors that users encounter
with a clean installation and no locally cloned repository.

If at any point you need to make changes to fix bugs discovered while testing, or there is another change to include with the release, follow the steps below after the item has been merged into `main`.

```bash
git checkout main
git fetch origin
git log origin/main # to identify the commit hash of the change you want to cherry-pick
git checkout releases/vx.y.z
git cherry-pick <commit-hash>
git push origin releases/vx.y.z
git tag -a vx.y.z -m "vx.y.z release" --force # to update the tag to the correct commit
git push origin vx.y.z --force
```

Note: You may need to build the package again if those changes modify any dependencies, and consider retesting the notebooks if the changes affect them. If you reuse the same environment, it is best to `uv pip uninstall pyrit` to force the reinstall.

Only move the tag while you are still validating, before step 9. Once the package is on PyPI
that version is permanent, so the tag must keep pointing at the commit that produced it; ship
any later fix as a new version instead.


## 8. Migrate Production Database Schema

Apply any pending Alembic migrations to the production database. This is the **only**
sanctioned path for modifying the production schema — normal startup only validates,
never upgrades.

**Run from the release branch with release dependencies.** This ensures the migration
files and model definitions match exactly what will be shipped to users. Running from
`main` or a dev environment could apply unreleased migrations that break prod.

```bash
git checkout releases/vx.y.z
uv run python -c "import pyrit; print(pyrit.__version__)"  # verify: x.y.z (no .dev0)
```

**Run the migration** (reads `AZURE_SQL_DB_CONNECTION_STRING_PROD` from `~/.pyrit/.env`):

```bash
uv run python -m build_scripts.migrate_prod_memory_schema
```

The script validates the environment (release branch, clean tree, no `.dev` version),
constructs an `AzureSQLMemory` pointed at prod, and runs `_run_schema_migration()` which
upgrades to head and verifies the schema matches models. Since you're on the release branch,
head is the release revision.

**Verify prod is usable after migration.** This connects to the prod DB using the
check-only path and confirms compatibility:

```bash
uv run python -c "
import os, dotenv
from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH
dotenv.load_dotenv(CONFIGURATION_DIRECTORY_PATH / '.env', override=False, interpolate=True)
from pyrit.memory import AzureSQLMemory
AzureSQLMemory(connection_string=os.environ['AZURE_SQL_DB_CONNECTION_STRING_PROD'])
"
```

If it exits without error (or only a schema mismatch warning), prod is ready.

If no schema changes landed in this release, `_run_schema_migration` is a no-op.
Still run it as confirmation.

**Rollback policy:** forward-fix only. Ship a new corrective migration rather than downgrading,
since `downgrade()` risks data loss.


## 9. Publish to PyPI

Complete this checklist in the release work item:

- [ ] The initial review is recorded, and the draft release notes are linked.
- [ ] High-priority security bugs and technical-review findings are resolved.
- [ ] A maintainer other than the release owner has recorded final approval for development, security, content, and UX.
- [ ] Release notes contain the required security and compatibility disclosures.

Do not publish the package until every item is complete.

Create an account on pypi.org if you don't have one yet.
Ask one of the other maintainers to add you to the `pyrit` project on PyPI.

Note: Before publishing to PyPI, have your API token for scope 'Project: pyrit' handy. You can create one by going to the Settings in the pyrit project and "Create a token for pyrit" under API tokens. This token will be used to publish the release.

```bash
uv pip install twine
twine upload dist/pyrit-x.y.z-py3-none-any.whl dist/pyrit-x.y.z.tar.gz
```

Upload the two files by name rather than `dist/*` so that a stale artifact from an earlier
release cannot be published by accident.

If successful, it will print

> View at:
> https://pypi.org/project/pyrit/x.y.z/

PyPI permanently reserves a filename once it is uploaded. If one artifact uploads and the other
fails, do not rebuild, because a rebuilt file will not match the one already published. Retry
the missing file as-is, or contact PyPI support.

## 10. Update main

After the release is on PyPI, make sure to create a PR for the `main` branch
where the changes are:

- the version increase in the version files listed in step 4, this time keeping the development
  suffix: `1.2.0.dev0` for the Python files and `1.2.0-dev.0` for the frontend files.
- Search for the previous release version in the codebase and replace any occurrences with the new version
  (without `.dev0`). For example, some installation pages refer to the latest release.
- Update the documentation site versions in `.github/docs-versions.yml` (see below).

The PR should be made from your fork and should be a different branch than the releases branch you created earlier,
named after the next development version, for example `1.2.0.dev0`.

### Update the documentation site versions

Add the new release as a version on the [documentation site](https://microsoft.github.io/PyRIT/) by
editing `.github/docs-versions.yml`:

- Insert the new release at the top of `versions:`, immediately after the `latest` entry, with `slug: "x.y.z"`, `name: "x.y.z"`, and `ref: releases/vx.y.z`. The picker renders the list in file order, which is newest first.
- Update `stable:` and `default:` at the top of the file to point at the new version (so the
  root URL `microsoft.github.io/PyRIT/` and the `/stable/` alias redirect to it).

Once merged, the docs workflow rebuilds the site and the new version appears in the version picker on
every page of every version.

For example, releasing `1.1.0` would change:

```yaml
default: "1.0.1"
stable: "1.0.1"
```

to:

```yaml
default: "1.1.0"
stable: "1.1.0"
```

and add an entry under `versions:` for `1.1.0` pointing at `releases/v1.1.0`.

## 11. Create GitHub Release

Finally, go to the [releases page](https://github.com/microsoft/PyRIT/releases), select
"Draft a new release", and choose the tag that matches the version published to PyPI.
Generate the release notes to produce the full change list. Verify that the list starts
where the previous release ended and that the new-contributor list is accurate. Add
"## Full list of changes" below "## What's changed" and place the generated list
there. Use the approved release notes linked from the release work item for
"## What's changed". Maintenance changes, build pipeline updates, and routine
documentation fixes can remain in the full list only.

If you are unsure about whether to include certain changes please consult with your fellow
maintainers.
When you're done, hit "Publish release" and mark it as the latest release.

## 12. Update internal deployments

The release is not complete until Microsoft-internal consumers pick up the new release. These
steps apply to maintainers with access to the internal environments; external contributors can
skip this section. The details live at
[aka.ms/internal-release](https://aka.ms/internal-release).

1. **Verify pyrit-internal is up to date** so the internal package tracks the new release.
2. **Coordinate the CoPyRIT production deployment before it runs.** A production deployment
   restarts the application and clears its in-memory state, so targets onboarded into a running
   instance are lost when it reloads, and an unannounced run can interrupt work in progress. Agree
   with the CoPyRIT maintainers on who queues it and when, rather than assuming that falls to the
   release owner; they have taken it themselves in the past. Whoever runs it should do so only
   after the package is on PyPI, so production runs a version users can install, and after the
   production database migration in step 8 has completed, so the application and the shared
   database schema agree. The pipeline definition, today `gui-deploy.yml` in this repository,
   restricts which branch may deploy to production and fails the run if it is queued from anywhere
   else; read it rather than assuming the rule. It also builds the image from a specific commit
   rather than from the published package, so the deployed application tracks that commit and not
   the release tag. Record the deployed commit in the release work item once the run finishes;
   step 12 is complete when that commit is recorded.

## Appendix: Patch Releases (Cherry-Pick Process)

A patch release (e.g., `1.0.0` → `1.0.1`) ships a targeted fix — typically a security
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
git checkout -b releases/vx.y.z vprevious.x.y.z
```

For example, to create `1.0.1` from `1.0.0`:

```bash
git checkout -b releases/v1.0.1 v1.0.0
```

**2. Cherry-pick the fix from `main`:**

Identify the merge commit SHA on `main` (e.g., from the merged PR) and cherry-pick it:

```bash
git cherry-pick <commit-sha>
```

If the cherry-pick has conflicts, resolve them manually. Since this is a patch release
the fix should apply cleanly in most cases.

**3. Bump the version:**

Update the version in every file listed in step 4 (`pyrit/_version.py`, `pyproject.toml`,
`frontend/package.json`, `frontend/package-lock.json`, and `uv.lock`)
to the new patch version (e.g., `1.0.1`). Also update any version-pinned links in `README.md`
(e.g., image URLs pointing to `releases/v1.0.0` → `releases/v1.0.1`).

Commit the version bump:

```bash
git add pyrit/_version.py pyproject.toml frontend/package.json \
  frontend/package-lock.json uv.lock README.md
git commit -m "release vx.y.z"
```

**4. Push and tag:**

Push the release branch and create the tag:

```bash
git push origin releases/vx.y.z
git tag -a vx.y.z -m "vx.y.z release"
git push origin vx.y.z
```

**5. Follow the regular release process from step 6 onward:**

- Build the package (step 6)
- Test the built package in a clean environment (step 7)
- Run integration tests (step 7)
- Migrate the production database (step 8) — required if the cherry-picked change carries an
  Alembic revision, and harmless to run as confirmation if it does not
- Publish to PyPI (step 9)
- Update `main` with the next dev version (step 10) — for a patch release after `x.y.z`,
  the next version on `main` may be either `x.y.(z+1).dev0` or `x.(y+1).0.dev0`
  depending on what the next planned release is. This is also where you update the docs
  site versions in `.github/docs-versions.yml` (add the new patch version and bump
  `stable:`/`default:` if appropriate).
- Create the GitHub release (step 11) — for patch releases the release notes should
  clearly state the reason for the patch (e.g., "Security fix for …" or "Critical bug fix
  for …"). Because a patch release contains only cherry-picked changes, the "What's
  changed?" summary and the full changelog will be much shorter than a regular release.
  Make sure to call out the specific issue or vulnerability that prompted the patch so
  users can quickly assess whether they need to upgrade.
- Update internal deployments (step 12)

### Key differences from a regular release

| Aspect | Regular release | Patch release |
|---|---|---|
| Branch base | `main` | Previous release tag (e.g., `v1.0.0`) |
| Changes included | Everything on `main` | Only cherry-picked fix(es) |
| Deprecated code removal | Yes (if minor bump) | No |
| Integration test scope | Full | Focused on affected areas |
| Release notes | Full changelog with curated summary | Short, focused on the reason for the patch |
