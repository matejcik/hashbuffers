"""Tests for schema.py — targeting uncovered edge cases."""

import typing as t

import pytest

from hashbuffers.schema import (
    U8,
    U16,
    U32,
    Array,
    Field,
    HashBuffer,
)
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


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


class TestArrayTypeDispatch:
    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError, match="Unsupported field type"):
            Array(42)  # type: ignore[arg-type]

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
