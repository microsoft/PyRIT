# CLAUDE.md — PyRIT (contributor working notes)

This is a **fork of `microsoft/PyRIT`** maintained by **shashank03-dev** for **open-source contributions** (portfolio target). Goal: land clean, mergeable PRs that pass Microsoft's AI Red Team review bar.

- `origin` = `shashank03-dev/PyRIT` (my fork) · `upstream` = `microsoft/PyRIT`
- PyRIT = Python Risk Identification Tool for generative AI (AI red-teaming framework). Python 3.10–3.14, package manager **`uv`**.

## Contributor identity (STRICT — every commit, push, and PR is mine)

**Sole contributor on everything in this fork: `shashank03-dev` (GitHub: https://github.com/shashank03-dev).**

Every commit, push, PR, issue comment, and review reply must be authored **and** committed as me — never as an assistant, bot, or tool. Before the first commit in any clone or session, set and verify:

```bash
git config user.name  "shashank03-dev"
git config user.email "235868702+shashank03-dev@users.noreply.github.com"   # GitHub no-reply → links to my profile
git config user.name && git config user.email                                  # verify before committing
```

- Check with `git log -1 --format='%an <%ae> | %cn <%ce>'` — **both** author and committer must be `shashank03-dev`. If either is wrong, fix it before pushing (`git commit --amend --reset-author --no-edit` on an unpushed commit).
- **No `Co-Authored-By` trailers, no `Generated with ...` lines, no session links, no AI/tool attribution** in commit messages, PR titles/bodies, or code comments.
- PRs are opened from my fork (`shashank03-dev:<branch>` → `microsoft/PyRIT:main`) and signed off by me (CLA is signed under my account).

## Git discipline (STRICT — do not deviate)

- **NEVER `git add .`, `git add -A`, or `git add -u`.** Stage **only** the specific files I actually modified or added, by explicit path:
  ```bash
  git add pyrit/converter/my_new_converter.py tests/unit/converter/test_my_new_converter.py
  ```
  Before staging, run `git status` and confirm the exact paths. If unsure whether a changed file is mine to include, ask — do not blanket-stage.
- One PR = one focused change. Do not sweep in unrelated edits, reformatting of untouched files, or stray generated artifacts.
- Branch from an up-to-date `main`; never commit directly to `main`. Sync first:
  ```bash
  git fetch upstream && git checkout main && git merge upstream/main
  git checkout -b fix/<short-description>
  ```
- Commit messages: no `Co-Authored-By` trailer (see Contributor identity). Use the `gh` CLI for GitHub operations (PRs, issues).
- This `CLAUDE.md` lives on my fork's working branches only — it must **never** be staged into an upstream PR branch (`fix/*`, `feat/*`). When branching for upstream work, drop it or add it to `.git/info/exclude`.

## Contribution workflow (from doc/contributing/)

1. Find an issue labeled `help wanted` or `good first issue`; check it isn't already claimed.
2. **Claim it**: comment `I would like to take this.` on the issue before starting.
3. Open an issue first if none exists for the change (align on approach before coding).
4. Branch → implement → **add/update tests** → **update docs** if behavior/API changed.
5. Push to `origin` fork, open PR against `upstream` main via `gh pr create`.
6. **CLA**: on the first PR a bot will ask for CLA signature (cla.opensource.microsoft.com) — sign once, covers all Microsoft repos.
7. CI must pass; only a maintainer can merge. Address review feedback on the same branch.
8. Claimed issues idle >14 days may be reassigned — keep momentum.

## Dev environment

```bash
uv sync                 # install deps + PyRIT in editable mode (includes pytest, ruff, ty, pre-commit)
uv run pre-commit install   # one-time: enable hooks
```

## Verify before opening a PR (run these; all must pass)

```bash
# Stage ONLY your changed files first (see Git discipline), then:
uv run pre-commit run --files <your changed files>   # style/lint hooks (isort, ruff, etc.)
uv run ty check pyrit tests/unit                      # type check
uv run -m pytest -n 4 --dist=loadfile tests/unit     # unit tests
```
- Coverage gates enforced in CI: overall `--cov-fail-under=78`; **diff coverage `--fail-under=90`** (new/changed lines must be ~90% covered) → new code needs thorough unit tests.
- Run a single test: `uv run -m pytest tests/unit/path/test_file.py::test_name`
- See `Makefile` for the canonical targets (`pre-commit`, `ty`, `unit-test`, `unit-test-diff-cover`).

## Architecture (where contributions live)

Attack pipeline: `dataset → converter(s) → prompt_normalizer → prompt_target (model) → response → scorer`, orchestrated by an `executor/attack` strategy; everything logged to `memory` (DB).

| Area | Path | Notes |
|---|---|---|
| Converters | `pyrit/converter/` | Prompt transforms (encodings, ciphers, media). ~87 files — easiest, most self-contained entry. |
| Attack strategies | `pyrit/executor/attack/` | `single_turn/`, `multi_turn/` (crescendo, PAIR, tree_of_attacks). Often implementing a published paper — see `doc/contributing/2_incorporating_research.md`. |
| Targets | `pyrit/prompt_target/` | Models/endpoints under test. |
| Scorers | `pyrit/score/` | Judge attack success (true/false or 0–1). |
| Tests | `tests/unit/` | Mirror the source layout; required for every code change. |
| Docs | `doc/` | Update when APIs/behavior change. |

## Conventions

- Match existing patterns in the folder you're editing — read a sibling file (e.g. `base64_converter.py` + its test) before writing a new one.
- Style is enforced by ruff/isort via pre-commit; don't hand-fight the formatter.
- Every code change needs tests; every behavior/API change needs doc updates. Maintainers check both.

## Verified bug candidates (found 2026-09-24 against upstream `47c6151`)

Each was reproduced by running the code. Before fixing: search upstream issues/PRs for duplicates, open an issue, claim it, then one PR per bug.

1. **`SelectiveTextConverter` wraps the whole prompt instead of the converted region** — `pyrit/converter/selective_text_converter.py` (token branch of `convert_async`, `if self._preserve_tokens and self._start_token not in result.output_text`).
   With `TokenSelectionStrategy()` + `preserve_tokens=True`, `convert_tokens_async` consumes the ⟪⟫ delimiters, then the whole output is re-wrapped:
   `"Hello ⟪secret⟫ world"` + ROT13 → `"⟪Hello frperg world⟫"` (expected `"Hello ⟪frperg⟫ world"`). Breaks the documented chaining example — the next chained converter converts the entire prompt.
   Fix idea: re-wrap each converted span individually (convert each token span and emit `start + converted + end`).
2. **`KeywordSelectionStrategy(case_sensitive=False)` selects the wrong characters** — `pyrit/converter/text_selection_strategy.py` (`KeywordSelectionStrategy.select_range`).
   It searches `text.lower()` but slices the original `text`; `str.lower()` can change length (e.g. `"İ".lower()` is 2 chars), so offsets drift:
   `"İstanbul SECRET plan"`, keyword `"secret"`, ROT13 → `"İstanbul SRPERG plan"` (first letter skipped, trailing space converted).
   Fix idea: `re.search(re.escape(keyword), text, re.IGNORECASE)` and use the match span on the original text.
3. **Word-level converters drop the word-selection strategy from their identifier** — `_build_identifier` in `charswap_attack_converter.py`, `leetspeak_converter.py`, `zalgo_converter.py`, `string_join_converter.py`, `bin_ascii_converter.py`, `first_letter_converter.py`, `unicode_replacement_converter.py`.
   They override `WordLevelConverter._build_identifier` without merging `super()._build_identifier().params` (as `BinaryConverter`/`EmojiConverter` do), so e.g. `CharSwapConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[0]))` and `indices=[5]` get the **same identifier hash**. Identifiers are persisted content-addressed by hash (`memory_interface.py`) and de-duplicated by hash (`backend/services/attack_service.py`), so provenance of which words were perturbed is lost.
   Fix idea: merge `**super()._build_identifier().params` in each override; add a test asserting different strategies yield different hashes.
