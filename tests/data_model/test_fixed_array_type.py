"""Unit tests for data_model.array.FixedArrayType — init, encode, decode paths."""

import pytest

from hashbuffers.codec import DataBlock, Link, TableBlock, VTableEntry, VTableEntryType
from hashbuffers.data_model.array import FixedArrayType
from hashbuffers.data_model.primitive import U8, U32
from hashbuffers.fitting import DirectEntry, Table
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestFixedArrayTypeInit:
    def test_valid(self):
        fa = FixedArrayType(U32, 3)
        assert fa.count == 3

    def test_too_large_raises(self):
        with pytest.raises(ValueError, match="too large"):
            FixedArrayType(U32, 100000)

    def test_get_size(self):
        fa = FixedArrayType(U32, 3)
        assert fa.get_size() == 12  # 3 * 4

    def test_get_alignment(self):
        fa = FixedArrayType(U32, 3)
        assert fa.get_alignment() == 4


class TestFixedArrayTypeEncodeBytes:
    def test_roundtrip(self):
        fa = FixedArrayType(U32, 3)
        encoded = fa.encode_bytes([1, 2, 3])
        decoded = fa.decode_bytes(encoded)
        assert list(decoded) == [1, 2, 3]

    def test_wrong_count_raises(self):
        fa = FixedArrayType(U32, 3)
        with pytest.raises(ValueError, match="expects 3 elements"):
            fa.encode_bytes([1, 2])

    def test_wrong_size_raises(self):
        fa = FixedArrayType(U32, 3)
        with pytest.raises(ValueError, match="Expected 12 bytes"):
            fa.decode_bytes(b"\x00" * 8)


class TestFixedArrayTypeDecode:
    def _build_table_with_direct(self, store, fa, values):
        """Build a TABLE containing a DIRECT entry with the encoded array."""
        entry = fa.encode(values, store)
        t = Table([entry])
        return t.build(store)

    def test_decode_direct(self, store):
        fa = FixedArrayType(U32, 3)
        table = self._build_table_with_direct(store, fa, [10, 20, 30])
        result = fa.decode(table, 0, store)
        assert result is not None and list(result) == [10, 20, 30]

    def test_decode_direct_misaligned(self, store):
        """DIRECT entry at an offset not aligned to element alignment should raise."""
        fa = FixedArrayType(U32, 1)
        # Build a table manually with misaligned DIRECT offset
        data = U32.encode_bytes(42)
        # heap_start for 2 vtable entries = 4 + 2*2 = 8 (4-aligned)
        # Put a padding byte first, then data at offset 9 (not 4-aligned)
        heap = b"\x00" + data + b"\x00" * 3
        heap_start = TableBlock.heap_start(2)
        table = TableBlock.build(
            [VTableEntry.null(), VTableEntry.direct(heap_start + 1)],
            heap,
        )
        with pytest.raises(ValueError, match="aligned"):
            fa.decode(table, 1, store)

    def test_decode_block_entry(self, store):
        """BLOCK entry containing a DataBlock should decode correctly."""
        fa = FixedArrayType(U32, 3)
        data_bytes = fa.encode_bytes([10, 20, 30])
        block = DataBlock.build(data_bytes, align=4)
        # Embed the DataBlock in a TABLE
        from hashbuffers.fitting import BlockEntry, Table

        entry = BlockEntry(block, 4, 3)
        t = Table([entry])
        table = t.build(store)
        result = fa.decode(table, 0, store)
        assert result is not None and list(result) == [10, 20, 30]

    def test_decode_block_misaligned(self, store):
        """BLOCK entry at misaligned offset should raise."""
        fa = FixedArrayType(U32, 1)
        data_block = DataBlock.build(U32.encode_bytes(42), align=4)
        block_bytes = data_block.encode()
        # Create table with BLOCK at an odd offset
        heap = b"\x00" + block_bytes
        heap_start = TableBlock.heap_start(2)
        # heap_start + 1 is odd → not 4-aligned
        # But BLOCK entries need 2-alignment for the block header.
        # Use offset that's 2-aligned but not 4-aligned.
        offset = heap_start + 2
        heap = b"\x00\x00" + block_bytes
        table = TableBlock.build(
            [VTableEntry.null(), VTableEntry.block(offset)],
            heap,
        )
        with pytest.raises(ValueError, match="aligned"):
            fa.decode(table, 1, store)

    def test_decode_block_wrong_type(self, store):
        """BLOCK entry that is not a DataBlock should raise."""
        fa = FixedArrayType(U8, 1)
        from hashbuffers.codec import SlotsBlock

        slots = SlotsBlock.build_slots([b"\x01"])
        from hashbuffers.fitting import BlockEntry, Table

        entry = BlockEntry(slots, 2, 1)
        t = Table([entry])
        table = t.build(store)
        with pytest.raises(ValueError, match="Expected DATA block"):
            fa.decode(table, 0, store)

    def test_decode_link_entry(self, store):
        """LINK entry pointing to a stored DataBlock should decode correctly."""
        fa = FixedArrayType(U32, 3)
        data_bytes = fa.encode_bytes([10, 20, 30])
        block = DataBlock.build(data_bytes, align=4)
        digest = store.store(block)
        link_bytes = Link(digest, 3).encode()
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build(
            [VTableEntry.link(heap_start)],
            link_bytes,
        )
        result = fa.decode(table, 0, store)
        assert result is not None and list(result) == [10, 20, 30]

    def test_decode_link_wrong_limit(self, store):
        """LINK with limit != count should raise."""
        fa = FixedArrayType(U32, 3)
        data_bytes = fa.encode_bytes([10, 20, 30])
        block = DataBlock.build(data_bytes, align=4)
        digest = store.store(block)
        link_bytes = Link(digest, 99).encode()  # wrong limit
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build(
            [VTableEntry.link(heap_start)],
            link_bytes,
        )
        with pytest.raises(ValueError, match="expects 3 elements, got 99"):
            fa.decode(table, 0, store)

    def test_decode_link_wrong_block_type(self, store):
        """LINK pointing to a non-DataBlock should raise."""
        fa = FixedArrayType(U8, 1)
        from hashbuffers.codec import SlotsBlock

        slots = SlotsBlock.build_slots([b"\x01"])
        digest = store.store(slots)
        link_bytes = Link(digest, 1).encode()
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build(
            [VTableEntry.link(heap_start)],
            link_bytes,
        )
        with pytest.raises(ValueError, match="Expected DATA block"):
            fa.decode(table, 0, store)

    def test_decode_inline_raises(self, store):
        """INLINE entry type should raise for a fixed array."""
        fa = FixedArrayType(U32, 1)
        table = TableBlock.build([VTableEntry.inline(42)], b"")
        with pytest.raises(ValueError, match="Expected DIRECT, BLOCK, or LINK"):
            fa.decode(table, 0, store)
