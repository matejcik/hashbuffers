"""Tests for fitting algorithms."""

import pytest

from hashbuffers.codec import (
    SIZE_MAX,
    DataBlock,
    Link,
    TableBlock,
    VTableEntryType,
)
from hashbuffers.fitting import (
    NULL_ENTRY,
    BlockEntry,
    DirectEntry,
    InlineIntEntry,
    LinkEntry,
    Table,
)
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


# --- fit_table ---


class TestFitTable:
    def test_empty_table(self, store):
        table = Table([])
        block = table.build(store)
        assert block.vtable == []

    def test_null_fields(self, store):
        table = Table([NULL_ENTRY, NULL_ENTRY, NULL_ENTRY])
        block = table.build(store)
        assert len(block.vtable) == 3
        assert all(e.type == VTableEntryType.NULL for e in block.vtable)

    def test_int_field_inline(self, store):
        """IntField that fits in 13-bit range becomes INLINE."""
        table = Table(
            [
                InlineIntEntry(0, signed=False),
                InlineIntEntry(100, signed=False),
                InlineIntEntry(-5, signed=True),
                InlineIntEntry(100, signed=True),
            ]
        )
        block = table.build(store)
        assert block.vtable[0].type == VTableEntryType.INLINE
        assert block.get_int(0, 8) == 0
        assert block.vtable[1].type == VTableEntryType.INLINE
        assert block.get_int(1, 4) == 100
        assert block.vtable[2].type == VTableEntryType.INLINE
        assert block.get_int(2, 2, signed=True) == -5

    def test_int_field_direct(self, store):
        """IntField too large for inline becomes DIRECT."""
        table = Table([DirectEntry.from_int(0xDEADBEEF, 4, signed=False)])
        block = table.build(store)
        assert block.vtable[0].type == VTableEntryType.DIRECT
        assert block.get_int(0, 4) == 0xDEADBEEF

    def test_direct_data(self, store):
        value = b"deadbeef"
        table = Table([DirectEntry(value, 4, 1)])
        block = table.build(store)
        assert block.vtable[0].type == VTableEntryType.DIRECT
        assert block.get_fixedsize(0, len(value)) == value

    def test_stored_block_field(self, store):
        inner = DataBlock.build(b"nested payload")
        table = Table([BlockEntry(inner, 2, 1)])
        block = table.build(store)
        result = block.get_block(0)
        assert isinstance(result, DataBlock)
        assert result.get_data() == b"nested payload"

    def test_link_field(self, store):
        """Pre-externalized link is placed on heap."""
        link = Link(b"x" * 32, 100)
        table = Table([LinkEntry(link)])
        block = table.build(store)
        result = block.get_block(0)
        assert isinstance(result, Link)
        assert result.digest == link.digest
        assert result.limit == link.limit
        assert table.alignment >= 4  # LINK requires 4-alignment

    def test_mixed_fields(self, store):
        """Table with a mix of INLINE, DIRECT, BLOCK, LINK, and NULL."""
        inner = DataBlock.build(b"sub")
        fields = [
            InlineIntEntry(0, signed=False),  # 0: INLINE
            NULL_ENTRY,  # 1: NULL
            DirectEntry.from_int(0x0102, 2, signed=False),  # 2: DIRECT
            BlockEntry(inner, 2, 1),  # 3: BLOCK
            LinkEntry(Link(b"a" * 32, 50)),  # 4: LINK
        ]
        table = Table(fields)
        block = table.build(store)
        assert block.get_int(0, 2) == 0
        assert block.get_int(1, 2) is None
        assert block.get_fixedsize(2, 2) == b"\x02\x01"
        inner_result = block.get_block(3)
        assert isinstance(inner_result, DataBlock)
        link_result = block.get_block(4)
        assert isinstance(link_result, Link)

    def test_small_stored_block_auto_embedded(self, store):
        """StoredBlocks ≤ 36 bytes must NOT become LINK even if block is tight."""
        small_data = b"\x00" * 34  # 36 bytes encoded (with 2-byte header)
        must_embed = DataBlock.build(small_data)
        # space consumed: header + entry_count + two entries + block header of small_data + small_data
        space_consumed = 2 + 2 + 2 * 2 + 2 + len(small_data)
        # space available for block data: SIZE_MAX - space_consumed - 2 (for block header)
        space_available = SIZE_MAX - space_consumed - 2
        # ...but the block is 1 byte too large
        can_outlink = DataBlock.build(b"x" * (space_available + 1))
        assert len(must_embed.encode()) <= Link.SIZE
        assert len(can_outlink.encode()) > Link.SIZE
        # put `can_outlink` first; both cannot fit in one SIZE_MAX table
        table = Table([BlockEntry(can_outlink, 2, 1), BlockEntry(must_embed, 2, 1)])
        block = table.build(store)
        assert block.vtable[0].type == VTableEntryType.LINK
        assert block.vtable[1].type == VTableEntryType.BLOCK

    def test_overflow_to_link(self, store):
        """StoredBlock too large for block overflows to LINK."""
        large_inner = DataBlock.build(b"x" * 8189)
        large_block = BlockEntry(large_inner, 2, 1)

        # Table overhead makes this impossible to embed under SIZE_MAX.
        table = Table([large_block])
        block = table.build(store)
        assert block.vtable[0].type == VTableEntryType.LINK

    def test_block_entry_clamps_alignment_to_2(self, store):
        """BlockEntry must have alignment >= 2, since embedded blocks are always 2-aligned."""
        inner = DataBlock.build(b"")
        entry = BlockEntry(inner, 1, 0)
        assert entry.alignment() == 2

    def test_block_entry_align1_not_placed_at_odd_offset(self, store):
        """A BlockEntry with data_alignment=1 must still be 2-aligned on the heap.

        Without clamping, the packer would treat it as 1-aligned, and a
        preceding odd-sized DIRECT field could push it to an odd offset,
        producing a TABLE that fails codec validation.
        """
        # 3-byte DIRECT field: odd size, so the next 1-aligned field would
        # land at an odd offset (heap_start is always even).
        odd_direct = DirectEntry(b"\x01\x02\x03", 1, 1)
        # BlockEntry with data_alignment=1 — the clamp to 2 is what saves us.
        inner = DataBlock.build(b"\xaa")
        block_entry = BlockEntry(inner, 1, 1)
        table = Table([NULL_ENTRY, odd_direct, block_entry])
        block = table.build(store)
        # The TABLE must survive its own validation (which checks 2-alignment
        # of all BLOCK entries).
        block.validate()

    def test_alignment_tracking(self, store):
        """Block alignment is the max of all field alignments."""
        field = DirectEntry(b"\x00" * 8, 8, 1)
        table = Table([field])
        table.fit(store)
        assert table.alignment == 8
