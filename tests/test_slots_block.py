"""Tests for SlotsBlock."""

import pytest

from hashbuffers.codec import SlotsBlock


def test_encode_decode_slots_block():
    """Round-trip encode/decode of a SLOTS block."""
    items = [b"slot1", b"slot2"]
    block = SlotsBlock.build_slots(items)
    encoded = block.encode()
    decoded = SlotsBlock.decode(encoded)
    assert decoded.get_entry(0) == b"slot1"
    assert decoded.get_entry(1) == b"slot2"
    assert decoded.element_count() == 2


def test_slots_block_non_decreasing():
    """Offsets must be non-decreasing."""
    heap = b"testdata"
    offsets = [0, 4, 3, len(heap)]
    heap_start = SlotsBlock.heap_start(len(offsets))
    offsets = [off + heap_start for off in offsets]
    block = SlotsBlock.build(offsets, heap)
    with pytest.raises(ValueError, match="non-decreasing"):
        block.validate()


def test_slots_block_invalid_sentinel():
    """Sentinel (last offset) must equal block size."""
    heap = b"testdata"
    offsets = [0, 2, 3]
    heap_start = SlotsBlock.heap_start(len(offsets))
    offsets = [off + heap_start for off in offsets]
    block = SlotsBlock.build(offsets, heap)
    with pytest.raises(ValueError, match="Sentinel offset"):
        block.validate()


def test_slots_block_get_entry_happy_path():
    items = [b"foo", b"barbaz"]
    block = SlotsBlock.build_slots(items)
    assert block.get_entry(0) == items[0]
    assert block.get_entry(1) == items[1]


def test_slots_block_get_entry_out_of_bounds():
    block = SlotsBlock.build_slots([b"x"])
    with pytest.raises(ValueError, match="out of bounds"):
        block.get_entry(-1)
    with pytest.raises(ValueError, match="out of bounds"):
        block.get_entry(1)


def test_slots_block_empty():
    """Empty SLOTS block (zero items) is valid."""
    block = SlotsBlock.build_slots([])
    decoded = SlotsBlock.decode(block.encode())
    assert decoded.element_count() == 0
    assert decoded.heap == b""
    # Should have exactly one offset (sentinel = 4)
    assert decoded.offsets == [4]


def test_slots_block_build_raw_single_entry():
    """Single entry round-trips correctly."""
    block = SlotsBlock.build_slots([b"single"])
    assert block.get_entry(0) == b"single"


def test_slots_block_build_raw_empty_first_entry():
    """Entries can include empty (zero-length) strings."""
    block = SlotsBlock.build_slots([b"", b"x"])
    assert block.get_entry(0) == b""
    assert block.get_entry(1) == b"x"


def test_slots_block_first_offset_validation():
    """First offset must be >= 4, divisible by 2, and <= size."""
    # First offset too small (< 4): build a block with offset [2] and matching size
    # compute_size = 2 + 2*1 + 0 = 4, but offset[0]=2 is invalid
    block = SlotsBlock(SlotsBlock.BLOCK_TYPE, 4, [2], b"")
    with pytest.raises(ValueError, match="at least 4"):
        block.validate()

    # First offset not divisible by 2
    block = SlotsBlock(SlotsBlock.BLOCK_TYPE, 5, [5], b"x")
    with pytest.raises(ValueError, match="divisible by 2"):
        block.validate()


def test_slots_block_first_offset_exceeds_size():
    """Spec: first offset must be no larger than the block size.

    We test this through decode: encode a valid SLOTS block, then patch
    the first offset to point beyond the block size. The decoder tries to
    read more offsets than exist, failing with IOError.
    """
    block = SlotsBlock.build_slots([b"x"])
    encoded = bytearray(block.encode())
    # Patch the first offset (bytes 2-3) to be larger than block size
    # Block size is 7 (header=2, offsets=4, data=1). Set first offset to 100.
    encoded[2:4] = (100).to_bytes(2, "little")
    with pytest.raises((ValueError, IOError)):
        SlotsBlock.decode(bytes(encoded))


def test_slots_block_offset_count_mismatch():
    """Offset count derived from first offset must match actual offsets."""
    # first_offset=8 -> expected (8-2)/2 = 3 offsets, but we have 2.
    # compute_size = 2 + 2*2 + 2 = 8, matching declared size.
    block = SlotsBlock(SlotsBlock.BLOCK_TYPE, 8, [8, 8], b"xx")
    with pytest.raises(ValueError, match="Offset count"):
        block.validate()


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes."""
    block = SlotsBlock.build_slots([b"x"])
    encoded = block.encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        SlotsBlock.decode(encoded + b"extra")


def test_len():
    """__len__ returns element count."""
    block = SlotsBlock.build_slots([b"a", b"bb", b"ccc"])
    assert len(block) == 3


def test_getitem_int():
    """__getitem__ with int returns the slot entry."""
    block = SlotsBlock.build_slots([b"foo", b"bar", b"baz"])
    assert block[0] == b"foo"
    assert block[1] == b"bar"
    assert block[2] == b"baz"


def test_getitem_slice():
    """__getitem__ with slice returns a list of slot entries."""
    block = SlotsBlock.build_slots([b"a", b"bb", b"ccc", b"dddd"])
    assert block[1:3] == [b"bb", b"ccc"]
    assert block[:2] == [b"a", b"bb"]


def test_get_entries():
    """get_entries() returns all slot entries."""
    items = [b"alpha", b"beta", b"gamma"]
    block = SlotsBlock.build_slots(items)
    assert block.get_entries() == items


def test_alignment():
    """alignment() returns 2."""
    block = SlotsBlock.build_slots([b"x"])
    assert block.alignment() == 2
