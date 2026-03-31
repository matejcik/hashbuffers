"""Tests for the schema DSL."""

import hashlib
import hmac
import math
from enum import Enum

import pytest

from hashbuffers.codec import (
    BlockType,
    DataBlock,
    Link,
    SlotsBlock,
    TableBlock,
    Tagged16,
    VTableEntry,
    VTableEntryType,
    decode_block,
)
from hashbuffers.schema import (
    Adapted,
    Array,
    Bool,
    Bytes,
    EnumType,
    Field,
    HashBuffer,
    Primitive,
    String,
    U8,
    U16,
    U32,
    U64,
    I8,
    I16,
    I32,
    I64,
    F32,
    F64,
)
from hashbuffers.store import BlockStore, StoredBlock


@pytest.fixture
def store():
    return BlockStore(b"test-key")


# --- Simple struct ---


class SimpleStruct(HashBuffer):
    x = Field(0, U32)
    y = Field(1, I16)


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


# --- Booleans and floats ---


class TypesStruct(HashBuffer):
    flag = Field(0, Bool)
    ratio = Field(1, F64)
    small_float = Field(2, F32)


class TestTypesStruct:
    def test_bool_roundtrip(self, store):
        obj = TypesStruct(flag=True)
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert decoded.flag is True

    def test_bool_false(self, store):
        obj = TypesStruct(flag=False)
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert decoded.flag is False

    def test_float_roundtrip(self, store):
        obj = TypesStruct(ratio=3.14159265358979)
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert decoded.ratio == pytest.approx(3.14159265358979)

    def test_f32_roundtrip(self, store):
        obj = TypesStruct(small_float=2.5)
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert decoded.small_float == pytest.approx(2.5)


# --- Nested struct ---


class Inner(HashBuffer):
    value = Field(0, U8)


class Outer(HashBuffer):
    name = Field(0, Bytes)
    inner = Field(1, Inner)


class TestNestedStruct:
    def test_roundtrip(self, store):
        obj = Outer(name=b"test", inner=Inner(value=42))
        decoded = Outer.decode(obj.encode(store).data, store)
        assert decoded.name == b"test"
        assert decoded.inner.value == 42

    def test_null_nested(self, store):
        obj = Outer(name=b"solo")
        decoded = Outer.decode(obj.encode(store).data, store)
        assert decoded.name == b"solo"
        assert decoded.inner is None


# --- Primitive arrays ---


class ArrayStruct(HashBuffer):
    values = Field(0, Array(U32))


class TestPrimitiveArray:
    def test_small_array(self, store):
        obj = ArrayStruct(values=[1, 2, 3, 4, 5])
        decoded = ArrayStruct.decode(obj.encode(store).data, store)
        assert decoded.values == [1, 2, 3, 4, 5]

    def test_empty_array(self, store):
        obj = ArrayStruct(values=[])
        decoded = ArrayStruct.decode(obj.encode(store).data, store)
        assert decoded.values == []

    def test_null_array(self, store):
        obj = ArrayStruct()
        decoded = ArrayStruct.decode(obj.encode(store).data, store)
        assert decoded.values is None


# --- Struct arrays ---


class Item(HashBuffer):
    id = Field(0, U16)
    data = Field(1, Bytes)


class Container(HashBuffer):
    items = Field(0, Array(Item))


class TestStructArray:
    def test_small_array(self, store):
        items = [Item(id=i, data=b"x" * 5) for i in range(3)]
        obj = Container(items=items)
        decoded = Container.decode(obj.encode(store).data, store)
        assert len(decoded.items) == 3
        for i, item in enumerate(decoded.items):
            assert item.id == i
            assert item.data == b"x" * 5

    def test_empty_array(self, store):
        obj = Container(items=[])
        decoded = Container.decode(obj.encode(store).data, store)
        assert decoded.items == []


# --- Bytes field ---


class BlobStruct(HashBuffer):
    data = Field(0, Bytes)


