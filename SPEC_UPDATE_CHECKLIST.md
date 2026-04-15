# Spec Update Checklist

When `wire-format.md` is modified, use this checklist to propagate changes through the codebase.

## 1. Understand the change

- [ ] Identify which spec sections changed (diff `wire-format.md`)
- [ ] Classify the change: new feature, behavioral change, constraint change, clarification-only
- [ ] If clarification-only with no behavioral impact, skip to step 6

## 2. Update core codec (`src/hashbuffers/codec.py`)

Changes to block encoding, headers, tagged values, links, or validation rules.

- [ ] `Tagged16` — tag bit layout, parameter/number field sizes
- [ ] `BlockType` enum — block type codes (TABLE, DATA, SLOTS, LINKS)
- [ ] `VTableEntryType` enum — entry type codes (NULL, INLINE, DIRECT, BLOCK, LINK)
- [ ] `VTableEntry` — vtable entry encoding/decoding
- [ ] `Link` — digest size, limit field size (currently 36 bytes: 32 + 4)
- [ ] `TableBlock` / `DataBlock` / `SlotsBlock` / `LinksBlock` — block structure, validation
- [ ] `Block.validate()` methods — alignment, size, and structural constraints
- [ ] `Reader` / `Writer` — low-level read/write primitives

Also check: `src/hashbuffers/util.py` — padding and alignment helpers.

## 3. Update fitting and array logic

Changes to how values are placed in blocks, or how arrays/link trees work.

### Fitting (`src/hashbuffers/fitting.py`)
- [ ] `InlineIntEntry` — inline value range (currently 13-bit)
- [ ] `DirectEntry` — heap placement and alignment
- [ ] `BlockEntry` / `LinkEntry` — sub-block and link placement
- [ ] `Table.fit_table()` — packing algorithm, max block size (currently 8 KiB)
- [ ] Alignment scoring and entry ordering

### Arrays (`src/hashbuffers/arrays.py`)
- [ ] `LinkTree` — link tree navigation, binary search
- [ ] `build_data_array()` / `build_bytestring_array()` / `build_table_array()` — array construction
- [ ] `build_bytestring_tree()` — link tree construction
- [ ] `DataArray` / `BytestringArray` / `TableArray` / `BytestringTree` — array readers
- [ ] Limit encoding (cumulative vs individual)

## 4. Update data model and schema layers

Changes to type system, struct encoding, or schema semantics.

### Data model (`src/hashbuffers/data_model/`)
- [ ] `abc.py` — `FieldType`, `FixedFieldType`, `BlockDecoderType` interfaces
- [ ] `primitive.py` — integer/float types and their sizes
- [ ] `struct.py` — `StructType`, `LazyStructMapping`, field indexing
- [ ] `array.py` — `FixedArrayType`, `DataArrayType`, `BytestringType`, `BytestringArrayType`, `BlockArrayType`
- [ ] `adapter.py` — type adapters (Bool, String, Enum conversions)

### Schema DSL (`src/hashbuffers/schema.py`)
- [ ] `Field` descriptor, `HashBuffer` base class
- [ ] `Struct` decorator, `Array`, `Bytes`, `String`, `Bool`, `EnumType`
- [ ] `encode()` / decode logic

### Schema JSON (`src/hashbuffers/schema_json.py`)
- [ ] `dump_schema()` / `load_schema()` — if schema serialization format changed

### Block store (`src/hashbuffers/store.py`)
- [ ] `BlockStore` — hashing algorithm, HMAC key handling, verification

## 5. Update tests

### Find affected tests

Map spec changes to test files:

| Spec area | Test files |
|---|---|
| Block encoding/structure | `tests/test_block_base.py`, `tests/test_table_block.py`, `tests/test_data_block.py`, `tests/test_slots_block.py`, `tests/test_links_block.py` |
| Primitives | `tests/test_primitives.py`, `tests/data_model/test_primitive.py` |
| Alignment / padding | `tests/test_util.py`, `tests/test_fitting.py` |
| Table fitting | `tests/test_fitting.py` |
| Block store / hashing | `tests/test_store.py` |
| Arrays (building) | `tests/arrays/test_build.py` |
| Arrays (reading) | `tests/arrays/test_data_array.py`, `tests/arrays/test_bytestring_array.py`, `tests/arrays/test_table_array.py` |
| Link trees | `tests/arrays/test_linktree.py`, `tests/arrays/test_linktree_reduce.py`, `tests/arrays/test_bytestring_tree.py` |
| Data model types | `tests/data_model/test_*.py` |
| Schema DSL | `tests/schema/test_*.py` |
| Schema JSON | `tests/test_schema_json.py` |
| Trezor bridge | `tests/test_trezorproto.py` |

### Update tests

- [ ] Modify existing tests to match new spec behavior
- [ ] Add new tests for any new spec features or constraints
- [ ] Add negative tests for any new invalid states the spec defines

### Regenerate test vectors

If the change affects encoding, block layout, or validation:

```bash
uv run python vectors_json/generate_vectors.py
```

- [ ] Update `vectors_json/generate_vectors.py` if vector generation logic needs changes
- [ ] Regenerate `vectors_json/positive.json` and `vectors_json/negative.json`
- [ ] Verify vectors pass: `uv run pytest tests/test_vectors_json.py -v`

## 6. Verify everything

Run in this order:

```bash
# Format and type-check
make style

# Run full test suite with coverage
make test

# Check coverage report if needed
make coverage
```

- [ ] `make style` passes (black, isort, pyright — ignore black's 3.14 warning)
- [ ] `make test` passes (all tests green, coverage >= 80%)
- [ ] No pyright type errors introduced

## 7. Update supporting docs

- [ ] `explained.md` — if the high-level explanation needs updating
- [ ] `CLAUDE.md` — if build/test instructions changed
- [ ] `README.md` — if project overview needs updating

## Quick reference: commands

```bash
# Style & type checking
make style

# All tests
uv run pytest tests/ -v

# Specific test file
uv run pytest tests/test_fitting.py -v

# Regenerate test vectors
uv run python vectors_json/generate_vectors.py

# Full test suite with coverage
make test

# HTML coverage report
make coverage
```
