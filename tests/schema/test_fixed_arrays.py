"""Tests for fixed-size arrays and Field count= validation."""

import typing as t

import pytest

from hashbuffers.schema import (
    U16,
    Array,
    Field,
    HashBuffer,
)

from .conftest import Inner, Vec3

# --- Fixed 2D array ---


class Matrix(HashBuffer):
    """A fixed-size 2D array stored as DIRECT on the heap."""

    data: t.Sequence[t.Sequence[int]] | None = Field(0, Array(Vec3, count=4))


class TestFixed2DArray:
    def test_roundtrip(self, store):
        val = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]
        obj = Matrix(data=val)
        decoded = Matrix.decode(obj.encode(store), store)
        assert decoded.data == val

    def test_identity_like(self, store):
        val = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]]
        obj = Matrix(data=val)
        decoded = Matrix.decode(obj.encode(store), store)
        assert decoded.data == val

    def test_wrong_inner_count(self, store):
        """Inner dimension mismatch should fail at encode time."""
        with pytest.raises(ValueError, match="expects 3 elements"):
            Matrix(data=[[1, 2], [3, 4], [5, 6], [7, 8]]).encode(store)

    def test_wrong_outer_count(self, store):
        """Outer dimension mismatch should fail at encode time."""
        with pytest.raises(ValueError, match="expects 4 elements"):
            Matrix(data=[[1, 2, 3], [4, 5, 6]]).encode(store)


# --- Fixed 3D array ---


Plane = Array(U16, count=2)
Slab = Array(Plane, count=3)
Cube = Array(Slab, count=2)


class CubeStruct(HashBuffer):
    cube: t.Sequence[t.Sequence[t.Sequence[int]]] | None = Field(0, Cube)


class TestFixed3DArray:
    def test_roundtrip(self, store):
        val = [
            [[1, 2], [3, 4], [5, 6]],
            [[7, 8], [9, 10], [11, 12]],
        ]
        obj = CubeStruct(cube=val)
        decoded = CubeStruct.decode(obj.encode(store), store)
        assert decoded.cube == val


# --- Variable array of fixed-size arrays ---


class VarOfFixed(HashBuffer):
    """Variable number of 3-element vectors."""

    vectors: t.Sequence[t.Sequence[int]] | None = Field(0, Array(Vec3))


class TestVarOfFixedArray:
    def test_roundtrip(self, store):
        vecs = [[10, 20, 30], [40, 50, 60], [70, 80, 90]]
        obj = VarOfFixed(vectors=vecs)
        decoded = VarOfFixed.decode(obj.encode(store), store)
        assert decoded.vectors == vecs

    def test_empty(self, store):
        obj = VarOfFixed(vectors=[])
        decoded = VarOfFixed.decode(obj.encode(store), store)
        assert decoded.vectors == []

    def test_single(self, store):
        obj = VarOfFixed(vectors=[[1, 2, 3]])
        decoded = VarOfFixed.decode(obj.encode(store), store)
        assert decoded.vectors == [[1, 2, 3]]

    def test_large_forces_link_tree(self, store):
        """Many fixed-size vectors force multi-block DATA + link tree."""
        vecs = [[i, i + 1, i + 2] for i in range(500)]
        obj = VarOfFixed(vectors=vecs)
        decoded = VarOfFixed.decode(obj.encode(store), store)
        assert decoded.vectors == vecs

    def test_inner_count_mismatch_on_encode(self, store):
        """Inner array with wrong element count fails at encode."""
        with pytest.raises(ValueError, match="expects 3 elements"):
            VarOfFixed(vectors=[[1, 2]]).encode(store)


# --- Field count= parameter (decode-time validation) ---


class FixedCountArray(HashBuffer):
    """Schema prescribes exactly 5 elements, but wire format is variable."""

    values: t.Sequence[int] | None = Field(0, Array(U16, count=5))


class TestFixedCountArray:
    def test_roundtrip_correct_count(self, store):
        obj = FixedCountArray(values=[10, 20, 30, 40, 50])
        decoded = FixedCountArray.decode(obj.encode(store), store)
        assert decoded.values == [10, 20, 30, 40, 50]

    def test_count_mismatch_on_decode(self, store):
        """Encode with wrong count, then decode with schema that expects 5."""

        class Unconstrained(HashBuffer):
            values: t.Sequence[int] | None = Field(0, Array(U16))

        obj = Unconstrained(values=[1, 2, 3])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Expected 10 bytes, got 6"):
            FixedCountArray.decode(sb, store)

    def test_empty_vs_count(self, store):
        """Empty array decoded with count=5 should fail."""

        class Unconstrained(HashBuffer):
            values: t.Sequence[int] | None = Field(0, Array(U16))

        obj = Unconstrained(values=[])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Expected 10 bytes, got 0"):
            FixedCountArray.decode(sb, store)


class FixedCountStructArray(HashBuffer):
    """Fixed-count array of structs."""

    items: t.Sequence[Inner] | None = Field(0, Array(Inner, count=2))


class TestFixedCountStructArray:
    def test_correct_count(self, store):
        obj = FixedCountStructArray(items=[Inner(value=1), Inner(value=2)])
        decoded = FixedCountStructArray.decode(obj.encode(store), store)
        assert decoded.items is not None
        assert len(decoded.items) == 2

    def test_wrong_count(self, store):
        """3 items decoded with count=2 schema."""

        class Unconstrained(HashBuffer):
            items: t.Sequence[Inner] | None = Field(0, Array(Inner))

        obj = Unconstrained(items=[Inner(value=i) for i in range(3)])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Array expects 2 elements, got 3"):
            FixedCountStructArray.decode(sb, store)


class FixedCountFixedElemArray(HashBuffer):
    """Fixed-count array of fixed-size vectors. Doubly constrained."""

    rows: t.Sequence[t.Sequence[int]] | None = Field(0, Array(Vec3, count=4))


class TestFixedCountFixedElemArray:
    def test_correct(self, store):
        rows = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]
        obj = FixedCountFixedElemArray(rows=rows)
        decoded = FixedCountFixedElemArray.decode(obj.encode(store), store)
        assert decoded.rows == rows

    def test_count_mismatch(self, store):
        class Unconstrained(HashBuffer):
            rows: t.Sequence[t.Sequence[int]] | None = Field(0, Array(Vec3))

        obj = Unconstrained(rows=[[1, 2, 3], [4, 5, 6]])
        sb = obj.encode(store)
        with pytest.raises(ValueError, match="Expected 48 bytes, got 24"):
            FixedCountFixedElemArray.decode(sb, store)
