"""Tests for TableBlock."""

import pytest

from hashbuffers.codec import (
    SIZE_MAX,
    BlockType,
    DataBlock,
    Link,
    TableBlock,
    Tagged16,
)
from hashbuffers.codec.table import (
    NULL_ENTRY,
    BlockEntry,
    DirectFixedEntry,
    InlineIntEntry,
    LinkEntry,
    TableEntryRaw,
    TableEntryType,
)


def test_encode_decode_table_block():
    vtable = [
        TableEntryRaw(TableEntryType.INLINE, 42),
        TableEntryRaw(TableEntryType.NULL, 0),
    ]
    heap = b"heap data" * 10
    encoded = TableBlock.build(vtable, heap).encode()
    block = TableBlock.decode(encoded)
    assert len(block.vtable) == len(vtable)
    assert block.vtable[0].type == TableEntryType.INLINE
    assert block.vtable[0].offset == 42
    assert block.heap == heap


def test_table_block_exceeds_max_size():
    vtable = []
    heap = b"A" * (SIZE_MAX - 2)
    with pytest.raises(ValueError, match="out of bounds"):
        TableBlock.build(vtable, heap).validate()


def test_sign_extend_13bit():
    assert InlineIntEntry.from_int(0, True).to_int(2, True) == 0
    assert InlineIntEntry.from_int(1, True).to_int(2, True) == 1
    assert InlineIntEntry.from_int(0xFFF, True).to_int(2, True) == 0xFFF
    assert InlineIntEntry.from_int(-4096, True).to_int(2, True) == -4096
    assert InlineIntEntry.from_int(-1, True).to_int(2, True) == -1


def test_decode_rejects_reserved_vtable_types():
    """Table decode must reject vtable entries with reserved type tags 0b011, 0b101."""
    valid = TableBlock.build(
        [TableEntryRaw(TableEntryType.NULL, 0)],
        b"",
    )
    encoded = valid.encode()
    # Layout: header 0-1, vtable_header 2-3, first vtable_entry 4-5
    for reserved_type in (0b001, 0b111):
        encoded_mut = bytearray(encoded)
        entry_val = int.from_bytes(encoded_mut[4:6], "little")
        entry_val = (entry_val & 0x1FFF) | (reserved_type << 13)
        encoded_mut[4:6] = entry_val.to_bytes(2, "little")
        with pytest.raises(ValueError):
            TableBlock.decode(bytes(encoded_mut))


