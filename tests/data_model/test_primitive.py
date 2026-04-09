"""Unit tests for data_model.primitive — PrimitiveInt, PrimitiveFloat."""

import pytest

from hashbuffers.codec import TableBlock, VTableEntry, VTableEntryType
from hashbuffers.data_model.primitive import F32, F64, I32, U8, U16, U32
from hashbuffers.fitting import DirectEntry, InlineIntEntry
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestPrimitiveInt:
    def test_encode_decode_bytes_u8(self):
        encoded = U8.encode_bytes(255)
        assert encoded == b"\xff"
        assert U8.decode_bytes(encoded) == 255

    def test_encode_decode_bytes_i32_negative(self):
        encoded = I32.encode_bytes(-1)
        assert I32.decode_bytes(encoded) == -1

    def test_get_size(self):
        assert U8.get_size() == 1
        assert U32.get_size() == 4

    def test_get_alignment(self):
        assert U8.get_alignment() == 1
        assert U32.get_alignment() == 4

    def test_encode_inline(self, store):
        entry = U8.encode(5, store)
        assert isinstance(entry, InlineIntEntry)

    def test_encode_direct(self, store):
        entry = U32.encode(0xDEADBEEF, store)
        assert isinstance(entry, DirectEntry)

    def test_decode_null(self, store):
        table = TableBlock.build([VTableEntry.null()], b"")
        assert U32.decode(table, 0, store) is None


class TestPrimitiveFloat:
    def test_f32_encode_decode_bytes(self):
        encoded = F32.encode_bytes(2.5)
        assert F32.decode_bytes(encoded) == pytest.approx(2.5)

    def test_f64_encode_decode_bytes(self):
        encoded = F64.encode_bytes(3.14)
        assert F64.decode_bytes(encoded) == pytest.approx(3.14)

    def test_f32_get_size(self):
        assert F32.get_size() == 4

    def test_f32_get_alignment(self):
        assert F32.get_alignment() == 4

    def test_f64_get_size(self):
        assert F64.get_size() == 8

    def test_format_property(self):
        assert F32.format == "<f"
        assert F64.format == "<d"

    def test_encode_direct(self, store):
        entry = F32.encode(1.0, store)
        assert isinstance(entry, DirectEntry)

    def test_decode_null(self, store):
        table = TableBlock.build([VTableEntry.null()], b"")
        assert F32.decode(table, 0, store) is None

    def test_decode_roundtrip(self, store):
        entry = F32.encode(2.5, store)
        assert isinstance(entry, DirectEntry)
        # Build a table containing this entry
        from hashbuffers.fitting import Table

        t = Table([entry])
        block = t.build(store)
        assert F32.decode(block, 0, store) == pytest.approx(2.5)
