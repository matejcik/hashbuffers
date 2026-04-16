"""Struct member fitting: packing fields into a single TABLE block.

Maps to spec section "Fitting → Struct Member Fitting".
"""

from __future__ import annotations

import typing as t
from collections import defaultdict

from hashbuffers.codec.table import TableEntry

from .codec import (
    SIZE_MAX,
    DataBlock,
    Link,
    TableBlock,
)
from .codec.table import (
    BlockEntry,
    DirectDataEntry,
    DirectFixedEntry,
    InlineIntEntry,
    LinkEntry,
    TableEntry,
)
from .store import BlockStore


def place(entry: TableEntry, heap: bytearray, heap_offset: int) -> None:
    start = heap_offset
    end = start + entry.size()
    assert len(heap) >= end, "Heap is too small"
    heap[start:end] = entry.encode()


def can_outlink(entry: TableEntry) -> bool:
    return entry.size() > Link.SIZE


def outlink(entry: TableEntry, store: BlockStore) -> TableEntry:
    match entry:
        case DirectDataEntry():
            block = DataBlock.build(entry.data, elem_size=1, elem_align=1)
            element_count = entry.size()
        case BlockEntry():
            block = entry.block
            element_count = block.element_count()
        case _:
            raise ValueError(f"Cannot outlink entry: {entry}")
    digest = store.store(block)
    link = Link(digest, element_count)
    return LinkEntry(link)


def int_inline_or_direct(value: int, signed: bool) -> TableEntry:
    if InlineIntEntry.fits(value, signed):
        return InlineIntEntry.from_int(value, signed)
    return DirectFixedEntry.from_int(value, signed)


def _available_alignment(offset: int) -> int:
    """Return the largest power-of-two alignment available at offset."""
    if offset == 0:
        return 256  # arbitrary large alignment at start
    return offset & -offset


class EntryPosition(t.NamedTuple):
    idx: int
    entry: TableEntry


class Table:
    entries: list[TableEntry]
    alignment: int

    placement: dict[int, int] | None = None
    heap_size: int

    def __init__(self, entries: list[TableEntry]) -> None:
        self.entries = entries
        self.alignment = 2
        self.placement = None
        self.heap_size = 0

    def alignment_pack(self) -> None:
        """Pack fields onto the heap using alignment-aware placement.

        Raises if the heap doesn't have enough space to pack all fields.

        Returns the actual heap space consumed and the maximum alignment used.
        """
        # optimization: ignore inline fields
        remaining = [
            EntryPosition(i, f) for i, f in enumerate(self.entries) if f.size() > 0
        ]

        heap_start = TableBlock.heap_start(len(self.entries))
        heap_size = SIZE_MAX - heap_start
        current_offset = 0

        max_align = 2

        align_groups: dict[int, list[EntryPosition]] = defaultdict(list)
        placement: dict[int, int] = {}

        # group fields by their alignment
        for ep in remaining:
            align = ep.entry.alignment()
            if align.bit_count() != 1:
                # not a power-of-two, raise:
                raise ValueError(
                    f"Field {ep.idx} has invalid alignment: {align} (not a power-of-two)"
                )
            align_groups[align].append(ep)

        # sort within groups: (1) alignment-preserving, (2) smallest first
        for group in align_groups.values():

            def align_score(entry: TableEntry) -> tuple[bool, int]:
                preserves_alignment = entry.size() % entry.alignment() == 0
                return preserves_alignment, entry.size()

            group.sort(key=lambda ep: align_score(ep.entry))

        while align_groups:
            # find the largest available alignment
            avail_align = _available_alignment(current_offset + heap_start)
            # find the group with highest alignment requirement that can fit here
            while avail_align > 0 and avail_align not in align_groups:
                avail_align //= 2

            # no alignment available, try adding 1 byte of padding
            if avail_align == 0:
                if current_offset + 1 >= heap_size:
                    raise ValueError(f"No space left in block")
                current_offset += 1
                continue  # retry with new offset

            # found a group that can fit here
            group = align_groups[avail_align]
            assert group, "Group is empty, we failed to clean up after ourselves."
            # take the first field
            ep = group.pop(0)
            if not group:
                del align_groups[avail_align]
            field_size = ep.entry.size()

            # this is the smallest field that can fit here
            # but in general, we have to fit all fields. if _any_ field
            # doesn't fit, raise an error.
            if field_size > heap_size - current_offset:
                raise ValueError(
                    f"Field {ep.idx} doesn't fit in block at offset {current_offset}"
                )
            # place the field
            placement[ep.idx] = current_offset
            max_align = max(max_align, ep.entry.alignment())
            current_offset += field_size

        self.placement = placement
        self.heap_size = current_offset
        self.alignment = max_align

    def fit(self, store: BlockStore) -> None:
        """Pack fields into a single TABLE block.

        The position in the sequence is the vtable index. Accepts:
        - None → NULL
        - IntField → auto INLINE/DIRECT4/DIRECT8 based on value range
        - DirectEntry → DIRECT4/DIRECT8 on heap
        - StoredBlock → BLOCK on heap; may overflow to LINK if too large
        - Link → LINK on heap (already externalized)

        StoredBlocks ≤ 36 bytes are always embedded. Larger StoredBlocks use
        smallest-first heuristic; overflow becomes LINK entries.
        """
        optionals = [(i, e) for i, e in enumerate(self.entries) if can_outlink(e)]
        # Sort optional by size ascending (smallest first for packing heuristic)
        optionals.sort(key=lambda x: x[1].size())

        while True:
            try:
                self.alignment_pack()
                return
            except ValueError:
                if not optionals:
                    # all optional blocks have been converted to links
                    # and still don't fit, so we can't fit the table
                    raise
                idx, last_block = optionals.pop()
                link = outlink(last_block, store)
                self.entries[idx] = link

    def build(self, store: BlockStore) -> TableBlock:
        """Build a TABLE block from a list of TableEntry objects.

        Input is a HeapEntry list with placement and ordering already resolved -- that is,
        all NULL entries must be part of the `entries` list.

        Generates the heap bytes and the vtable, and returns the TABLE block.
        """
        if self.placement is None:
            self.fit(store)
            assert self.placement is not None
        heap_start = TableBlock.heap_start(len(self.entries))
        vtable = []
        heap = bytearray(self.heap_size)
        for i, entry in enumerate(self.entries):
            heap_offset = self.placement.get(i, 0)
            place(entry, heap, heap_offset)
            vtable.append(entry.to_entry_raw(heap_start + heap_offset))
        return TableBlock.build(vtable, bytes(heap))

    def build_entry(self, store: BlockStore) -> BlockEntry:
        block = self.build(store)
        return BlockEntry(block)
