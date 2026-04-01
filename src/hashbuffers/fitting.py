"""Struct member fitting: packing fields into a single TABLE block.

Maps to spec section "Fitting → Struct Member Fitting".
"""

from __future__ import annotations

import typing as t
from collections import defaultdict
from dataclasses import dataclass

from .codec import (
    SIZE_MAX,
    Link,
    TableBlock,
    VTableEntry,
    VTableEntryType,
)
from .store import BlockStore, StoredBlock

INLINE_MAX_UNSIGNED = (1 << 13) - 1  # 8191
INLINE_MIN_SIGNED = -(1 << 12)  # -4096
INLINE_MAX_SIGNED = (1 << 12) - 1  # 4095


@dataclass
class IntField:
    """An integer field with known wire size.

    Fitting decides INLINE vs DIRECT based on value range:
    - unsigned: 0..8191 → INLINE, else DIRECT
    - signed: -4096..4095 → INLINE, else DIRECT
    """

    value: int
    size: int  # byte size for DIRECT encoding (1, 2, 4, 8)
    signed: bool = False

    def as_inline(self) -> VTableEntry:
        if not _fits_inline(self.value, signed=self.signed):
            raise ValueError(
                f"Value {self.value} doesn't fit in 13-bit {self.signed} inline"
            )
        return VTableEntry.inline(_inline_encode(self.value, self.signed))

    def as_direct(self) -> TableEntry:
        data = self.value.to_bytes(self.size, "little", signed=self.signed)
        return TableEntry(VTableEntryType.DIRECT, data, alignment=self.size)


@dataclass
class DirectData:
    """Fixed-size bytes to place as DIRECT on the heap.

    Always mandatory — cannot be externalized.
    """

    data: bytes
    alignment: int

    def as_direct(self) -> TableEntry:
        return TableEntry(VTableEntryType.DIRECT, self.data, alignment=self.alignment)


# Input type for fit_table.
# None=NULL, int=INLINE (must fit 13-bit unsigned), IntField=auto INLINE/DIRECT,
# DirectData=DIRECT, StoredBlock=BLOCK (may externalize to LINK), Link=LINK.
TableField = None | IntField | DirectData | StoredBlock | Link


@dataclass
class TableEntry:
    """Resolved heap entry ready for placement."""

    vt_type: VTableEntryType
    data: bytes
    alignment: int

    inline_value: int | None = None
    heap_offset: int | None = None
    """Zero-based offset into the heap. None if not yet placed."""
    link: Link | None = None

    @classmethod
    def _inline_entry(cls, vt_entry: VTableEntry) -> t.Self:
        return cls(vt_entry.type, b"", 0, inline_value=vt_entry.offset, heap_offset=0)

    @classmethod
    def null(cls) -> t.Self:
        return cls._inline_entry(VTableEntry.null())

    @classmethod
    def int_field(cls, field: IntField) -> "TableEntry":
        try:
            return cls._inline_entry(field.as_inline())
        except ValueError:
            return field.as_direct()

    @classmethod
    def from_block(cls, block: StoredBlock) -> t.Self:
        return cls(VTableEntryType.BLOCK, block.data, block.alignment, link=block.link)

    @classmethod
    def from_link(cls, link: Link) -> t.Self:
        return cls(VTableEntryType.LINK, link.encode(), 4)

    @property
    def preserves_alignment(self) -> bool:
        return len(self.data) % self.alignment == 0

    @property
    def align_score(self) -> tuple[int, bool]:
        return (self.alignment, self.preserves_alignment)

    def place(self, heap: bytearray, heap_start: int) -> VTableEntry:
        assert self.heap_offset is not None, "Heap entry is not placed yet!"
        start = self.heap_offset
        end = start + len(self.data)
        assert len(heap) >= end, "Heap is too small"
        heap[start:end] = self.data
        if self.inline_value is not None:
            vt_offset = self.inline_value
        else:
            vt_offset = self.heap_offset + heap_start
        return VTableEntry(self.vt_type, vt_offset)


def _fits_inline(value: int, *, signed: bool) -> bool:
    if signed:
        return INLINE_MIN_SIGNED <= value <= INLINE_MAX_SIGNED
    return 0 <= value <= INLINE_MAX_UNSIGNED


def _inline_encode(value: int, signed: bool) -> int:
    """Encode value for INLINE vtable entry (13-bit two's complement for signed)."""
    if signed and value < 0:
        return value & 0x1FFF
    return value