class TestBytesField:
    def test_small_bytes(self, store):
        obj = BlobStruct(data=b"hello world")
        decoded = BlobStruct.decode(obj.encode(store).data, store)
        assert decoded.data == b"hello world"

    def test_empty_bytes(self, store):
        obj = BlobStruct(data=b"")
        decoded = BlobStruct.decode(obj.encode(store).data, store)
        assert decoded.data == b""

    def test_null_bytes(self, store):
        obj = BlobStruct()
        decoded = BlobStruct.decode(obj.encode(store).data, store)
        assert decoded.data is None


# --- Bytes array ---


class StringsStruct(HashBuffer):
    strings = Field(0, Array(Bytes))


class TestBytesArray:
    def test_roundtrip(self, store):
        obj = StringsStruct(strings=[b"foo", b"bar", b"baz"])
        decoded = StringsStruct.decode(obj.encode(store).data, store)
        assert decoded.strings == [b"foo", b"bar", b"baz"]

    def test_empty_strings(self, store):
        obj = StringsStruct(strings=[b"", b"x", b""])
        decoded = StringsStruct.decode(obj.encode(store).data, store)
        assert decoded.strings == [b"", b"x", b""]

    def test_null(self, store):
        obj = StringsStruct()
        decoded = StringsStruct.decode(obj.encode(store).data, store)
        assert decoded.strings is None


# --- Required fields ---


class RequiredStruct(HashBuffer):
    name = Field(0, Bytes, required=True)
    value = Field(1, U32, required=True)


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


# ============================================================
# Ugly / edge-case / negative tests
# ============================================================


# --- Deep nesting: struct containing struct containing struct ---


class Level0(HashBuffer):
    val = Field(0, U8)


class Level1(HashBuffer):
    child = Field(0, Level0)
    tag = Field(1, U16)


class Level2(HashBuffer):
    child = Field(0, Level1)


class Level3(HashBuffer):
    child = Field(0, Level2)


class TestDeepNesting:
    def test_four_levels(self, store):
        obj = Level3(child=Level2(child=Level1(child=Level0(val=77), tag=999)))
        decoded = Level3.decode(obj.encode(store).data, store)
        assert decoded.child.child.child.val == 77
        assert decoded.child.child.tag == 999


# --- Array of structs that themselves contain arrays ---


class Row(HashBuffer):
    cells = Field(0, Array(U16))


class Grid(HashBuffer):
    rows = Field(0, Array(Row))


class TestArrayOfStructArrays:
    def test_grid_roundtrip(self, store):
        rows = [Row(cells=[i * 10 + j for j in range(5)]) for i in range(4)]
        obj = Grid(rows=rows)
        decoded = Grid.decode(obj.encode(store).data, store)
        assert len(decoded.rows) == 4
        for i, row in enumerate(decoded.rows):
            assert row.cells == [i * 10 + j for j in range(5)]

    def test_grid_with_empty_rows(self, store):
        rows = [Row(cells=[]), Row(cells=[1, 2]), Row(cells=[])]
        obj = Grid(rows=rows)
        decoded = Grid.decode(obj.encode(store).data, store)
        assert decoded.rows[0].cells == []
        assert decoded.rows[1].cells == [1, 2]
        assert decoded.rows[2].cells == []


# --- Struct containing array of bytes arrays (double nesting) ---


class Document(HashBuffer):
    title = Field(0, Bytes)
    pages = Field(1, Array(Bytes))


class Library(HashBuffer):
    docs = Field(0, Array(Document))


class TestNestedBytesArrays:
    def test_library_roundtrip(self, store):
        docs = [
            Document(title=b"Book A", pages=[b"page1", b"page2"]),
            Document(title=b"Book B", pages=[b"single page"]),
        ]
        obj = Library(docs=docs)
        decoded = Library.decode(obj.encode(store).data, store)
        assert len(decoded.docs) == 2
        assert decoded.docs[0].title == b"Book A"
        assert decoded.docs[0].pages == [b"page1", b"page2"]
        assert decoded.docs[1].pages == [b"single page"]


# --- Sparse index: gaps in field indices ---


