"""Tests for adapter types: Bool, String, EnumType."""

import typing as t
from enum import IntEnum

import pytest

from hashbuffers.schema import (
    U16,
    Array,
    Bool,
    EnumType,
    Field,
    HashBuffer,
    String,
)

# --- IntEnum support ---


class Color(IntEnum):
    RED = 0
    GREEN = 1
    BLUE = 2


class TestEnumType:
    def test_roundtrip(self, store):
        class WithEnum(HashBuffer):
            color: Color | None = Field(0, EnumType(Color))

        obj = WithEnum(color=Color.GREEN)
        decoded = WithEnum.decode(obj.encode(store), store)
        assert decoded.color is Color.GREEN

    def test_enum_array(self, store):
        class WithEnumArray(HashBuffer):
            colors: t.Sequence[Color] | None = Field(0, Array(EnumType(Color)))

        obj = WithEnumArray(colors=[Color.RED, Color.BLUE, Color.GREEN])
        decoded = WithEnumArray.decode(obj.encode(store), store)
        assert decoded.colors == [Color.RED, Color.BLUE, Color.GREEN]

    def test_enum_fixed_array(self, store):
        class WithEnumFixedArray(HashBuffer):
            colors: t.Sequence[Color] | None = Field(0, Array(EnumType(Color), count=3))

        obj = WithEnumFixedArray(colors=[Color.RED, Color.BLUE, Color.GREEN])
        decoded = WithEnumFixedArray.decode(obj.encode(store), store)
        assert decoded.colors == [Color.RED, Color.BLUE, Color.GREEN]

    def test_enum_with_u16_repr(self, store):
        class BigEnum(IntEnum):
            A = 1000
            B = 2000

        class WithBigEnum(HashBuffer):
            val: BigEnum | None = Field(0, EnumType(BigEnum, repr=U16))

        obj = WithBigEnum(val=BigEnum.B)
        decoded = WithBigEnum.decode(obj.encode(store), store)
        assert decoded.val is BigEnum.B

    def test_null_enum(self, store):
        class WithEnum(HashBuffer):
            color: Color | None = Field(0, EnumType(Color))

        obj = WithEnum()
        decoded = WithEnum.decode(obj.encode(store), store)
        assert decoded.color is None

    def test_invalid_enum_value_on_decode(self, store):
        """Decoding an int that isn't a valid enum member should raise ValueError."""
        from hashbuffers.schema import U8

        class WithEnum(HashBuffer):
            color: Color | None = Field(0, EnumType(Color))

        # Encode a raw U8 value of 99 (not in Color)
        class RawStruct(HashBuffer):
            color: int | None = Field(0, U8)

        sb = RawStruct(color=99).encode(store)
        with pytest.raises(ValueError):
            WithEnum.decode(sb, store)


# --- String type ---


class TestStringType:
    def test_roundtrip(self, store):
        class WithString(HashBuffer):
            name: str | None = Field(0, String)

        obj = WithString(name="hello world")
        decoded = WithString.decode(obj.encode(store), store)
        assert decoded.name == "hello world"

    def test_empty_string(self, store):
        class WithString(HashBuffer):
            name: str | None = Field(0, String)

        obj = WithString(name="")
        decoded = WithString.decode(obj.encode(store), store)
        assert decoded.name == ""

    def test_string_array(self, store):
        class WithStringArray(HashBuffer):
            names: t.Sequence[str] | None = Field(0, Array(String))

        obj = WithStringArray(names=["alice", "bob", "charlie"])
        decoded = WithStringArray.decode(obj.encode(store), store)
        assert decoded.names == ["alice", "bob", "charlie"]

    def test_unicode(self, store):
        class WithString(HashBuffer):
            text: str | None = Field(0, String)

        obj = WithString(text="hello \u2603 snowman")
        decoded = WithString.decode(obj.encode(store), store)
        assert decoded.text == obj.text

    def test_null_string(self, store):
        class WithString(HashBuffer):
            name: str | None = Field(0, String)

        obj = WithString()
        decoded = WithString.decode(obj.encode(store), store)
        assert decoded.name is None

    def test_zero_bytes(self, store):
        class WithString(HashBuffer):
            name: str | None = Field(0, String)

        obj = WithString(name="hello\0world")
        decoded = WithString.decode(obj.encode(store), store)
        assert decoded.name == obj.name


# --- Bool adapter ---


class TestBoolAdapter:
    def test_true(self, store):
        class WithBool(HashBuffer):
            flag: bool | None = Field(0, Bool)

        obj = WithBool(flag=True)
        decoded = WithBool.decode(obj.encode(store), store)
        assert decoded.flag is True

    def test_false(self, store):
        class WithBool(HashBuffer):
            flag: bool | None = Field(0, Bool)

        obj = WithBool(flag=False)
        decoded = WithBool.decode(obj.encode(store), store)
        assert decoded.flag is False

    def test_bool_array(self, store):
        class WithBoolArray(HashBuffer):
            flags: t.Sequence[bool] | None = Field(0, Array(Bool))

        obj = WithBoolArray(flags=[True, False, True, True])
        decoded = WithBoolArray.decode(obj.encode(store), store)
        assert decoded.flags == [True, False, True, True]
