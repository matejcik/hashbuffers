# Project: Hashbuffers Wire Format

Refer to [wire-format.md](wire-format.md) for the specification. That specification is the source of truth for the project.
Generally, code SHOULD be updated to match the spec, NOT vice versa.

## Build & Test

- This project uses `uv` for dependency management and running commands.
- Run tests: `uv run pytest tests/ -v`
- Run a single test file: `uv run pytest tests/test_foo.py -v`
- All Python commands should be prefixed with `uv run`.

## Style & Type Checking

Run all style/type checks at once:

```
make style
```

This runs in order:
1. `uv run black src/ tests/` — code formatting
2. `uv run isort src/ tests/` — import sorting
3. `uv run pyright` — static type checking
