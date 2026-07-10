# PyRIT - Repository Instructions

PyRIT (Python Risk Identification Tool for generative AI) is an open-source framework for security professionals to proactively identify risks in generative AI systems.

## Architecture

PyRIT uses a modular pluggable-brick design.

**[`doc/code/framework.md`](../doc/code/framework.md) is the canonical reference for how these pieces fit together.** It defines each component's responsibilities — what it owns and, critically, what it *does not* own — and how scenarios, attack techniques, executors, and the core/shared layers relate. Read it before adding or reviewing components so new code lands in the right place.

## Code Review Guidelines

When performing a code review, be selective. Only leave comments for issues that genuinely matter:

- Bugs, correctness, logic errors, or security concerns
- **Component responsibilities** — Each component should do its job and *only* its job, per [`doc/code/framework.md`](../doc/code/framework.md). Flag responsibility bleed: e.g. an executor assembling prepended/system prompts or role-play framing (that's an attack technique), a converter or target making branching decisions (that's an attack/scorer), a scorer acting on its own result (the attack branches), or business logic living in memory/output. If logic belongs in a different brick, say so.
- Unclear code that would benefit from refactoring for readability
- Violations of the critical coding conventions above (async suffix, keyword-only args, type annotations)

Do NOT leave comments about:
- Style nitpicks that ruff/isort would catch automatically
- Missing docstrings or comments — we prefer minimal documentation. Code should be self-explanatory.
- Suggestions to add inline comments, logging, or error handling that isn't clearly needed
- Minor naming preferences or subjective "improvements"

Aim for fewer, higher-signal comments. A review with 2-3 important comments is better than 15 trivial ones.

## Instruction Files

BEFORE editing or code-reviewing any file, you MUST read the `.github/instructions/` files whose `applyTo` patterns match the files you are about to edit. For example:
- Editing/code-reviewing `pyrit/**/*.py` → read `style-guide.instructions.md` and `user-custom.instructions.md`
- Editing/code-reviewing `pyrit/scenario/**` → also read `scenarios.instructions.md`
- Editing/code-reviewing `pyrit/prompt_converter/**` → also read `converters.instructions.md`
- Editing/code-reviewing `tests/**` → also read `test.instructions.md`
- Editing/code-reviewing `doc/**/*.py` or `doc/**/*.ipynb` → also read `docs.instructions.md`
- Editing/code-reviewing `frontend/**/*.{ts,tsx}` → also read `frontend-style-guide.instructions.md`
- Editing/code-reviewing `frontend/**/*.test.{ts,tsx}` → also read `frontend-test.instructions.md`

Follow every rule in the applicable instruction files. Do not skip this step.
