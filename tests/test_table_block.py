"""Tests for TableBlock."""

import pytest

from hashbuffers.codec import (
    SIZE_MAX,
    BlockType,
    DataBlock,
    Link,
    TableBlock,
    Tagged16,
    VTableEntry,
    VTableEntryType,
)


def test_encode_decode_table_block():
    vtable = [
        VTableEntry(VTableEntryType.INLINE, 42),
        VTableEntry(VTableEntryType.NULL, 0),
    ]
    heap = b"heap data" * 10
    encoded = TableBlock.build(vtable, heap).encode()
    block = TableBlock.decode(encoded)
    assert len(block.vtable) == len(vtable)
    assert block.vtable[0].type == VTableEntryType.INLINE
    assert block.vtable[0].offset == 42
    assert block.heap == heap


def test_table_block_exceeds_max_size():
    vtable = []
    heap = b"A" * (SIZE_MAX - 2)
    with pytest.raises(ValueError, match="out of bounds"):
        TableBlock.build(vtable, heap).validate()


def test_sign_extend_13bit():
    assert TableBlock._sign_extend_13bit(0) == 0
    assert TableBlock._sign_extend_13bit(1) == 1
    assert TableBlock._sign_extend_13bit(0xFFF) == 0xFFF
    assert TableBlock._sign_extend_13bit(0x1000) == -0x1000
    assert TableBlock._sign_extend_13bit(0x1FFF) == -1


def test_decode_rejects_reserved_vtable_types():
    """Table decode must reject vtable entries with reserved type tags 0b001, 0b010, 0b011."""
    valid = TableBlock.build(
        [VTableEntry(VTableEntryType.NULL, 0)],
        b"",
    )
    encoded = bytearray(valid.encode())
    # Layout: header 0-1, vtable_header 2-3, first vtable_entry 4-5
    for reserved_type in (0b001, 0b010, 0b011):
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
    encoded = TableBlock.build([VTableEntry(VTableEntryType.NULL, 0)], b"").encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        TableBlock.decode(encoded + b"x")


def test_get_int_direct_u32():
    """DIRECT entry pointing to a u32 on the heap is read back correctly."""
    value = 0xDEAD_BEEF
    heap = value.to_bytes(4, "little")
    heap_start = 4 + 2 * 1  # header + vtable_header + 1 entry
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.DIRECT, heap_start)],
        heap,
    )
    assert block.get_int(0, 4) == value


def test_get_int_signed_inline():
    """Signed INLINE integer is sign-extended correctly."""
    # -1 in 13 bits is 0x1FFF
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.INLINE, 0x1FFF)],
        b"",
    )
    assert block.get_int(0, 2, signed=True) == -1
    # 0x1000 = -4096 in 13-bit signed
    block2 = TableBlock.build(
        [VTableEntry(VTableEntryType.INLINE, 0x1000)],
        b"",
    )
    assert block2.get_int(0, 2, signed=True) == -4096
    # Positive value stays positive
    assert block2.get_int(0, 2, signed=False) == 0x1000


def test_get_block_with_nested_block():
    """BLOCK entry containing a real sub-block is read back correctly."""
    inner = DataBlock.build(b"nested payload")
    inner_bytes = inner.encode()
    heap_start = 4 + 2 * 1
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.BLOCK, heap_start)],
        inner_bytes,
    )
    result = block.get_block(0)
    assert isinstance(result, DataBlock)
    assert result.get_data() == b"nested payload"


def test_get_block_with_real_link():
    """LINK entry containing a properly encoded Link is read back correctly."""
    link = Link(b"x" * 32, 42)
    link_bytes = link.encode()
    heap_start = 4 + 2 * 1
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.LINK, heap_start)],
        link_bytes,
    )
    result = block.get_block(0)
    assert isinstance(result, Link)
    assert result.digest == b"x" * 32
    assert result.limit == 42


def test_get_fixedsize():
    """DIRECT entry for fixed-size data is read back correctly."""
    raw = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    heap_start = 4 + 2 * 1
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.DIRECT, heap_start)],
        raw,
    )
    assert block.get_fixedsize(0, 8) == raw


def test_get_int_null_returns_none():
    """NULL entry returns None from get_int."""
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.NULL, 0)],
        b"",
    )
    assert block.get_int(0, 4) is None


def test_get_int_out_of_range_index_returns_none():
    """Out-of-range vtable index returns None."""
    block = TableBlock.build([], b"")
    assert block.get_int(0, 4) is None
    assert block.get_fixedsize(0, 4) is None
    assert block.get_block(0) is None


