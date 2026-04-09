"""Unit tests for VarArrayType subclasses — DataArrayType, BytestringArrayType, BlockArrayType."""

import pytest

from hashbuffers.codec import Link, TableBlock, VTableEntry
from hashbuffers.data_model.adapter import AdapterCodec
from hashbuffers.data_model.array import (
    BlockArrayType,
    BytestringArrayType,
    DataArrayType,
)
from hashbuffers.data_model.primitive import U32
from hashbuffers.data_model.struct import StructField, StructType
from hashbuffers.fitting import BlockEntry, Table
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestDataArrayType:
    def test_encode_decode_roundtrip(self, store):
        dat = DataArrayType(U32)
        entry = dat.encode([1, 2, 3], store)
        t = Table([entry])
        table = t.build(store)
        result = dat.decode(table, 0, store)
        assert result is not None and list(result) == [1, 2, 3]

    def test_decode_null(self, store):
        dat = DataArrayType(U32)
        table = TableBlock.build([VTableEntry.null()], b"")
        assert dat.decode(table, 0, store) is None

    def test_count_mismatch_raises(self, store):
        dat = DataArrayType(U32, count=3)
        with pytest.raises(ValueError, match="expects 3 elements"):
            dat.encode([1, 2], store)

    def test_decode_link_with_count(self, store):
        """Decode a LINK entry; count should be checked against link.limit."""
        dat = DataArrayType(U32, count=3)
        entry = dat.encode([10, 20, 30], store)
        assert isinstance(entry, BlockEntry)
        digest = store.store(entry.block)
        link_bytes = Link(digest, 3).encode()
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build(
            [VTableEntry.link(heap_start)],
            link_bytes,
        )
        result = dat.decode(table, 0, store)
        assert result is not None and list(result) == [10, 20, 30]

    def test_decode_link_wrong_count_raises(self, store):
        """LINK with limit != count should raise."""
        dat = DataArrayType(U32, count=3)
        entry = dat.encode([10, 20, 30], store)
        assert isinstance(entry, BlockEntry)
        digest = store.store(entry.block)
        # LINK with wrong limit
        link_bytes = Link(digest, 99).encode()
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build(
            [VTableEntry.link(heap_start)],
            link_bytes,
        )
        with pytest.raises(ValueError, match="expects 3 elements, got 99"):
            dat.decode(table, 0, store)


class TestBytestringArrayType:
    def test_encode_decode_roundtrip(self, store):
        bat = BytestringArrayType()
        entry = bat.encode([b"foo", b"bar"], store)
        t = Table([entry])
        table = t.build(store)
        result = bat.decode(table, 0, store)
        assert result is not None and list(result) == [b"foo", b"bar"]

    def test_with_adapter(self, store):
        adapter = AdapterCodec(
            encode=lambda s: s.encode("utf-8"),
            decode=lambda b: b.decode("utf-8"),
        )
        bat = BytestringArrayType(adapter=adapter)
        entry = bat.encode(["hello", "world"], store)
        t = Table([entry])
        table = t.build(store)
        result = bat.decode(table, 0, store)
        assert result is not None and list(result) == ["hello", "world"]


class TestBlockArrayType:
    def test_encode_decode_roundtrip(self, store):
        inner_st = StructType([StructField(0, "x", U32)])
        bat = BlockArrayType(inner_st)
        # Encode as mappings
        entry = bat.encode([{"x": 1}, {"x": 2}], store)
        t = Table([entry])
        table = t.build(store)
        result = bat.decode(table, 0, store)
        assert result is not None
        assert len(result) == 2
        assert result[0]["x"] == 1
        assert result[1]["x"] == 2
