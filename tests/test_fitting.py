"""Tests for fitting algorithms."""

import pytest

from hashbuffers.arrays import (
    build_bytestring_array,
    build_data_array,
    build_table_array,
    linktree_reduce,
)
from hashbuffers.codec import (
    SIZE_MAX,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
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

    def test_alignment_tracking(self, store):
        """Block alignment is the max of all field alignments."""
        field = DirectEntry(b"\x00" * 8, 8, 1)
        table = Table([field])
        table.fit(store)
        assert table.alignment == 8


# --- build_data_array ---


class TestBuildDataArray:
    def test_empty(self, store):
        entry = build_data_array([], 4, store)
        assert entry.element_count == 0
        assert isinstance(entry.block, DataBlock)
        assert entry.block.get_array(4, align=4) == []

    def test_single_block(self, store):
        elements = [b"\x01\x00\x00\x00", b"\x02\x00\x00\x00"]
        entry = build_data_array(elements, 4, store)
        assert entry.element_count == 2
        assert isinstance(entry.block, DataBlock)
        assert entry.block.get_array(4, align=4) == elements

    def test_multi_block_creates_links_tree(self, store: BlockStore):
        """Large array spans multiple DATA blocks linked by a LINKS tree."""
        elements = [i.to_bytes(4, "little") for i in range(3000)]
        entry = build_data_array(elements, 4, store)
        assert entry.element_count == 3000
        # Root should be a LINKS block
        assert isinstance(entry.block, LinksBlock)
        # Should have stored multiple blocks
        assert len(store) >= 2


# --- build_slots_array ---


class TestBuildSlotsArray:
    def test_empty(self, store):
        entry = build_bytestring_array([], store)
        assert isinstance(entry.block, SlotsBlock)
        assert entry.block.element_count() == 0

    def test_single_block(self, store):
        items = [b"hello", b"world"]
        entry = build_bytestring_array(items, store)
        assert entry.element_count == 2
        assert isinstance(entry.block, SlotsBlock)
        assert entry.block.get_entry(0) == b"hello"
        assert entry.block.get_entry(1) == b"world"

    def test_multi_block_creates_links_tree(self, store):
        """Large SLOTS array spans multiple blocks."""
        items = [b"x" * 50 for _ in range(300)]
        entry = build_bytestring_array(items, store)
        assert entry.element_count == 300
        assert isinstance(entry.block, LinksBlock)


# --- build_table_array ---


class TestBuildTableArray:
    def test_empty(self, store):
        entry = build_table_array([], store)
        assert entry.element_count == 0
        assert isinstance(entry.block, TableBlock)
        assert entry.block.vtable == []

    def test_single_element(self, store):
        inner = DataBlock.build(b"elem")
        entry = build_table_array([BlockEntry.from_data(inner, 2, 1)], store)
        assert entry.element_count == 1
        assert isinstance(entry.block, TableBlock)
        assert len(entry.block.vtable) == 1
        assert entry.block.vtable[0].type == VTableEntryType.BLOCK

    def test_multi_block(self, store: BlockStore):
        """Multiple elements that don't fit in one TABLE produce a LINKS tree."""
        elems = []
        for _ in range(300):
            inner = DataBlock.build(b"x" * 100)
            elems.append(BlockEntry.from_data(inner, 2, 1))

        entry = build_table_array(elems, store)
        assert entry.element_count == 300
        assert isinstance(entry.block, LinksBlock)
        assert entry.block.links[-1].limit == 300


# --- build_links_tree ---


class TestBuildLinksTree:
    def test_single_level(self, store: BlockStore):
        blocks = []
        for _ in range(3):
            data = DataBlock.build(b"x")
            blocks.append(BlockEntry.from_data(data, 2, 10))

        entry = linktree_reduce(blocks, store)
        assert entry.element_count == 30
        assert isinstance(entry.block, LinksBlock)
        assert len(entry.block.links) == 3
        assert entry.block.links[0].limit == 10
        assert entry.block.links[1].limit == 20
        assert entry.block.links[2].limit == 30

    def test_no_tree(self, store: BlockStore):
        block = DataBlock.build(b"x")
        entry = BlockEntry.from_data(block, 2, 1)
        linktree = linktree_reduce([entry], store)
        assert linktree.element_count == 1
        assert isinstance(linktree.block, DataBlock)
        assert linktree.block.get_data() == b"x"

    def test_empty_raises(self, store: BlockStore):
        with pytest.raises(ValueError):
            linktree_reduce([], store)
