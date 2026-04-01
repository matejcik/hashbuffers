"""Tests for primitive type encoding: signed/unsigned edges, floats, inline boundaries."""

import math

import pytest

from hashbuffers.codec import TableBlock, VTableEntryType
from hashbuffers.schema import (
    F32,
    F64,
    I8,
    I16,
    I64,
    U8,
    U16,
    U64,
    Field,
    HashBuffer,
)

# --- Schema classes ---


class SignedEdges(HashBuffer):
    i8_min: int | None = Field(0, I8)
    i8_max: int | None = Field(1, I8)
    i16_min: int | None = Field(2, I16)
    i64_big: int | None = Field(3, I64)


class UnsignedEdges(HashBuffer):
    u8_max: int | None = Field(0, U8)
    u64_max: int | None = Field(1, U64)
    inline_boundary: int | None = Field(2, U16)


class FloatStruct(HashBuffer):
    f64_val: float | None = Field(0, F64)
    f32_val: float | None = Field(1, F32)


# --- Signed edge cases ---


class TestSignedEdgeCases:
    def test_i8_extremes(self, store):
        obj = SignedEdges(i8_min=-128, i8_max=127)
        decoded = SignedEdges.decode(obj.encode(store).data, store)
        assert decoded.i8_min == -128
        assert decoded.i8_max == 127

    def test_i16_min(self, store):
        obj = SignedEdges(i16_min=-32768)
        decoded = SignedEdges.decode(obj.encode(store).data, store)
        assert decoded.i16_min == -32768

    def test_i64_large_negative(self, store):
        obj = SignedEdges(i64_big=-(2**63))
        decoded = SignedEdges.decode(obj.encode(store).data, store)
        assert decoded.i64_big == -(2**63)

    def test_inline_boundary_signed(self, store):
        """I16 value -4096 fits inline (13-bit two's complement), -4097 does not."""
        obj = SignedEdges(i16_min=-4096)
        sb = obj.encode(store)
        table = TableBlock.decode(sb.data)
        assert table.vtable[2].type == VTableEntryType.INLINE

        obj2 = SignedEdges(i16_min=-4097)
        sb2 = obj2.encode(store)
        table2 = TableBlock.decode(sb2.data)
        assert table2.vtable[2].type == VTableEntryType.DIRECT


# --- Unsigned edge cases ---


class TestUnsignedEdgeCases:
    def test_u8_255(self, store):
        obj = UnsignedEdges(u8_max=255)
        decoded = UnsignedEdges.decode(obj.encode(store).data, store)
        assert decoded.u8_max == 255

    def test_u64_max(self, store):
        obj = UnsignedEdges(u64_max=2**64 - 1)
        decoded = UnsignedEdges.decode(obj.encode(store).data, store)
        assert decoded.u64_max == 2**64 - 1

    def test_inline_boundary_unsigned(self, store):
        """U16 value 8191 fits inline, 8192 does not."""
        obj = UnsignedEdges(inline_boundary=8191)
        sb = obj.encode(store)
        table = TableBlock.decode(sb.data)
        assert table.vtable[2].type == VTableEntryType.INLINE

        obj2 = UnsignedEdges(inline_boundary=8192)
        sb2 = obj2.encode(store)
        table2 = TableBlock.decode(sb2.data)
        assert table2.vtable[2].type == VTableEntryType.DIRECT


# --- Float edge cases ---


class TestFloatEdges:
    def test_f64_roundtrip(self, store):
        obj = FloatStruct(f64_val=3.14159265358979)
        decoded = FloatStruct.decode(obj.encode(store).data, store)
        assert decoded.f64_val == pytest.approx(3.14159265358979)

    def test_f32_roundtrip(self, store):
        obj = FloatStruct(f32_val=2.5)
        decoded = FloatStruct.decode(obj.encode(store).data, store)
        assert decoded.f32_val == pytest.approx(2.5)

    def test_negative_zero(self, store):
        obj = FloatStruct(f64_val=-0.0)
        decoded = FloatStruct.decode(obj.encode(store).data, store)
        assert decoded.f64_val is not None
        assert decoded.f64_val == 0.0
        assert math.copysign(1, decoded.f64_val) == -1.0

    def test_infinity(self, store):
        obj = FloatStruct(f64_val=float("inf"))
        decoded = FloatStruct.decode(obj.encode(store).data, store)
        assert decoded.f64_val == float("inf")

    def test_negative_infinity(self, store):
        obj = FloatStruct(f64_val=float("-inf"))
        decoded = FloatStruct.decode(obj.encode(store).data, store)
        assert decoded.f64_val == float("-inf")

    def test_nan(self, store):
        obj = FloatStruct(f64_val=float("nan"))
        decoded = FloatStruct.decode(obj.encode(store).data, store)
        assert decoded.f64_val is not None
        assert math.isnan(decoded.f64_val)


# --- Out-of-range primitive values ---


class TestOutOfRange:
    def test_u8_large_value_goes_direct_and_overflows(self, store):
        """U8 value >8191 forces DIRECT path where to_bytes raises."""

        class S(HashBuffer):
            v: int | None = Field(0, U8)

        with pytest.raises(OverflowError):
            S(v=0x1_0000).encode(store)

    def test_u8_negative(self, store):
        """Negative value for unsigned type overflows on DIRECT path."""

        class S(HashBuffer):
            v: int | None = Field(0, U8)

        # -1 is signed, doesn't fit unsigned inline, goes DIRECT → OverflowError
        with pytest.raises(OverflowError):
            S(v=-1).encode(store)

    def test_i8_large_positive_goes_direct_and_overflows(self, store):
        """I8 value >4095 forces DIRECT path where to_bytes raises."""

        class S(HashBuffer):
            v: int | None = Field(0, I8)

        with pytest.raises(OverflowError):
            S(v=0x1_0000).encode(store)

    def test_i8_large_negative_goes_direct_and_overflows(self, store):
        """I8 value < -4096 forces DIRECT path where to_bytes raises."""

        class S(HashBuffer):
            v: int | None = Field(0, I8)

        with pytest.raises(OverflowError):
            S(v=-0x1_0000).encode(store)

    def test_wrong_type_for_primitive(self, store):
        class S(HashBuffer):
            v: int | None = Field(0, U8)

        with pytest.raises((TypeError, ValueError)):
            S(v="hello").encode(store)  # type: ignore[arg-type]
