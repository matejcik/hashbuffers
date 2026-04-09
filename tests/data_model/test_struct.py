"""Tests for data_model.struct — StructType and LazyStructMapping directly."""

import pytest

from hashbuffers.codec import TableBlock
from hashbuffers.data_model.primitive import U8, U16, U32
from hashbuffers.data_model.struct import LazyStructMapping, StructField, StructType
from hashbuffers.fitting import BlockEntry
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


def make_struct_type(*fields):
    return StructType(fields)


class TestStructTypeInit:
    def test_empty_struct_rejected(self):
        with pytest.raises(ValueError, match="Empty structs"):
            StructType([])

    def test_duplicate_indices_rejected(self):
        with pytest.raises(ValueError, match="Duplicate"):
            StructType(
                [
                    StructField(0, "a", U8),
                    StructField(0, "b", U16),
                ]
            )

    def test_duplicate_names_rejected(self):
        with pytest.raises(ValueError, match="Duplicate"):
            StructType(
                [
                    StructField(0, "a", U8),
                    StructField(1, "a", U16),
                ]
            )

    def test_valid_struct(self):
        st = StructType(
            [
                StructField(0, "x", U32),
                StructField(1, "y", U16),
            ]
        )
        assert len(st.fields) == 2


class TestStructTypeEncode:
    def test_unknown_field_rejected(self, store):
        st = make_struct_type(StructField(0, "x", U32))
        with pytest.raises(ValueError, match="Unknown field name"):
            st.encode({"x": 1, "bogus": 2}, store)

    def test_missing_required_rejected(self, store):
        st = make_struct_type(StructField(0, "x", U32, required=True))
        with pytest.raises(ValueError, match="Required field"):
            st.encode({}, store)

    def test_optional_none_ok(self, store):
        st = make_struct_type(StructField(0, "x", U32))
        entry = st.encode({}, store)
        assert entry is not None

    def test_roundtrip(self, store):
        st = make_struct_type(
            StructField(0, "x", U32),
            StructField(1, "y", U16),
        )
        entry = st.encode({"x": 42, "y": 7}, store)
        data = entry.encode()
        # Use block_decoder for a top-level decode (StructType.decode
        # expects the struct to be at table[index] as a BLOCK/LINK entry)
        decoder = st.block_decoder(store)
        result = decoder(TableBlock.decode(data))
        assert result["x"] == 42
        assert result["y"] == 7


class TestStructTypeDecode:
    def test_decode_returns_none_for_null(self, store):
        # Build a table with a NULL at index 0
        st = make_struct_type(StructField(0, "x", U32))
        outer = make_struct_type(StructField(0, "inner", st))
        entry = outer.encode({}, store)
        data = entry.encode()
        table = TableBlock.decode(data)
        result = st.decode(table, 0, store)
        assert result is None

    def test_decode_embedded_struct(self, store):
        """StructType.decode should decode a struct embedded as a BLOCK entry."""
        inner_st = make_struct_type(StructField(0, "x", U32))
        outer_st = make_struct_type(StructField(0, "inner", inner_st))
        entry = outer_st.encode({"inner": {"x": 42}}, store)
        data = entry.encode()
        table = TableBlock.decode(data)
        result = inner_st.decode(table, 0, store)
        assert result is not None
        assert result["x"] == 42

    def test_decode_linked_struct(self, store):
        """StructType.decode should handle LINK entries (limit=1)."""
        from hashbuffers.codec import Link, VTableEntry

        inner_st = make_struct_type(StructField(0, "x", U32))
        # Build a valid inner table and store it as a LINK with limit=1
        inner_entry = inner_st.encode({"x": 99}, store)
        assert isinstance(inner_entry, BlockEntry)
        digest = store.store(inner_entry.block)
        link_bytes = Link(digest, 1).encode()
        heap_start = TableBlock.heap_start(1)
        parent = TableBlock.build([VTableEntry.link(heap_start)], link_bytes)
        result = inner_st.decode(parent, 0, store)
        assert result is not None
        assert result["x"] == 99

    def test_decode_link_bad_limit(self, store):
        """LINK entry with limit != 1 should be rejected."""
        from hashbuffers.codec import Link, VTableEntry

        st = make_struct_type(StructField(0, "x", U32))
        # Build a valid inner table to store
        inner_entry = st.encode({"x": 42}, store)
        assert isinstance(inner_entry, BlockEntry)
        digest = store.store(inner_entry.block)
        # Build a parent TABLE with a LINK entry having limit=5 (not 1)
        link_bytes = Link(digest, 5).encode()
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build([VTableEntry.link(heap_start)], link_bytes)
        with pytest.raises(ValueError, match="Expected LINK with limit 1"):
            st.decode(table, 0, store)

    def test_block_decoder_rejects_non_table(self, store):
        from hashbuffers.codec import DataBlock

        st = make_struct_type(StructField(0, "x", U32))
        decoder = st.block_decoder(store)
        data_block = DataBlock.build(b"hello")
        with pytest.raises(ValueError, match="Expected TABLE"):
            decoder(data_block)


class TestLazyStructMapping:
    def _make_mapping(self, store, values):
        """Encode values and return LazyStructMapping."""
        fields = [StructField(i, name, U32) for i, name in enumerate(values.keys())]
        st = StructType(fields)
        entry = st.encode(values, store)
        table = TableBlock.decode(entry.encode())
        return LazyStructMapping(fields, store, table)

    def test_getitem(self, store):
        m = self._make_mapping(store, {"a": 1, "b": 2})
        assert m["a"] == 1
        assert m["b"] == 2

    def test_getitem_missing_key(self, store):
        m = self._make_mapping(store, {"a": 1})
        with pytest.raises(KeyError):
            m["missing"]

    def test_iter(self, store):
        m = self._make_mapping(store, {"a": 1, "b": 2})
        assert set(m) == {"a", "b"}

    def test_len(self, store):
        m = self._make_mapping(store, {"a": 1, "b": 2})
        assert len(m) == 2

    def test_caching(self, store):
        m = self._make_mapping(store, {"a": 42})
        # First access decodes
        assert m["a"] == 42
        # Second access should return cached value
        assert m["a"] == 42

    def test_required_field_missing(self, store):
        """Required field that is None in the table should raise."""
        fields = [StructField(0, "x", U32, required=True)]
        st = StructType(fields)
        # Encode without the required field by using a different struct type
        other = StructType([StructField(0, "x", U32)])
        entry = other.encode({}, store)
        table = TableBlock.decode(entry.encode())
        m = LazyStructMapping(fields, store, table)
        with pytest.raises(ValueError, match="Required field"):
            m["x"]
