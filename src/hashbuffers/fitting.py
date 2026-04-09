"""Struct member fitting: packing fields into a single TABLE block.

Maps to spec section "Fitting → Struct Member Fitting".
"""

from __future__ import annotations

import typing as t
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass

from .codec import (
    SIZE_MAX,
    Block,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
    VTableEntry,
)
from .store import BlockStore

INLINE_MAX_UNSIGNED = (1 << 13) - 1  # 8191
INLINE_MIN_SIGNED = -(1 << 12)  # -4096
INLINE_MAX_SIGNED = (1 << 12) - 1  # 4095


class TableEntry(ABC):
    @abstractmethod
    def alignment(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def size(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def vtable_entry(self, offset: int) -> VTableEntry:
        raise NotImplementedError

    @abstractmethod
    def encode(self) -> bytes:
        raise NotImplementedError

    @property
    def preserves_alignment(self) -> bool:
        return self.size() % self.alignment() == 0

    @property
    def align_score(self) -> tuple[int, bool]:
        return (self.alignment(), self.preserves_alignment)

    def can_outlink(self) -> bool:
        return self.size() > Link.SIZE

    def outlink(self, store: BlockStore) -> TableEntry:
        raise NotImplementedError

    def place(self, heap: bytearray, heap_offset: int) -> None:
        start = heap_offset
        end = start + self.size()
        assert len(heap) >= end, "Heap is too small"
        heap[start:end] = self.encode()


class InlineEntryBase(TableEntry):
    def alignment(self) -> int:
        return 0

    def size(self) -> int:
        return 0

    def encode(self) -> bytes:
        return b""

    def can_outlink(self) -> bool:
        return False


@dataclass
class InlineIntEntry(InlineEntryBase):
    value: int
    signed: bool

    @staticmethod
    def fits(value: int, signed: bool) -> bool:
        if signed:
            return INLINE_MIN_SIGNED <= value <= INLINE_MAX_SIGNED
        return 0 <= value <= INLINE_MAX_UNSIGNED

    def vtable_entry(self, offset: int) -> VTableEntry:
        # value & SIZE_MAX converts signed to unsigned
        return VTableEntry.inline(self.value & SIZE_MAX)

    def outlink(self, store: BlockStore) -> TableEntry:
        raise ValueError("Inline ints cannot be outlinked")


class NullEntry(InlineEntryBase):
    def vtable_entry(self, offset: int) -> VTableEntry:
        return VTableEntry.null()


NULL_ENTRY = NullEntry()


@dataclass
class DirectEntry(TableEntry):
    data: bytes
    _alignment: int
    element_count: int

    def alignment(self) -> int:
        return self._alignment

    def size(self) -> int:
        return len(self.data)

    def vtable_entry(self, offset: int) -> VTableEntry:
        return VTableEntry.direct(offset)

    def encode(self) -> bytes:
        return self.data

    @classmethod
    def from_int(cls, value: int, size: int, signed: bool) -> t.Self:
        return cls(value.to_bytes(size, "little", signed=signed), size, 1)

    def outlink(self, store: BlockStore) -> TableEntry:
        data_block = DataBlock.build(self.data, align=self._alignment)
        return LinkEntry(Link(store.store(data_block), self.element_count))


@dataclass
class BlockEntry(TableEntry):
    block: Block
    _alignment: int
    element_count: int

    def alignment(self) -> int:
        return self._alignment

    def size(self) -> int:
        return self.block.size

    def vtable_entry(self, offset: int) -> VTableEntry:
        return VTableEntry.block(offset)

    def encode(self) -> bytes:
        return self.block.encode()

    def outlink(self, store: BlockStore) -> TableEntry:
        digest = store.store(self.block)
        return LinkEntry(Link(digest, self.element_count))

    @classmethod
    def from_data(cls, data: DataBlock, alignment: int, element_count: int) -> t.Self:
        return cls(data, alignment, element_count)

    @classmethod
    def from_table(cls, table: TableBlock, alignment: int) -> t.Self:
        return cls(table, alignment, len(table.vtable))

    @classmethod
    def from_link(cls, link: LinksBlock) -> t.Self:
        return cls(link, 4, link.links[-1].limit)

    @classmethod
    def from_slots(cls, slots: SlotsBlock) -> t.Self:
        return cls(slots, 2, slots.element_count())


@dataclass
class LinkEntry(TableEntry):
    link: Link

    def alignment(self) -> int:
        return 4

    def size(self) -> int:
        return Link.SIZE

    def vtable_entry(self, offset: int) -> VTableEntry:
        return VTableEntry.link(offset)

    def encode(self) -> bytes:
        return self.link.encode()

    def outlink(self, store: BlockStore) -> TableEntry:
        raise ValueError("Links cannot be outlinked")


def int_inline_or_direct(value: int, size: int, signed: bool) -> TableEntry:
    if InlineIntEntry.fits(value, signed):
        return InlineIntEntry(value, signed)
    return DirectEntry.from_int(value, size, signed)


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
            group.sort(key=lambda f: (f.entry.preserves_alignment, f.entry.size()))

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
        - IntField → auto INLINE/DIRECT based on value range
        - DirectData → DIRECT on heap (mandatory)
        - StoredBlock → BLOCK on heap; may overflow to LINK if too large
        - Link → LINK on heap (already externalized)

        StoredBlocks ≤ 36 bytes are always embedded. Larger StoredBlocks use
        smallest-first heuristic; overflow becomes LINK entries.
        """
        optionals = [(i, e) for i, e in enumerate(self.entries) if e.can_outlink()]
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
                link = last_block.outlink(store)
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
            entry.place(heap, heap_offset)
            vtable.append(entry.vtable_entry(heap_start + heap_offset))
        return TableBlock.build(vtable, bytes(heap))

    def build_entry(self, store: BlockStore) -> BlockEntry:
        block = self.build(store)
        return BlockEntry.from_table(block, self.alignment)
