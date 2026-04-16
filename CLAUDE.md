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

## Coverage Review Protocol

When reviewing test coverage, check that each module is covered **by its own dedicated
tests** at ~95%. This ensures functionality is exercised at the right level, not
incidentally through higher-level integration tests.

Run per-module coverage like this:

```bash
uv run pytest tests/<test_files> --cov=src/hashbuffers/<module> --cov-report=term-missing -q
```

The module → test mapping:

| Source module | Dedicated tests |
|---|---|
| `codec/base.py` | `tests/test_block_base.py` |
| `codec/data.py` | `tests/test_data_block.py` |
| `codec/links.py` | `tests/test_links_block.py` |
| `codec/slots.py` | `tests/test_slots_block.py` |
| `codec/table.py` | `tests/test_table_block.py` |
| `codec/io.py` | (covered transitively by other codec tests) |
| `data_model/*` | `tests/data_model/` |
| `arrays.py` | `tests/arrays/` |
| `fitting.py` | `tests/test_fitting.py` |
| `schema.py` | `tests/schema/`, `tests/test_schema_coverage.py` |
| `schema_json.py` | `tests/test_schema_json.py` |
| `store.py` | `tests/test_store.py` |
| `trezorproto.py` | `tests/test_trezorproto.py` |
| `util.py` | `tests/test_util.py` |

The target is ~95% coverage per module from its dedicated tests. Use judgement: if
coverage falls below that, it's fine as long as nothing important is missed.

## Spec changes

When spec is updated, refer to [SPEC_UPDATE_CHECKLIST.md](SPEC_UPDATE_CHECKLIST.md) to update the codebase.
