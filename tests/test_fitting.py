"""Tests for fitting algorithms."""

import pytest

from hashbuffers.arrays import (
    build_data_array,
    build_slots_array,
    build_table_array,
    linktree_reduce,
)
from hashbuffers.codec import (
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
    VTableEntryType,
    decode_block,
)
from hashbuffers.fitting import DirectData, IntField, fit_table
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


# --- fit_table ---


class TestFitTable:
    def test_empty_table(self, store):
        sb = fit_table([], store)
        block = TableBlock.decode(sb.data)
        assert block.vtable == []
        assert sb.link.limit == 0

    def test_null_fields(self, store):
        sb = fit_table([None, None, None], store)
        block = TableBlock.decode(sb.data)
        assert len(block.vtable) == 3
        assert all(e.type == VTableEntryType.NULL for e in block.vtable)

    def test_int_field_inline(self, store):
        """IntField that fits in 13-bit range becomes INLINE."""
        sb = fit_table(
            [
                IntField(0, 8),
                IntField(100, 4),
                IntField(-5, 2, signed=True),
            ],
            store,
        )
        block = TableBlock.decode(sb.data)
        assert block.vtable[0].type == VTableEntryType.INLINE
        assert block.get_int(0, 8) == 0
        assert block.vtable[1].type == VTableEntryType.INLINE
        assert block.get_int(1, 4) == 100
        assert block.vtable[2].type == VTableEntryType.INLINE
        assert block.get_int(2, 2, signed=True) == -5

    def test_int_field_direct(self, store):
        """IntField too large for inline becomes DIRECT."""
        sb = fit_table([IntField(0xDEADBEEF, 4)], store)
        block = TableBlock.decode(sb.data)
        assert block.vtable[0].type == VTableEntryType.DIRECT
        assert block.get_int(0, 4) == 0xDEADBEEF

    def test_direct_data(self, store):
        value = b"deadbeef"
        sb = fit_table([DirectData(value, 4)], store)
        block = TableBlock.decode(sb.data)
        assert block.vtable[0].type == VTableEntryType.DIRECT
        assert block.get_fixedsize(0, len(value)) == value

    def test_stored_block_field(self, store):
        inner = DataBlock.build(b"nested payload")
        inner_sb = store.store(inner.encode(), limit=len(inner.data), alignment=2)
        sb = fit_table([inner_sb], store)
        block = TableBlock.decode(sb.data)
        result = block.get_block(0)
        assert isinstance(result, DataBlock)
        assert result.get_data() == b"nested payload"

    def test_link_field(self, store):
        """Pre-externalized link is placed on heap."""
        link = Link(b"x" * 32, 100)
        sb = fit_table([link], store)
        block = TableBlock.decode(sb.data)
        result = block.get_block(0)
        assert isinstance(result, Link)
        assert result.digest == link.digest
        assert result.limit == link.limit
        assert sb.alignment >= 4  # LINK requires 4-alignment

    def test_mixed_fields(self, store):
        """Table with a mix of INLINE, DIRECT, BLOCK, LINK, and NULL."""
        inner = DataBlock.build(b"sub")
        inner_sb = store.store(inner.encode(), limit=1, alignment=2)
        fields = [
            IntField(0, 2),  # 0: INLINE
            None,  # 1: NULL
            DirectData(b"\x01\x02", 2),  # 2: DIRECT
            inner_sb,  # 3: BLOCK
            Link(b"a" * 32, 50),  # 4: LINK
        ]
        sb = fit_table(fields, store)
        block = TableBlock.decode(sb.data)
        assert block.get_int(0, 2) == 0
        assert block.get_int(1, 2) is None
        assert block.get_fixedsize(2, 2) == b"\x01\x02"
        inner_result = block.get_block(3)
        assert isinstance(inner_result, DataBlock)
        link_result = block.get_block(4)
        assert isinstance(link_result, Link)

    def test_small_stored_block_auto_embedded(self, store):
        """StoredBlocks ≤ 36 bytes must NOT become LINK even if block is tight."""
        small_data = b"\x00" * 34  # 36 bytes encoded (with 2-byte header)
        must_embed = DataBlock.build(small_data)
        can_outlink = DataBlock.build(small_data + b"x")
        assert len(must_embed.encode()) <= Link.SIZE
        assert len(can_outlink.encode()) > Link.SIZE
        must_embed_sb = store.store(must_embed.encode(), limit=1, alignment=1)
        can_outlink_sb = store.store(can_outlink.encode(), limit=1, alignment=1)
        # put `can_outlink` first
        sb = fit_table(
            [can_outlink_sb, must_embed_sb],
            store,
            max_block_size=2  # header
            + 2  # entry count
            + 2 * 2  # entry offsets
            + len(must_embed_sb.data)
            + len(can_outlink_sb.data)
            - 1,  # so that both data blocks can't fit
        )
        block = TableBlock.decode(sb.data)
        assert block.vtable[0].type == VTableEntryType.LINK
        assert block.vtable[1].type == VTableEntryType.BLOCK

    def test_overflow_to_link(self, store):
        """StoredBlock too large for block overflows to LINK."""
        large_inner = DataBlock.build(b"x" * 200)
        large_sb = store.store(large_inner.encode(), limit=1, alignment=2)

        # Use a very small max_block_size to force overflow
        sb = fit_table(
            [large_sb],
            store,
            max_block_size=2  # header
            + 2  # entry count
            + 2  # entry offset
            + len(large_sb.data)
            - 1,  # so that the data block can't fit
        )
        block = TableBlock.decode(sb.data)
        assert block.vtable[0].type == VTableEntryType.LINK

    def test_alignment_tracking(self, store):
        """Block alignment is the max of all field alignments."""
        field = DirectData(b"\x00" * 8, 8)
        sb = fit_table([field], store)
        assert sb.alignment == 8


