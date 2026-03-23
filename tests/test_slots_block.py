"""Tests for SlotsBlock."""

import pytest

from wire_format.codec import BlockType, DataBlock, SlotsBlock, Tagged16


def test_encode_decode_slots_block():
    heap = b"slot1slot2"
    encoded = SlotsBlock.build(True, [10, 15, 20], heap).encode()
    block = SlotsBlock.decode(encoded)
    assert block.raw_entries is True
    assert block.offsets == [10, 15, 20]
    assert block.heap == heap


def test_slots_block_raw_non_decreasing():
    heap = b"testdata"
    offsets = [0, 4, 3, len(heap)]
    heap_start = SlotsBlock.heap_start(offsets)
    offsets = [off + heap_start for off in offsets]
    block = SlotsBlock.build(True, offsets, heap)
    with pytest.raises(ValueError, match="non-decreasing"):
        block.validate()


def test_slots_block_raw_invalid_sentinel():
    heap = b"testdata"
    offsets = [0, 2, 3]
    heap_start = SlotsBlock.heap_start(offsets)
    offsets = [off + heap_start for off in offsets]
    block = SlotsBlock.build(True, offsets, heap)
    with pytest.raises(ValueError, match="Sentinel offset"):
        block.validate()


def test_slots_block_non_raw_any_order():
    data_block = DataBlock.build(b"data")
    block = SlotsBlock.build_blocks([data_block, data_block, data_block])
    block.validate()

    block.offsets = list(reversed(block.offsets))
    block.validate()



def test_slots_block_get_raw_entry_happy_path():
    items = [b"foo", b"barbaz"]
    block = SlotsBlock.build_raw(items)
    assert block.get_raw_entry(0) == items[0]
    assert block.get_raw_entry(1) == items[1]


def test_slots_block_get_raw_entry_out_of_bounds():
    block = SlotsBlock.build_raw([b"x"])
    with pytest.raises(ValueError, match="out of bounds"):
        block.get_raw_entry(-1)
    with pytest.raises(ValueError, match="out of bounds"):
        block.get_raw_entry(1)


def test_slots_block_decode_rejects_reserved_bits():
    block = SlotsBlock.build_raw([b"abc", b"def"])
    encoded = bytearray(block.encode())
    header = Tagged16.decode(bytes(encoded[2:4]))
    mutated = Tagged16(header.parameters | 0b011, header.number).encode()
    encoded[2:4] = mutated
    with pytest.raises(ValueError):
        SlotsBlock.decode(bytes(encoded))


def test_slots_block_non_raw_offsets_out_of_range_rejected():
    block = SlotsBlock.build_blocks([DataBlock.build(b"x")])
    block.offsets[0] = block.size + 10
    with pytest.raises(ValueError, match="out of bounds"):
        block.validate()


def test_slots_block_empty_raw():
    """Empty raw SLOTS block (zero items) is valid."""
    block = SlotsBlock.build_raw([])
    decoded = SlotsBlock.decode(block.encode())
    assert decoded.raw_entries is True
    assert decoded.offsets == [SlotsBlock.heap_start([0])]  # sentinel only
    assert decoded.heap == b""


def test_slots_block_empty_non_raw():
    """Empty non-raw SLOTS block (zero sub-blocks) is valid."""
    block = SlotsBlock.build_blocks([])
    decoded = SlotsBlock.decode(block.encode())
    assert decoded.raw_entries is False
    assert decoded.offsets == []
    assert decoded.heap == b""


def test_slots_block_build_raw_single_entry():
    """Single raw entry round-trips correctly."""
    block = SlotsBlock.build_raw([b"single"])
    assert block.get_raw_entry(0) == b"single"


def test_slots_block_build_raw_empty_first_entry():
    """Raw entries can include empty (zero-length) strings."""
    block = SlotsBlock.build_raw([b"", b"x"])
    assert block.get_raw_entry(0) == b""
    assert block.get_raw_entry(1) == b"x"


def test_slots_block_non_raw_duplicate_offsets():
    """Non-raw SLOTS blocks allow the same offset multiple times (spec: same sub-block placed multiple times)."""
    inner = DataBlock.build(b"shared")
    inner_bytes = inner.encode()
    # 2 offsets pointing to the same sub-block
    heap_start = SlotsBlock.heap_start([0, 0])  # = 4 + 2*2 = 8
    block = SlotsBlock.build(False, [heap_start, heap_start], inner_bytes)
    block.validate()
    assert block.get_block(0).get_data() == b"shared"
    assert block.get_block(1).get_data() == b"shared"


def test_slots_block_get_block_roundtrip():
    """Sub-blocks stored via build_blocks can be read back with get_block."""
    inner1 = DataBlock.build(b"first")
    inner2 = DataBlock.build(b"second")
    block = SlotsBlock.build_blocks([inner1, inner2])
    result0 = block.get_block(0)
    assert isinstance(result0, DataBlock)
    assert result0.get_data() == b"first"
    result1 = block.get_block(1)
    assert isinstance(result1, DataBlock)
    assert result1.get_data() == b"second"


def test_decode_rejects_trailing_data():
    """decode() must reject input with unparsed trailing bytes."""
    block = SlotsBlock.build_raw([b"x"])
    encoded = block.encode()
    with pytest.raises(IOError, match="Unparsed trailing data"):
        SlotsBlock.decode(encoded + b"extra")


def test_slots_block_sub_block_declared_size_exceeds_parent():
    """Nested block header size must not extend past the enclosing SLOTS block."""
    inner = DataBlock.build(b"x")
    block = SlotsBlock.build_blocks([inner])
    encoded = bytearray(block.encode())
    heap_start = SlotsBlock.heap_start(block.offsets)
    encoded[heap_start : heap_start + 2] = BlockType.DATA.encode(5000)
    with pytest.raises(IOError, match="Expected to read to offset"):
        SlotsBlock.decode(bytes(encoded))
