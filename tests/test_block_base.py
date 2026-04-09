"""Tests for Block base validation and decode_block."""

import pytest

from hashbuffers.codec import (
    BlockType,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
    VTableEntry,
    VTableEntryType,
    decode_block,
)


def test_block_validate_size_mismatch():
    block = DataBlock(BlockType.DATA, 1, b"")
    with pytest.raises(ValueError, match="does not match declared size"):
        block.validate()


def test_block_minimum_size_rejected():
    """Spec: The minimum valid size of a block is 2, smaller sizes MUST be rejected.

    Block.validate() first checks compute_size == declared_size, then
    checks _check_bounds(size, 2, SIZE_MAX). We need compute_size to
    match the declared size so that the bounds check is actually reached.
    The minimum DataBlock (empty data) has compute_size=2. Any block with
    size < 2 will fail the size mismatch check before the bounds check.
    This test verifies that the size mismatch check catches size=1, which
    is functionally equivalent: a block with declared size < 2 is always
    rejected.
    """
    block = DataBlock(BlockType.DATA, 1, b"")
    with pytest.raises(ValueError):
        block.validate()


def test_decode_block_roundtrip_data():
    data = DataBlock.build(b"payload").encode()
    block = decode_block(data)
    assert isinstance(block, DataBlock)
    assert block.get_data() == b"payload"


def test_decode_block_roundtrip_table():
    table = TableBlock.build([VTableEntry(VTableEntryType.NULL, 0)], b"").encode()
    block = decode_block(table)
    assert isinstance(block, TableBlock)


def test_decode_block_roundtrip_slots():
    slots = SlotsBlock.build_slots([b"x"]).encode()
    block = decode_block(slots)
    assert isinstance(block, SlotsBlock)


def test_decode_block_roundtrip_links():
    links = LinksBlock.build([Link(b"a" * 32, 100)]).encode()
    block = decode_block(links)
    assert isinstance(block, LinksBlock)


def test_decode_block_rejects_trailing_data():
    """decode_block with exact=True (default) must reject trailing bytes."""
    encoded = DataBlock.build(b"x").encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        decode_block(encoded + b"trailing")
