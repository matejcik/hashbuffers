# Hashbuffers Codebase Map

Quick-reference for navigating the project. Read this instead of exploring from scratch.

## Architecture Overview

Hashbuffers is a content-addressed wire format that splits data into ≤8 KiB blocks linked by HMAC-SHA256 digests. This Python implementation provides encode/decode for the format, with a schema DSL on top.

**Layer stack** (bottom to top):

1. **codec.py** — Raw block types: `TableBlock`, `DataBlock`, `SlotsBlock`, `LinksBlock`, plus `Tagged16`, `VTableEntry`, `Link`, `Reader`, `Writer`
2. **util.py** — `padded_element_size`, `pack_flat_array`, `unpack_flat_array`, `bit_length`
3. **store.py** — `BlockStore`: content-addressed storage with HMAC-SHA256
4. **fitting.py** — Packing fields into TABLE blocks: `Table`, `InlineIntEntry`, `DirectEntry`, `BlockEntry`, `LinkEntry`, `NullEntry`
5. **arrays.py** — Array representations + link trees: `LinkTree`, `DataArray`, `BytestringArray`, `TableArray`, `BytestringTree`, plus builders `build_data_array`, `build_bytestring_array`, `build_table_array`, `build_bytestring_tree`, `linktree_reduce`
6. **data_model/** — Typed field encode/decode layer
7. **schema.py** — DSL: `HashBuffer` base class, `Field` descriptor, `Array()`, `Bool`, `String`, `Bytes`, `EnumType`
8. **schema_json.py** — JSON schema serialization: `dump_schema`, `load_schema`, `LoadedSchema`
9. **trezorproto.py** — Bridge from trezorlib protobuf `MessageType` to hashbuffers

## Source Files

### `src/hashbuffers/codec.py`
Core binary format. Block types as an enum (`TABLE=0, DATA=1, SLOTS=2, LINKS=3`). Each block has `encode()`, `decode()`, `validate()`, `alignment()`. Key constants: `SIZE_MAX = 0x1FFF` (8191).

- `TableBlock` — vtable (list of `VTableEntry`) + heap bytes. VTable entry types: `NULL`, `DIRECTDATA`, `INLINE`, `DIRECT4`, `DIRECT8`, `BLOCK`, `LINK`
- `DataBlock` — self-describing flat array: has `elem_size` and `elem_align` fields in an `elem_info` t16 header. Used for primitive arrays and bytestrings
- `SlotsBlock` — variable-length entries (offsets array + heap), used for bytestring arrays
- `LinksBlock` — array of `Link` (digest + cumulative limit), used for link trees. Has `depth` field (3 bits, max `DEPTH_MAX=4`) bounding tree traversal depth, plus 13 reserved bits. Minimum 2 links.
- `Link` — 36 bytes: 32-byte digest + u32 limit

### `src/hashbuffers/fitting.py`
Table packing algorithm. `Table.fit()` tries `alignment_pack()`, outlinking largest blocks if they don't fit. Entry types: `InlineIntEntry` (≤13-bit values), `DirectEntry` (4 or 8 bytes on heap), `DirectDataEntry` (alignment-1 data with t16 header), `BlockEntry` (embedded sub-block), `LinkEntry` (36-byte link), `NullEntry`.

### `src/hashbuffers/arrays.py`
**Read side**: `LinkTree` navigates LINKS blocks via binary search. `DataArray`, `BytestringArray`, `TableArray` extend `TreeArray` (implements `Sequence[T]`). `BytestringTree` handles single large bytestrings split across blocks.

**Write side**: `build_data_array()`, `build_bytestring_array()`, `build_table_array()`, `build_bytestring_tree()` chunk data into blocks and call `linktree_reduce()` to build the tree.

### `src/hashbuffers/data_model/`
- **abc.py** — `FieldType[T]` (encode/decode), `FixedFieldType[T]` (+ get_size/get_alignment/encode_bytes/decode_bytes), `BlockDecoderType[T]` (+ block_decoder)
- **primitive.py** — `PrimitiveInt`, `PrimitiveFloat`. Singletons: `U8`, `U16`, `U32`, `U64`, `I8`, `I16`, `I32`, `I64`, `F32`, `F64`
- **array.py** — `FixedArrayType` (known count, stored as DATA block), `DataArrayType` (variable count of fixed-size elements), `BytestringArrayType` (variable-length byte entries), `BlockArrayType` (array of structs)
- **struct.py** — `StructType`, `StructField`, `LazyStructMapping`
- **adapter.py** — `AdapterCodec[Outer, Inner]` for type transformations

### `src/hashbuffers/schema.py`
User-facing DSL. `HashBuffer` subclasses use `Field(index, type)` descriptors. `Array(element, count=)` dispatches to the right array type. Adapters: `Bool` (U8→bool), `String` (Bytes→str), `EnumType(cls, repr)`.

### `src/hashbuffers/schema_json.py`
Dump/load schema as JSON. `dump_schema(root_cls)` walks `HashBuffer` hierarchy. `load_schema(dict)` builds `StructType` trees with topo-sorted struct resolution. `LoadedSchema` has `decode_root()`.

### `src/hashbuffers/store.py`
Simple `BlockStore(key)`: `store(block) → digest`, `fetch(digest) → Block`. HMAC-SHA256 with caller-provided key. In-memory dict backend.

### `src/hashbuffers/trezorproto.py`
Converts trezorlib `protobuf.MessageType` ↔ hashbuffers. Maps protobuf field tags to vtable indices (tag-1). Type mapping: uint32→U32, sint32→I32, enum→EnumType(U16), message→recursive.

## Test Organization

### Unit tests (by module)
| Test file | Tests for |
|---|---|
| `tests/test_block_base.py` | `Block` base class, header encode/decode |
| `tests/test_table_block.py` | `TableBlock` encode/decode/validate, vtable access, heap reads |
| `tests/test_data_block.py` | `DataBlock` encode/decode, array packing |
| `tests/test_slots_block.py` | `SlotsBlock` encode/decode/validate |
| `tests/test_links_block.py` | `LinksBlock` encode/decode/validate |
| `tests/test_primitives.py` | `Tagged16`, `VTableEntry`, `Link` encode/decode |
| `tests/test_util.py` | `padded_element_size`, `pack_flat_array`, `unpack_flat_array`, `bit_length` |
| `tests/test_fitting.py` | Table packing, alignment, outlink logic |
| `tests/test_store.py` | `BlockStore` store/fetch/HMAC verification |

### Array tests (`tests/arrays/`)
| Test file | Tests for |
|---|---|
| `tests/arrays/conftest.py` | Shared fixtures: `store`, helper builders |
| `tests/arrays/test_build.py` | `build_*` functions (bytestring_tree, data_array, bytestring_array, table_array) |
| `tests/arrays/test_data_array.py` | `DataArray` read: len, getitem, slices, equality |
| `tests/arrays/test_bytestring_array.py` | `BytestringArray` read + oversized elements |
| `tests/arrays/test_bytestring_tree.py` | `BytestringTree` leaf_length |
| `tests/arrays/test_table_array.py` | `TableArray` read |
| `tests/arrays/test_linktree.py` | `LinkTree` find_leaf, collect_leaves, size mismatches |
| `tests/arrays/test_linktree_reduce.py` | `linktree_reduce` edge cases |
| `tests/arrays/test_helpers.py` | `limits_to_cumulative`, `limits_to_individual` |

### Data model tests (`tests/data_model/`)
| Test file | Tests for |
|---|---|
| `tests/data_model/conftest.py` | Shared fixtures |
| `tests/data_model/test_primitive.py` | `PrimitiveInt`, `PrimitiveFloat` encode/decode |
| `tests/data_model/test_fixed_array_type.py` | `FixedArrayType` init, encode_bytes, decode from blocks/links |
| `tests/data_model/test_var_array_types.py` | `DataArrayType`, `BytestringArrayType`, `BlockArrayType` |
| `tests/data_model/test_struct.py` | `StructType`, `LazyStructMapping` |
| `tests/data_model/test_adapter.py` | `AdapterCodec` |
| `tests/data_model/test_bytestring_type.py` | `BytestringType` |

### Schema tests (`tests/schema/`)
| Test file | Tests for |
|---|---|
| `tests/schema/conftest.py` | Shared fixtures, sample `HashBuffer` subclasses |
| `tests/schema/test_struct.py` | `HashBuffer` encode/decode roundtrip |
| `tests/schema/test_field_types.py` | `Field` descriptor, type resolution |
| `tests/schema/test_arrays.py` | `Array()` dispatch to correct array type |
| `tests/schema/test_fixed_arrays.py` | Fixed-count arrays via schema DSL |
| `tests/schema/test_adapters.py` | `Bool`, `String`, `EnumType` adapters |
| `tests/schema/test_decode.py` | Decode edge cases |
| `tests/schema/test_lazy.py` | Lazy LINK resolution |

### Integration tests
| Test file | Tests for |
|---|---|
| `tests/test_vectors_json.py` | JSON test vectors (positive: decode + encode roundtrip + exact bytes; negative: expected failures) |
| `tests/test_schema_json.py` | `dump_schema` / `load_schema` roundtrip |
| `tests/test_schema_coverage.py` | Schema coverage checks |
| `tests/test_trezorproto.py` | trezorlib ↔ hashbuffers bridge |

## JSON Test Vectors

Located in `vectors_json/`. Generated by `vectors_json/generate_vectors.py`.

### Positive vectors (`positive.json`)
Each vector has: `name`, `schema` (JSON schema dict), `message` (JSON data), `store_key`, `root_digest`, `store` (digest→hex block map).

Tests: `test_decode` (store→decode→compare), `test_encode_roundtrip` (encode→decode→compare), `test_encode_exact` (encode→compare bytes+digests).

Vector names: `inline_int`, `all_primitives`, `signed_negative`, `bool_field`, `string_field`, `bytes_field`, `enum_default_repr`, `enum_u16_repr`, `nested_struct`, `all_null`, `required_fields`, `fixed_array`, `var_data_array`, `string_array`, `bytes_array`, `struct_array`, `struct_array_with_count`, `large_data_outlink`, `large_array_linktree`, `nested_struct_outlink`, `sparse_indices`

### Negative vectors (`negative.json`)
Each vector has same shape plus `error` description. Test: `test_decode_fails` (decode must raise).

Vector names: `reserved_header_bit`, `table_too_small`, `vtable_reserved_bits`, `entry_offset_oob`, `link_not_4_aligned`, `link_limit_zero`, `block_not_2_aligned`, `subblock_exceeds_parent`, `slots_bad_sentinel`, `slots_decreasing_offsets`, `links_reserved_nonzero`, `links_not_increasing`, `wrong_block_type`, `direct4_not_4_aligned`, `direct8_not_8_aligned`, `fixed_array_block_misaligned`, `directdata_params_nonzero`, `directdata_length_exceeds_block`

## Key Concepts Quick Reference

- **Inline**: value ≤ 13 bits stored directly in vtable entry (no heap space)
- **DirectData**: alignment-1 data on heap with t16 length header (2 bytes overhead vs 4 for BLOCK+DATA)
- **Direct4/Direct8**: 4 or 8 byte value on heap, alignment-constrained
- **Block entry**: sub-block embedded on heap (any block type)
- **Link entry**: 36-byte hash reference to an external block
- **Outlink**: converting an embedded block to a link when it doesn't fit
- **Link tree**: recursive `LinksBlock` structure for arrays > 8 KiB. Limits are cumulative. Max depth 4 (5 levels of LINKS blocks), enforced by depth countdown field.
- **FixedArray**: known count of fixed-size elements in a single DATA block
- **DataArray**: variable count of fixed-size elements, possibly in a link tree
- **BytestringArray**: variable-length entries via SLOTS blocks, possibly in a link tree
- **TableArray**: array of structs via TABLE blocks, possibly in a link tree