def test_decode_rejects_reserved_vtable_header_flags():
    """Non-zero flags in vtable_header must be rejected."""
    valid = TableBlock.build([VTableEntry(VTableEntryType.NULL, 0)], b"")
    encoded = bytearray(valid.encode())
    # vtable_header is at bytes 2-3; set a flags bit
    header = Tagged16.decode(bytes(encoded[2:4]))
    mutated = Tagged16(header.parameters | 0b001, header.number).encode()
    encoded[2:4] = mutated
    with pytest.raises(ValueError, match="Reserved bits"):
        TableBlock.decode(bytes(encoded))


def test_direct_offset_out_of_bounds():
    """DIRECT entry with offset past heap boundary is caught during validation."""
    heap_start = 4 + 2 * 1
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.DIRECT, heap_start + 100)],
        b"small",
    )
    with pytest.raises(ValueError, match="out of bounds"):
        block.validate()


def test_table_block_heap_pointer_offset_zero_rejected():
    """Spec bounds: offset 0 is the block header, not a heap pointer."""
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.DIRECT, 0)],
        b"heap",
    )
    with pytest.raises(ValueError, match="out of bounds"):
        block.validate()


def test_table_block_nested_block_declared_size_exceeds_parent():
    """Sub-block wire size must fit inside the parent block (bounds checking)."""
    heap_start = 4 + 2 * 1
    inner = DataBlock.build(b"x")
    inner_bytes = bytearray(inner.encode())
    inner_bytes[0:2] = BlockType.DATA.encode(5000)
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.BLOCK, heap_start)],
        bytes(inner_bytes),
    )
    with pytest.raises(IOError, match="Expected to read to offset"):
        block.validate()


def test_table_block_link_payload_truncated():
    """LINK entry must have a full 36-byte encoding inside the parent."""
    heap_start = 4 + 2 * 1
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.LINK, heap_start)],
        b"\x00" * 10,
    )
    with pytest.raises(ValueError, match="doesn't fit"):
        block.validate()


def test_table_block_link_alignment():
    """LINK entry offset must be 4-aligned."""
    # heap_start = 4 + 2*1 = 6, which is not 4-aligned
    heap_start = 6
    link_bytes = Link(b"x" * 32, 42).encode()
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.LINK, heap_start)],
        link_bytes,
    )
    with pytest.raises(ValueError, match="not 4-aligned"):
        block.validate()


def test_table_block_link_limit_zero_rejected():
    """LINK entry with limit=0 is rejected during TABLE validation."""
    # Need to bypass Link.encode()'s own limit check by building raw bytes
    heap_start = 4 + 2 * 1  # = 6, but we need 4-alignment for link
    # Use 2 entries so heap_start = 4 + 2*2 = 8 (4-aligned)
    link_data = b"x" * 32 + (0).to_bytes(4, "little")  # limit=0
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.LINK, 8), VTableEntry(VTableEntryType.NULL, 0)],
        link_data,
    )
    with pytest.raises(ValueError, match="limit must not be 0"):
        block.validate()


def test_table_block_block_alignment():
    """BLOCK entry offset must be 2-aligned."""
    # Construct a block where the BLOCK entry has an odd offset
    # heap_start = 4 + 2*1 = 6. Place padding byte + inner block at offset 7
    inner = DataBlock.build(b"x")
    inner_bytes = inner.encode()
    heap = b"\x00" + inner_bytes  # padding byte shifts inner to offset 7
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.BLOCK, 7)],
        heap,
    )
    with pytest.raises(ValueError, match="not 2-aligned"):
        block.validate()


def test_table_block_block_exceeds_parent():
    """Sub-block declared size exceeding remaining space is caught during decode."""
    heap_start = 4 + 2 * 1  # = 6
    inner = DataBlock.build(b"x")
    inner_bytes = bytearray(inner.encode())
    # Inflate the inner block's declared size beyond what the parent provides
    inner_bytes[0:2] = BlockType.DATA.encode(5000)
    block = TableBlock.build(
        [VTableEntry(VTableEntryType.BLOCK, heap_start)],
        bytes(inner_bytes),
    )
    # The reader fails when trying to decode the sub-block (IOError from read_until)
    with pytest.raises((ValueError, IOError)):
        block.validate()


def test_table_block_heap_start_exceeds_size():
    """entry_count that would place heap_start past block size is rejected."""
    # Build a table with many entries but tiny heap
    # 100 entries: heap_start = 4 + 200 = 204, but block size might be < 204
    entries = [VTableEntry(VTableEntryType.NULL, 0)] * 100
    block = TableBlock.build(entries, b"")
    # This should be fine as-is (size = 204). But if we set size too small...
    block.size = 10
    with pytest.raises(ValueError):
        block.validate()
