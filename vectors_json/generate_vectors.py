#!/usr/bin/env python3
"""Generate JSON test vectors for the hashbuffers wire format.

Run from the project root:
    uv run python vectors_json/generate_vectors.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import typing as t
from enum import IntEnum
from pathlib import Path

from hashbuffers.codec import (
    BlockType,
    Tagged16,
)
from hashbuffers.codec.table import TableEntryRaw, TableEntryType
from hashbuffers.schema import (
    U8,
    U16,
    U32,
    U64,
    I8,
    I16,
    I32,
    I64,
    F32,
    F64,
    Array,
    Bool,
    Bytes,
    EnumType,
    Field,
    HashBuffer,
    String,
)
from hashbuffers.schema_json import dump_schema
from hashbuffers.store import BlockStore

STORE_KEY = b"test-vectors"
OUTPUT_DIR = Path(__file__).parent


# ---- Helpers ----


def make_store() -> BlockStore:
    return BlockStore(STORE_KEY)


def encode_and_capture(
    obj: HashBuffer, store: BlockStore
) -> bytes:
    """Encode obj, store root block in store, return root digest."""
    root_bytes = obj.encode(store)
    return store.store_bytes(root_bytes)


def store_to_hex(store: BlockStore) -> dict[str, str]:
    return {k.hex(): v.hex() for k, v in store.blocks.items()}


def positive(
    name: str,
    description: str,
    root_cls: type[HashBuffer],
    obj: HashBuffer,
    message: dict[str, t.Any],
) -> dict[str, t.Any]:
    store = make_store()
    root_digest = encode_and_capture(obj, store)
    return {
        "name": name,
        "description": description,
        "schema": dump_schema(root_cls),
        "message": message,
        "store_key": STORE_KEY.hex(),
        "root_digest": root_digest.hex(),
        "store": store_to_hex(store),
    }


def hmac_digest(data: bytes) -> bytes:
    return hmac.new(STORE_KEY, data, hashlib.sha256).digest()


def negative(
    name: str,
    description: str,
    schema: dict[str, t.Any],
    root_bytes: bytes,
    error: str,
    extra_blocks: list[bytes] | None = None,
) -> dict[str, t.Any]:
    store_dict: dict[str, str] = {}
    if extra_blocks:
        for block_bytes in extra_blocks:
            d = hmac_digest(block_bytes)
            store_dict[d.hex()] = block_bytes.hex()
    root_digest = hmac_digest(root_bytes)
    store_dict[root_digest.hex()] = root_bytes.hex()
    return {
        "name": name,
        "description": description,
        "schema": schema,
        "store_key": STORE_KEY.hex(),
        "root_digest": root_digest.hex(),
        "store": store_dict,
        "error": error,
    }


def build_table_bytes(
    entries: list[TableEntryRaw], heap: bytes
) -> bytes:
    """Manually build TABLE block bytes."""
    vtable_count = len(entries)
    size = 4 + 2 * vtable_count + len(heap)
    header = BlockType.TABLE.encode(size)
    vtable_hdr = Tagged16(0, vtable_count).encode()
    entry_bytes = b"".join(e.encode() for e in entries)
    return header + vtable_hdr + entry_bytes + heap


SIMPLE_SCHEMA = {
    "version": 1,
    "root": "Root",
    "structs": {
        "Root": {
            "fields": [{"index": 0, "name": "x", "type": "u32"}],
        }
    },
}


# ============================================================
# Positive test vectors
# ============================================================


def gen_positive() -> list[dict[str, t.Any]]:
    vectors: list[dict[str, t.Any]] = []

    # 1. inline_int: small u8 stored INLINE in vtable
    class InlineInt(HashBuffer):
        x: int | None = Field(0, U8)

    vectors.append(
        positive(
            "inline_int",
            "Small u8 value (42) stored INLINE in vtable entry",
            InlineInt,
            InlineInt(x=42),
            {"x": 42},
        )
    )

    # 2. all_primitives: every primitive type
    class AllPrims(HashBuffer):
        a: int | None = Field(0, U8)
        b: int | None = Field(1, U16)
        c: int | None = Field(2, U32)
        d: int | None = Field(3, U64)
        e: int | None = Field(4, I8)
        f: int | None = Field(5, I16)
        g: int | None = Field(6, I32)
        h: int | None = Field(7, I64)
        i: float | None = Field(8, F32)
        j: float | None = Field(9, F64)

    vectors.append(
        positive(
            "all_primitives",
            "All 10 primitive types with representative values",
            AllPrims,
            AllPrims(
                a=255,
                b=65535,
                c=100_000,
                d=1_000_000_000_000,
                e=-1,
                f=-1000,
                g=-100_000,
                h=-1_000_000_000_000,
                i=1.5,
                j=-2.25,
            ),
            {
                "a": 255,
                "b": 65535,
                "c": 100_000,
                "d": 1_000_000_000_000,
                "e": -1,
                "f": -1000,
                "g": -100_000,
                "h": -1_000_000_000_000,
                "i": 1.5,
                "j": -2.25,
            },
        )
    )

    # 3. signed_negative: signed ints with negative values
    class SignedNeg(HashBuffer):
        a: int | None = Field(0, I8)
        b: int | None = Field(1, I16)
        c: int | None = Field(2, I32)
        d: int | None = Field(3, I64)

    vectors.append(
        positive(
            "signed_negative",
            "Signed integer types with negative values, including extremes",
            SignedNeg,
            SignedNeg(a=-128, b=-32768, c=-2_147_483_648, d=-9_223_372_036_854_775_808),
            {"a": -128, "b": -32768, "c": -2_147_483_648, "d": -9_223_372_036_854_775_808},
        )
    )

    # 4. bool_field
    class BoolStruct(HashBuffer):
        flag: bool | None = Field(0, Bool)
        other: bool | None = Field(1, Bool)

    vectors.append(
        positive(
            "bool_field",
            "Bool fields (true and false) stored as adapted U8",
            BoolStruct,
            BoolStruct(flag=True, other=False),
            {"flag": True, "other": False},
        )
    )

    # 5. string_field
    class StringStruct(HashBuffer):
        name: str | None = Field(0, String)

    vectors.append(
        positive(
            "string_field",
            "String field encoded as UTF-8 bytestring",
            StringStruct,
            StringStruct(name="hello world"),
            {"name": "hello world"},
        )
    )

    # 6. bytes_field
    class BytesStruct(HashBuffer):
        data: bytes | None = Field(0, Bytes)

    vectors.append(
        positive(
            "bytes_field",
            "Raw bytes field stored as bytestring",
            BytesStruct,
            BytesStruct(data=b"\xde\xad\xbe\xef"),
            {"data": "deadbeef"},
        )
    )

    # 7. enum_default_repr
    class Color(IntEnum):
        RED = 0
        GREEN = 1
        BLUE = 2

    class EnumDefault(HashBuffer):
        color: Color | None = Field(0, EnumType(Color))

    vectors.append(
        positive(
            "enum_default_repr",
            "Enum with default u8 repr",
            EnumDefault,
            EnumDefault(color=Color.GREEN),
            {"color": "GREEN"},
        )
    )

    # 8. enum_u16_repr
    class Priority(IntEnum):
        LOW = 1
        MEDIUM = 2
        HIGH = 3

    class EnumU16(HashBuffer):
        priority: Priority | None = Field(0, EnumType(Priority, repr=U16))

    vectors.append(
        positive(
            "enum_u16_repr",
            "Enum with u16 repr",
            EnumU16,
            EnumU16(priority=Priority.HIGH),
            {"priority": "HIGH"},
        )
    )

    # 9. nested_struct
    class Inner(HashBuffer):
        value: int | None = Field(0, U8)

    class Nested(HashBuffer):
        inner: Inner | None = Field(0, Inner)
        tag: int | None = Field(1, U16)

    vectors.append(
        positive(
            "nested_struct",
            "Struct containing another struct as BLOCK entry",
            Nested,
            Nested(inner=Inner(value=99), tag=7),
            {"inner": {"value": 99}, "tag": 7},
        )
    )

    # 10. all_null
    class AllNull(HashBuffer):
        a: int | None = Field(0, U32)
        b: str | None = Field(1, String)
        c: bytes | None = Field(2, Bytes)

    vectors.append(
        positive(
            "all_null",
            "All optional fields absent (NULL vtable entries)",
            AllNull,
            AllNull(),
            {},
        )
    )

    # 11. required_fields
    class Required(HashBuffer):
        name: bytes = Field(0, Bytes, required=True)
        value: int = Field(1, U32, required=True)

    vectors.append(
        positive(
            "required_fields",
            "Struct with required fields",
            Required,
            Required(name=b"test", value=42),
            {"name": "74657374", "value": 42},
        )
    )

    # 12. fixed_array: u32[3] as FixedArrayType
    class FixedArr(HashBuffer):
        vec: t.Sequence[int] | None = Field(0, Array(U32, count=3))

    vectors.append(
        positive(
            "fixed_array",
            "Fixed-count array u32[3] stored as BLOCK of DATA on heap",
            FixedArr,
            FixedArr(vec=[10, 20, 30]),
            {"vec": [10, 20, 30]},
        )
    )

    # 13. var_data_array: u32[] as DataArrayType
    class VarDataArr(HashBuffer):
        ids: t.Sequence[int] | None = Field(0, Array(U32))

    vectors.append(
        positive(
            "var_data_array",
            "Variable-length array u32[] stored as DATA BLOCK",
            VarDataArr,
            VarDataArr(ids=[100, 200, 300, 400, 500]),
            {"ids": [100, 200, 300, 400, 500]},
        )
    )

    # 14. string_array: str[]
    class StringArr(HashBuffer):
        names: t.Sequence[str] | None = Field(0, Array(String))

    vectors.append(
        positive(
            "string_array",
            "Array of strings stored as SLOTS BLOCK",
            StringArr,
            StringArr(names=["alice", "bob", "charlie"]),
            {"names": ["alice", "bob", "charlie"]},
        )
    )

    # 15. bytes_array: bytes[]
    class BytesArr(HashBuffer):
        blobs: t.Sequence[bytes] | None = Field(0, Array(Bytes))

    vectors.append(
        positive(
            "bytes_array",
            "Array of byte strings stored as SLOTS BLOCK",
            BytesArr,
            BytesArr(blobs=[b"\x01\x02", b"\x03\x04\x05", b""]),
            {"blobs": ["0102", "030405", ""]},
        )
    )

    # 16. struct_array: Inner[]
    class StructArr(HashBuffer):
        items: t.Sequence[Inner] | None = Field(0, Array(Inner))

    vectors.append(
        positive(
            "struct_array",
            "Variable-length array of structs stored as TABLE BLOCK",
            StructArr,
            StructArr(items=[Inner(value=1), Inner(value=2), Inner(value=3)]),
            {"items": [{"value": 1}, {"value": 2}, {"value": 3}]},
        )
    )

    # 17. struct_array_with_count: Inner[3]
    class StructArrCount(HashBuffer):
        items: t.Sequence[Inner] | None = Field(0, Array(Inner, count=3))

    vectors.append(
        positive(
            "struct_array_with_count",
            "Fixed-count array of structs Inner[3]",
            StructArrCount,
            StructArrCount(
                items=[Inner(value=10), Inner(value=20), Inner(value=30)]
            ),
            {"items": [{"value": 10}, {"value": 20}, {"value": 30}]},
        )
    )

    # 18. large_data_outlink: bytes field large enough to outlink
    class LargeData(HashBuffer):
        tag: int | None = Field(0, U32)
        data: bytes | None = Field(1, Bytes)

    large_bytes = bytes(range(256)) * 32  # 8192 bytes, forces outlink
    vectors.append(
        positive(
            "large_data_outlink",
            "Large bytes field (8192 bytes) that outlinks from root TABLE as LINK entry",
            LargeData,
            LargeData(tag=1, data=large_bytes),
            {"tag": 1, "data": large_bytes.hex()},
        )
    )

    # 19. large_array_linktree: array spanning multiple DATA blocks
    class LargeArray(HashBuffer):
        values: t.Sequence[int] | None = Field(0, Array(U32))

    # ~3000 u32s: each DATA block holds ~2046 elements (with 4-byte alignment)
    large_arr = list(range(3000))
    vectors.append(
        positive(
            "large_array_linktree",
            "Array of 3000 u32 values spanning multiple DATA blocks with LINKS tree",
            LargeArray,
            LargeArray(values=large_arr),
            {"values": large_arr},
        )
    )

    # 20. nested_struct_outlink: nested struct forced to outlink
    class BigInner(HashBuffer):
        data: bytes | None = Field(0, Bytes)

    class OutlinkNested(HashBuffer):
        a: BigInner | None = Field(0, BigInner)
        b: BigInner | None = Field(1, BigInner)

    big_data = bytes(range(256)) * 16  # 4096 bytes each
    vectors.append(
        positive(
            "nested_struct_outlink",
            "Two nested structs each with ~4KB data, one outlinks as LINK entry",
            OutlinkNested,
            OutlinkNested(
                a=BigInner(data=big_data), b=BigInner(data=big_data)
            ),
            {"a": {"data": big_data.hex()}, "b": {"data": big_data.hex()}},
        )
    )

    # 21. sparse_indices: non-contiguous field indices with NULL gaps
    class Sparse(HashBuffer):
        first: int | None = Field(0, U8)
        third: int | None = Field(3, U16)
        fifth: int | None = Field(5, U32)

    vectors.append(
        positive(
            "sparse_indices",
            "Non-contiguous field indices (0, 3, 5) with NULL gaps in vtable",
            Sparse,
            Sparse(first=1, third=2, fifth=3),
            {"first": 1, "third": 2, "fifth": 3},
        )
    )

    # 22. empty_array: zero-length variable-size array
    class EmptyArr(HashBuffer):
        ids: t.Sequence[int] | None = Field(0, Array(U32))

    vectors.append(
        positive(
            "empty_array",
            "Empty variable-length array u32[] stored as BLOCK (cannot be linked because limit=0 is reserved)",
            EmptyArr,
            EmptyArr(ids=[]),
            {"ids": []},
        )
    )

    # 23. inline_edge_values: INLINE vs DIRECT4 boundary
    # INLINE holds 13-bit values: unsigned 0..8191, signed -4096..4095
    class InlineEdge(HashBuffer):
        max_inline_u: int | None = Field(0, U32)  # 8191 → INLINE
        overflow_u: int | None = Field(1, U32)  # 8192 → DIRECT4
        max_inline_s: int | None = Field(2, I32)  # 4095 → INLINE
        min_inline_s: int | None = Field(3, I32)  # -4096 → INLINE
        overflow_s_pos: int | None = Field(4, I32)  # 4096 → DIRECT4
        overflow_s_neg: int | None = Field(5, I32)  # -4097 → DIRECT4

    vectors.append(
        positive(
            "inline_edge_values",
            "Values at the INLINE/DIRECT4 boundary (13-bit limit: unsigned 0..8191, signed -4096..4095)",
            InlineEdge,
            InlineEdge(
                max_inline_u=8191,
                overflow_u=8192,
                max_inline_s=4095,
                min_inline_s=-4096,
                overflow_s_pos=4096,
                overflow_s_neg=-4097,
            ),
            {
                "max_inline_u": 8191,
                "overflow_u": 8192,
                "max_inline_s": 4095,
                "min_inline_s": -4096,
                "overflow_s_pos": 4096,
                "overflow_s_neg": -4097,
            },
        )
    )

    # 24. mixed_bytestring_array: SLOTS + TABLE leaves in one link tree
    # Most elements are small (fit in SLOTS), one is oversized (triggers TABLE fallback)
    class MixedBytesArr(HashBuffer):
        blobs: t.Sequence[bytes] | None = Field(0, Array(Bytes))

    small_elems = [b"hello", b"world", b"!"]
    oversized_elem = bytes(range(256)) * 33  # 8448 bytes > 8185 max SLOTS element
    mixed_elems = small_elems + [oversized_elem] + [b"after"]

    vectors.append(
        positive(
            "mixed_bytestring_array",
            "Bytestring array with mix of SLOTS leaves (small elements) and TABLE leaf (oversized element as DATA link tree)",
            MixedBytesArr,
            MixedBytesArr(blobs=mixed_elems),
            {"blobs": [e.hex() for e in mixed_elems]},
        )
    )

    return vectors


# ============================================================
# Negative test vectors
# ============================================================


def gen_negative() -> list[dict[str, t.Any]]:
    vectors: list[dict[str, t.Any]] = []

    # 1. reserved_header_bit: bit 13 set in block header
    valid = build_table_bytes([TableEntryRaw(TableEntryType.NULL, 0)], b"")
    corrupted = bytearray(valid)
    # Header is LE u16 at [0:2]. Set bit 13 (reserved bit in BlockType encoding).
    hdr = int.from_bytes(corrupted[0:2], "little")
    hdr |= 0x2000
    corrupted[0:2] = hdr.to_bytes(2, "little")
    vectors.append(
        negative(
            "reserved_header_bit",
            "Block header has the reserved bit (bit 13) set",
            SIMPLE_SCHEMA,
            bytes(corrupted),
            "Reserved bit set in block header",
        )
    )

    # 2. table_too_small: TABLE with size < 4
    # Manually encode a TABLE header with size=3 + 1 padding byte
    hdr_bytes = BlockType.TABLE.encode(3)
    too_small = hdr_bytes + b"\x00"  # extra byte so there's data to read
    vectors.append(
        negative(
            "table_too_small",
            "TABLE block with declared size 3 (minimum is 4)",
            SIMPLE_SCHEMA,
            too_small,
            "TABLE block too small (size < 4)",
        )
    )

    # 3. vtable_reserved_bits: non-zero reserved bits in vtable header
    # Build a TABLE where the vtable header has parameters != 0
    hdr_bytes = BlockType.TABLE.encode(6)  # header(2) + vtable_hdr(2) + 1 entry(2)
    vtable_hdr = Tagged16(0b001, 1).encode()  # reserved bits set, 1 entry
    entry = TableEntryRaw(TableEntryType.NULL, 0).encode()
    vectors.append(
        negative(
            "vtable_reserved_bits",
            "TABLE vtable header has non-zero reserved bits",
            SIMPLE_SCHEMA,
            hdr_bytes + vtable_hdr + entry,
            "Reserved bits in TABLE vtable header are not zero",
        )
    )

    # 4. entry_offset_oob: DIRECT4 entry pointing past block end
    heap = b"\x00" * 4
    entry = TableEntryRaw(TableEntryType.DIRECT4, 100)  # offset 100, way past block
    block = build_table_bytes([entry], heap)
    vectors.append(
        negative(
            "entry_offset_oob",
            "DIRECT4 vtable entry with offset 100 in a block of size 10",
            SIMPLE_SCHEMA,
            block,
            "Vtable entry offset out of bounds",
        )
    )

    # 5. link_not_4_aligned: LINK at offset not divisible by 4
    # With 1 entry, heap_start = 6. LINK at offset 6 → 6 % 4 = 2 → error.
    link_data = b"\x00" * 32 + (1).to_bytes(4, "little")  # valid link: dummy digest, limit=1
    heap = link_data + b"\x00" * 2  # padding to fill block
    block = build_table_bytes([TableEntryRaw(TableEntryType.LINK, 6)], heap)
    vectors.append(
        negative(
            "link_not_4_aligned",
            "LINK entry at offset 6 which is not 4-byte aligned",
            SIMPLE_SCHEMA,
            block,
            "LINK entry is not 4-byte aligned",
        )
    )

    # 6. link_limit_zero: LINK with limit=0
    # Need LINK at 4-aligned offset. With 1 entry, heap_start=6. Next 4-aligned=8.
    # So 2 bytes padding + 36 bytes link.
    link_data = b"\x00" * 32 + (0).to_bytes(4, "little")  # limit=0
    heap = b"\x00\x00" + link_data  # 2 pad + 36 link = 38
    block = build_table_bytes([TableEntryRaw(TableEntryType.LINK, 8)], heap)
    vectors.append(
        negative(
            "link_limit_zero",
            "LINK entry with limit=0 (reserved, must be >= 1)",
            SIMPLE_SCHEMA,
            block,
            "Link limit must not be 0",
        )
    )

    # 7. block_inner_alignment: BLOCK at 2-aligned offset but sub-block needs 4-alignment
    # The sub-block is a DATA block of u32 elements (alignment 4).
    # With 1 entry: heap_start = 6. BLOCK at offset 6 → 6 is 2-aligned (valid for
    # general BLOCK) but the DATA sub-block's alignment is 4, so offset must be
    # 4-aligned. This tests TABLE validation step 7.4.
    # DATA sub-block: header(2) + elem_info(2) + one u32(4) = 8 bytes, align=4
    sub_block = BlockType.DATA.encode(8) + Tagged16(2, 4).encode() + b"\x01\x00\x00\x00"  # align=4, size=4, one u32
    heap = sub_block
    block = build_table_bytes([TableEntryRaw(TableEntryType.BLOCK, 6)], heap)
    vectors.append(
        negative(
            "block_inner_alignment",
            "BLOCK entry at offset 6 (2-aligned) but sub-block is DATA with alignment 4",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [
                            {"index": 0, "name": "vec", "type": "u32[1]"},
                        ]
                    },
                },
            },
            block,
            "Sub-block alignment requirement not satisfied",
        )
    )

    # 8. subblock_exceeds_parent: sub-block size extends past parent TABLE
    # Create a sub-block that claims to be larger than remaining space.
    # 1 entry, heap_start=6. Heap has a sub-block at offset 6 with size=100.
    fake_sub_header = BlockType.TABLE.encode(100)  # claims size=100
    heap = fake_sub_header + b"\x00" * 4  # small heap, but sub-block says 100
    block = build_table_bytes([TableEntryRaw(TableEntryType.BLOCK, 6)], heap)
    vectors.append(
        negative(
            "subblock_exceeds_parent",
            "Sub-block at offset 6 claims size 100 but parent is only ~12 bytes",
            SIMPLE_SCHEMA,
            block,
            "Sub-block exceeds parent block",
        )
    )

    # 9. slots_bad_sentinel: SLOTS sentinel offset != block size
    # Build SLOTS block manually: 1 element, but sentinel points wrong.
    # Layout: header(2) + offsets(2*2=4) + heap.
    # offsets = [6, 10] (first_offset=6=heap_start, sentinel=10)
    # heap = 4 bytes. size = 2+4+4=10. sentinel=10 matches.
    # Corrupt: change sentinel to 8.
    slots_hdr = BlockType.SLOTS.encode(10)
    offsets_bytes = (6).to_bytes(2, "little") + (8).to_bytes(2, "little")  # bad sentinel
    heap = b"abcd"
    vectors.append(
        negative(
            "slots_bad_sentinel",
            "SLOTS block sentinel offset (8) does not match block size (10)",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [{"index": 0, "name": "data", "type": "bytes"}],
                    }
                },
            },
            slots_hdr + offsets_bytes + heap,
            "SLOTS sentinel offset does not match block size",
        )
    )

    # 10. slots_decreasing_offsets: non-monotonic offsets
    # 2 elements: offsets = [8, 10, 6] (last < second → not non-decreasing)
    # heap_start = 2 + 2*3 = 8. size = 8 + 4 = 12.
    slots_hdr = BlockType.SLOTS.encode(12)
    offsets_bytes = (
        (8).to_bytes(2, "little")
        + (10).to_bytes(2, "little")
        + (6).to_bytes(2, "little")  # decreasing!
    )
    heap = b"abcd"
    vectors.append(
        negative(
            "slots_decreasing_offsets",
            "SLOTS block with non-monotonic offset sequence [8, 10, 6]",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [{"index": 0, "name": "data", "type": "bytes[]"}],
                    }
                },
            },
            slots_hdr + offsets_bytes + heap,
            "SLOTS offsets are not non-decreasing",
        )
    )

    # 11. links_reserved_nonzero: LINKS reserved field != 0
    link1_bytes = b"\x00" * 32 + (5).to_bytes(4, "little")  # digest + limit=5
    link2_bytes = b"\x01" * 32 + (10).to_bytes(4, "little")  # digest + limit=10
    links_hdr = BlockType.LINKS.encode(4 + 72)
    # depth_field: depth=0 (low 3 bits), reserved=1 (bit 3 set)
    depth_field = (1 << 3).to_bytes(2, "little")
    vectors.append(
        negative(
            "links_reserved_nonzero",
            "LINKS block with non-zero reserved bits in depth field",
            SIMPLE_SCHEMA,
            links_hdr + depth_field + link1_bytes + link2_bytes,
            "LINKS block reserved bits are not zero",
        )
    )

    # 11b. links_depth_too_high: LINKS depth exceeds maximum (4)
    links_hdr = BlockType.LINKS.encode(4 + 72)
    depth_field = (5).to_bytes(2, "little")  # depth=5, reserved=0
    vectors.append(
        negative(
            "links_depth_too_high",
            "LINKS block with depth 5 (max is 4)",
            SIMPLE_SCHEMA,
            links_hdr + depth_field + link1_bytes + link2_bytes,
            "LINKS block depth exceeds maximum",
        )
    )

    # 11c. links_single_link: LINKS block with only one link
    links_hdr_1 = BlockType.LINKS.encode(4 + 36)
    depth_field_0 = (0).to_bytes(2, "little")
    vectors.append(
        negative(
            "links_single_link",
            "LINKS block with only 1 link (minimum is 2)",
            SIMPLE_SCHEMA,
            links_hdr_1 + depth_field_0 + link1_bytes,
            "LINKS block must have at least 2 links",
        )
    )

    # 12. links_not_increasing: LINKS limits not strictly increasing
    link1 = b"\x00" * 32 + (5).to_bytes(4, "little")
    link2 = b"\x01" * 32 + (3).to_bytes(4, "little")  # 3 < 5 → not increasing
    links_hdr = BlockType.LINKS.encode(4 + 72)
    depth_field_0 = (0).to_bytes(2, "little")
    vectors.append(
        negative(
            "links_not_increasing",
            "LINKS block with non-increasing limits [5, 3]",
            SIMPLE_SCHEMA,
            links_hdr + depth_field_0 + link1 + link2,
            "LINKS limits are not strictly increasing",
        )
    )

    # 13. wrong_block_type: DATA block where TABLE expected (root struct)
    # Root should be TABLE, but we provide a DATA block.
    # DATA block: block_header(2) + elem_info(2) = 4 bytes minimum
    data_block = BlockType.DATA.encode(4) + Tagged16(0, 1).encode()  # elem_align=1, elem_size=1
    vectors.append(
        negative(
            "wrong_block_type",
            "Root block is DATA but schema expects TABLE (struct)",
            SIMPLE_SCHEMA,
            data_block,
            "Expected TABLE block, got DATA",
        )
    )

    # 14. direct4_not_4_aligned: DIRECT4 at non-4-aligned offset
    # TABLE with 1 entry. heap_start = 6. 6 % 4 = 2 → error.
    heap = b"\x00" * 4
    block = build_table_bytes([TableEntryRaw(TableEntryType.DIRECT4, 6)], heap)
    vectors.append(
        negative(
            "direct4_not_4_aligned",
            "DIRECT4 entry at offset 6 (not 4-byte aligned)",
            SIMPLE_SCHEMA,
            block,
            "DIRECT4 entry is not 4-byte aligned",
        )
    )

    # 15. direct8_not_8_aligned: DIRECT8 at non-8-aligned offset
    # TABLE with 2 entries. heap_start = 8. Put DIRECT8 at offset 12 (4-aligned but not 8).
    heap = b"\x00" * 16
    block = build_table_bytes(
        [TableEntryRaw(TableEntryType.NULL, 0), TableEntryRaw(TableEntryType.DIRECT8, 12)],
        heap,
    )
    vectors.append(
        negative(
            "direct8_not_8_aligned",
            "DIRECT8 entry at offset 12 (not 8-byte aligned)",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [
                            {"index": 0, "name": "x", "type": "u8"},
                            {"index": 1, "name": "big", "type": "u64"},
                        ]
                    },
                },
            },
            block,
            "DIRECT8 entry is not 8-byte aligned",
        )
    )

    # 16. fixed_array_block_misaligned: u32[3] BLOCK at non-4-aligned offset
    # TABLE with 2 entries. heap_start = 8. First entry = DIRECT4 at 8 (4 bytes).
    # Second entry = BLOCK at offset 14 (not 4-aligned) containing a DATA block.
    # DATA block: block_header(2) + elem_info(2) + 12 bytes data = 16
    sub = BlockType.DATA.encode(16) + Tagged16(2, 4).encode() + b"\x00" * 12  # align=4, size=4, 3 u32s
    heap = b"\x00" * 4 + b"\x42\x42" + sub  # 4 bytes + 2 padding + sub_block
    block = build_table_bytes(
        [TableEntryRaw(TableEntryType.DIRECT4, 8), TableEntryRaw(TableEntryType.BLOCK, 14)],
        heap,
    )
    vectors.append(
        negative(
            "fixed_array_block_misaligned",
            "BLOCK entry for u32[3] at offset 14 (not aligned to element alignment 4)",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [
                            {"index": 0, "name": "x", "type": "u32"},
                            {"index": 1, "name": "vec", "type": "u32[3]"},
                        ]
                    },
                },
            },
            block,
            "Fixed array BLOCK entry is not properly aligned",
        )
    )

    # 17. directdata_params_nonzero: DIRECTDATA header with params != 0
    # TABLE with 1 entry, heap_start=6. DIRECTDATA at offset 6.
    # Header: params=1, number=2 → t16(1, 2) + 2 bytes data
    dd_header = Tagged16(1, 2).encode()  # params=1 (reserved!)
    dd_data = b"\xAB\xCD"
    heap = dd_header + dd_data
    block = build_table_bytes([TableEntryRaw(TableEntryType.DIRECTDATA, 6)], heap)
    vectors.append(
        negative(
            "directdata_params_nonzero",
            "DIRECTDATA header with non-zero params (reserved)",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [{"index": 0, "name": "data", "type": "bytes"}],
                    }
                },
            },
            block,
            "DIRECTDATA header params are not zero",
        )
    )

    # 18. directdata_length_exceeds_block: DIRECTDATA length overflows block
    # TABLE with 1 entry, heap_start=6. DIRECTDATA at offset 6.
    # Header: params=0, number=100 but block only has 4 bytes of data after header
    dd_header = Tagged16(0, 100).encode()  # claims 100 bytes
    heap = dd_header + b"\x00" * 4  # only 4 bytes, not 100
    block = build_table_bytes([TableEntryRaw(TableEntryType.DIRECTDATA, 6)], heap)
    vectors.append(
        negative(
            "directdata_length_exceeds_block",
            "DIRECTDATA at offset 6 claims length 100 but block is too small",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [{"index": 0, "name": "data", "type": "bytes"}],
                    }
                },
            },
            block,
            "DIRECTDATA data exceeds block size",
        )
    )

    # 19. zero_offset: TABLE entry with offset 0
    # Offset 0 points to the block header itself, creating a cycle.
    heap = b"\x00" * 4
    block = build_table_bytes([TableEntryRaw(TableEntryType.DIRECT4, 0)], heap)
    vectors.append(
        negative(
            "zero_offset",
            "TABLE entry with offset 0 (invalid: would point to block header, creating a cycle)",
            SIMPLE_SCHEMA,
            block,
            "Zero offset in TABLE entry",
        )
    )

    # 20. data_too_small: DATA block with size < 4
    # DATA blocks need at least 4 bytes (header + elem_info)
    data_block = BlockType.DATA.encode(2)  # just the header, no elem_info
    vectors.append(
        negative(
            "data_too_small",
            "DATA block with declared size 2 (minimum is 4, need room for header + elem_info)",
            {
                "version": 1,
                "root": "Root",
                "structs": {
                    "Root": {
                        "fields": [{"index": 0, "name": "values", "type": "u8[]"}],
                    }
                },
            },
            data_block,
            "DATA block too small (size < 4)",
        )
    )

    return vectors


# ============================================================
# Main
# ============================================================


def main() -> None:
    pos = gen_positive()
    neg = gen_negative()

    (OUTPUT_DIR / "positive.json").write_text(
        json.dumps(pos, indent=2, ensure_ascii=False) + "\n"
    )
    (OUTPUT_DIR / "negative.json").write_text(
        json.dumps(neg, indent=2, ensure_ascii=False) + "\n"
    )

    print(f"Generated {len(pos)} positive vectors -> vectors_json/positive.json")
    print(f"Generated {len(neg)} negative vectors -> vectors_json/negative.json")


if __name__ == "__main__":
    main()
