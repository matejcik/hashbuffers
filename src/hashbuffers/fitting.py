"""Struct member fitting: packing fields into a single TABLE block.

Maps to spec section "Fitting → Struct Member Fitting".
"""

from __future__ import annotations

from typing import NamedTuple, Sequence

from .codec import (
    SIZE_MAX,
    Link,
    TableBlock,
    VTableEntry,
    VTableEntryType,
)
from .store import BlockStore, StoredBlock


class InlineValue(NamedTuple):
    """A field stored inline in the vtable (13-bit integer)."""

    value: int


class HeapField(NamedTuple):
    """A field to be placed on the TABLE heap.

    - entry_type: DIRECT (raw value) or BLOCK (sub-block with header)
    - data: the bytes to place on the heap
    - alignment: required alignment for this data
    - link: pre-computed Link if this field can be externalized (None if mandatory inline)
    """

    entry_type: VTableEntryType
    data: bytes
    alignment: int
    link: Link | None


# A table field: None=NULL, InlineValue, HeapField, or Link (already external).
TableField = None | InlineValue | HeapField | Link


def sb_to_table_field(sb: StoredBlock) -> TableField:
    """Convert a StoredBlock to a TableField for embedding in a TABLE.

    Fields whose encoded size is no larger than a Link (36 bytes) are always
    embedded as BLOCK entries. Larger fields are embedded if possible, with
    a pre-computed link for externalization by fit_table if space is tight.
    """
    if len(sb.data) <= Link.SIZE:
        return HeapField(VTableEntryType.BLOCK, sb.data, sb.alignment, None)
    return HeapField(VTableEntryType.BLOCK, sb.data, sb.alignment, sb.link)


def _available_alignment(offset: int) -> int:
    """Return the largest power-of-two alignment available at offset."""
    if offset == 0:
        return 8  # arbitrary large alignment at start
    return offset & -offset


def _alignment_pack(
    fields: list[tuple[int, HeapField]],
    heap_start: int,
    max_size: int,
) -> tuple[list[tuple[int, int, HeapField]], int]:
    """Pack fields onto the heap using alignment-aware placement.

    Returns (placements, final_offset) where placements is
    [(vtable_index, heap_offset, field), ...].
    Fields that don't fit are excluded.
    """
    placements: list[tuple[int, int, HeapField]] = []
    current_offset = heap_start
    remaining = list(fields)

    while remaining:
        avail_align = _available_alignment(current_offset)
        best_idx = None
        best_score = (-1, False)

        for i, (vi, f) in enumerate(remaining):
            if f.alignment > avail_align:
                continue
            if current_offset + len(f.data) > max_size:
                continue
            preserving = len(f.data) % f.alignment == 0
            score = (f.alignment, preserving)
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx is not None:
            vi, f = remaining.pop(best_idx)
            placements.append((vi, current_offset, f))
            current_offset += len(f.data)
        else:
            # Try adding 1 byte of padding
            if current_offset + 1 <= max_size:
                current_offset += 1
            else:
                break  # no more space

            # Check if any remaining field could still fit
            if not any(current_offset + len(f.data) <= max_size for _, f in remaining):
                break

    return placements, current_offset


def fit_table(
    fields: Sequence[TableField],
    store: BlockStore,
    *,
    max_block_size: int = SIZE_MAX,
) -> StoredBlock:
    """Pack fields into a single TABLE block.

    The position in the sequence is the vtable index.
    Fields are: None (NULL), InlineValue (INLINE), HeapField (DIRECT/BLOCK),
    or Link (already externalized).

    HeapFields with encoded_size <= 36 bytes or link=None are mandatory
    (must be placed on heap). Others use smallest-first heuristic; overflow
    becomes LINK entries.
    """
    entry_count = len(fields)
    heap_start = 4 + 2 * entry_count

    # Classify fields
    vtable = [VTableEntry(VTableEntryType.NULL, 0)] * entry_count
    max_align = 2

    # Fixed entries (INLINE, NULL, Link) and heap candidates
    mandatory_heap: list[tuple[int, HeapField]] = []
    optional_heap: list[tuple[int, HeapField]] = []
    link_entries: list[tuple[int, Link]] = []

    for i, field in enumerate(fields):
        if field is None:
            continue
        elif isinstance(field, InlineValue):
            vtable[i] = VTableEntry(VTableEntryType.INLINE, field.value)
        elif isinstance(field, Link):
            link_entries.append((i, field))
            max_align = max(max_align, 4)
        elif isinstance(field, HeapField):
            is_mandatory = len(field.data) <= Link.SIZE or field.link is None
            if is_mandatory:
                mandatory_heap.append((i, field))
            else:
                optional_heap.append((i, field))
        else:
            raise TypeError(f"Unexpected field type: {type(field)}")

    # Sort optional by size ascending (smallest first)
    optional_heap.sort(key=lambda x: len(x[1].data))

    # Pre-place all Link entries (they're always on heap as 36-byte link data)
    link_heap_fields: list[tuple[int, HeapField]] = []
    for i, link in link_entries:
        link_heap_fields.append(
            (
                i,
                HeapField(VTableEntryType.LINK, link.encode(), 4, None),
            )
        )

    # Combine all heap candidates: links + mandatory + optional
    all_heap = link_heap_fields + mandatory_heap + optional_heap

    # Run alignment packing
    placements, _ = _alignment_pack(all_heap, heap_start, max_block_size)

    # Determine which optional fields didn't make it → externalize as LINK
    placed_indices = {vi for vi, _, _ in placements}
    for i, field in optional_heap:
        if i not in placed_indices:
            if field.link is None:
                raise ValueError(
                    f"Field at index {i} doesn't fit and has no link for externalization"
                )
            link_entries.append((i, field.link))
            # Add link data to placements
            link_field = HeapField(VTableEntryType.LINK, field.link.encode(), 4, None)
            # Re-run packing with the new link entry
            # (simpler: just append it, it's smaller than the original)
            all_heap_retry = [(vi, f) for vi, _, f in placements if vi != i] + [
                (i, link_field)
            ]
            placements, _ = _alignment_pack(all_heap_retry, heap_start, max_block_size)
            placed_indices = {vi for vi, _, _ in placements}

    # Build the heap
    # Sort placements by offset for heap construction
    placements.sort(key=lambda x: x[1])

    heap = bytearray()
    for vi, offset, field in placements:
        # Add padding to reach the target offset
        pad_needed = offset - (heap_start + len(heap))
        if pad_needed > 0:
            heap.extend(b"\x00" * pad_needed)
        heap.extend(field.data)

        if field.entry_type == VTableEntryType.LINK:
            vtable[vi] = VTableEntry(VTableEntryType.LINK, offset)
            max_align = max(max_align, 4)
        else:
            vtable[vi] = VTableEntry(field.entry_type, offset)
            max_align = max(max_align, field.alignment)

    block = TableBlock.build(vtable, bytes(heap))
    return store.store(block.encode(), limit=entry_count, alignment=max_align)
