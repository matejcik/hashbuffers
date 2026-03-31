"""Tests for LinksBlock."""

import pytest

from hashbuffers.codec import BlockType, Link, LinksBlock


def test_encode_decode_links_block():
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 20)]
    encoded = LinksBlock.build(links).encode()
    block = LinksBlock.decode(encoded)
    assert block.links == links


def test_links_block_non_increasing():
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 5)]
    block = LinksBlock.build(links)
    with pytest.raises(ValueError, match="strictly increasing"):
        block.validate()


def test_links_block_zero_limit():
    links = [Link(b"a" * 32, 10)]
    # limit=0 is rejected at Link.encode level now, so build manually
    block = LinksBlock(BlockType.LINKS, 40, [Link(b"a" * 32, 0)])
    with pytest.raises(ValueError, match="limit 0"):
        block.validate()


def test_links_block_equal_limits():
    """Limits must be strictly increasing; equal limits are rejected."""
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 10)]
    block = LinksBlock.build(links)
    with pytest.raises(ValueError, match="strictly increasing"):
        block.validate()


def test_links_block_decode_rejects_reserved_bits():
    block = LinksBlock.build([Link(b"a" * 32, 10)])
    encoded = bytearray(block.encode())
    # reserved field is at bytes 2-3; set a bit
    params = int.from_bytes(encoded[2:4], "little")
    params |= 0b1
    encoded[2:4] = params.to_bytes(2, "little")
    with pytest.raises(ValueError):
        LinksBlock.decode(bytes(encoded))


def test_links_block_single_link():
    """Single link with limit > 0 is valid."""
    block = LinksBlock.build([Link(b"a" * 32, 50)])
    decoded = LinksBlock.decode(block.encode())
    assert len(decoded.links) == 1
    assert decoded.links[0].limit == 50


def test_links_block_empty_rejected():
    """Empty LINKS block (zero links) is rejected."""
    block = LinksBlock.build([])
    with pytest.raises(ValueError, match="at least one link"):
        block.validate()


def test_links_block_rejects_non_multiple_of_link_size():
    """LINKS block data area must be a multiple of 36 bytes."""
    block = LinksBlock.build([Link(b"a" * 32, 10)])
    encoded = bytearray(block.encode())
    # Append 5 extra bytes and re-encode header with new size
    encoded.extend(b"\x00" * 5)
    encoded[0:2] = BlockType.LINKS.encode(len(encoded))
    with pytest.raises(ValueError, match="not a multiple of link size"):
        LinksBlock.decode(bytes(encoded))


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes."""
    block = LinksBlock.build([Link(b"a" * 32, 10)])
    encoded = block.encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        LinksBlock.decode(encoded + b"trailing")
