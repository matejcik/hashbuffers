"""Tests for LinksBlock."""

import pytest

from hashbuffers.codec import BlockType, Link, LinksBlock
from hashbuffers.codec.links import DEPTH_MAX


def _two_links() -> list[Link]:
    return [Link(b"a" * 32, 10), Link(b"b" * 32, 20)]


def test_encode_decode_links_block():
    links = _two_links()
    encoded = LinksBlock.build(links).encode()
    block = LinksBlock.decode(encoded)
    assert block.links == links


def test_links_block_non_increasing():
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 5)]
    block = LinksBlock.build(links)
    with pytest.raises(ValueError, match="strictly increasing"):
        block.validate()


def test_links_block_zero_limit():
    block = LinksBlock(BlockType.LINKS, 76, [Link(b"a" * 32, 0), Link(b"b" * 32, 10)])
    with pytest.raises(ValueError, match="limit 0"):
        block.validate()


def test_links_block_equal_limits():
    """Limits must be strictly increasing; equal limits are rejected."""
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 10)]
    block = LinksBlock.build(links)
    with pytest.raises(ValueError, match="strictly increasing"):
        block.validate()


def test_links_block_decode_rejects_reserved_bits():
    block = LinksBlock.build(_two_links())
    encoded = bytearray(block.encode())
    # depth_field is at bytes 2-3; set a reserved bit (bit 3+)
    depth_field = int.from_bytes(encoded[2:4], "little")
    depth_field |= 0b1000  # set lowest reserved bit
    encoded[2:4] = depth_field.to_bytes(2, "little")
    with pytest.raises(ValueError):
        LinksBlock.decode(bytes(encoded))


def test_links_block_two_links_ok():
    """Smoke test: links block with two links is valid."""
    block = LinksBlock.build([Link(b"a" * 32, 50), Link(b"b" * 32, 100)])
    decoded = LinksBlock.decode(block.encode())
    assert len(decoded) == 2


def test_links_block_single_link_rejected():
    """Single-link LINKS block is rejected."""
    block = LinksBlock(BlockType.LINKS, 40, [Link(b"a" * 32, 50)])
    with pytest.raises(ValueError, match="at least 2 links"):
        block.validate()


def test_links_block_empty_rejected():
    """Empty LINKS block (zero links) is rejected."""
    block = LinksBlock(BlockType.LINKS, 4, [])
    with pytest.raises(ValueError, match="at least 2 links"):
        block.validate()


def test_links_block_rejects_non_multiple_of_link_size():
    """LINKS block data area must be a multiple of 36 bytes."""
    block = LinksBlock.build(_two_links())
    encoded = bytearray(block.encode())
    # Append 5 extra bytes and re-encode header with new size
    encoded.extend(b"\x00" * 5)
    encoded[0:2] = BlockType.LINKS.encode(len(encoded))
    with pytest.raises(ValueError, match="not a multiple of link size"):
        LinksBlock.decode(bytes(encoded))


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes."""
    block = LinksBlock.build(_two_links())
    encoded = block.encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        LinksBlock.decode(encoded + b"trailing")


# --- Depth tests ---


def test_depth_encode_decode_roundtrip():
    """Depth field survives encode/decode roundtrip."""
    links = _two_links()
    block = LinksBlock.build(links, depth=3)
    assert block.depth == 3
    decoded = LinksBlock.decode(block.encode())
    assert decoded.depth == 3


def test_depth_max_valid():
    """Depth at DEPTH_MAX is valid."""
    block = LinksBlock.build(_two_links(), depth=DEPTH_MAX)
    block.validate()


def test_depth_exceeds_max_rejected():
    """Depth above DEPTH_MAX is rejected."""
    block = LinksBlock.build(_two_links(), depth=DEPTH_MAX + 1)
    with pytest.raises(ValueError, match="depth.*exceeds maximum"):
        block.validate()
