"""Unit tests for array build functions — encode side."""

import pytest

from hashbuffers.arrays import (
    BytestringArray,
    BytestringTree,
    DataArray,
    TableArray,
    build_bytestring_array,
    build_bytestring_tree,
    build_data_array,
    build_table_array,
)
from hashbuffers.codec import SIZE_MAX, DataBlock, LinksBlock
from hashbuffers.fitting import BlockEntry, Table
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestBuildBytestringTree:
    def test_empty(self, store):
        entry = build_bytestring_tree(b"", store)
        tree = BytestringTree(entry.block, store)
        assert tree.to_bytes() == b""

    def test_small(self, store):
        entry = build_bytestring_tree(b"hello", store)
        tree = BytestringTree(entry.block, store)
        assert tree.to_bytes() == b"hello"

    def test_large_creates_link_tree(self, store):
        data = b"x" * (SIZE_MAX * 2)
        entry = build_bytestring_tree(data, store)
        assert isinstance(entry.block, LinksBlock)
        tree = BytestringTree(entry.block, store)
        assert tree.to_bytes() == data


class TestBuildDataArray:
    def test_empty(self, store):
        entry = build_data_array([], 4, store)
        arr = DataArray(entry.block, store, 4, 4)
        assert len(arr) == 0

    def test_small(self, store):
        values = [i.to_bytes(4, "little") for i in range(10)]
        entry = build_data_array(values, 4, store)
        arr = DataArray(
            entry.block,
            store,
            4,
            4,
            decode_element=lambda b: int.from_bytes(b, "little"),
        )
        assert len(arr) == 10
        assert arr[0] == 0
        assert arr[9] == 9

    def test_large_creates_link_tree(self, store):
        values = [i.to_bytes(4, "little") for i in range(2000)]
        entry = build_data_array(values, 4, store)
        arr = DataArray(
            entry.block,
            store,
            4,
            4,
            decode_element=lambda b: int.from_bytes(b, "little"),
        )
        assert len(arr) == 2000
        assert arr[0] == 0
        assert arr[1999] == 1999

    def test_elem_too_large_raises(self, store):
        """Element whose padded size exceeds block capacity."""
        # SIZE_MAX = 8191, start_offset = max(align, 2).
        # With align=2, start_offset=2, available = 8189.
        # Element of size 8190 with align=2 → padded=8190 > 8189 → max_elems=0
        big_elem = b"\x00" * 8190
        with pytest.raises(ValueError, match="too large"):
            build_data_array([big_elem], 2, store)


class TestBuildBytestringArray:
    def test_empty(self, store):
        entry = build_bytestring_array([], store)
        arr = BytestringArray(entry.block, store)
        assert len(arr) == 0

    def test_small(self, store):
        entry = build_bytestring_array([b"foo", b"bar"], store)
        arr = BytestringArray(entry.block, store)
        assert arr[0] == b"foo"
        assert arr[1] == b"bar"

    def test_many_items_creates_link_tree(self, store):
        items = [f"item-{i}".encode() for i in range(500)]
        entry = build_bytestring_array(items, store)
        arr = BytestringArray(entry.block, store)
        assert len(arr) == 500
        assert arr[0] == b"item-0"
        assert arr[499] == b"item-499"

    def test_oversized_element_uses_table(self, store):
        """An element larger than SIZE_MAX-6 should go through bytestring tree in TABLE."""
        big = b"x" * (SIZE_MAX + 100)
        entry = build_bytestring_array([big], store)
        # The leaf should be a TABLE block (not SLOTS)
        arr = BytestringArray(entry.block, store)
        assert arr[0] == big

    def test_block_overflow_seals_current(self, store):
        """Elements that collectively overflow a SLOTS block should produce multiple blocks."""
        # Fill near block capacity to force multiple SLOTS blocks
        elem_size = 4000  # ~half of SIZE_MAX
        items = [b"x" * elem_size for _ in range(5)]
        entry = build_bytestring_array(items, store)
        arr = BytestringArray(entry.block, store)
        assert len(arr) == 5
        for i in range(5):
            assert arr[i] == b"x" * elem_size


class TestBuildTableArray:
    def test_empty(self, store):
        entry = build_table_array([], store)
        arr = TableArray(entry.block, store)
        assert len(arr) == 0

    def test_small(self, store):
        entries = [Table([]).build_entry(store) for _ in range(3)]
        entry = build_table_array(entries, store)
        arr = TableArray(entry.block, store)
        assert len(arr) == 3

    def test_many_items_creates_link_tree(self, store):
        entries = [Table([]).build_entry(store) for _ in range(200)]
        entry = build_table_array(entries, store)
        arr = TableArray(entry.block, store)
        assert len(arr) == 200

    def test_overflow_seals_and_retries(self, store):
        """Elements that don't fit in a single TABLE block should be split."""
        # Create elements large enough that only a few fit per block
        big_data = DataBlock.build(b"\x00" * 2000, align=4)
        entries = [BlockEntry(big_data, 4, 1) for _ in range(10)]
        entry = build_table_array(entries, store)
        arr = TableArray(entry.block, store)
        assert len(arr) == 10