class SparseStruct(HashBuffer):
    first = Field(0, U8)
    # gap at 1..9
    last = Field(10, U8)


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


# --- Signed edge cases ---


class SignedEdges(HashBuffer):
    i8_min = Field(0, I8)
    i8_max = Field(1, I8)
    i16_min = Field(2, I16)
    i64_big = Field(3, I64)


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


class UnsignedEdges(HashBuffer):
    u8_max = Field(0, U8)
    u64_max = Field(1, U64)
    inline_boundary = Field(2, U16)


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


# --- Manually crafted invalid blocks fed to decode ---


class TestManuallyInvalidBlocks:
    def test_decode_wrong_block_type(self, store):
        """Feed a DATA block where a TABLE is expected."""
        data_block = DataBlock.build(b"not a table").encode()
        with pytest.raises(ValueError, match="Expected .* block, got"):
            SimpleStruct.decode(data_block, store)

    def test_decode_truncated_block(self, store):
        """Truncated block data should fail."""
        obj = SimpleStruct(x=42, y=1)
        sb = obj.encode(store)
        with pytest.raises((ValueError, IOError)):
            SimpleStruct.decode(sb.data[:4], store)

    def test_decode_reserved_entry_type(self, store):
        """TABLE with a reserved entry type (0b010) should be rejected."""
        valid = TableBlock.build(
            [VTableEntry(VTableEntryType.NULL, 0)], b""
        )
        encoded = bytearray(valid.encode())
        # Mutate entry at bytes 4-5 to reserved type 0b010
        entry_val = (0b010 << 13) | 0
        encoded[4:6] = entry_val.to_bytes(2, "little")
        with pytest.raises(ValueError):
            SimpleStruct.decode(bytes(encoded), store)

    def test_decode_link_with_missing_store_block(self, store):
        """LINK pointing to a digest not in the store should raise KeyError."""
        fake_link = Link(b"\xaa" * 32, 1)
        link_bytes = fake_link.encode()
        vtable = [
            VTableEntry(VTableEntryType.LINK, 8),
            VTableEntry(VTableEntryType.NULL, 0),
        ]
        table = TableBlock.build(vtable, link_bytes)
        with pytest.raises(KeyError):
            Outer.decode(table.encode(), store)

    def test_decode_corrupted_nested_block(self, store):
        """Nested BLOCK with garbage data should fail validation."""
        garbage = b"\xff" * 20
        vtable = [VTableEntry(VTableEntryType.BLOCK, 6)]
        table = TableBlock.build(vtable, garbage)
        with pytest.raises((ValueError, IOError)):
            Outer.decode(table.encode(), store)

    def test_decode_extra_fields_ignored(self, store):
        """TABLE with more entries than schema expects should still decode."""
        vtable = [
            VTableEntry(VTableEntryType.INLINE, 42),  # x
            VTableEntry(VTableEntryType.INLINE, 7),    # y
            VTableEntry(VTableEntryType.NULL, 0),
            VTableEntry(VTableEntryType.NULL, 0),
            VTableEntry(VTableEntryType.NULL, 0),
        ]
        table = TableBlock.build(vtable, b"")
        decoded = SimpleStruct.decode(table.encode(), store)
        assert decoded.x == 42
        assert decoded.y == 7

    def test_decode_shorter_table_than_schema(self, store):
        """TABLE shorter than schema: missing fields become None."""
        vtable = [VTableEntry(VTableEntryType.INLINE, 42)]
        table = TableBlock.build(vtable, b"")
        decoded = SimpleStruct.decode(table.encode(), store)
        assert decoded.x == 42
        assert decoded.y is None

    def test_decode_required_missing_from_short_table(self, store):
        """Required field missing from a short TABLE should raise."""
        vtable = []
        table = TableBlock.build(vtable, b"")
        with pytest.raises(ValueError, match="Required field"):
            RequiredStruct.decode(table.encode(), store)

    def test_decode_slots_where_data_expected(self, store):
        """A SLOTS block where a DATA array is expected should fail."""
        slots = SlotsBlock.build_slots([b"wrong"]).encode()
        vtable = [VTableEntry(VTableEntryType.BLOCK, 6)]
        table = TableBlock.build(vtable, slots)
        with pytest.raises(ValueError, match="Unexpected block type"):
            ArrayStruct.decode(table.encode(), store)


