# Project: Hashbuffers Wire Format

Refer to [wire-format.md](wire-format.md) for the specification. That specification is the source of truth for the project.
Generally, code SHOULD be updated to match the spec, NOT vice versa.

## Build & Test

- This project uses `uv` for dependency management and running commands.
- Run tests: `uv run pytest tests/ -v`
- Run a single test file: `uv run pytest tests/test_foo.py -v`
- All Python commands should be prefixed with `uv run`.

## Style & Type Checking

Run all style/type checks at once: `make style`

This runs, in order: `black`, `isort`, and `pyright`.

Ignore the warning coming from `black` about not being able to format for 3.14.

## Codebase Map

See [.claude/CODEBASE_MAP.md](.claude/CODEBASE_MAP.md) for a full map of source
files, test organization, and key concepts. Use it to navigate directly to the
right file instead of exploring from scratch. Make sure to update it when you make changes!
If the map is not present (missing from checkout), notify the user and offer to generate it.

## Spec changes

When spec is updated, refer to [SPEC_UPDATE_CHECKLIST.md](SPEC_UPDATE_CHECKLIST.md) to update the codebase.
