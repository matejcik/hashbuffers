"""Tests for DataBlock."""

import pytest

from wire_format.codec import DataBlock, SIZE_MAX


def test_encode_decode_data_block():
    data = b"hello world 123"
    encoded = DataBlock.build(data).encode()
    block = DataBlock.decode(encoded)
    assert block.size == len(encoded)
    assert block.get_data() == data


def test_encode_data_block_padding_alignment():
    raw_payload = b"ABCD"
    encoded = DataBlock.build(raw_payload, align=4).encode()
    assert len(encoded) == 2 + 2 + len(raw_payload)  # header + pad + payload
    assert encoded[4:] == raw_payload

    block = DataBlock.decode(encoded)
    assert block.get_data(align=4) == raw_payload
    assert block.get_data(align=1)[2:] == raw_payload


def test_data_block_exceeds_max_size():
    data = b"A" * SIZE_MAX
    with pytest.raises(ValueError, match="out of bounds"):
        DataBlock.build(data).encode()


def test_decode_block_size_mismatch():
    data = b"hello world 123"
    encoded = DataBlock.build(data).encode()
    with pytest.raises(IOError, match="Expected to read to offset"):
        DataBlock.decode(encoded[:5])


def test_datablock_build_array_and_get_array_alignment():
    elems = [b"abc", b"def"]
    block = DataBlock.build_array(elems, align=4)
    assert block.get_array(elem_size=3, align=4) == elems


def test_get_array_rejects_indivisible_element_count():
    """When data length is not divisible by padded element size, get_array must reject."""
    block = DataBlock.build(b"abcdef")  # 6 bytes, no padding
    with pytest.raises(ValueError, match="not divisible"):
        block.get_array(elem_size=4, align=1)  # 6 % 4 != 0


def test_data_block_exactly_at_size_max():
    """A DataBlock with total size exactly SIZE_MAX (8191) is valid."""
    payload = b"A" * (SIZE_MAX - 2)  # header is 2 bytes
    block = DataBlock.build(payload)
    assert block.size == SIZE_MAX
    decoded = DataBlock.decode(block.encode())
    assert decoded.get_data() == payload


def test_data_block_padding_align_8():
    """align=8 produces 6 bytes of padding before data."""
    payload = b"ABCDEFGH"
    block = DataBlock.build(payload, align=8)
    # header(2) + padding(6) + payload(8) = 16
    assert block.size == 16
    decoded = DataBlock.decode(block.encode())
    assert decoded.get_data(align=8) == payload


def test_data_block_array_length():
    """array_length returns the correct element count."""
    elems = [b"aa", b"bb", b"cc"]
    block = DataBlock.build_array(elems, align=2)
    assert block.array_length(elem_size=2, align=2) == 3


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes (exact block)."""
    encoded = DataBlock.build(b"x").encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        DataBlock.decode(encoded + b"trailing")


def test_zero_length_array_as_empty_data_block():
    """Spec: zero-length arrays can use an empty DATA block (via BLOCK wrapper)."""
    empty = DataBlock.build_array([], align=4)
    assert empty.size == 2 + (max(4, 2) - 2)  # header + alignment padding only
    roundtrip = DataBlock.decode(empty.encode())
    assert roundtrip.array_length(elem_size=1, align=4) == 0
    assert roundtrip.get_array(elem_size=1, align=4) == []
