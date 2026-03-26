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
