"""Tests for LinksBlock."""

import pytest

from wire_format.codec import Link, LinksBlock


def test_encode_decode_links_block_leaf_parent():
    links = [Link(b"a" * 32, 0), Link(b"b" * 32, 0)]
    encoded = LinksBlock.build(True, links).encode()
    block = LinksBlock.decode(encoded)
    assert block.leaf_parent is True
    assert len(block.links) == 2
    assert all(link.limit == 0 for link in block.links)


def test_links_block_leaf_parent_invalid_limit():
    links = [Link(b"a" * 32, 0), Link(b"b" * 32, 5)]
    block = LinksBlock.build(True, links)
    with pytest.raises(ValueError, match="limit 0"):
        block.validate()


def test_encode_decode_links_block_inner():
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 20)]
    encoded = LinksBlock.build(False, links).encode()
    block = LinksBlock.decode(encoded)
    assert block.leaf_parent is False
    assert block.links == links


def test_links_block_inner_non_increasing():
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 5)]
    block = LinksBlock.build(False, links)
    with pytest.raises(ValueError, match="strictly increasing"):
        block.validate()


def test_links_block_inner_zero_limit():
    links = [Link(b"a" * 32, 0)]
    block = LinksBlock.build(False, links)
    with pytest.raises(ValueError, match="limit 0"):
        block.validate()


def test_links_block_inner_equal_limits():
    """Inner node with equal limits must be rejected (not strictly increasing)."""
    links = [Link(b"a" * 32, 10), Link(b"b" * 32, 10)]
    block = LinksBlock.build(False, links)
    with pytest.raises(ValueError, match="strictly increasing"):
        block.validate()


def test_links_block_decode_rejects_reserved_bits():
    links = [Link(b"a" * 32, 0)]
    block = LinksBlock.build(True, links)
    encoded = bytearray(block.encode())
    params = int.from_bytes(encoded[2:4], "little")
    params |= 0b1
    encoded[2:4] = params.to_bytes(2, "little")
    with pytest.raises(ValueError):
        LinksBlock.decode(bytes(encoded))


def test_links_block_single_inner_link():
    """Inner node with a single link with limit > 0 is valid."""
    block = LinksBlock.build(False, [Link(b"a" * 32, 50)])
    decoded = LinksBlock.decode(block.encode())
    assert decoded.leaf_parent is False
    assert len(decoded.links) == 1
    assert decoded.links[0].limit == 50


def test_links_block_empty_leaf_parent():
    """Empty leaf-parent LINKS block (zero links) is valid."""
    block = LinksBlock.build(True, [])
    decoded = LinksBlock.decode(block.encode())
    assert decoded.leaf_parent is True
    assert decoded.links == []


def test_links_block_empty_inner_rejected():
    """Empty inner LINKS block (zero links) is rejected."""
    block = LinksBlock.build(False, [])
    with pytest.raises(ValueError, match="at least one link"):
        block.validate()


def test_links_block_rejects_non_multiple_of_link_size():
    """LINKS block data area must be a multiple of 36 bytes."""
    from wire_format.codec import BlockType

    block = LinksBlock.build(True, [Link(b"a" * 32, 0)])
    encoded = bytearray(block.encode())
    # Append 5 extra bytes and re-encode header with new size
    encoded.extend(b"\x00" * 5)
    encoded[0:2] = BlockType.LINKS.encode(len(encoded))
    with pytest.raises(ValueError, match="not a multiple of link size"):
        LinksBlock.decode(bytes(encoded))


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes."""
    block = LinksBlock.build(True, [Link(b"a" * 32, 0)])
    encoded = block.encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        LinksBlock.decode(encoded + b"trailing")
