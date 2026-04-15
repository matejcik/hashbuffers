"""Unit tests for TableArray — leaf_length, leaf_to_list, entry_to_element."""

import pytest

from hashbuffers.arrays import TableArray, build_table_array
from hashbuffers.codec import DataBlock, Link, TableBlock
from hashbuffers.fitting import Table
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


def make_table_array(store, count):
    """Build a TABLE array of small empty tables."""
    entries = []
    for _ in range(count):
        t = Table([])
        entries.append(t.build_entry(store))
    entry = build_table_array(entries, store)
    return TableArray(entry.block, store)


class TestTableArrayBasic:
    def test_getitem(self, store):
        arr = make_table_array(store, 3)
        item = arr[0]
        assert isinstance(item, TableBlock)

    def test_len(self, store):
        arr = make_table_array(store, 5)
        assert len(arr) == 5

    def test_empty(self, store):
        arr = make_table_array(store, 0)
        assert len(arr) == 0


class TestTableArrayTypeChecks:
    def test_leaf_length_rejects_non_table(self):
        arr = TableArray.__new__(TableArray)
        block = DataBlock.build(b"data")
        with pytest.raises(ValueError, match="TableBlock"):
            arr.leaf_length(block)

    def test_leaf_to_list_rejects_non_table(self):
        arr = TableArray.__new__(TableArray)
        block = DataBlock.build(b"data")
        with pytest.raises(ValueError, match="TableBlock"):
            arr.leaf_to_list(block)


class TestTableArrayEntryToElement:
    def test_link_entry_fetches(self, store):
        """entry_to_element should fetch blocks via store for Link entries."""
        inner = TableBlock.build([], b"")
        digest = store.store(inner)
        link = Link(digest, 1)

        arr = TableArray.__new__(TableArray)
        arr.store = store
        result = arr.entry_to_element(link)
        assert isinstance(result, TableBlock)

    def test_block_entry_passthrough(self, store):
        """entry_to_element should return Block entries directly."""
        block = TableBlock.build([], b"")
        arr = TableArray.__new__(TableArray)
        arr.store = store
        result = arr.entry_to_element(block)
        assert result is block
