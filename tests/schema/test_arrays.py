"""Tests for variable-length arrays of all element types."""

import typing as t

from hashbuffers.schema import (
    U16,
    U32,
    Array,
    Bytes,
    Field,
    HashBuffer,
)

from .conftest import (
    ArrayStruct,
    BlobStruct,
    Container,
    Item,
    StringsStruct,
)

# --- Primitive arrays ---


class TestPrimitiveArray:
    def test_small_array(self, store):
        obj = ArrayStruct(values=[1, 2, 3, 4, 5])
        decoded = ArrayStruct.decode(obj.encode(store), store)
        assert decoded.values == [1, 2, 3, 4, 5]

    def test_empty_array(self, store):
        obj = ArrayStruct(values=[])
        decoded = ArrayStruct.decode(obj.encode(store), store)
        assert decoded.values == []

    def test_null_array(self, store):
        obj = ArrayStruct()
        decoded = ArrayStruct.decode(obj.encode(store), store)
        assert decoded.values is None


# --- Struct arrays ---


class TestStructArray:
    def test_small_array(self, store):
        items = [Item(id=i, data=b"x" * 5) for i in range(3)]
        obj = Container(items=items)
        decoded = Container.decode(obj.encode(store), store)
        assert decoded.items is not None
        assert len(decoded.items) == 3
        for i, item in enumerate(decoded.items):
            assert item.id == i
            assert item.data == b"x" * 5

    def test_empty_array(self, store):
        obj = Container(items=[])
        decoded = Container.decode(obj.encode(store), store)
        assert decoded.items == []


# --- Bytes field ---


class TestBytesField:
    def test_small_bytes(self, store):
        obj = BlobStruct(data=b"hello world")
        decoded = BlobStruct.decode(obj.encode(store), store)
        assert decoded.data == b"hello world"

    def test_empty_bytes(self, store):
        obj = BlobStruct(data=b"")
        decoded = BlobStruct.decode(obj.encode(store), store)
        assert decoded.data == b""

    def test_null_bytes(self, store):
        obj = BlobStruct()
        decoded = BlobStruct.decode(obj.encode(store), store)
        assert decoded.data is None


# --- Bytes array ---


class TestBytesArray:
    def test_roundtrip(self, store):
        obj = StringsStruct(strings=[b"foo", b"bar", b"baz"])
        decoded = StringsStruct.decode(obj.encode(store), store)
        assert decoded.strings == [b"foo", b"bar", b"baz"]

    def test_empty_strings(self, store):
        obj = StringsStruct(strings=[b"", b"x", b""])
        decoded = StringsStruct.decode(obj.encode(store), store)
        assert decoded.strings == [b"", b"x", b""]

    def test_null(self, store):
        obj = StringsStruct()
        decoded = StringsStruct.decode(obj.encode(store), store)
        assert decoded.strings is None


# --- Array of structs containing arrays ---


class Row(HashBuffer):
    cells: t.Sequence[int] | None = Field(0, Array(U16))


class Grid(HashBuffer):
    rows: t.Sequence[Row] | None = Field(0, Array(Row))


class TestArrayOfStructArrays:
    def test_grid_roundtrip(self, store):
        rows = [Row(cells=[i * 10 + j for j in range(5)]) for i in range(4)]
        obj = Grid(rows=rows)
        decoded = Grid.decode(obj.encode(store), store)
        assert decoded.rows is not None
        assert len(decoded.rows) == 4
        for i, row in enumerate(decoded.rows):
            assert row.cells == [i * 10 + j for j in range(5)]

    def test_grid_with_empty_rows(self, store):
        rows = [Row(cells=[]), Row(cells=[1, 2]), Row(cells=[])]
        obj = Grid(rows=rows)
        decoded = Grid.decode(obj.encode(store), store)
        assert decoded.rows is not None
        assert decoded.rows[0].cells == []
        assert decoded.rows[1].cells == [1, 2]
        assert decoded.rows[2].cells == []


# --- Array of arrays (no wrapper structs) ---


class TestArrayOfArrays:
    """Array(Array(U32)) — variable array of variable arrays, native support."""

    def test_roundtrip(self, store):
        class VarOfVar(HashBuffer):
            arrays: t.Sequence[t.Sequence[int]] | None = Field(0, Array(Array(U32)))

        obj = VarOfVar(arrays=[[1, 2, 3], [], [100, 200]])
        decoded = VarOfVar.decode(obj.encode(store), store)
        assert decoded.arrays == [[1, 2, 3], [], [100, 200]]

    def test_empty_outer(self, store):
        class VarOfVar(HashBuffer):
            arrays: t.Sequence[t.Sequence[int]] | None = Field(0, Array(Array(U32)))

        obj = VarOfVar(arrays=[])
        decoded = VarOfVar.decode(obj.encode(store), store)
        assert decoded.arrays == []

    def test_all_empty_inner(self, store):
        class VarOfVar(HashBuffer):
            arrays: t.Sequence[t.Sequence[int]] | None = Field(0, Array(Array(U32)))

        obj = VarOfVar(arrays=[[] for _ in range(5)])
        decoded = VarOfVar.decode(obj.encode(store), store)
        assert decoded.arrays is not None
        assert len(decoded.arrays) == 5
        assert all(a == [] for a in decoded.arrays)

    def test_nested_bytes_arrays(self, store):
        """Array(Array(Bytes)) — array of arrays of byte strings."""

        class NestedBytes(HashBuffer):
            groups: t.Sequence[t.Sequence[bytes]] | None = Field(0, Array(Array(Bytes)))

        obj = NestedBytes(groups=[[b"a", b"bb"], [], [b"ccc"]])
        decoded = NestedBytes.decode(obj.encode(store), store)
        assert decoded.groups == [[b"a", b"bb"], [], [b"ccc"]]


# --- Large arrays forcing link trees ---


class TestLargeArrays:
    def test_large_primitive_array(self, store):
        """Array of 2000 u32s should create a link tree and still roundtrip."""
        values = list(range(2000))
        obj = ArrayStruct(values=values)
        sb = obj.encode(store)
        decoded = ArrayStruct.decode(sb, store)
        assert decoded.values == values

    def test_large_bytes_array(self, store):
        """Many byte strings forcing multi-block SLOTS + link tree."""
        strings = [f"string-{i:04d}".encode() for i in range(500)]
        obj = StringsStruct(strings=strings)
        sb = obj.encode(store)
        decoded = StringsStruct.decode(sb, store)
        assert decoded.strings == strings

    def test_large_struct_array(self, store):
        """Many small structs forcing multi-block TABLE array."""
        items = [Item(id=i, data=f"d{i}".encode()) for i in range(200)]
        obj = Container(items=items)
        sb = obj.encode(store)
        decoded = Container.decode(sb, store)
        assert decoded.items is not None
        assert len(decoded.items) == 200
        for i, item in enumerate(decoded.items):
            assert item.id == i
            assert item.data == f"d{i}".encode()