# --- Large arrays that force link trees ---


class TestLargeArrays:
    def test_large_primitive_array(self, store):
        """Array of 2000 u32s should create a link tree and still roundtrip."""
        values = list(range(2000))
        obj = ArrayStruct(values=values)
        sb = obj.encode(store)
        decoded = ArrayStruct.decode(sb.data, store)
        assert decoded.values == values

    def test_large_bytes_array(self, store):
        """Many byte strings forcing multi-block SLOTS + link tree."""
        strings = [f"string-{i:04d}".encode() for i in range(500)]
        obj = StringsStruct(strings=strings)
        sb = obj.encode(store)
        decoded = StringsStruct.decode(sb.data, store)
        assert decoded.strings == strings

    def test_large_struct_array(self, store):
        """Many small structs forcing multi-block TABLE array."""
        items = [Item(id=i, data=f"d{i}".encode()) for i in range(200)]
        obj = Container(items=items)
        sb = obj.encode(store)
        decoded = Container.decode(sb.data, store)
        assert len(decoded.items) == 200
        for i, item in enumerate(decoded.items):
            assert item.id == i
            assert item.data == f"d{i}".encode()


# --- All-NULL struct ---


class AllOptional(HashBuffer):
    a = Field(0, U32)
    b = Field(1, Bytes)
    c = Field(2, Array(U8))
    d = Field(3, Array(Bytes))


class TestAllNull:
    def test_all_none(self, store):
        obj = AllOptional()
        decoded = AllOptional.decode(obj.encode(store).data, store)
        assert decoded.a is None
        assert decoded.b is None
        assert decoded.c is None
        assert decoded.d is None


# --- Kitchen sink: struct with every field type ---


class KitchenSink(HashBuffer):
    u8_val = Field(0, U8)
    i64_val = Field(1, I64)
    flag = Field(2, Bool)
    ratio = Field(3, F64)
    name = Field(4, Bytes, required=True)
    inner = Field(5, Inner)
    numbers = Field(6, Array(U32))
    children = Field(7, Array(Item))
    tags = Field(8, Array(Bytes))


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
        assert decoded.inner.value == 7
        assert decoded.numbers == [10, 20, 30, 40]
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


# --- Duplicate field index (schema abuse) ---


class TestDuplicateIndex:
    def test_duplicate_index_last_wins(self, store):
        """Two fields mapping to the same index: last descriptor wins on encode."""

        class Dupe(HashBuffer):
            a = Field(0, U8)
            b = Field(0, U16)  # same index!

        obj = Dupe(a=1, b=2)
        sb = obj.encode(store)
        table = TableBlock.decode(sb.data)
        assert len(table.vtable) == 1


# --- Float edge cases ---


class TestFloatEdges:
    def test_negative_zero(self, store):
        obj = TypesStruct(ratio=-0.0)
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert decoded.ratio == 0.0
        assert math.copysign(1, decoded.ratio) == -1.0

    def test_infinity(self, store):
        obj = TypesStruct(ratio=float("inf"))
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert decoded.ratio == float("inf")

    def test_negative_infinity(self, store):
        obj = TypesStruct(ratio=float("-inf"))
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert decoded.ratio == float("-inf")

    def test_nan(self, store):
        obj = TypesStruct(ratio=float("nan"))
        decoded = TypesStruct.decode(obj.encode(store).data, store)
        assert math.isnan(decoded.ratio)


# --- HMAC mismatch on decode from store ---


class TestStoreIntegrity:
    def test_tampered_block_in_store(self, store):
        """If a stored block is tampered with, retrieval should fail."""
        inner = Inner(value=5)
        sb = inner.encode(store)
        tampered = StoredBlock(b"\x00" * len(sb.data), sb.link, sb.alignment)
        store._blocks[sb.link.digest] = tampered
        with pytest.raises(ValueError, match="HMAC verification failed"):
            store[sb.link.digest]


