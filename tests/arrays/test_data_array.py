"""Unit tests for DataArray — leaf_length, leaf_to_list, __getitem__, __eq__."""

import pytest

from hashbuffers.arrays import DataArray, build_data_array
from hashbuffers.codec import SlotsBlock
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


def make_data_array(store, values):
    byte_values = [v.to_bytes(4, "little") for v in values]
    block = build_data_array(byte_values, 4, 4, store)
    return DataArray(
        block,
        store,
        elem_size=4,
        elem_align=4,
        decode_element=lambda b: int.from_bytes(b, "little"),
    )


class TestDataArrayLen:
    def test_len(self, store):
        arr = make_data_array(store, [10, 20, 30])
        assert len(arr) == 3

    def test_len_empty(self, store):
        arr = make_data_array(store, [])
        assert len(arr) == 0


class TestDataArrayGetitem:
    def test_int_index(self, store):
        arr = make_data_array(store, [10, 20, 30])
        assert arr[0] == 10
        assert arr[2] == 30

    def test_full_slice(self, store):
        arr = make_data_array(store, [10, 20, 30])
        assert arr[:] == [10, 20, 30]

    def test_partial_slice_single_leaf(self, store):
        """Partial slice on a single-leaf array should return the correct sub-range."""
        arr = make_data_array(store, [10, 20, 30, 40, 50])
        result = arr[1:4]
        assert result == [20, 30, 40]

    def test_slice_step_raises(self, store):
        arr = make_data_array(store, [10, 20, 30])
        with pytest.raises(NotImplementedError, match="Step"):
            arr[::2]


class TestDataArrayEq:
    def test_equal_to_list(self, store):
        arr = make_data_array(store, [10, 20, 30])
        assert arr == [10, 20, 30]

    def test_not_equal_to_list(self, store):
        arr = make_data_array(store, [10, 20, 30])
        assert not (arr == [10, 20, 99])

    def test_not_equal_to_non_sequence(self, store):
        arr = make_data_array(store, [10, 20, 30])
        assert arr.__eq__(42) is NotImplemented


class TestDataArrayTypeChecks:
    def test_leaf_to_list_rejects_non_data(self):
        arr = DataArray.__new__(DataArray)
        arr.elem_size = 4
        arr.elem_align = 4
        block = SlotsBlock.build_slots([b"a"])
        with pytest.raises(ValueError, match="DATA leaf"):
            arr.leaf_to_list(block)
