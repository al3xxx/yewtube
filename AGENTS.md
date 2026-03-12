# Repository Guidelines

## Project structure & module organization
This repository is a Python CLI application packaged as `yewtube`.
Core code lives in `mps_youtube/`, with command handlers in
`mps_youtube/commands/`, player backends in `mps_youtube/players/`, and
list rendering helpers in `mps_youtube/listview/`. Project configuration is in
`pyproject.toml`, dependency locking is in `uv.lock`, and packaging metadata is
in `yewtube.egg-info/`.

## Build, test, and development commands
Use `uv` for local workflows.

- `uv sync` installs runtime and dev dependencies from `pyproject.toml` and
  `uv.lock`.
- `uv run yt` runs the CLI entry point (`yt = mps_youtube.main:main`).
- `uv run ruff check .` runs lint checks.
- `uv run ruff check . --fix` auto-fixes safe lint issues.
- `uv run python -m mps_youtube.main` runs the app directly during debugging.

## Coding style & naming conventions
Follow the existing Python style in `mps_youtube/`.

- Use 4-space indentation and keep lines readable around the configured
  80-character target.
- Keep module and function names `snake_case`, classes `PascalCase`, and
  constants `UPPER_SNAKE_CASE`.
- Prefer small, focused command modules under `mps_youtube/commands/`.
- Run `ruff` before committing; lint rules are configured in
  `pyproject.toml`.

## Testing guidelines
There is currently no dedicated automated test suite in this repository.

- Add new tests under a top-level `tests/` directory using `test_*.py` naming.
- For behavior changes, include at least one automated test or document manual
  verification steps in your pull request.
- Minimum manual check: run `uv run yt` and verify the changed command path.

## Commit & pull request guidelines
The current Git history uses short, imperative commit messages
(for example, `Capture current state for review`, `initial commit`).

- Write concise, imperative commits focused on one logical change.
- In pull requests, include: what changed, why, and how you validated it.
- Link related issues when available and include terminal output or screenshots
  for user-visible CLI behavior changes.
