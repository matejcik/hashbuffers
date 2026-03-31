"""Struct member fitting: packing fields into a single TABLE block.

Maps to spec section "Fitting → Struct Member Fitting".
"""

from __future__ import annotations

import typing as t
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

    def as_direct(self, index: int) -> HeapEntry:
        data = self.value.to_bytes(self.size, "little", signed=self.signed)
        return HeapEntry(index, VTableEntryType.DIRECT, data, alignment=self.size)


@dataclass
class DirectData:
    """Fixed-size bytes to place as DIRECT on the heap.

    Always mandatory — cannot be externalized.
    """

    data: bytes
    alignment: int

    def as_direct(self, index: int) -> HeapEntry:
        return HeapEntry(
            index, VTableEntryType.DIRECT, self.data, alignment=self.alignment
        )


# Input type for fit_table.
# None=NULL, int=INLINE (must fit 13-bit unsigned), IntField=auto INLINE/DIRECT,
# DirectData=DIRECT, StoredBlock=BLOCK (may externalize to LINK), Link=LINK.
TableField = None | int | IntField | DirectData | StoredBlock | Link


@dataclass
class HeapEntry:
    """Resolved heap entry ready for placement."""

    vt_index: int
    entry_type: VTableEntryType
    data: bytes
    alignment: int
    heap_offset: int | None = None
    link: Link | None = None

    @classmethod
    def from_block(cls, index: int, block: StoredBlock) -> t.Self:
        return cls(
            index, VTableEntryType.BLOCK, block.data, block.alignment, link=block.link
        )

    @classmethod
    def from_link(cls, index: int, link: Link) -> t.Self:
        return cls(index, VTableEntryType.LINK, link.encode(), 4)

    @property
    def preserves_alignment(self) -> bool:
        return len(self.data) % self.alignment == 0

    @property
    def align_score(self) -> tuple[int, bool]:
        return (self.alignment, self.preserves_alignment)


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


def _alignment_pack(
    fields: list[HeapEntry],
    heap_start: int,
    max_size: int,
) -> int:
    """Pack fields onto the heap using alignment-aware placement.

    Returns the final offset of the heap.

    Raises if it is not possible to pack all fields into the block.
    """
    current_offset = heap_start
    remaining = fields[:]

    while remaining:
        avail_align = _available_alignment(current_offset)
        best_idx = None
        best_score = (-1, False)

        for i, f in enumerate(remaining):
            if f.alignment > avail_align:
                continue
            if current_offset + len(f.data) > max_size:
                continue
            if f.align_score > best_score:
                best_score = f.align_score
                best_idx = i

        if best_idx is not None:
            f = remaining.pop(best_idx)
            f.heap_offset = current_offset
            current_offset += len(f.data)
        else:
            # Try adding 1 byte of padding
            if current_offset + 1 <= max_size:
                current_offset += 1
            else:
                raise ValueError(f"No field fits in block at offset {current_offset}")

            # Check if any remaining field could still fit
            if not any(current_offset + len(f.data) <= max_size for f in remaining):
                raise ValueError(f"No field fits in block at offset {current_offset}")

    return current_offset


def fit_table(
    fields: t.Sequence[TableField],
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Pack fields into a single TABLE block.

    The position in the sequence is the vtable index. Accepts:
    - None → NULL
    - int → INLINE (must fit 13-bit unsigned)
    - IntField → auto INLINE/DIRECT based on value range
    - DirectData → DIRECT on heap (mandatory)
    - StoredBlock → BLOCK on heap; may overflow to LINK if too large
    - Link → LINK on heap (already externalized)

    StoredBlocks ≤ 36 bytes are always embedded. Larger StoredBlocks use
    smallest-first heuristic; overflow becomes LINK entries.
    """
    entry_count = len(fields)
    heap_start = 4 + 2 * entry_count

    vtable = [VTableEntry.null()] * entry_count
    max_align = 2

    # Mandatory heap entries (always placed)
    mandatory: list[HeapEntry] = []
    # Optional StoredBlocks (can be externalized to LINK)
    optional: list[tuple[int, StoredBlock]] = []
    optional_blocks: list[HeapEntry] = []
    optional_links: list[HeapEntry] = []

    for i, field in enumerate(fields):
        if field is None:
            # already NULL in the pre-filled vtable
            continue
        elif isinstance(field, int):
            if not _fits_inline(field, signed=False):
                raise ValueError(
                    f"Bare int {field} doesn't fit in 13-bit unsigned inline"
                )
            vtable[i] = VTableEntry.inline(field)
        elif isinstance(field, IntField):
            try:
                vtable[i] = field.as_inline()
            except ValueError:
                mandatory.append(field.as_direct(i))
        elif isinstance(field, DirectData):
            mandatory.append(field.as_direct(i))
        elif isinstance(field, Link):
            mandatory.append(HeapEntry.from_link(i, field))
            max_align = max(max_align, 4)
        elif isinstance(field, StoredBlock):
            block_entry = HeapEntry.from_block(i, field)
            if len(field.data) <= Link.SIZE:
                mandatory.append(block_entry)
            else:
                optional_blocks.append(block_entry)
        else:
            raise TypeError(f"Unexpected field type: {type(field)}")

    # Sort optional by size ascending (smallest first for packing heuristic)
    optional_blocks.sort(key=lambda x: len(x.data))

    while True:
        all_heap = mandatory + optional_links + optional_blocks
        try:
            _alignment_pack(all_heap, heap_start, max_block_size)
            break
        except ValueError:
            if not optional_blocks:
                raise
            last_block = optional_blocks.pop()
            assert last_block.link is not None, "Block in optional_blocks is missing its link"
            optional_links.append(HeapEntry.from_link(last_block.vt_index, last_block.link))

    # Build the heap
    all_heap.sort(key=lambda x: x.heap_offset or 0)

    heap = bytearray()
    for entry in all_heap:
        assert entry.heap_offset is not None, "Heap entry must have an offset"
        pad_needed = entry.heap_offset - (heap_start + len(heap))
        if pad_needed > 0:
            heap.extend(b"\x00" * pad_needed)
        heap.extend(entry.data)

        vtable[entry.vt_index] = VTableEntry(entry.entry_type, entry.heap_offset)
        max_align = max(max_align, entry.alignment)

    block = TableBlock.build(vtable, bytes(heap))
    return store.store(block.encode(), limit=entry_count, alignment=max_align)
