"""Tests for fitting algorithms."""

import pytest

from hashbuffers.codec import (
    SIZE_MAX,
    DataBlock,
    Link,
)
from hashbuffers.codec.table import (
    NULL_ENTRY,
    BlockEntry,
    DirectDataEntry,
    DirectFixedEntry,
    InlineIntEntry,
    LinkEntry,
    TableEntryType,
)
from hashbuffers.fitting import Table
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
        assert len(block) == 3
        assert all(e is NULL_ENTRY for e in block)

    def test_int_field_inline(self, store):
        """IntField that fits in 13-bit range becomes INLINE."""
        table = Table(
            [
                InlineIntEntry.from_int(0, False),
                InlineIntEntry.from_int(100, False),
                InlineIntEntry.from_int(-5, True),
                InlineIntEntry.from_int(100, True),
            ]
        )
        block = table.build(store)
        assert block[0] == InlineIntEntry.from_int(0, False)
        assert block[1] == InlineIntEntry.from_int(100, False)
        assert block[2] == InlineIntEntry.from_int(-5, True)
        assert block[3] == InlineIntEntry.from_int(100, True)

    def test_int_field_direct4(self, store):
        """IntField too large for inline becomes DIRECT4."""
        table = Table([DirectFixedEntry.from_int(0xDEADBEEF, False)])
        block = table.build(store)
        assert block.vtable[0].type == TableEntryType.DIRECT4
        assert block[0].to_int(4, False) == 0xDEADBEEF  # type: ignore

    def test_int_field_direct8(self, store):
        """IntField too large for DIRECT4 becomes DIRECT8."""
        table = Table([DirectFixedEntry.from_int(0xDEAD_BEEF_CAFE_BABE, False)])
        block = table.build(store)
        assert block.vtable[0].type == TableEntryType.DIRECT8
        assert block[0].to_int(8, False) == 0xDEAD_BEEF_CAFE_BABE  # type: ignore

    def test_stored_block_field(self, store):
        inner = DataBlock.build(b"nested payload", elem_size=1, elem_align=1)
        table = Table([BlockEntry(inner)])
        block = table.build(store)
        result = block[0]
        assert isinstance(result, BlockEntry)
        assert isinstance(result.block, DataBlock)
        assert bytes(result.block.get_data()) == b"nested payload"

    def test_link_field(self, store):
        """Pre-externalized link is placed on heap."""
        link = Link(b"x" * 32, 100)
        table = Table([LinkEntry(link)])
        block = table.build(store)
        result = block[0]
        assert isinstance(result, LinkEntry)
        assert result.link.digest == link.digest
        assert result.link.limit == link.limit
        assert table.alignment >= 4  # LINK requires 4-alignment

    def test_mixed_fields(self, store):
        """Table with a mix of INLINE, DIRECT4, BLOCK, LINK, and NULL."""
        inner = DataBlock.build(b"sub", elem_size=1, elem_align=1)
        fields = [
            InlineIntEntry.from_int(0, False),  # 0: INLINE
            NULL_ENTRY,  # 1: NULL
            DirectFixedEntry.from_int(0x0102, False),  # 2: DIRECT4
            BlockEntry(inner),  # 3: BLOCK
            LinkEntry(Link(b"a" * 32, 50)),  # 4: LINK
            DirectDataEntry(b"\x03\x04\x05"),  # 5: DIRECTDATA of odd length
        ]
        table = Table(fields)
        block = table.build(store)
        assert block[0] == InlineIntEntry.from_int(0, False)
        assert block[1] is NULL_ENTRY
        assert block[2] == DirectFixedEntry.from_int(0x0102, False)
        block_entry = block[3]
        assert isinstance(block_entry, BlockEntry)
        assert block_entry.block == inner
        assert block[4] == LinkEntry(Link(b"a" * 32, 50))
        assert block[5] == DirectDataEntry(b"\x03\x04\x05")

    def test_small_stored_block_auto_embedded(self, store):
        """StoredBlocks ≤ 36 bytes must NOT become LINK even if block is tight."""
        small_data = b"\x00" * 32  # 36 bytes encoded (with 4-byte headers)
        must_embed = DataBlock.build(small_data, elem_size=1, elem_align=1)
        # space consumed: header + entry_count + two entries + data block headers + small_data
        space_consumed = 2 + 2 + 2 * 2 + 4 + len(small_data)
        # space available for block data: SIZE_MAX - space_consumed - 4 (for data block headers)
        space_available = SIZE_MAX - space_consumed - 4
        # ...but the block is 1 byte too large
        can_outlink = DataBlock.build(
            b"x" * (space_available + 1), elem_size=1, elem_align=1
        )
        assert len(must_embed.encode()) <= Link.SIZE
        assert len(can_outlink.encode()) > Link.SIZE
        # put `can_outlink` first; both cannot fit in one SIZE_MAX table
        table = Table([BlockEntry(can_outlink), BlockEntry(must_embed)])
        block = table.build(store)
        assert block.vtable[0].type == TableEntryType.LINK
        assert block.vtable[1].type == TableEntryType.BLOCK

    def test_overflow_to_link(self, store):
        """StoredBlock too large for block overflows to LINK."""
        large_inner = DataBlock.build(b"x" * 8187, elem_size=1, elem_align=1)
        large_block = BlockEntry(large_inner)

        # Table overhead makes this impossible to embed under SIZE_MAX.
        table = Table([large_block])
        block = table.build(store)
        assert block.vtable[0].type == TableEntryType.LINK

    def test_block_entry_clamps_alignment_to_2(self, store):
        """BlockEntry must have alignment >= 2, since embedded blocks are always 2-aligned."""
        inner = DataBlock.build(b"", elem_size=1, elem_align=1)
        entry = BlockEntry(inner)
        assert entry.alignment() == 2

    def test_block_entry_align1_not_placed_at_odd_offset(self, store):
        """A BlockEntry with data_alignment=1 must still be 2-aligned on the heap.

        Without clamping, the packer would treat it as 1-aligned, and a
        preceding odd-sized BLOCK field could push it to an odd offset,
        producing a TABLE that fails codec validation.
        """
        # Use a 3-byte DataBlock (7 bytes with headers): odd total size
        odd_block = BlockEntry(
            DataBlock.build(b"\x01\x02\x03", elem_size=1, elem_align=1)
        )
        # BlockEntry alignment comes from the block itself (min 2).
        inner = DataBlock.build(b"\xaa", elem_size=1, elem_align=1)
        block_entry = BlockEntry(inner)
        table = Table([NULL_ENTRY, odd_block, block_entry])
        block = table.build(store)
        # The TABLE must survive its own validation (which checks 2-alignment
        # of all BLOCK entries).
        block.validate()

    def test_alignment_tracking(self, store):
        """Block alignment is the max of all field alignments."""
        field = DirectFixedEntry(b"\x00" * 8)
        table = Table([field])
        table.fit(store)
        assert table.alignment == 8
