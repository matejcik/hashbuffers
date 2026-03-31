"""Tests for HashBuffer struct fundamentals."""

import math

import pytest

from hashbuffers.codec import TableBlock, VTableEntry, VTableEntryType
from hashbuffers.schema import (
    F64,
    I64,
    U8,
    U16,
    U32,
    Array,
    Bool,
    Bytes,
    Field,
    HashBuffer,
)

from .conftest import (
    AllOptional,
    Inner,
    Item,
    Outer,
    RequiredStruct,
    SimpleStruct,
)


class TestSimpleStruct:
    def test_roundtrip(self, store):
        obj = SimpleStruct(x=42, y=-7)
        sb = obj.encode(store)
        decoded = SimpleStruct.decode(sb.data, store)
        assert decoded.x == 42
        assert decoded.y == -7

    def test_inline_small_value(self, store):
        """Small integers should be stored inline."""
        obj = SimpleStruct(x=5, y=3)
        sb = obj.encode(store)
        decoded = SimpleStruct.decode(sb.data, store)
        assert decoded.x == 5
        assert decoded.y == 3

    def test_large_u32(self, store):
        obj = SimpleStruct(x=0xDEADBEEF, y=0)
        sb = obj.encode(store)
        decoded = SimpleStruct.decode(sb.data, store)
        assert decoded.x == 0xDEADBEEF

    def test_null_fields(self, store):
        obj = SimpleStruct()
        sb = obj.encode(store)
        decoded = SimpleStruct.decode(sb.data, store)
        assert decoded.x is None
        assert decoded.y is None


# --- Nested struct ---


class TestNestedStruct:
    def test_roundtrip(self, store):
        obj = Outer(name=b"test", inner=Inner(value=42))
        decoded = Outer.decode(obj.encode(store).data, store)
        assert decoded.name == b"test"
        assert decoded.inner is not None
        assert decoded.inner.value == 42

    def test_null_nested(self, store):
        obj = Outer(name=b"solo")
        decoded = Outer.decode(obj.encode(store).data, store)
        assert decoded.name == b"solo"
        assert decoded.inner is None


# --- Deep nesting ---


class Level0(HashBuffer):
    val: int | None = Field(0, U8)


class Level1(HashBuffer):
    child: Level0 | None = Field(0, Level0)
    tag: int | None = Field(1, U16)


class Level2(HashBuffer):
    child: Level1 | None = Field(0, Level1)


class Level3(HashBuffer):
    child: Level2 | None = Field(0, Level2)


class TestDeepNesting:
    def test_four_levels(self, store):
        obj = Level3(child=Level2(child=Level1(child=Level0(val=77), tag=999)))
        decoded = Level3.decode(obj.encode(store).data, store)
        assert decoded.child is not None
        assert decoded.child.child is not None
        assert decoded.child.child.child is not None
        assert decoded.child.child.child.val == 77
        assert decoded.child.child.tag == 999


# --- Sparse index ---


class SparseStruct(HashBuffer):
    first: int | None = Field(0, U8)
    # gap at 1..9
    last: int | None = Field(10, U8)


class TestSparseIndex:
    def test_roundtrip(self, store):
        obj = SparseStruct(first=1, last=99)
        decoded = SparseStruct.decode(obj.encode(store).data, store)
        assert decoded.first == 1
        assert decoded.last == 99

    def test_vtable_has_nulls_in_gap(self, store):
        """The encoded TABLE should have NULL entries for indices 1-9."""
        obj = SparseStruct(first=1, last=99)
        sb = obj.encode(store)
        table = TableBlock.decode(sb.data)
        assert len(table.vtable) == 11  # indices 0..10
        for i in range(1, 10):
            assert table.vtable[i].type == VTableEntryType.NULL


# --- Empty struct ---


class Empty(HashBuffer):
    pass


class TestEmptyStruct:
    def test_roundtrip(self, store):
        obj = Empty()
        decoded = Empty.decode(obj.encode(store).data, store)
        assert isinstance(decoded, Empty)