# --- build_data_array ---


class TestBuildDataArray:
    def test_empty(self, store):
        sb = build_data_array([], 4, store)
        assert sb.link.limit == 0

    def test_single_block(self, store):
        elements = [b"\x01\x00\x00\x00", b"\x02\x00\x00\x00"]
        sb = build_data_array(elements, 4, store)
        block = DataBlock.decode(sb.data)
        assert block.get_array(4, align=4) == elements
        assert sb.link.limit == 2

    def test_multi_block_creates_links_tree(self, store):
        """Large array spans multiple DATA blocks linked by a LINKS tree."""
        elements = [i.to_bytes(4, "little") for i in range(500)]
        sb = build_data_array(elements, 4, store, max_block_size=200)
        # Root should be a LINKS block
        block = decode_block(sb.data)
        assert isinstance(block, LinksBlock)
        assert sb.link.limit == 500
        # Should have stored multiple blocks
        assert len(store) > 2


# --- build_slots_array ---


class TestBuildSlotsArray:
    def test_empty(self, store):
        sb = build_slots_array([], store)
        assert sb.link.limit == 0

    def test_single_block(self, store):
        items = [b"hello", b"world"]
        sb = build_slots_array(items, store)
        block = SlotsBlock.decode(sb.data)
        assert block.get_entry(0) == b"hello"
        assert block.get_entry(1) == b"world"
        assert sb.link.limit == 2

    def test_multi_block_creates_links_tree(self, store):
        """Large SLOTS array spans multiple blocks."""
        items = [b"x" * 50 for _ in range(100)]
        sb = build_slots_array(items, store, max_block_size=200)
        block = decode_block(sb.data)
        assert isinstance(block, LinksBlock)
        assert sb.link.limit == 100


# --- build_table_array ---


class TestBuildTableArray:
    def test_empty(self, store):
        sb = build_table_array([], store)
        block = TableBlock.decode(sb.data)
        assert block.vtable == []
        assert sb.link.limit == 0

    def test_single_element(self, store):
        inner = DataBlock.build(b"elem")
        inner_sb = store.store(inner.encode(), limit=1, alignment=2)
        sb = build_table_array([inner_sb], store)
        block = TableBlock.decode(sb.data)
        assert len(block.vtable) == 1
        assert block.vtable[0].type == VTableEntryType.BLOCK

    def test_multi_block(self, store):
        """Multiple elements that don't fit in one TABLE produce a LINKS tree."""
        elems = []
        for i in range(20):
            inner = DataBlock.build(b"x" * 100)
            elems.append(store.store(inner.encode(), limit=1, alignment=2))

        sb = build_table_array(elems, store, max_block_size=300)
        block = decode_block(sb.data)
        assert isinstance(block, LinksBlock)
        assert sb.link.limit == 20


# --- build_links_tree ---


class TestBuildLinksTree:
    def test_single_level(self, store):
        blocks = []
        for _ in range(3):
            data = DataBlock.build(b"x").encode()
            blocks.append(store.store(data, limit=10, alignment=2))

        sb = linktree_reduce(blocks, store)
        block = LinksBlock.decode(sb.data)
        assert len(block.links) == 3
        assert block.links[0].limit == 10
        assert block.links[1].limit == 20
        assert block.links[2].limit == 30
        assert sb.link.limit == 30

    def test_no_tree(self, store):
        block = DataBlock.build(b"x")
        sb = store.store(block.encode(), limit=1, alignment=1)
        linktree = linktree_reduce([sb], store)
        block = decode_block(linktree.data)
        assert isinstance(block, DataBlock)
        assert block.get_data() == b"x"

    def test_empty_raises(self, store):
        with pytest.raises(ValueError):
            linktree_reduce([], store)
