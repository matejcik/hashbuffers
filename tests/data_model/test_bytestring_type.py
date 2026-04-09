"""Unit tests for data_model.array.BytestringType — encode, decode, null."""

import pytest

from hashbuffers.codec import Link, TableBlock, VTableEntry
from hashbuffers.data_model.array import BytestringType
from hashbuffers.fitting import Table
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestBytestringType:
    def test_encode_decode_roundtrip(self, store):
        bt = BytestringType()
        entry = bt.encode(b"hello", store)
        t = Table([entry])
        table = t.build(store)
        result = bt.decode(table, 0, store)
        assert result == b"hello"

    def test_decode_null(self, store):
        bt = BytestringType()
        table = TableBlock.build([VTableEntry.null()], b"")
        assert bt.decode(table, 0, store) is None

    def test_decode_link(self, store):
        """LINK entry should be fetched and decoded."""
        from hashbuffers.fitting import BlockEntry

        bt = BytestringType()
        entry = bt.encode(b"linked data", store)
        assert isinstance(entry, BlockEntry)
        digest = store.store(entry.block)
        link_bytes = Link(digest, 1).encode()
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build(
            [VTableEntry.link(heap_start)],
            link_bytes,
        )
        result = bt.decode(table, 0, store)
        assert result == b"linked data"

    def test_encode_empty(self, store):
        bt = BytestringType()
        entry = bt.encode(b"", store)
        t = Table([entry])
        table = t.build(store)
        result = bt.decode(table, 0, store)
        assert result == b""
