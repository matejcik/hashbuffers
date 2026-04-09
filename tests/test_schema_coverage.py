"""Tests for schema.py — targeting uncovered edge cases."""

import typing as t
from enum import IntEnum

import pytest

from hashbuffers.schema import (
    U8,
    U16,
    U32,
    Array,
    Bool,
    Bytes,
    EnumType,
    Field,
    HashBuffer,
    String,
)
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestStringType:
    def test_roundtrip(self, store):
        class WithString(HashBuffer):
            name: str | None = Field(0, String)

        obj = WithString(name="hello")
        decoded = WithString.decode(obj.encode(store), store)
        assert decoded.name == "hello"

    def test_null(self, store):
        class WithString(HashBuffer):
            name: str | None = Field(0, String)

        obj = WithString()
        decoded = WithString.decode(obj.encode(store), store)
        assert decoded.name is None


class TestEnumType:
    def test_roundtrip(self, store):
        class Color(IntEnum):
            RED = 1
            GREEN = 2
            BLUE = 3

        class WithEnum(HashBuffer):
            color: Color | None = Field(0, EnumType(Color))

        obj = WithEnum(color=Color.GREEN)
        decoded = WithEnum.decode(obj.encode(store), store)
        assert decoded.color == Color.GREEN

    def test_null(self, store):
        class Color(IntEnum):
            RED = 1

        class WithEnum(HashBuffer):
            color: Color | None = Field(0, EnumType(Color))

        obj = WithEnum()
        decoded = WithEnum.decode(obj.encode(store), store)
        assert decoded.color is None


class TestHashBufferInit:
    def test_unknown_field_rejected(self):
        class Simple(HashBuffer):
            x: int | None = Field(0, U8)

        with pytest.raises(ValueError, match="Unknown field"):
            Simple(x=1, bogus=2)  # type: ignore[call-arg]


class TestHashBufferEquality:
    def test_not_equal_different_type(self):
        class A(HashBuffer):
            x: int | None = Field(0, U8)

        a = A(x=1)
        assert a.__eq__("not a hashbuffer") is NotImplemented


class TestFieldDescriptor:
    def test_unbound_field_type_raises(self):
        """Accessing field_type before bind() should raise RuntimeError."""
        field = Field(0, U8)
        with pytest.raises(RuntimeError, match="not bound"):
            field.field_type  # type: ignore[union-attr]

    def test_normalize_required_none_raises(self):
        """Setting a required field to None should raise ValueError."""
        field = Field(0, U8, required=True)
        field.name = "test_field"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="Required field"):
            field._normalize(None)  # type: ignore[arg-type]

    def test_class_level_access_returns_field(self):
        """Accessing a field on the class (not instance) should return the Field descriptor."""

        class Simple(HashBuffer):
            x: int | None = Field(0, U8)

        assert isinstance(Simple.x, Field)

    def test_unset_field_returns_none(self):
        """Accessing a field that was never set should return None."""

        class Simple(HashBuffer):
            x: int | None = Field(0, U8)
            y: int | None = Field(1, U16)

        obj = Simple.__new__(Simple)
        obj._hb_state = {}  # type: ignore[attr-defined]  # empty state, fields not populated
        assert obj.x is None


class TestHashBufferFieldTypeLinkLimit:
    def test_link_limit_not_1_raises(self, store):
        """A HashBuffer field decoded from a LINK with limit != 1 should raise."""
        from hashbuffers.codec import Link, TableBlock, VTableEntry

        class Inner(HashBuffer):
            val: int | None = Field(0, U8)

        class Outer(HashBuffer):
            inner: Inner | None = Field(0, Inner)

        # Build a valid Inner and store it
        inner_obj = Inner(val=42)
        inner_entry = inner_obj._encode_table_entry(store)  # type: ignore[attr-defined]
        digest = store.store(inner_entry.block)

        # Build a parent TABLE with a LINK having limit=5
        link_bytes = Link(digest, 5).encode()
        heap_start = TableBlock.heap_start(1)
        table = TableBlock.build(
            [VTableEntry.link(heap_start)],
            link_bytes,
        )
        decoded = Outer._decode_from_table(table, store)  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="Expected LINK with limit 1"):
            _ = decoded.inner  # lazy access triggers the decode


class TestArrayTypeDispatch:
    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError, match="Unsupported field type"):
            Array(42)  # type: ignore[arg-type]

    def test_bytestring_array_with_adapter(self, store):
        """Array(String) should create a BytestringArrayType with adapter."""

        class WithStrings(HashBuffer):
            items: t.Sequence[str] | None = Field(0, Array(String))

        obj = WithStrings(items=["hello", "world"])
        decoded = WithStrings.decode(obj.encode(store), store)
        assert decoded.items == ["hello", "world"]

    def test_fixed_array_too_large_falls_through(self, store):
        """A fixed array with too many elements should fall through to variable array."""

        class Big(HashBuffer):
            data: t.Sequence[int] | None = Field(0, Array(U32, count=5000))

        obj = Big(data=list(range(5000)))
        decoded = Big.decode(obj.encode(store), store)
        assert decoded.data == list(range(5000))

    def test_block_decoder_type_array(self, store):
        """Array() with a BlockDecoderType (not _HashBufferFieldType) should work."""
        from hashbuffers.data_model.struct import StructField, StructType

        st = StructType([StructField(0, "x", U32)])
        # Pass the StructType directly to Array() — it's a BlockDecoderType
        array_type = Array(st)
        assert array_type is not None
