# 7. Notebooks

Notebooks are the primary way many of our operators interact with PyRIT. As such, it's very important for us to keep them up to date.

We use notebooks to ensure that we can connect to actual endpoints and that broad functionality is working as expected.

## Writing docs

These rules apply the [docs principles](../code/framework.md#docs).

- Cover one task or concept per page, and lead with the common case.
- Show working code instead of describing it. Link to the API reference instead of restating parameters.
- Keep change history and design rationale in the pull request, not in the docs.
- When behavior changes, update the docs that describe it, and delete what is no longer true.

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
project-unique MyST labels for stable cross-document references; a bare number
does not identify a unique cell across notebooks.

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

### Troubleshooting

#### Jupytext metadata warnings

The locked reader can report `'jupytext' extra keys ignored:
cell_metadata_filter, main_language`. These are valid Jupytext fields; the
warnings reflect an upstream reader limitation, not invalid notebook metadata.
Keep Jupytext sections, pairing information (`formats` and
`text_representation`), metadata filters (`cell_metadata_filter`), language
information (`main_language`), cell metadata, and saved outputs intact. Do not
remove them or broadly suppress frontmatter validation to silence these warnings.

#### API extraction diagnostics

The extractor validates Google-style docstrings against static source signatures.
Descriptions continued under an argument or exception need an additional
indentation level; otherwise a sentence can be parsed as another table row.
Document result fields under `Attributes`, not constructor `Args`.

Remaining signature diagnostics include parameters deliberately forwarded through
`@forward_init_parameters` and legacy `Generic[T]` type parameters. These are
static-parser limitations, not missing public arguments. Keep those documented
contracts and runtime signatures intact rather than removing useful documentation
or filtering warnings to obtain a zero count.

#### External-link failures

Transient external-link failures still fail the native strict build. The locked
CLI does not expose the original failed response's `Retry-After` header or a
per-request timeout.
A later HTTP probe cannot recover an earlier response's requested delay. Do not
replace this check with blanket build retries or classify every network error as
transient; permanent HTTP failures, invalid URLs, and local-reference errors must
remain visible.
