# 7. Notebooks

Notebooks are the primary way many of our operators interact with PyRIT. As such, it's very important for us to keep them up to date.

We use notebooks to ensure that we can connect to actual endpoints and that broad functionality is working as expected.

## Updates using percent files

All documentation should be a `.md` file or a `.py` file in the percent format file. We then use jupytext to execute this code and convert to `.ipynb` for consumption. We have several reasons for this. 1) `.py` and `.md` files are much easier to review. 2) documentation code was tough to keep up to date without running it (which we can do automatically with jupytext). 3) It gives us some level of integration testing; if models change from underneath us, we have some way of detecting the changes.

Here are contributor guidelines:

- The code should be able to execute in a reasonable timeframe. Before we build out test infrastructure, we often run this manually and long running files are not ideal. Not all code scenarios need to be documented like this in code that runs.
- This is *not* a replacement for unit tests or for integrations tests. Coverage is not needed here. Notebooks are built for understanding.
- This code often connects to various endpoints so it may not be easy to run (not all contributors will have everything deployed). However, it is an expectation that maintainers have all documented infrastructure available and configured.
  - Contributors: if your notebook updates a `.py` file or how it works specifically, rerun it as ` jupytext --execute --to notebook  ./doc/affected_file.py`
  - Some contributors use jupytext to generate `.py` files from `.ipynb` files. This is also acceptable. `jupytext --to py:percent ./doc/affected_file.ipynb`
  - Before a release, re-generate all notebooks by using [pct_to_ipynb.py](../generate_docs/pct_to_ipynb.py). Because this executes against real systems, it can detect many issues.
- Please do not re-commit updated generated `.ipynb` files with slight changes if nothing has changed in the source
- We use [Jupyter-Book](https://jupyterbook.org/) with [Markedly Structured Text (MyST)](https://mystmd.org/).

## Building the documentation

Rendering the committed notebooks does not execute their code or call their AI
providers. Notebook execution is a separate step that requires configured
credentials. Keep the committed notebook outputs and JupyText metadata when
editing documentation.

Keep existing notebook cell IDs. Our `nbstripout` hook preserves them with
`--keep-id`, rather than renumbering each notebook from zero. Use explicit,
project-unique MyST labels for stable cross-document references. Legacy numeric
IDs can cause duplicate-reference warnings in MyST's project-wide index; do not
renumber notebooks or change their outputs just to silence these warnings.

### Jupytext metadata warnings

The locked Jupyter Book 2.1.7 reader can report `'jupytext' extra keys ignored:
cell_metadata_filter, main_language`. These are valid Jupytext fields, not invalid
notebook metadata. MyST's [Jupytext frontmatter validator](https://github.com/jupyter-book/mystmd/blob/main/packages/myst-frontmatter/src/jupytext/validators.ts)
only accepts `formats` and `text_representation`; it ignores the other fields in
its rendering model, without changing the source notebook.

Keep the metadata used by the notebook tools:

- `[tool.jupytext].formats = "ipynb,py:percent"` in `pyproject.toml` configures
  repository-wide pairing. A notebook's `jupytext.formats`, when present, specifies
  its own pairing; `text_representation` identifies the percent-file format.
- `cell_metadata_filter` controls which cell metadata is written to the percent
  file. For example, `-all` excludes cell metadata, while `class,-all` retains
  `class`. Jupytext restores filtered metadata and saved outputs from the
  `.ipynb` when synchronizing the pair.
- `main_language` identifies the notebook's primary language, including how
  non-primary language cells are interpreted. Jupytext generates it when reading
  percent files without a stored kernel, so deleting it is not a lasting fix.

These warnings describe an upstream reader limitation. Do not remove Jupytext
sections, filters, pairing information, cell metadata, or outputs to silence
them, and do not broadly suppress frontmatter validation. The focused Jupytext
contract tests in `tests/unit/build_scripts/test_docs_build_contract.py` cover
pair resolution, metadata filtering, language handling, and output restoration.

### HTML build

Use this checkout's uv environment and locked development dependencies:

```powershell
uv sync --frozen --group dev --python 3.13
```

Generate the API reference before building the book. The TOC includes generated
pages, so a documentation-structure check alone is not a substitute for this
preparation:

```powershell
uv run --frozen --no-sync python -m build_scripts.pydoc2json pyrit --submodules -o doc\_api\pyrit_all.json
uv run --frozen --no-sync python -m build_scripts.gen_api_md
uv run --frozen --no-sync python -m build_scripts.validate_docs
```

The documentation is built and published as HTML only. No PDF export or LaTeX
toolchain is required. Run from `doc`:

```powershell
Set-Location doc
uv run --frozen --no-sync jupyter-book build --html --strict --check-links
```

Use explicit `--html` rather than `--all`, which can also select exports declared
in historical release configurations. With the locked Jupyter Book 2 version,
`--strict` fails on errors and reports warnings separately; a successful strict
build is not necessarily warning-free. Do not suppress errors to make a build
pass.

`--check-links` checks link destinations, including missing Python source files
that otherwise remain ordinary hyperlinks. External URL checks require network
access; auth-required and robot-policy exceptions are listed in `doc/myst.yml`.
The docs workflow runs strict link checking for the `latest` slot, including PR
validation, before rendering. Frozen release versions retain their existing build
behavior.

Where GNU Make is available, `make docs-build` prepares the API reference, builds
strict HTML with link checking, and generates RSS. The hosted multiversion
publication workflow builds, caches, and uploads only HTML for GitHub Pages.
