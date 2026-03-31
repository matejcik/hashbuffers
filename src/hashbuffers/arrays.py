"""Array representation: building and traversing DATA/SLOTS/TABLE arrays and link trees.

Maps to spec sections "Fitting → Array Representation" and "Arbitrary size arrays → Link trees".
"""

from __future__ import annotations

from .codec import (
    SIZE_MAX,
    Block,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
    VTableEntry,
    VTableEntryType,
    decode_block,
)
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

    if len(elements) <= max_elems_per_block:
        block = DataBlock.build_array(elements, align=elem_align)
        return store.store(block.encode(), limit=len(elements), alignment=alignment)

    # Chunk into multiple blocks
    blocks: list[StoredBlock] = []
    counts: list[int] = []
    for i in range(0, len(elements), max_elems_per_block):
        chunk = elements[i : i + max_elems_per_block]
        block = DataBlock.build_array(chunk, align=elem_align)
        sb = store.store(block.encode(), limit=len(chunk), alignment=alignment)
        blocks.append(sb)
        counts.append(len(chunk))

    return build_links_tree(blocks, counts, store)


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
    counts: list[int] = []
    current_items: list[bytes] = []
    current_block_size = 2 + 2 # header + sentinel

    for i, elem in enumerate(elements):
        # Check if this element can fit alone in a block:
        # header + offset + sentinel + element
        if 2 + 4 + len(elem) > max_block_size:
            raise ValueError(f"Element {i} too large for block even alone (size {len(elem)})")

        # Check if adding this element and its offset would exceed block size
        if current_block_size + 2 + len(elem) > max_block_size:
            # Seal current block
            block = SlotsBlock.build_slots(current_items)
            sb = store.store(block.encode(), limit=len(current_items), alignment=2)
            blocks.append(sb)
            counts.append(len(current_items))
            current_items = [elem]
        else:
            current_items.append(elem)

    # Seal final block
    if current_items:
        block = SlotsBlock.build_slots(current_items)
        sb = store.store(block.encode(), limit=len(current_items), alignment=2)
        blocks.append(sb)
        counts.append(len(current_items))

    if len(blocks) == 1:
        return blocks[0]

    return build_links_tree(blocks, counts, store)


def _build_one_table_block(
    entries: list[tuple[VTableEntryType, bytes, int]],
) -> tuple[bytes, int]:
    """Build a TABLE block from a list of (entry_type, data, alignment) tuples.

    Computes correct offsets in one pass after all entries are known.
    Returns (block_bytes, max_alignment).
    """
    entry_count = len(entries)
    heap_start = 4 + 2 * entry_count
    max_align = 2

    vtable: list[VTableEntry] = []
    heap = bytearray()
    current_offset = heap_start

    for entry_type, data, alignment in entries:
        aligned_offset = _align_up(current_offset, alignment)
        pad = aligned_offset - current_offset
        if pad > 0:
            heap.extend(b"\x00" * pad)
        current_offset = aligned_offset

        vtable.append(VTableEntry(entry_type, current_offset))
        heap.extend(data)
        current_offset += len(data)
        max_align = max(max_align, alignment)

    block = TableBlock.build(vtable, bytes(heap))
    return block.encode(), max_align


def _estimate_table_size(
    current_entries: list[tuple[VTableEntryType, bytes, int]],
    new_entry: tuple[VTableEntryType, bytes, int],
) -> int:
    """Estimate the total block size if new_entry were added."""
    all_entries = current_entries + [new_entry]
    entry_count = len(all_entries)
    heap_start = 4 + 2 * entry_count
    offset = heap_start
    for _, data, alignment in all_entries:
        offset = _align_up(offset, alignment) + len(data)
    return offset


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
    result_counts: list[int] = []

    current_entries: list[tuple[VTableEntryType, bytes, int]] = []

    def seal_current() -> None:
        if not current_entries:
            return
        block_bytes, max_align = _build_one_table_block(current_entries)
        sb = store.store(block_bytes, limit=len(current_entries), alignment=max_align)
        result_blocks.append(sb)
        result_counts.append(len(current_entries))
        current_entries.clear()

    for elem in elements:
        elem_data = elem.data
        elem_align = elem.alignment

        # Can this element ever fit as a BLOCK in a TABLE with 1 entry?
        min_size = _estimate_table_size(
            [], (VTableEntryType.BLOCK, elem_data, elem_align)
        )
        if min_size > max_block_size:
            # Too large to embed; use LINK
            link_data = elem.link.encode()
            new_entry = (VTableEntryType.LINK, link_data, 4)
        else:
            new_entry = (VTableEntryType.BLOCK, elem_data, elem_align)

        # Does it fit in the current block?
        estimated = _estimate_table_size(current_entries, new_entry)
        if estimated > max_block_size:
            seal_current()

        current_entries.append(new_entry)

    seal_current()

    if len(result_blocks) == 1:
        return result_blocks[0]

    return build_links_tree(result_blocks, result_counts, store)


def build_links_tree(
    leaf_blocks: list[StoredBlock],
    counts: list[int],
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Build a LINKS tree over leaf blocks.

    Each leaf_block has a corresponding element count in counts.
    Returns StoredBlock of the root LINKS block.
    """
    if not leaf_blocks:
        raise ValueError("Cannot build links tree from empty list")

    # Build links with cumulative limits
    cumulative = 0
    links: list[Link] = []
    for sb, count in zip(leaf_blocks, counts):
        cumulative += count
        links.append(Link(sb.link.digest, cumulative))

    # How many links fit in one LINKS block?
    # LINKS block: 4 bytes header + 36 * n links
    max_links_per_block = (max_block_size - 4) // Link.SIZE

    if len(links) <= max_links_per_block:
        block = LinksBlock.build(links)
        total_count = cumulative
        return store.store(block.encode(), limit=total_count, alignment=4)

    # Need multiple levels — chunk links into LINKS blocks and recurse
    inner_blocks: list[StoredBlock] = []
    inner_counts: list[int] = []

    for i in range(0, len(links), max_links_per_block):
        chunk = links[i : i + max_links_per_block]
        block = LinksBlock.build(chunk)
        chunk_total = chunk[-1].limit - (links[i - 1].limit if i > 0 else 0)
        sb = store.store(block.encode(), limit=chunk[-1].limit, alignment=4)
        inner_blocks.append(sb)
        inner_counts.append(chunk_total)

    return build_links_tree(inner_blocks, inner_counts, store)


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
