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
