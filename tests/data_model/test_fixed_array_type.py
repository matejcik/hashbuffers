"""Unit tests for data_model.array.FixedArrayType — init, encode, decode paths."""

import pytest

from hashbuffers.codec import DataBlock, Link, TableBlock, VTableEntry
from hashbuffers.data_model.array import FixedArrayType
from hashbuffers.data_model.primitive import U8, U32
from hashbuffers.fitting import Table
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
    def _build_table_with_block(self, store, fa, values):
        """Build a TABLE containing a BLOCK entry with the encoded array."""
        entry = fa.encode(values, store)
        t = Table([entry])
        return t.build(store)

    def test_decode_block(self, store):
        fa = FixedArrayType(U32, 3)
        table = self._build_table_with_block(store, fa, [10, 20, 30])
        result = fa.decode(table, 0, store)
        assert result is not None and list(result) == [10, 20, 30]

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

    def test_decode_block_wrong_data_type(self, store):
        """BLOCK entry containing non-DATA block should raise for fixed array."""
        fa = FixedArrayType(U32, 1)
        from hashbuffers.codec import SlotsBlock
        from hashbuffers.fitting import BlockEntry, Table

        slots = SlotsBlock.build_slots([b"\x00\x00\x00\x00"])
        entry = BlockEntry(slots, 2, 1)
        t = Table([entry])
        table = t.build(store)
        with pytest.raises(ValueError, match="Expected DATA block"):
            fa.decode(table, 0, store)

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
        with pytest.raises(ValueError, match="Expected BLOCK or LINK"):
            fa.decode(table, 0, store)
