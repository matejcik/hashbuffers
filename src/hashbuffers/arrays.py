"""Array representation: building and traversing DATA/SLOTS/TABLE arrays and link trees.

Maps to spec sections "Fitting → Array Representation" and "Arbitrary size arrays → Link trees".
"""

from __future__ import annotations

import itertools

from .codec import (
    SIZE_MAX,
    Block,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
    decode_block,
)
from .fitting import TableEntry, alignment_pack, build_table
from .store import BlockStore, StoredBlock


def _align_up(offset: int, alignment: int) -> int:
    """Round offset up to the next multiple of alignment."""
    return (offset + alignment - 1) & ~(alignment - 1)


# ============================================================
# Encode side: building arrays into blocks
# ============================================================


def build_data_array(
    elements: list[bytes],
    elem_align: int,
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Build a DATA array, possibly spanning multiple blocks as a link tree.

    All elements must be the same size (derived from elements[0]).
    """
    alignment = max(elem_align, 2)
    if not elements:
        block = DataBlock.build(b"", align=elem_align)
        return store.store(block.encode(), limit=0, alignment=alignment)

    elem_size = len(elements[0])
    padded = DataBlock.padded_elem_size(elem_size, elem_align)
    start_offset = max(elem_align, 2)
    max_elems_per_block = (max_block_size - start_offset) // padded

    if max_elems_per_block == 0:
        raise ValueError(
            f"Element size {elem_size} (padded {padded}) too large for block"
        )

    # Chunk into multiple blocks
    blocks: list[StoredBlock] = []
    for chunk in itertools.batched(elements, max_elems_per_block):
        block = DataBlock.build_array(list(chunk), align=elem_align)
        sb = store.store(block.encode(), limit=len(chunk), alignment=alignment)
        blocks.append(sb)

    return linktree_reduce(blocks, store)


def build_slots_array(
    elements: list[bytes],
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Build a SLOTS array, possibly spanning multiple blocks as a link tree."""
    if not elements:
        block = SlotsBlock.build_slots([])
        return store.store(block.encode(), limit=0, alignment=2)

    # Pack elements into SLOTS blocks sequentially
    blocks: list[StoredBlock] = []
    current_items: list[bytes] = []
    current_block_size = 2 + 2  # header + sentinel

    def seal_current() -> None:
        nonlocal current_block_size
        current_block_size = 2 + 2  # header + sentinel
        if not current_items:
            return
        block = SlotsBlock.build_slots(current_items)
        sb = store.store(block.encode(), limit=len(current_items), alignment=2)
        blocks.append(sb)
        current_items.clear()

    for i, elem in enumerate(elements):
        # Check if this element can fit alone in a block:
        # header + offset + sentinel + element
        if 2 + 4 + len(elem) > max_block_size:
            raise ValueError(
                f"Element {i} too large for block even alone (size {len(elem)})"
            )

        # Check if adding this element and its offset would exceed block size
        if current_block_size + 2 + len(elem) > max_block_size:
            # Seal current block
            seal_current()

        current_items.append(elem)
        current_block_size += 2 + len(elem)

    # Seal final block
    seal_current()

    return linktree_reduce(blocks, store)


def build_table_array(
    elements: list[StoredBlock],
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Build a TABLE array of complex elements.

    Uses the "always inline" algorithm: embed each element as a BLOCK entry
    when possible, fall back to LINK for elements too large to ever embed.
    """
    if not elements:
        block = TableBlock.build([], b"")
        return store.store(block.encode(), limit=0, alignment=2)

    result_blocks: list[StoredBlock] = []
    current_entries: list[TableEntry] = []

    space_remaining = max_block_size - 2 - 2

    def seal_current() -> None:
        nonlocal space_remaining
        if not current_entries:
            return
        heap_size, max_align = alignment_pack(current_entries, max_block_size)
        block = build_table(current_entries, heap_size)
        sb = store.store(block.encode(), limit=len(block.vtable), alignment=max_align)
        result_blocks.append(sb)
        current_entries.clear()

    for elem in elements:
        # Can this element ever fit as a BLOCK in a TABLE with 1 entry?
        block_entry = TableEntry.from_block(elem)
        try:
            alignment_pack([block_entry], max_block_size)
        except ValueError:
            # Too large to embed; use LINK
            new_entry = TableEntry.from_link(elem.link)
        else:
            new_entry = block_entry

        # Does it fit in the current block?
        try:
            alignment_pack(current_entries + [new_entry], max_block_size)
        except ValueError:
            seal_current()

        current_entries.append(new_entry)

    seal_current()

    return linktree_reduce(result_blocks, store)


def linktree_reduce(
    leaf_blocks: list[StoredBlock],
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Reduces a non-empty list to a single root block.

    Returns a single StoredBlock. If the list has just one element, it is
    returned. Otherwise, builds a link tree from the list and returns its root.
    """
    if not leaf_blocks:
        raise ValueError("Cannot build links tree from empty list")

    if len(leaf_blocks) == 1:
        return leaf_blocks[0]

    # How many links fit in one LINKS block?
    # LINKS block: 4 bytes header + 36 * n links
    max_links_per_block = (max_block_size - 4) // Link.SIZE

    links = [sb.link for sb in leaf_blocks]
    inner_blocks: list[StoredBlock] = []

    for chunk in itertools.batched(links, max_links_per_block):
        limits = itertools.accumulate(l.limit for l in chunk)
        chunk_links = [Link(l.digest, limit) for l, limit in zip(chunk, limits)]
        block = LinksBlock.build(chunk_links)
        sb = store.store(block.encode(), limit=block.links[-1].limit, alignment=4)
        inner_blocks.append(sb)

    if len(inner_blocks) == 1:
        return inner_blocks[0]

    return linktree_reduce(inner_blocks, store)


# ============================================================
# Decode side: traversing link trees and extracting elements
# ============================================================


def collect_leaves(data: bytes, store: BlockStore) -> list[Block]:
    """Traverse a link tree, collecting all leaf blocks.

    If `data` decodes to a LINKS block, recursively follow each link and
    collect the leaves. Otherwise, return the decoded block as a single leaf.
    """
    block = decode_block(data)
    if isinstance(block, LinksBlock):
        result: list[Block] = []
        for link in block.links:
            sb = store[link.digest]
            result.extend(collect_leaves(sb.data, store))
        return result
    return [block]


def decode_data_elements(
    data: bytes, elem_size: int, elem_align: int, store: BlockStore
) -> list[bytes]:
    """Decode a DATA array (possibly a link tree) into raw element bytes.

    Each returned bytes object is exactly `elem_size` bytes.
    """
    leaves = collect_leaves(data, store)
    result: list[bytes] = []
    for leaf in leaves:
        if not isinstance(leaf, DataBlock):
            raise ValueError(
                f"Expected DATA leaf in DATA array, got {type(leaf).__name__}"
            )
        result.extend(leaf.get_array(elem_size, align=elem_align))
    return result


def decode_table_entries(data: bytes, store: BlockStore) -> list[Block | Link]:
    """Decode a TABLE array (possibly a link tree) into individual entries.

    Each entry is a Block (for BLOCK entries) or a Link (for LINK entries).
    """
    leaves = collect_leaves(data, store)
    result: list[Block | Link] = []
    for leaf in leaves:
        if not isinstance(leaf, TableBlock):
            raise ValueError(
                f"Expected TABLE leaf in TABLE array, got {type(leaf).__name__}"
            )
        for i in range(len(leaf.vtable)):
            entry = leaf.get_block(i)
            if entry is not None:
                result.append(entry)
    return result


def decode_slots_entries(data: bytes, store: BlockStore) -> list[bytes]:
    """Decode a SLOTS array (possibly a link tree) into raw slot bytes."""
    leaves = collect_leaves(data, store)
    result: list[bytes] = []
    for leaf in leaves:
        if not isinstance(leaf, SlotsBlock):
            raise ValueError(
                f"Expected SLOTS leaf in SLOTS array, got {type(leaf).__name__}"
            )
        for i in range(leaf.element_count):
            result.append(leaf.get_entry(i))
    return result
