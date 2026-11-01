# CLAUDE.md

Sprite Studio: train LoRAs on pixel-art sprites and generate new sprites in
the same style, through a web app (FastAPI + hand-written JavaScript) and command-line tools (Python 3.11+, Bash) on top of ComfyUI and kohya
sd-scripts. A standalone, generic tool: nothing in it belongs to one game.

Read before working: @README.md (what it is, layout, checks) and, depending
on the task, `docs/ARCHITECTURE.md` (components, data layout, formats,
pipelines), `docs/GUIDE.md` (every feature as users see it),
`docs/INSTALL.md` (installation, settings).

## Model and effort

Pick the model and effort for the task, for the main session and for any
subagent you start (the Agent tool's `model` / `effort`):

| Task | Model | Effort |
|---|---|---|
| Small questions, lookups, one-file fixes, renames, doc or text edits, running checks | Sonnet | medium |
| Complex tasks: features, multi-file changes, refactors, data-format migrations, training or generation pipeline changes | Opus 5.5 | medium |
| Deep reasoning when needed: architecture or pipeline design, hard bugs that resist a first look, file-format or default-setting decisions with wide impact | Opus 5.5 | high |

Start at the lowest row that fits and move up only when the work shows it
needs it (a "small" fix that turns into a cross-file bug moves to Opus).
Say which model/effort a planned step needs when you plan it.

## Commits

[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/),
always the same syntax, **one commit per feature or fix**. Each commit
carries what its change needs (code, tests, docs, changelog line) and
leaves the project passing the checks.

```
<type>(<scope>): <description>

<body: what and why, wrapped at 72 columns, English>

BREAKING CHANGE: <what breaks and how it migrates>   (only when it applies)
Co-Authored-By: ...          (the attribution lines the session provides)
```

- Types: `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `build`, `ci`,
  `chore`, `style`, `revert`.
- Scopes (lowercase, optional but preferred): `webapp`, `models`, `train`,
  `generate`, `batch`, `palettes`, `results`, `projects`, `jobs`, `comfy`,
  `pxtool`, `pxmodel`, `install`, `docs`, `ci`, `deps`, `release`,
  `claude`.
- Description: imperative mood, lowercase, no final period, 72 characters
  at most.
- Breaking changes (a file format, a setting, a command-line option, an
  API route that changes meaning) add `!` after the scope and a
  `BREAKING CHANGE:` footer saying what migrates.
- Commit as `Claude <noreply@anthropic.com>` (author and committer), with
  the attribution lines the session provides.

## Changelog

`CHANGELOG.md` follows [Keep a Changelog
1.1.0](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/):

- Newest first. `## [Unreleased]` on top collects every change as it lands;
  a release turns it into `## [X.Y.Z] - YYYY-MM-DD` (ISO date) and adds a new
  empty `[Unreleased]`.
- Change types, in this order and only these: `### Added`, `### Changed`,
  `### Deprecated`, `### Removed`, `### Fixed`, `### Security`. Leave out
  the empty ones.
- Written for users, not a git log: one bullet per user-visible change,
  bold area first (`- **Palettes**: ...`). Breaking changes say what
  migrates. Internal changes with no visible effect (refactors, tests, CI)
  go under `Changed` only when worth knowing, else nowhere.
- Every feature or fix commit adds its bullet to `[Unreleased]` in the
  same commit.
- The version lives in `VERSION` (read by the app and `pyproject.toml`). A
  release is its own commits: `docs(release): X.Y.Z` (the changelog
  section dated, the compare links at the bottom updated) and
  `chore(release): bump version to X.Y.Z`. Before 1.0.0, a new feature
  bumps the minor version, fixes alone the patch version.
- Publishing is automatic: once both commits are on `main` and CI is green,
  push an annotated tag `vX.Y.Z` on the last one. `release.yml` checks that
  the tag matches `VERSION` and is on `main`, reruns the checks, and creates
  the GitHub release with the changelog section as notes and a source
  archive with its checksum. A tag with a suffix (`v0.2.0-rc.1`) makes a
  pre-release.

## Checks

All must pass before committing (CI runs the same on every push):

```bash
ruff check . && ruff format --check .
shellcheck scripts/*.sh webapp/run.sh
for f in webapp/static/*.js; do node --check "$f"; done
pytest
```

Add or update tests with every fix and every new rule; tests run without a
GPU or ComfyUI (synthetic sprites, temporary folders, FastAPI's test
client). For UI changes, look at the page in a browser (Playwright and
Chromium are available) before calling it done.

## Conventions

- **The scripts are the source of truth.** The web app builds command lines
  and reads files; logic about models, prompts and images lives in
  `scripts/` (`pxmodel.py`, `pxtool.py`), not in `app.py` or `app.js`.
- **Saved data never breaks.** `model.json`, palettes, `job.txt` and the
  app's state files are migrated on load (`pxmodel.normalise`,
  `pxtool.load_palette`), never invalidated. A format change is a
  breaking change unless it is migrated.
- **Generic.** No game, faction or asset names in code, defaults, examples
  or docs; examples use neutral names (`units`, `red`, `small`). No
  hardware assumptions: detect the card (precision, memory, PyTorch build).
- **Python**: Ruff-formatted (120 columns), type hints in `app.py`, the
  standard library plus numpy, Pillow, scipy, FastAPI and uvicorn only.
  `pxcomfy.py` stays standard-library only.
- **Bash**: `set -euo pipefail` (or `-uo` where failures are handled),
  tabs, ShellCheck clean, a comment header that is also the `-h` help.
- **JavaScript**: `app.js` (the page) and `palette.js` (pure palette
  arithmetic), no framework, no bundler, no CDN; elements
  through `h()`; in-app dialogs (`askText`, `askConfirm`), never the
  browser's `prompt` or `confirm`; 2-space indentation.
- **Palette arithmetic is duplicated on purpose**: `webapp/static/palette.js`
  mirrors `pxtool.py` (`tune_rgb`, `relight`, `apply_palette`) so the
  preview equals the output, rounding included. Change both together;
  `tests/test_palette_js.py` compares them pixel for pixel.
- **Paths from the browser** go through `safe_path`; writes only to the
  output, model and state folders.
- **ComfyUI**: built-in nodes only.
- **Text**: user-facing text in plain English, short sentences, no jargon
  that the guide does not explain.

## Docs to keep in sync

When behaviour changes, update in the same commit: `docs/GUIDE.md` (what
users see), `docs/ARCHITECTURE.md` (formats, pipelines, data layout),
`docs/INSTALL.md` (installation, settings, environment variables),
`README.md` (features, commands), and always `CHANGELOG.md`. When these
rules change, edit this file in a `docs(claude)` commit.
