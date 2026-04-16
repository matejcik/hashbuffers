"""Unit tests for BytestringArray — leaf types, entry_to_element, mixed elements."""

import pytest

from hashbuffers.arrays import BytestringArray, build_bytestring_array
from hashbuffers.codec import SIZE_MAX, DataBlock
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestBytestringArrayBasic:
    def test_getitem_int(self, store):
        block = build_bytestring_array([b"foo", b"bar", b"baz"], store)
        arr = BytestringArray(block, store)
        assert arr[0] == b"foo"
        assert arr[2] == b"baz"

    def test_full_slice(self, store):
        block = build_bytestring_array([b"a", b"bb", b"ccc"], store)
        arr = BytestringArray(block, store)
        assert arr[:] == [b"a", b"bb", b"ccc"]

    def test_len(self, store):
        block = build_bytestring_array([b"a", b"b", b"c"], store)
        arr = BytestringArray(block, store)
        assert len(arr) == 3

    def test_empty(self, store):
        block = build_bytestring_array([], store)
        arr = BytestringArray(block, store)
        assert len(arr) == 0


class TestBytestringArrayTypeChecks:
    def test_leaf_to_list_rejects_data_block(self):
        arr = BytestringArray.__new__(BytestringArray)
        block = DataBlock.build(b"data", elem_size=1, elem_align=1)
        with pytest.raises(ValueError, match="SlotsBlock or TableBlock"):
            arr.leaf_to_list(block)


class TestBytestringArrayOversized:
    def test_single_oversized_element(self, store):
        """A single element too large for SLOTS gets TABLE+bytestring tree."""
        big = b"x" * (SIZE_MAX + 100)
        block = build_bytestring_array([big], store)
        arr = BytestringArray(block, store)
        assert len(arr) == 1
        assert arr[0] == big

    def test_mixed_regular_and_oversized(self, store):
        """Mix of regular and oversized elements should all be accessible."""
        big = b"x" * (SIZE_MAX + 100)
        block = build_bytestring_array([b"small", big, b"after"], store)
        arr = BytestringArray(block, store)
        assert arr[0] == b"small"
        assert arr[1] == big
        assert arr[2] == b"after"
