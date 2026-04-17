"""Tests for Block base validation and decode_block."""

import pytest

from hashbuffers.codec import (
    BlockType,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
    decode_block,
)
from hashbuffers.codec.table import (
    TableEntryRaw,
    TableEntryType,
)


def test_block_validate_size_mismatch():
    block = DataBlock(BlockType.DATA, 1, b"", 1, 1)
    with pytest.raises(ValueError, match="does not match declared size"):
        block.validate()


def test_block_minimum_size_rejected():
    """Spec: The minimum valid size of a DATA block is 4.

    Block.validate() first checks compute_size == declared_size, then
    checks _check_bounds(size, 2, SIZE_MAX). A DataBlock with empty data
    has compute_size=4 (two t16 headers). Any block with size < 4 will
    fail the size mismatch check. This test verifies that.
    """
    block = DataBlock(BlockType.DATA, 1, b"", 1, 1)
    with pytest.raises(ValueError):
        block.validate()


def test_decode_block_roundtrip_data():
    data = DataBlock.build(b"payload", elem_size=1, elem_align=1).encode()
    block = decode_block(data)
    assert isinstance(block, DataBlock)
    assert bytes(block.get_data()) == b"payload"


def test_decode_block_roundtrip_table():
    table = TableBlock.build([TableEntryRaw(TableEntryType.NULL, 0)], b"").encode()
    block = decode_block(table)
    assert isinstance(block, TableBlock)


def test_decode_block_roundtrip_slots():
    slots = SlotsBlock.build_slots([b"x"]).encode()
    block = decode_block(slots)
    assert isinstance(block, SlotsBlock)


def test_decode_block_roundtrip_links():
    links = LinksBlock.build([Link(b"a" * 32, 100), Link(b"b" * 32, 200)]).encode()
    block = decode_block(links)
    assert isinstance(block, LinksBlock)


def test_decode_block_rejects_trailing_data():
    """decode_block with exact=True (default) must reject trailing bytes."""
    encoded = DataBlock.build(b"x", elem_size=1, elem_align=1).encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        decode_block(encoded + b"trailing")
