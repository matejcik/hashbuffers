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
    NullEntry,
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


# ---- Entry type unit tests ----


class TestNullEntry:
    def test_from_table(self):
        block = TableBlock.build([TableEntryRaw(TableEntryType.NULL, 0)], b"")
        entry = block[0]
        assert entry is NULL_ENTRY

    def test_to_entry_raw(self):
        raw = NULL_ENTRY.to_entry_raw(0)
        assert raw.type == TableEntryType.NULL
        assert raw.offset == 0

    def test_alignment_and_size(self):
        assert NULL_ENTRY.alignment() == 0
        assert NULL_ENTRY.size() == 0

    def test_encode(self):
        assert NULL_ENTRY.encode() == b""


class TestInlineIntEntry:
    def test_from_int_unsigned(self):
        entry = InlineIntEntry.from_int(100, signed=False)
        assert entry.to_int(2, signed=False) == 100

    def test_from_int_signed_negative(self):
        entry = InlineIntEntry.from_int(-1, signed=True)
        assert entry.to_int(2, signed=True) == -1

    def test_from_int_too_large_raises(self):
        with pytest.raises(ValueError, match="too large"):
            InlineIntEntry.from_int(0x2000, signed=False)

    def test_fits(self):
        assert InlineIntEntry.fits(0, False)
        assert InlineIntEntry.fits(0x1FFF, False)
        assert not InlineIntEntry.fits(0x2000, False)
        assert InlineIntEntry.fits(-4096, True)
        assert not InlineIntEntry.fits(-4097, True)
        assert not InlineIntEntry.fits(-1, False)

    def test_to_entry_raw(self):
        entry = InlineIntEntry.from_int(42, signed=False)
        raw = entry.to_entry_raw(0)
        assert raw.type == TableEntryType.INLINE
        assert raw.offset == 42

    def test_alignment_and_size(self):
        entry = InlineIntEntry.from_int(0, signed=False)
        assert entry.alignment() == 0
        assert entry.size() == 0


class TestDirectFixedEntry:
    def test_from_int_u32(self):
        entry = DirectFixedEntry.from_int(0xDEADBEEF, signed=False)
        assert len(entry.data) == 4
        assert entry.to_int(4, signed=False) == 0xDEADBEEF

    def test_from_int_u64(self):
        entry = DirectFixedEntry.from_int(0xDEAD_BEEF_CAFE_BABE, signed=False)
        assert len(entry.data) == 8
        assert entry.to_int(8, signed=False) == 0xDEAD_BEEF_CAFE_BABE

    def test_from_int_negative_unsigned_raises(self):
        with pytest.raises(OverflowError):
            DirectFixedEntry.from_int(-1, signed=False)

    def test_invalid_data_size_raises(self):
        with pytest.raises(ValueError, match="must be 4 or 8"):
            DirectFixedEntry(b"\x00\x00\x00")

    def test_to_entry_raw_direct4(self):
        entry = DirectFixedEntry(b"\x00" * 4)
        raw = entry.to_entry_raw(8)
        assert raw.type == TableEntryType.DIRECT4

    def test_to_entry_raw_direct8(self):
        entry = DirectFixedEntry(b"\x00" * 8)
        raw = entry.to_entry_raw(8)
        assert raw.type == TableEntryType.DIRECT8

    def test_alignment(self):
        assert DirectFixedEntry(b"\x00" * 4).alignment() == 4
        assert DirectFixedEntry(b"\x00" * 8).alignment() == 8

    def test_encode(self):
        entry = DirectFixedEntry(b"\x01\x02\x03\x04")
        assert entry.encode() == b"\x01\x02\x03\x04"

    def test_validate_out_of_bounds(self):
        block = TableBlock.build([TableEntryRaw(TableEntryType.DIRECT4, 200)], b"small")
        with pytest.raises(ValueError, match="out of bounds"):
            block.validate()


class TestDirectDataEntry:
    def _build_table_with_directdata(self, data: bytes) -> TableBlock:
        from hashbuffers.codec.table import DirectDataEntry

        entry = DirectDataEntry(data)
        heap = entry.encode()
        # heap_start for 1 entry = 6
        return TableBlock.build([TableEntryRaw(TableEntryType.DIRECTDATA, 6)], heap)

    def test_roundtrip(self):
        from hashbuffers.codec.table import DirectDataEntry

        block = self._build_table_with_directdata(b"hello")
        entry = block[0]
        assert isinstance(entry, DirectDataEntry)
        assert entry.data == b"hello"

    def test_empty_data(self):
        from hashbuffers.codec.table import DirectDataEntry

        block = self._build_table_with_directdata(b"")
        entry = block[0]
        assert isinstance(entry, DirectDataEntry)
        assert entry.data == b""

    def test_alignment_and_size(self):
        from hashbuffers.codec.table import DirectDataEntry

        entry = DirectDataEntry(b"abc")
        assert entry.alignment() == 2
        assert entry.size() == 2 + 3  # header + data

    def test_to_entry_raw(self):
        from hashbuffers.codec.table import DirectDataEntry

        entry = DirectDataEntry(b"x")
        raw = entry.to_entry_raw(10)
        assert raw.type == TableEntryType.DIRECTDATA
        assert raw.offset == 10

    def test_validate_out_of_bounds(self):
        block = TableBlock.build(
            [TableEntryRaw(TableEntryType.DIRECTDATA, 200)], b"small"
        )
        with pytest.raises(ValueError, match="out of bounds"):
            block.validate()

    def test_validate_bad_params(self):
        """DIRECTDATA header with non-zero params is rejected."""
        # Build a valid DIRECTDATA entry, then corrupt the header params
        from hashbuffers.codec.table import DirectDataEntry

        entry = DirectDataEntry(b"x")
        heap = bytearray(entry.encode())
        # Corrupt the params field (top 3 bits of first 2 bytes)
        val = int.from_bytes(heap[0:2], "little")
        val |= 0b001 << 13  # set params to 1
        heap[0:2] = val.to_bytes(2, "little")
        block = TableBlock.build(
            [TableEntryRaw(TableEntryType.DIRECTDATA, 6)], bytes(heap)
        )
        with pytest.raises(ValueError, match="not zero"):
            block.validate()


