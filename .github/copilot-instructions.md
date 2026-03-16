# PyRIT - Repository Instructions

PyRIT (Python Risk Identification Tool for generative AI) is an open-source framework for security professionals to proactively identify risks in generative AI systems.

## Architecture

PyRIT uses a modular "Lego brick" design. The main extensibility points are:

- **Prompt Converters** (`pyrit/prompt_converter/`) — Transform prompts (70+ implementations). Base: `PromptConverter`.
- **Scorers** (`pyrit/score/`) — Evaluate responses. Base: `Scorer`.
- **Prompt Targets** (`pyrit/prompt_target/`) — Send prompts to LLMs/APIs. Base: `PromptTarget`.
- **Executors / Scenarios** (`pyrit/executor/`, `pyrit/scenario/`) — Orchestrate multi-turn attacks.
- **Memory** (`pyrit/memory/`) — `CentralMemory` for prompt/response persistence.

## Code Review Guidelines

When performing a code review, be selective. Only leave comments for issues that genuinely matter:

- Bugs, logic errors, or security concerns
- Unclear code that would benefit from refactoring for readability
- Violations of the critical coding conventions above (async suffix, keyword-only args, type annotations)

Do NOT leave comments about:
- Style nitpicks that ruff/isort would catch automatically
- Missing docstrings or comments — we prefer minimal documentation. Code should be self-explanatory.
- Suggestions to add inline comments, logging, or error handling that isn't clearly needed
- Minor naming preferences or subjective "improvements"

Aim for fewer, higher-signal comments. A review with 2-3 important comments is better than 15 trivial ones.

Follow `.github/instructions/style-guide.instructions.md` for style guidelines. And look in `.github/instructions/` for specific instructions on the different components.