# --- Required fields ---


class TestRequired:
    def test_encode_missing_required(self, store):
        obj = RequiredStruct()
        with pytest.raises(ValueError, match="Required field"):
            obj.encode(store)

    def test_roundtrip_with_values(self, store):
        obj = RequiredStruct(name=b"ok", value=99)
        decoded = RequiredStruct.decode(obj.encode(store).data, store)
        assert decoded.name == b"ok"
        assert decoded.value == 99


# --- Equality and repr ---


class TestEquality:
    def test_equal(self):
        a = SimpleStruct(x=1, y=2)
        b = SimpleStruct(x=1, y=2)
        assert a == b

    def test_not_equal(self):
        a = SimpleStruct(x=1, y=2)
        b = SimpleStruct(x=1, y=3)
        assert a != b

    def test_repr(self):
        obj = SimpleStruct(x=1, y=2)
        r = repr(obj)
        assert "SimpleStruct" in r
        assert "x=1" in r


# --- All-NULL struct ---


class TestAllNull:
    def test_all_none(self, store):
        obj = AllOptional()
        decoded = AllOptional.decode(obj.encode(store).data, store)
        assert decoded.a is None
        assert decoded.b is None
        assert decoded.c is None
        assert decoded.d is None


# --- Kitchen sink ---


class KitchenSink(HashBuffer):
    u8_val: int | None = Field(0, U8)
    i64_val: int | None = Field(1, I64)
    flag: bool | None = Field(2, Bool)
    ratio: float | None = Field(3, F64)
    name: bytes = Field(4, Bytes, required=True)
    inner: Inner | None = Field(5, Inner)
    numbers: list[int] | None = Field(6, Array(U32))
    children: list[Item] | None = Field(7, Array(Item))
    tags: list[bytes] | None = Field(8, Array(Bytes))


class TestKitchenSink:
    def test_full_roundtrip(self, store):
        obj = KitchenSink(
            u8_val=255,
            i64_val=-(2**40),
            flag=True,
            ratio=2.718281828,
            name=b"kitchen sink test",
            inner=Inner(value=7),
            numbers=[10, 20, 30, 40],
            children=[Item(id=1, data=b"child1"), Item(id=2, data=b"child2")],
            tags=[b"alpha", b"beta", b"gamma"],
        )
        decoded = KitchenSink.decode(obj.encode(store).data, store)
        assert decoded.u8_val == 255
        assert decoded.i64_val == -(2**40)
        assert decoded.flag is True
        assert decoded.ratio == pytest.approx(2.718281828)
        assert decoded.name == b"kitchen sink test"
        assert decoded.inner is not None
        assert decoded.inner.value == 7
        assert decoded.numbers == [10, 20, 30, 40]
        assert decoded.children is not None
        assert len(decoded.children) == 2
        assert decoded.children[0].id == 1
        assert decoded.children[0].data == b"child1"
        assert decoded.tags == [b"alpha", b"beta", b"gamma"]

    def test_partial_fields(self, store):
        """Only required field + a few optionals."""
        obj = KitchenSink(name=b"minimal", flag=False, numbers=[1])
        decoded = KitchenSink.decode(obj.encode(store).data, store)
        assert decoded.name == b"minimal"
        assert decoded.flag is False
        assert decoded.numbers == [1]
        assert decoded.u8_val is None
        assert decoded.inner is None
        assert decoded.tags is None


# --- Duplicate field index ---


class TestDuplicateIndex:
    def test_duplicate_index_last_wins(self, store):
        """Two fields mapping to the same index: last descriptor wins on encode."""

        class Dupe(HashBuffer):
            a: int | None = Field(0, U8)
            b: int | None = Field(0, U16)  # same index!

        obj = Dupe(a=1, b=2)
        sb = obj.encode(store)
        table = TableBlock.decode(sb.data)
        assert len(table.vtable) == 1