class TestLinkEntry:
    def test_roundtrip(self):
        link = Link(b"\xaa" * 32, 42)
        # Need 4-aligned offset. 2 entries: heap_start = 8
        block = TableBlock.build(
            [
                TableEntryRaw(TableEntryType.LINK, 8),
                TableEntryRaw(TableEntryType.NULL, 0),
            ],
            link.encode(),
        )
        entry = block[0]
        assert isinstance(entry, LinkEntry)
        assert entry.link.digest == b"\xaa" * 32
        assert entry.link.limit == 42

    def test_alignment_and_size(self):
        entry = LinkEntry(Link(b"\x00" * 32, 1))
        assert entry.alignment() == Link.ALIGNMENT
        assert entry.size() == Link.SIZE

    def test_to_entry_raw(self):
        entry = LinkEntry(Link(b"\x00" * 32, 1))
        raw = entry.to_entry_raw(8)
        assert raw.type == TableEntryType.LINK
        assert raw.offset == 8

    def test_encode(self):
        link = Link(b"\xbb" * 32, 5)
        entry = LinkEntry(link)
        assert entry.encode() == link.encode()


class TestBlockEntry:
    def test_roundtrip(self):
        inner = DataBlock.build(b"payload", elem_size=1, elem_align=1)
        block = TableBlock.build(
            [TableEntryRaw(TableEntryType.BLOCK, TableBlock.heap_start(1))],
            inner.encode(),
        )
        entry = block[0]
        assert isinstance(entry, BlockEntry)
        assert isinstance(entry.block, DataBlock)

    def test_alignment_and_size(self):
        inner = DataBlock.build(b"x", elem_size=1, elem_align=1)
        entry = BlockEntry(inner)
        assert entry.alignment() == inner.alignment()
        assert entry.size() == inner.size

    def test_to_entry_raw(self):
        inner = DataBlock.build(b"x", elem_size=1, elem_align=1)
        entry = BlockEntry(inner)
        raw = entry.to_entry_raw(6)
        assert raw.type == TableEntryType.BLOCK
        assert raw.offset == 6

    def test_encode(self):
        inner = DataBlock.build(b"x", elem_size=1, elem_align=1)
        entry = BlockEntry(inner)
        assert entry.encode() == inner.encode()


class TestTableBlockSequenceProtocol:
    def test_len(self):
        block = TableBlock.build(
            [
                TableEntryRaw(TableEntryType.INLINE, 1),
                TableEntryRaw(TableEntryType.INLINE, 2),
            ],
            b"",
        )
        assert len(block) == 2

    def test_iter(self):
        block = TableBlock.build(
            [
                TableEntryRaw(TableEntryType.INLINE, 10),
                TableEntryRaw(TableEntryType.NULL, 0),
            ],
            b"",
        )
        entries = list(block)
        assert len(entries) == 2
        assert isinstance(entries[0], InlineIntEntry)
        assert isinstance(entries[1], NullEntry)

    def test_getitem_slice(self):
        block = TableBlock.build(
            [
                TableEntryRaw(TableEntryType.INLINE, 1),
                TableEntryRaw(TableEntryType.INLINE, 2),
                TableEntryRaw(TableEntryType.INLINE, 3),
            ],
            b"",
        )
        entries = block[0:2]
        assert len(entries) == 2

    def test_getitem_negative_raises(self):
        block = TableBlock.build([TableEntryRaw(TableEntryType.INLINE, 1)], b"")
        with pytest.raises(IndexError, match="Negative"):
            block[-1]

    def test_alignment_from_entries(self):
        """alignment() returns max alignment of all entries."""
        # INLINE only → alignment 2 (minimum)
        block = TableBlock.build([TableEntryRaw(TableEntryType.INLINE, 1)], b"")
        assert block.alignment() == 2

    def test_heap_data_out_of_bounds(self):
        block = TableBlock.build([TableEntryRaw(TableEntryType.NULL, 0)], b"")
        with pytest.raises(ValueError, match="out of bounds"):
            block.get_heap_data(100, 10)
