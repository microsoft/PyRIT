# PyRIT - Repository Instructions

PyRIT (Python Risk Identification Tool for generative AI) is an open-source framework for security professionals to proactively identify risks in generative AI systems.

## Architecture

PyRIT uses a modular pluggable-brick design. The main extensibility points are:

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

## Skills

PyRIT ships subsystem-specific **skills** under `.github/skills/`. BEFORE you create, modify, or **review** code in one of these areas, invoke the matching skill and follow its contract:

| Area (path) | Skill |
| --- | --- |
| `pyrit/prompt_converter/**` | `prompt-converter-development` |
| `pyrit/prompt_target/**` | `prompt-target-development` |
| `pyrit/score/**` | `scorer-development` |
| `pyrit/scenario/**` | `scenario-development` |
| `pyrit/executor/attack/**` | `attack-strategy-development` |
| `pyrit/models/**` | `models-guidelines` |
| `pyrit/output/**` | `output-module` |
| `pyrit/datasets/seed_datasets/**` | `seed-dataset-loaders` |
| `doc/**/*.{py,ipynb}` | `docs-sync` |
| `frontend/**/*.test.{ts,tsx}` | `frontend-testing` |

Do not skip this step. If a skill's guidance and these repository instructions ever conflict, follow the skill for that subsystem.

## Instructions

These always-on instructions apply automatically by file path — no skill invocation needed:

- `.github/instructions/style-guide.instructions.md` — all Python (`**/*.py`)
- `.github/instructions/frontend-style-guide.instructions.md` — frontend TypeScript/React (`frontend/**/*.{ts,tsx}`)
- `.github/instructions/test.instructions.md` — all tests (`**/tests/**`)
