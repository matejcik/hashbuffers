"""Tests for DataBlock."""

import pytest

from hashbuffers.codec import SIZE_MAX, DataBlock


def test_encode_decode_data_block():
    data = b"hello world 123"
    encoded = DataBlock.build(data, elem_size=1, elem_align=1).encode()
    block = DataBlock.decode(encoded)
    assert block.size == len(encoded)
    assert bytes(block.get_data()) == data


def test_encode_data_block_padding_alignment():
    raw_payload = b"ABCD"
    encoded = DataBlock.build(raw_payload, elem_size=4, elem_align=4).encode()
    # block_header(2) + elem_info(2) + payload(4) = 8
    assert len(encoded) == 2 + 2 + len(raw_payload)
    assert encoded[4:] == raw_payload

    block = DataBlock.decode(encoded)
    assert bytes(block.get_data()) == raw_payload


def test_decode_block_size_mismatch():
    data = b"hello world 123"
    encoded = DataBlock.build(data, elem_size=1, elem_align=1).encode()
    with pytest.raises(IOError, match="Expected to read to offset"):
        DataBlock.decode(encoded[:5])


def test_datablock_build_array_and_get_array_alignment():
    elems = [b"abc", b"def"]
    block = DataBlock.build_array(elems, align=4)
    assert list(block) == elems


def test_get_array_rejects_indivisible_element_count():
    """When data length is not divisible by padded element size, get_array must reject."""
    # 6 bytes of data with elem_size=4, align=1: 6 % 4 != 0
    block = DataBlock.build(b"abcdef", elem_size=4, elem_align=1)
    with pytest.raises(ValueError, match="not divisible"):
        list(block)


def test_data_block_exactly_at_size_max():
    """A DataBlock with total size exactly SIZE_MAX (8191) is valid."""
    payload = b"A" * (SIZE_MAX - 4)  # block_header(2) + elem_info(2) = 4
    block = DataBlock.build(payload, elem_size=1, elem_align=1)
    assert block.size == SIZE_MAX
    decoded = DataBlock.decode(block.encode())
    assert bytes(decoded.get_data()) == payload


def test_data_block_exceeds_max_size():
    data = b"A" * (SIZE_MAX - 3)  # headers are 4 bytes, so max payload is SIZE_MAX - 4
    with pytest.raises(ValueError, match="out of bounds"):
        DataBlock.build(data, elem_size=1, elem_align=1).encode()


def test_data_block_padding_align_8():
    """align=8 produces 4 bytes of padding between elem_info and data."""
    payload = b"ABCDEFGH"
    block = DataBlock.build(payload, elem_size=8, elem_align=8)
    # block_header(2) + elem_info(2) + padding(4) + payload(8) = 16
    assert block.size == 16
    decoded = DataBlock.decode(block.encode())
    assert bytes(decoded.get_data()) == payload


def test_data_block_array_length():
    """element_count returns the correct element count."""
    elems = [b"aa", b"bb", b"cc"]
    block = DataBlock.build_array(elems, align=2)
    assert block.element_count() == 3


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes (exact block)."""
    encoded = DataBlock.build(b"x", elem_size=1, elem_align=1).encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        DataBlock.decode(encoded + b"trailing")


def test_zero_length_array_as_empty_data_block():
    """Spec: zero-length arrays can use an empty DATA block."""
    empty = DataBlock.build(b"", elem_size=4, elem_align=4)
    assert empty.size == 4  # block_header(2) + elem_info(2), no padding needed
    roundtrip = DataBlock.decode(empty.encode())
    assert roundtrip.element_count() == 0
    assert list(roundtrip) == []


def test_elem_info_roundtrip():
    """elem_size and elem_align survive encode/decode."""
    block = DataBlock.build(b"\x00" * 12, elem_size=4, elem_align=4)
    decoded = DataBlock.decode(block.encode())
    assert decoded.elem_size == 4
    assert decoded.elem_align == 4
    assert decoded.element_count() == 3


def test_alignment_method():
    """alignment() returns max(elem_align, 2)."""
    assert DataBlock.build(b"", elem_size=1, elem_align=1).alignment() == 2
    assert DataBlock.build(b"", elem_size=2, elem_align=2).alignment() == 2
    assert DataBlock.build(b"", elem_size=4, elem_align=4).alignment() == 4
    assert DataBlock.build(b"", elem_size=8, elem_align=8).alignment() == 8
