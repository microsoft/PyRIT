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

For HTML only, run from `doc`:

```powershell
Set-Location doc
uv run --frozen --no-sync jupyter-book build --html --strict
```

Do not add `--all` to an HTML-only build. It selects the PDF export declared in
`myst.yml` too, even when `--html` is present. With the locked Jupyter Book 2
version, `--strict` fails on errors and reports warnings separately; a successful
strict build is not necessarily warning-free. Do not suppress errors to make a
build pass.

### PDF prerequisites and validation

The `plain_latex_book` export requires `latexmk`, `xelatex`, and the template's
LaTeX packages. Provision those tools separately before requesting a PDF build.
For example, Ubuntu needs `latexmk`, `texlive-xetex`,
`texlive-fonts-recommended`, and `texlive-plain-generic`. PDF image conversion can
also require ImageMagick. HTML builds do not need this toolchain.

After generating the API reference, run the checked PDF export from the
repository root:

```powershell
uv run --frozen --no-sync python -m build_scripts.build_docs_pdf
```

The helper checks prerequisites before building, runs
`jupyter-book build --site --pdf --strict --logs`, and requires a fresh, readable
`doc\exports\book.pdf` with fresh native logs free of fatal LaTeX diagnostics.
`--site` validates the book content in strict mode without building static HTML;
the locked Jupyter Book version only applies strict error checking to site
content, not to standalone exports.
Jupyter Book can log an export failure without returning a nonzero exit, so its
exit code alone is not sufficient PDF evidence. A stale PDF does not count as a
successful new export. Inspect the PDF's chapters, images, citations, and layout
as well; compilation does not prove that every interactive HTML element has a
correct print representation.

Where GNU Make is available, `make docs-build` prepares the API reference, builds
strict HTML, and generates RSS. `make docs-build-pdf` prepares the API reference
and runs the checked PDF export. `make docs-build-all` builds HTML first and then
checks the PDF export. These local targets do not change the hosted multiversion
publication workflow.