# --- Single-element edge cases ---


class Singleton(HashBuffer):
    only = Field(0, U64)


class TestSingleton:
    def test_zero(self, store):
        obj = Singleton(only=0)
        decoded = Singleton.decode(obj.encode(store).data, store)
        assert decoded.only == 0

    def test_u64_one(self, store):
        obj = Singleton(only=1)
        decoded = Singleton.decode(obj.encode(store).data, store)
        assert decoded.only == 1


# --- Empty HashBuffer (no fields at all) ---


class Empty(HashBuffer):
    pass


class TestEmptyStruct:
    def test_roundtrip(self, store):
        obj = Empty()
        decoded = Empty.decode(obj.encode(store).data, store)
        assert isinstance(decoded, Empty)


# ============================================================
# Fixed-size array tests
# ============================================================


Vec3 = Array(U32, count=3)


class Matrix(HashBuffer):
    """A fixed-size 2D array stored as DIRECT on the heap."""
    data = Field(0, Array(Vec3, count=4))


class TestFixed2DArray:
    def test_roundtrip(self, store):
        val = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]
        obj = Matrix(data=val)
        decoded = Matrix.decode(obj.encode(store).data, store)
        assert decoded.data == val

    def test_identity_like(self, store):
        val = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]]
        obj = Matrix(data=val)
        decoded = Matrix.decode(obj.encode(store).data, store)
        assert decoded.data == val

    def test_wrong_inner_count(self, store):
        """Inner dimension mismatch should fail at encode time."""
        with pytest.raises(ValueError, match="expects 3 elements"):
            Matrix(data=[[1, 2], [3, 4], [5, 6], [7, 8]]).encode(store)

    def test_wrong_outer_count(self, store):
        """Outer dimension mismatch should fail at encode time."""
        with pytest.raises(ValueError, match="expects 4 elements"):
            Matrix(data=[[1, 2, 3], [4, 5, 6]]).encode(store)


# --- Fixed-size 3D array (u16[2][3][2] = 2x3x2 cube) ---

Plane = Array(U16, count=2)
Slab = Array(Plane, count=3)
Cube = Array(Slab, count=2)


class CubeStruct(HashBuffer):
    cube = Field(0, Cube)


class TestFixed3DArray:
    def test_roundtrip(self, store):
        val = [
            [[1, 2], [3, 4], [5, 6]],
            [[7, 8], [9, 10], [11, 12]],
        ]
        obj = CubeStruct(cube=val)
        decoded = CubeStruct.decode(obj.encode(store).data, store)
        assert decoded.cube == val


# --- Variable array of fixed-size arrays (list[u32[3]]) ---


class VarOfFixed(HashBuffer):
    """Variable number of 3-element vectors."""
    vectors = Field(0, Array(Vec3))


class TestVarOfFixedArray:
    def test_roundtrip(self, store):
        vecs = [[10, 20, 30], [40, 50, 60], [70, 80, 90]]
        obj = VarOfFixed(vectors=vecs)
        decoded = VarOfFixed.decode(obj.encode(store).data, store)
        assert decoded.vectors == vecs

    def test_empty(self, store):
        obj = VarOfFixed(vectors=[])
        decoded = VarOfFixed.decode(obj.encode(store).data, store)
        assert decoded.vectors == []

    def test_single(self, store):
        obj = VarOfFixed(vectors=[[1, 2, 3]])
        decoded = VarOfFixed.decode(obj.encode(store).data, store)
        assert decoded.vectors == [[1, 2, 3]]

    def test_large_forces_link_tree(self, store):
        """Many fixed-size vectors force multi-block DATA + link tree."""
        vecs = [[i, i + 1, i + 2] for i in range(500)]
        obj = VarOfFixed(vectors=vecs)
        decoded = VarOfFixed.decode(obj.encode(store).data, store)
        assert decoded.vectors == vecs

    def test_inner_count_mismatch_on_encode(self, store):
        """Inner array with wrong element count fails at encode."""
        with pytest.raises(ValueError, match="expects 3 elements"):
            VarOfFixed(vectors=[[1, 2]]).encode(store)