def _available_alignment(offset: int) -> int:
    """Return the largest power-of-two alignment available at offset."""
    if offset == 0:
        return 256  # arbitrary large alignment at start
    return offset & -offset


def alignment_pack(
    fields: list[TableEntry],
    max_block_size: int,
) -> tuple[int, int]:
    """Pack fields onto the heap using alignment-aware placement.

    Raises if the heap doesn't have enough space to pack all fields.

    Returns the actual heap space consumed and the maximum alignment used.
    """
    # optimization: ignore inline fields (alignment 0)
    remaining = [f for f in fields if f.alignment > 0]

    heap_start = 4 + 2 * len(fields)
    heap_size = max_block_size - heap_start
    current_offset = 0

    max_align = 2

    align_groups = defaultdict(list)

    # group fields by their alignment
    for f in remaining:
        if f.alignment.bit_count() != 1:
            # not a power-of-two, raise:
            raise ValueError(
                f"Field {f} has invalid alignment: {f.alignment} (not a power-of-two)"
            )
        align_groups[f.alignment].append(f)

    # sort within groups: (1) alignment-preserving, (2) smallest first
    for group in align_groups.values():
        group.sort(key=lambda f: (f.preserves_alignment, len(f.data)))

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
        field = group.pop(0)
        if not group:
            del align_groups[avail_align]

        # this is the smallest field that can fit here
        # but in general, we have to fit all fields. if _any_ field
        # doesn't fit, raise an error.
        if len(field.data) > heap_size - current_offset:
            raise ValueError(
                f"Field {field} doesn't fit in block at offset {current_offset}"
            )
        # place the field
        field.heap_offset = current_offset
        max_align = max(max_align, field.alignment)
        current_offset += len(field.data)

    return current_offset, max_align


def fit_table(
    fields: list[TableField],
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Pack fields into a single TABLE block.

    The position in the sequence is the vtable index. Accepts:
    - None → NULL
    - IntField → auto INLINE/DIRECT based on value range
    - DirectData → DIRECT on heap (mandatory)
    - StoredBlock → BLOCK on heap; may overflow to LINK if too large
    - Link → LINK on heap (already externalized)

    StoredBlocks ≤ 36 bytes are always embedded. Larger StoredBlocks use
    smallest-first heuristic; overflow becomes LINK entries.
    """
    # All vtable entries
    entries: list[TableEntry] = []
    # Optional heap entries that are allowed to be linked out
    optional: list[tuple[int, TableEntry]] = []

    for i, field in enumerate(fields):
        if field is None:
            # already NULL in the pre-filled vtable
            entries.append(TableEntry.null())
        elif isinstance(field, IntField):
            entries.append(TableEntry.int_field(field))
        elif isinstance(field, DirectData):
            entries.append(field.as_direct())
        elif isinstance(field, Link):
            entries.append(TableEntry.from_link(field))
        elif isinstance(field, StoredBlock):
            block_entry = TableEntry.from_block(field)
            entries.append(block_entry)
            if len(field.data) > Link.SIZE:
                assert (
                    field.link.limit > 0
                ), "Link limit is 0 -- outlinked empty array? should not happen"
                # try to store as block, fitting pass will
                # convert to link if necessary
                optional.append((i, block_entry))
        else:
            raise TypeError(f"Unexpected field type: {type(field)}")

    # Sort optional by size ascending (smallest first for packing heuristic)
    optional.sort(key=lambda x: len(x[1].data))

    while True:
        try:
            heap_size, max_align = alignment_pack(entries, max_block_size)
            break
        except ValueError:
            if not optional:
                # all optional blocks have been converted to links
                # and still don't fit, so we can't fit the table
                raise
            idx, last_block = optional.pop()
            assert (
                last_block.link is not None
            ), "Block in optional_blocks is missing its link"
            entries[idx] = TableEntry.from_link(last_block.link)

    block = build_table(entries, heap_size)
    return store.store(block.encode(), limit=len(block.vtable), alignment=max_align)


def build_table(entries: list[TableEntry], heap_size: int) -> TableBlock:
    """Build a TABLE block from a list of TableEntry objects.

    Input is a HeapEntry list with placement and ordering already resolved -- that is,
    all NULL entries must be part of the `entries` list.

    Generates the heap bytes and the vtable, and returns the TABLE block.
    """
    heap_start = 4 + 2 * len(entries)
    vtable = []
    heap = bytearray(heap_size)
    for entry in entries:
        vtable.append(entry.place(heap, heap_start))
    return TableBlock.build(vtable, bytes(heap))
