"""Unit tests for hashbuffers.util — padded_element_size, pack/unpack_flat_array."""

import pytest

from hashbuffers.util import pack_flat_array, padded_element_size, unpack_flat_array


class TestPaddedElementSize:
    def test_no_padding_needed(self):
        assert padded_element_size(4, 4) == 4

    def test_padding_applied(self):
        assert padded_element_size(3, 4) == 4

    def test_align_1(self):
        assert padded_element_size(7, 1) == 7

    def test_align_8(self):
        assert padded_element_size(5, 8) == 8


class TestPackFlatArray:
    def test_empty_list(self):
        assert pack_flat_array([], 1) == b""

    def test_single_element(self):
        assert pack_flat_array([b"\x01\x02\x03\x04"], 4) == b"\x01\x02\x03\x04"

    def test_elements_with_padding(self):
        result = pack_flat_array([b"\x01\x02\x03", b"\x04\x05\x06"], 4)
        # 3-byte elements padded to 4 bytes each
        assert result == b"\x01\x02\x03\x00\x04\x05\x06\x00"

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="same length"):
            pack_flat_array([b"\x01", b"\x02\x03"], 1)


class TestUnpackFlatArray:
    def test_roundtrip_with_pack(self):
        elements = [b"\x01\x02\x03", b"\x04\x05\x06"]
        packed = pack_flat_array(elements, 4)
        result = unpack_flat_array(packed, 3, 4)
        assert [bytes(r) for r in result] == elements

    def test_non_divisible_length_raises(self):
        with pytest.raises(ValueError, match="not divisible"):
            unpack_flat_array(b"\x01\x02\x03\x04\x05", 4, 4)

    def test_strips_padding(self):
        # 2-byte elements with align=4 → padded to 4 bytes each
        packed = pack_flat_array([b"\x01\x02", b"\x03\x04"], 4)
        result = unpack_flat_array(packed, 2, 4)
        assert len(result) == 2
        assert bytes(result[0]) == b"\x01\x02"
        assert bytes(result[1]) == b"\x03\x04"