# ============================================================
# True array-of-arrays tests (no wrapper structs!)
# ============================================================


class TestArrayOfArrays:
    """Array(Array(U32)) — variable array of variable arrays, native support."""

    def test_roundtrip(self, store):
        class VarOfVar(HashBuffer):
            arrays = Field(0, Array(Array(U32)))

        obj = VarOfVar(arrays=[[1, 2, 3], [], [100, 200]])
        decoded = VarOfVar.decode(obj.encode(store).data, store)
        assert decoded.arrays == [[1, 2, 3], [], [100, 200]]

    def test_empty_outer(self, store):
        class VarOfVar(HashBuffer):
            arrays = Field(0, Array(Array(U32)))

        obj = VarOfVar(arrays=[])
        decoded = VarOfVar.decode(obj.encode(store).data, store)
        assert decoded.arrays == []

    def test_all_empty_inner(self, store):
        class VarOfVar(HashBuffer):
            arrays = Field(0, Array(Array(U32)))

        obj = VarOfVar(arrays=[[] for _ in range(5)])
        decoded = VarOfVar.decode(obj.encode(store).data, store)
        assert all(a == [] for a in decoded.arrays)

    def test_nested_bytes_arrays(self, store):
        """Array(Array(Bytes)) — array of arrays of byte strings."""

        class NestedBytes(HashBuffer):
            groups = Field(0, Array(Array(Bytes)))

        obj = NestedBytes(groups=[[b"a", b"bb"], [], [b"ccc"]])
        decoded = NestedBytes.decode(obj.encode(store).data, store)
        assert decoded.groups == [[b"a", b"bb"], [], [b"ccc"]]


# ============================================================
# Fixed-count array validation (count parameter)
# ============================================================


class FixedCountArray(HashBuffer):
    """Schema prescribes exactly 5 elements, but wire format is variable."""
    values = Field(0, Array(U16), count=5)


class TestFixedCountArray:
    def test_roundtrip_correct_count(self, store):
        obj = FixedCountArray(values=[10, 20, 30, 40, 50])
        decoded = FixedCountArray.decode(obj.encode(store).data, store)
        assert decoded.values == [10, 20, 30, 40, 50]

    def test_count_mismatch_on_decode(self, store):
        """Encode with wrong count, then decode with schema that expects 5."""

        class Unconstrained(HashBuffer):
            values = Field(0, Array(U16))

        obj = Unconstrained(values=[1, 2, 3])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Array count mismatch.*expects 5.*got 3"):
            FixedCountArray.decode(sb.data, store)

    def test_empty_vs_count(self, store):
        """Empty array decoded with count=5 should fail."""

        class Unconstrained(HashBuffer):
            values = Field(0, Array(U16))

        obj = Unconstrained(values=[])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Array count mismatch"):
            FixedCountArray.decode(sb.data, store)


class FixedCountStructArray(HashBuffer):
    """Fixed-count array of structs."""
    items = Field(0, Array(Inner), count=2)


class TestFixedCountStructArray:
    def test_correct_count(self, store):
        obj = FixedCountStructArray(items=[Inner(value=1), Inner(value=2)])
        decoded = FixedCountStructArray.decode(obj.encode(store).data, store)
        assert len(decoded.items) == 2

    def test_wrong_count(self, store):
        """3 items decoded with count=2 schema."""

        class Unconstrained(HashBuffer):
            items = Field(0, Array(Inner))

        obj = Unconstrained(items=[Inner(value=i) for i in range(3)])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Array count mismatch.*expects 2.*got 3"):
            FixedCountStructArray.decode(sb.data, store)


class FixedCountFixedElemArray(HashBuffer):
    """Fixed-count array of fixed-size vectors. Doubly constrained."""
    rows = Field(0, Array(Vec3), count=4)


