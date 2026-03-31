"""Tests for Tagged16, BlockType, VTableEntry, and Link primitives."""

import pytest

from hashbuffers.codec import (
    SIZE_MAX,
    BlockType,
    Link,
    Tagged16,
    VTableEntry,
    VTableEntryType,
)


def test_t16_tags():
    val = Tagged16(5, 1234)
    assert val == Tagged16.decode(val.encode())
    val = Tagged16(5, SIZE_MAX)
    assert val == Tagged16.decode(val.encode())


def test_t16_out_of_bounds():
    with pytest.raises(ValueError):
        Tagged16(8, 100).encode()  # params out of bounds
    with pytest.raises(ValueError):
        Tagged16(0, SIZE_MAX + 1).encode()


def test_block_header():
    val = BlockType.TABLE.encode(8000)
    assert BlockType.decode(val) == (BlockType.TABLE, 8000)


def test_block_header_reserved_bit_disallowed():
    params = (BlockType.TABLE << 1) | 0b1
    header = Tagged16(params, 10).encode()
    with pytest.raises(ValueError):
        BlockType.decode(header)


def test_vtable_entry():
    entry = VTableEntry(VTableEntryType.INLINE, 400)
    assert entry == VTableEntry.decode(entry.encode())


def test_link():
    link = Link(b"a" * 32, 100)
    assert link == Link.decode(link.encode())


BAD_LINKS = (
    (b"a" * 31, 0),  # digest too short
    (b"a" * 33, 100),  # digest too long
    (b"a" * 32, 0),  # limit zero is reserved
    (b"a" * 32, -1),  # limit out of bounds
    (b"a" * 32, 0xFFFF_FFFF + 1),  # limit out of bounds
)


@pytest.mark.parametrize("data, limit", BAD_LINKS)
def test_link_bad(data, limit):
    with pytest.raises(ValueError):
        Link(data, limit).encode()
