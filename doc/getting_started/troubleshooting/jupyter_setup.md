# What can I do if Jupyter cannot find PyRIT?

First, you need to find the corresponding environment for your project.
You can do this with the following command:

```bash
uv pip list
```

Then activate it using

```bash
# Windows
.\.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate
```

Next, you need to install the IPython kernel in the virtual environment.

Note: Jupyter and ipykernel are no longer installed by default with the base package. If you need to use Jupyter notebooks with PyRIT, you'll need to install these dependencies using one of the following methods:

1. Install with development dependencies: `uv sync`
2. Install with all optional dependencies: `uv sync --extra all`
3. Install just the notebook dependencies manually: `uv pip install jupyter ipykernel`

`uv sync` already installs a `python3` kernel into `.venv/share/jupyter/kernels/`, so in most
cases no extra step is needed. If you want a separate, clearly named kernel, register one that is
scoped to this virtual environment:

```bash
uv run python -m ipykernel install --sys-prefix --name=pyrit_kernel
```

Prefer `--sys-prefix` over `--user`. `--sys-prefix` installs into `.venv/share/jupyter/kernels/`,
so the kernel is tied to this checkout and disappears when the environment does. `--user`
installs into your machine-wide Jupyter data directory, where kernels accumulate across
checkouts and keep pointing at interpreters that may later be deleted.

Now you can start Jupyter Notebook:

```bash
uv run jupyter notebook
```

Once the notebook is open, you can select the kernel that matches the name you gave earlier.
To do this, go to `Kernel > Change kernel > pyrit_kernel`.

## Kernel fails to start with `FileNotFoundError: [WinError 2]`

If launching a kernel (or running the notebook integration tests) fails before any cell executes,
the kernelspec is likely pointing at an interpreter that no longer exists. Inspect it:

```bash
uv run jupyter kernelspec list
```

and open the `kernel.json` of the kernel being used. A healthy `python3` kernelspec that ships with
`ipykernel` has a relocatable first argument:

```json
{"argv": ["python", "-m", "ipykernel_launcher", "-f", "{connection_file}"]}
```

Jupyter rewrites that `"python"` to the interpreter running Jupyter, so the same file works in any
environment. If `argv[0]` is instead an absolute path into a different (or deleted) checkout, the
kernelspec has been rewritten in place. Re-install a correct one:

```bash
uv sync --reinstall-package ipykernel
```

This is easy to hit when you use several checkouts or git worktrees. On Windows, `uv` installs
package files by hardlinking them from its shared cache, so a single `kernel.json` inode can be
shared by the cache and every virtual environment on the machine. Any tool that edits that file
**in place** therefore corrupts all of them at once. If the problem keeps coming back in freshly
created environments, the shared cache itself is corrupted; clear just that package and re-sync:

```bash
uv cache clean ipykernel
uv sync --reinstall-package ipykernel
```

Note that `python -m ipykernel install` is safe here: it replaces the kernelspec directory rather
than editing files in place, so it does not corrupt the shared cache.