class TestFixedCountFixedElemArray:
    def test_correct(self, store):
        rows = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]
        obj = FixedCountFixedElemArray(rows=rows)
        decoded = FixedCountFixedElemArray.decode(obj.encode(store).data, store)
        assert decoded.rows == rows

    def test_count_mismatch(self, store):
        class Unconstrained(HashBuffer):
            rows = Field(0, Array(Vec3))

        obj = Unconstrained(rows=[[1, 2, 3], [4, 5, 6]])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Array count mismatch.*expects 4.*got 2"):
            FixedCountFixedElemArray.decode(sb.data, store)


# ============================================================
# Adapter tests
# ============================================================


class Color(Enum):
    RED = 0
    GREEN = 1
    BLUE = 2


class TestEnumType:
    def test_roundtrip(self, store):
        class WithEnum(HashBuffer):
            color = Field(0, EnumType(Color))

        obj = WithEnum(color=Color.GREEN)
        decoded = WithEnum.decode(obj.encode(store).data, store)
        assert decoded.color is Color.GREEN

    def test_enum_array(self, store):
        class WithEnumArray(HashBuffer):
            colors = Field(0, Array(EnumType(Color)))

        obj = WithEnumArray(colors=[Color.RED, Color.BLUE, Color.GREEN])
        decoded = WithEnumArray.decode(obj.encode(store).data, store)
        assert decoded.colors == [Color.RED, Color.BLUE, Color.GREEN]

    def test_enum_with_u16_repr(self, store):
        class BigEnum(Enum):
            A = 1000
            B = 2000

        class WithBigEnum(HashBuffer):
            val = Field(0, EnumType(BigEnum, repr=U16))

        obj = WithBigEnum(val=BigEnum.B)
        decoded = WithBigEnum.decode(obj.encode(store).data, store)
        assert decoded.val is BigEnum.B

    def test_null_enum(self, store):
        class WithEnum(HashBuffer):
            color = Field(0, EnumType(Color))

        obj = WithEnum()
        decoded = WithEnum.decode(obj.encode(store).data, store)
        assert decoded.color is None


class TestStringType:
    def test_roundtrip(self, store):
        class WithString(HashBuffer):
            name = Field(0, String)

        obj = WithString(name="hello world")
        decoded = WithString.decode(obj.encode(store).data, store)
        assert decoded.name == "hello world"

    def test_string_array(self, store):
        class WithStringArray(HashBuffer):
            names = Field(0, Array(String))

        obj = WithStringArray(names=["alice", "bob", "charlie"])
        decoded = WithStringArray.decode(obj.encode(store).data, store)
        assert decoded.names == ["alice", "bob", "charlie"]

    def test_unicode(self, store):
        class WithString(HashBuffer):
            text = Field(0, String)

        obj = WithString(text="hello \u2603 snowman")
        decoded = WithString.decode(obj.encode(store).data, store)
        assert decoded.text == "hello \u2603 snowman"

    def test_null_string(self, store):
        class WithString(HashBuffer):
            name = Field(0, String)

        obj = WithString()
        decoded = WithString.decode(obj.encode(store).data, store)
        assert decoded.name is None


class TestBoolAdapter:
    def test_true(self, store):
        class WithBool(HashBuffer):
            flag = Field(0, Bool)

        obj = WithBool(flag=True)
        decoded = WithBool.decode(obj.encode(store).data, store)
        assert decoded.flag is True

    def test_false(self, store):
        class WithBool(HashBuffer):
            flag = Field(0, Bool)

        obj = WithBool(flag=False)
        decoded = WithBool.decode(obj.encode(store).data, store)
        assert decoded.flag is False

    def test_bool_array(self, store):
        class WithBoolArray(HashBuffer):
            flags = Field(0, Array(Bool))

        obj = WithBoolArray(flags=[True, False, True, True])
        decoded = WithBoolArray.decode(obj.encode(store).data, store)
        assert decoded.flags == [True, False, True, True]
