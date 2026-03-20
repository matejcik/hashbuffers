"""Tests for StructBlock."""

import pytest

from wire_format.codec import (
    DataBlock,
    Link,
    Tagged16,
    StructBlock,
    VTableEntry,
    VTableEntryType,
    SIZE_MAX,
)


def test_encode_decode_struct_block():
    vtable = [
        VTableEntry(VTableEntryType.INLINE, 42),
        VTableEntry(VTableEntryType.NULL, 0),
    ]
    heap = b"heap data" * 10
    encoded = StructBlock.build(vtable, heap).encode()
    block = StructBlock.decode(encoded)
    assert len(block.vtable) == len(vtable)
    assert block.vtable[0].type == VTableEntryType.INLINE
    assert block.vtable[0].offset == 42
    assert block.heap == heap


def test_struct_block_exceeds_max_size():
    vtable = []
    heap = b"A" * (SIZE_MAX - 2)
    with pytest.raises(ValueError, match="out of bounds"):
        StructBlock.build(vtable, heap).validate()


def test_sign_extend_13bit():
    assert StructBlock._sign_extend_13bit(0) == 0
    assert StructBlock._sign_extend_13bit(1) == 1
    assert StructBlock._sign_extend_13bit(0xFFF) == 0xFFF
    assert StructBlock._sign_extend_13bit(0x1000) == -0x1000
    assert StructBlock._sign_extend_13bit(0x1FFF) == -1


def test_decode_rejects_reserved_vtable_types():
    """Struct decode must reject vtable entries with reserved type tags 0b001, 0b010, 0b011."""
    valid = StructBlock.build(
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
            StructBlock.decode(bytes(encoded_mut))


def test_empty_struct_vtable():
    """Struct with empty vtable is valid."""
    block = StructBlock.build([], b"")
    decoded = StructBlock.decode(block.encode())
    assert decoded.vtable == []
    assert decoded.heap == b""


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes."""
    encoded = StructBlock.build([VTableEntry(VTableEntryType.NULL, 0)], b"").encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        StructBlock.decode(encoded + b"x")


def test_get_int_direct_u32():
    """DIRECT entry pointing to a u32 on the heap is read back correctly."""
    value = 0xDEAD_BEEF
    heap = value.to_bytes(4, "little")
    heap_start = 4 + 2 * 1  # header + vtable_header + 1 entry
    block = StructBlock.build(
        [VTableEntry(VTableEntryType.DIRECT, heap_start)],
        heap,
    )
    assert block.get_int(0, 4) == value


def test_get_int_signed_inline():
    """Signed INLINE integer is sign-extended correctly."""
    # -1 in 13 bits is 0x1FFF
    block = StructBlock.build(
        [VTableEntry(VTableEntryType.INLINE, 0x1FFF)],
        b"",
    )
    assert block.get_int(0, 2, signed=True) == -1
    # 0x1000 = -4096 in 13-bit signed
    block2 = StructBlock.build(
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
    block = StructBlock.build(
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
    block = StructBlock.build(
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
    block = StructBlock.build(
        [VTableEntry(VTableEntryType.DIRECT, heap_start)],
        raw,
    )
    assert block.get_fixedsize(0, 8) == raw


def test_get_int_null_returns_none():
    """NULL entry returns None from get_int."""
    block = StructBlock.build(
        [VTableEntry(VTableEntryType.NULL, 0)],
        b"",
    )
    assert block.get_int(0, 4) is None


def test_get_int_out_of_range_index_returns_none():
    """Out-of-range vtable index returns None."""
    block = StructBlock.build([], b"")
    assert block.get_int(0, 4) is None
    assert block.get_fixedsize(0, 4) is None
    assert block.get_block(0) is None


def test_decode_rejects_reserved_vtable_header_flags():
    """Non-zero flags in vtable_header must be rejected."""
    valid = StructBlock.build([VTableEntry(VTableEntryType.NULL, 0)], b"")
    encoded = bytearray(valid.encode())
    # vtable_header is at bytes 2-3; set a flags bit
    header = Tagged16.decode(bytes(encoded[2:4]))
    mutated = Tagged16(header.parameters | 0b001, header.number).encode()
    encoded[2:4] = mutated
    with pytest.raises(ValueError, match="Reserved bits"):
        StructBlock.decode(bytes(encoded))


def test_direct_offset_out_of_bounds():
    """DIRECT entry with offset past heap boundary is caught during validation."""
    heap_start = 4 + 2 * 1
    block = StructBlock.build(
        [VTableEntry(VTableEntryType.DIRECT, heap_start + 100)],
        b"small",
    )
    with pytest.raises(ValueError, match="out of bounds"):
        block.validate()