def test_empty_table_vtable():
    """Table with empty vtable is valid."""
    block = TableBlock.build([], b"")
    decoded = TableBlock.decode(block.encode())
    assert decoded.vtable == []
    assert decoded.heap == b""


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes."""
    encoded = TableBlock.build([TableEntryRaw(TableEntryType.NULL, 0)], b"").encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        TableBlock.decode(encoded + b"x")


def test_get_int_direct4_u32():
    """DIRECT4 entry pointing to a u32 on the heap is read back correctly."""
    value = 0xDEAD_BEEF
    heap = b"\x00\x00" + value.to_bytes(4, "little")  # 2 bytes padding for 4-alignment
    # heap_start for 1 entry = 6, so offset 8 is 4-aligned
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT4, 8)],
        heap,
    )
    assert block[0].to_int(4, False) == value  # type: ignore


def test_get_int_direct8_u64():
    """DIRECT8 entry pointing to a u64 on the heap is read back correctly."""
    value = 0xDEAD_BEEF_CAFE_BABE
    # heap_start for 1 entry = 6, need 8-aligned offset = 8
    heap = b"\x00\x00" + value.to_bytes(8, "little")
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT8, 8)],
        heap,
    )
    assert block[0].to_int(8, False) == value  # type: ignore


def test_get_int_rejects_oversized_direct8_for_u32():
    """DIRECT8 must be rejected for a u32 field."""
    value = 0xDEAD_BEEF
    heap = b"\x00\x00" + value.to_bytes(8, "little")
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT8, 8)],
        heap,
    )
    with pytest.raises(ValueError, match="Encoding too big"):
        block[0].to_int(4, False)  # type: ignore


def test_get_int_rejects_oversized_direct4_for_u8():
    """DIRECT4 must be rejected for a u8 field."""
    heap = b"\x00\x00" + b"\x05\x00\x00\x00"
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT4, 8)],
        heap,
    )
    with pytest.raises(ValueError, match="Encoding too big"):
        block[0].to_int(1, False)  # type: ignore


def test_get_int_signed_inline():
    """Signed INLINE integer is sign-extended correctly."""
    # -1 in 13 bits is 0x1FFF
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.INLINE, 0x1FFF)],
        b"",
    )
    assert block[0].to_int(2, True) == -1  # type: ignore
    # 0x1000 = -4096 in 13-bit signed
    block2 = TableBlock.build(
        [TableEntryRaw(TableEntryType.INLINE, 0x1000)],
        b"",
    )
    assert block2[0].to_int(2, True) == -4096  # type: ignore
    # Positive value stays positive
    assert block2[0].to_int(2, False) == 0x1000  # type: ignore


def test_get_block_with_nested_block():
    """BLOCK entry containing a real sub-block is read back correctly."""
    inner = DataBlock.build(b"nested payload", elem_size=1, elem_align=1)
    inner_bytes = inner.encode()
    heap_start = TableBlock.heap_start(1)
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.BLOCK, heap_start)],
        inner_bytes,
    )
    result = block[0]
    assert isinstance(result, BlockEntry)
    assert isinstance(result.block, DataBlock)
    assert bytes(result.block.get_data()) == b"nested payload"


def test_get_block_with_real_link():
    """LINK entry containing a properly encoded Link is read back correctly."""
    link = Link(b"x" * 32, 42)
    link_bytes = link.encode()
    heap_start = TableBlock.heap_start(1)
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.LINK, heap_start)],
        link_bytes,
    )
    result = block[0]
    assert isinstance(result, LinkEntry)
    assert result.link.digest == b"x" * 32
    assert result.link.limit == 42


def test_get_float_direct4():
    """DIRECT4 entry for f32 is read back correctly."""
    import struct

    raw = struct.pack("<f", 2.5)
    heap = b"\x00\x00" + raw  # pad to 4-alignment at offset 8
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT4, 8)],
        heap,
    )
    data = block[0]
    assert isinstance(data, DirectFixedEntry)
    assert struct.unpack("<f", data.data)[0] == pytest.approx(2.5)


def test_get_float_direct8():
    """DIRECT8 entry for f64 is read back correctly."""
    import struct

    raw = struct.pack("<d", 3.14)
    heap = b"\x00\x00" + raw  # pad to 8-alignment at offset 8
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT8, 8)],
        heap,
    )
    data = block[0]
    assert isinstance(data, DirectFixedEntry)
    assert data is not None
    assert struct.unpack("<d", data.data)[0] == pytest.approx(3.14)


def test_get_int_null_returns_none():
    """NULL entry returns None from get_int."""
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.NULL, 0)],
        b"",
    )
    assert block[0] is NULL_ENTRY


def test_get_int_out_of_range_index_returns_null():
    """Out-of-range vtable index returns NULL_ENTRY."""
    block = TableBlock.build([], b"")
    assert block[0] is NULL_ENTRY


def test_decode_rejects_reserved_vtable_header_flags():
    """Non-zero flags in vtable_header must be rejected."""
    valid = TableBlock.build([TableEntryRaw(TableEntryType.NULL, 0)], b"")
    encoded = bytearray(valid.encode())
    # vtable_header is at bytes 2-3; set a flags bit
    header = Tagged16.decode(bytes(encoded[2:4]))
    mutated = Tagged16(header.parameters | 0b001, header.number).encode()
    encoded[2:4] = mutated
    with pytest.raises(ValueError, match="Reserved bits"):
        TableBlock.decode(bytes(encoded))


def test_direct4_offset_out_of_bounds():
    """DIRECT4 entry with offset past heap boundary is caught during validation."""
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT4, 200)],
        b"small",
    )
    with pytest.raises(ValueError, match="out of bounds"):
        block.validate()


def test_direct4_offset_zero_rejected():
    """Spec bounds: offset 0 is the block header, not a heap pointer."""
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT4, 0)],
        b"heap",
    )
    with pytest.raises(ValueError, match="out of bounds"):
        block.validate()


def test_direct4_not_4_aligned():
    """DIRECT4 entry must be 4-aligned."""
    heap = b"\x00" * 10
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT4, 7)],
        heap,
    )
    with pytest.raises(ValueError, match="not 4-aligned"):
        block.validate()


def test_direct8_not_8_aligned():
    """DIRECT8 entry must be 8-aligned."""
    heap = b"\x00" * 16
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT8, 12)],
        heap,
    )
    with pytest.raises(ValueError, match="not 8-aligned"):
        block.validate()


def test_table_block_link_payload_truncated():
    """LINK entry must have a full 36-byte encoding inside the parent."""
    heap_start = TableBlock.heap_start(1)
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.LINK, heap_start)],
        b"\x00" * 10,
    )
    with pytest.raises(ValueError, match="out of bounds"):
        block.validate()


def test_table_block_link_alignment():
    """LINK entry offset must be 4-aligned."""
    # heap_start = 4 + 2*1 = 6, which is not 4-aligned
    heap_start = TableBlock.heap_start(1)
    link_bytes = Link(b"x" * 32, 42).encode()
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.LINK, heap_start)],
        link_bytes,
    )
    with pytest.raises(ValueError, match="not 4-aligned"):
        block.validate()


def test_table_block_link_limit_zero_rejected():
    """LINK entry with limit=0 is rejected during TABLE validation."""
    # Need to bypass Link.encode()'s own limit check by building raw bytes
    # Use 2 entries so heap_start = 4 + 2*2 = 8 (4-aligned)
    link_data = b"x" * 32 + (0).to_bytes(4, "little")  # limit=0
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.LINK, 8), TableEntryRaw(TableEntryType.NULL, 0)],
        link_data,
    )
    with pytest.raises(ValueError, match="limit must not be 0"):
        block.validate()


def test_table_block_block_alignment():
    """BLOCK entry offset must be 2-aligned."""
    # Construct a block where the BLOCK entry has an odd offset
    # heap_start = 4 + 2*1 = 6. Place padding byte + inner block at offset 7
    heap_start = TableBlock.heap_start(1)
    inner = DataBlock.build(b"x", elem_size=1, elem_align=1)
    inner_bytes = inner.encode()
    heap = b"\x00" + inner_bytes  # padding byte shifts inner to offset 7
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.BLOCK, heap_start + 1)],
        heap,
    )
    with pytest.raises(ValueError, match="not 2-aligned"):
        block.validate()


def test_table_block_nested_table_alignment():
    """Nested TABLE with DIRECT4 entry requires 4-alignment in parent."""
    # Inner TABLE: 1 field (DIRECT4 at offset 8), heap = 2 pad + 4 data
    inner_heap = b"\x00\x00" + b"\x01\x00\x00\x00"
    inner = TableBlock.build(
        [TableEntryRaw(TableEntryType.DIRECT4, 8)],
        inner_heap,
    )
    inner_bytes = inner.encode()
    # Outer TABLE with 1 entry: heap_start = 6.
    # Inner block at offset 6: 6 % 2 == 0 (passes basic check), but 6 % 4 != 0.
    outer = TableBlock.build(
        [TableEntryRaw(TableEntryType.BLOCK, 6)],
        inner_bytes,
    )
    with pytest.raises(ValueError, match="not.*aligned"):
        outer.validate()


def test_table_block_block_exceeds_parent():
    """Sub-block declared size exceeding remaining space is caught during decode."""
    heap_start = TableBlock.heap_start(1)  # = 6
    inner = DataBlock.build(b"x", elem_size=1, elem_align=1)
    inner_bytes = bytearray(inner.encode())
    # Inflate the inner block's declared size beyond what the parent provides
    inner_bytes[0:2] = BlockType.DATA.encode(5000)
    block = TableBlock.build(
        [TableEntryRaw(TableEntryType.BLOCK, heap_start)],
        bytes(inner_bytes),
    )
    # The reader fails when trying to decode the sub-block (IOError from read_until)
    with pytest.raises((ValueError, IOError)):
        block.validate()


def test_table_block_heap_start_exceeds_size():
    """entry_count that would place heap_start past block size is rejected."""
    # Build a table with many entries but tiny heap
    # 100 entries: heap_start = 4 + 200 = 204, but block size might be < 204
    entries = [TableEntryRaw(TableEntryType.NULL, 0)] * 100
    block = TableBlock.build(entries, b"")
    # This should be fine as-is (size = 204). But if we set size too small...
    block.size = 10
    with pytest.raises(ValueError):
        block.validate()
