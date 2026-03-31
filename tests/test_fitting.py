"""Tests for fitting algorithms."""

import pytest

from hashbuffers.arrays import (
    build_data_array,
    build_links_tree,
    build_slots_array,
    build_table_array,
)
from hashbuffers.codec import (
    SIZE_MAX,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
    VTableEntryType,
    decode_block,
)
from hashbuffers.fitting import (
    HeapField,
    InlineValue,
    fit_table,
)
from hashbuffers.store import BlockStore, StoredBlock


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

    def test_inline_value(self, store):
        sb = fit_table([InlineValue(42), None, InlineValue(7)], store)
        block = TableBlock.decode(sb.data)
        assert block.get_int(0, 2) == 42
        assert block.get_int(1, 2) is None
        assert block.get_int(2, 2) == 7

    def test_direct_field(self, store):
        value = (0xDEADBEEF).to_bytes(4, "little")
        heap_start = 4 + 2 * 1  # 1 entry
        field = HeapField(VTableEntryType.DIRECT, value, 4, None)
        sb = fit_table([field], store)
        block = TableBlock.decode(sb.data)
        assert block.get_int(0, 4) == 0xDEADBEEF

    def test_block_field(self, store):
        inner = DataBlock.build(b"nested payload")
        inner_bytes = inner.encode()
        field = HeapField(VTableEntryType.BLOCK, inner_bytes, 2, None)
        sb = fit_table([field], store)
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
        assert result.digest == b"x" * 32
        assert result.limit == 100
        assert sb.alignment >= 4  # LINK requires 4-alignment

    def test_mixed_fields(self, store):
        """Table with a mix of INLINE, DIRECT, BLOCK, LINK, and NULL."""
        inner = DataBlock.build(b"sub")
        fields = [
            InlineValue(5),  # 0: INLINE
            None,  # 1: NULL
            HeapField(VTableEntryType.DIRECT, b"\x01\x02", 2, None),  # 2: DIRECT
            HeapField(VTableEntryType.BLOCK, inner.encode(), 2, None),  # 3: BLOCK
            Link(b"a" * 32, 50),  # 4: LINK
        ]
        sb = fit_table(fields, store)
        block = TableBlock.decode(sb.data)
        assert block.get_int(0, 2) == 5
        assert block.get_int(1, 2) is None
        assert block.get_fixedsize(2, 2) == b"\x01\x02"
        inner_result = block.get_block(3)
        assert isinstance(inner_result, DataBlock)
        link_result = block.get_block(4)
        assert isinstance(link_result, Link)

    def test_small_values_rule(self, store):
        """Fields ≤ 36 bytes must NOT become LINK even if block is tight."""
        small_data = b"x" * 36  # exactly 36 bytes = Link.SIZE
        link = Link(b"d" * 32, 1)
        field = HeapField(VTableEntryType.DIRECT, small_data, 1, link)
        sb = fit_table([field], store)
        block = TableBlock.decode(sb.data)
        # Must be DIRECT, not LINK
        assert block.vtable[0].type == VTableEntryType.DIRECT

    def test_overflow_to_link(self, store):
        """Field too large for block overflows to LINK."""
        large_data = b"x" * 200
        link = Link(b"d" * 32, 1)
        field = HeapField(VTableEntryType.BLOCK, large_data, 2, link)

        # Use a very small max_block_size to force overflow
        sb = fit_table([field], store, max_block_size=50)
        block = TableBlock.decode(sb.data)
        assert block.vtable[0].type == VTableEntryType.LINK

    def test_smallest_first_heuristic(self, store):
        """Smallest fields are packed first when space is limited."""
        link_big = Link(b"b" * 32, 1)
        link_small = Link(b"s" * 32, 2)
        big = HeapField(VTableEntryType.DIRECT, b"x" * 100, 1, link_big)
        small = HeapField(VTableEntryType.DIRECT, b"y" * 10, 1, link_small)
        # Order: big first, small second. But small should be packed first.
        sb = fit_table([big, small], store, max_block_size=60)
        block = TableBlock.decode(sb.data)
        # Small should be DIRECT, big should overflow to LINK
        assert block.vtable[1].type == VTableEntryType.DIRECT
        assert block.vtable[0].type == VTableEntryType.LINK

    def test_alignment_tracking(self, store):
        """Block alignment is the max of all field alignments."""
        field = HeapField(VTableEntryType.DIRECT, b"\x00" * 8, 8, None)
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
        for i in range(3):
            data = DataBlock.build(b"x").encode()
            blocks.append(store.store(data, limit=10, alignment=2))

        sb = build_links_tree(blocks, [10, 10, 10], store)
        block = LinksBlock.decode(sb.data)
        assert len(block.links) == 3
        assert block.links[0].limit == 10
        assert block.links[1].limit == 20
        assert block.links[2].limit == 30
        assert sb.link.limit == 30

    def test_empty_raises(self, store):
        with pytest.raises(ValueError):
            build_links_tree([], [], store)
