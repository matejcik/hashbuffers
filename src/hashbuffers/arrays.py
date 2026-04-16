"""Array representation: building and traversing DATA/SLOTS/TABLE arrays and link trees.

Maps to spec sections "Fitting → Array Representation" and "Arbitrary size arrays → Link trees".
"""

from __future__ import annotations

import bisect
import itertools
import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .codec import (
    SIZE_MAX,
    Block,
    DataBlock,
    Link,
    LinksBlock,
    SlotsBlock,
    TableBlock,
)
from .codec.table import LinkEntry, TableEntry
from .fitting import BlockEntry, Table, outlink
from .store import BlockStore
from .util import align_up

T = t.TypeVar("T")
EntryType = t.TypeVar("EntryType")
ElemType = t.TypeVar("ElemType")


def limits_to_cumulative(links: t.Sequence[Link]) -> list[Link]:
    limits = itertools.accumulate(l.limit for l in links)
    return [Link(l.digest, limit) for l, limit in zip(links, limits)]


def limits_to_individual(links: t.Sequence[Link]) -> list[Link]:
    limits = [links[0].limit] + [
        b.limit - a.limit for a, b in itertools.pairwise(links)
    ]
    return [Link(l.digest, limit) for l, limit in zip(links, limits)]


@dataclass
class LinkTree:
    root: Block
    store: BlockStore

    def __len__(self) -> int:
        return self.root.element_count()

    def find_leaf(self, relative_index: int) -> tuple[int, Block]:
        node = self.root
        if not isinstance(node, LinksBlock):
            # short circuit
            return relative_index, node

        expected_size = node.element_count()

        # use a loop instead of recursion to avoid stack overflow -- trees are allowed to be arbitrarily deep
        while isinstance(node, LinksBlock) and expected_size > 1:
            # verify actual size against expected from previous round
            actual_size = node.element_count()
            if expected_size != actual_size:
                raise ValueError(
                    f"Expected {expected_size} elements, got {actual_size}"
                )

            # binary-search the index of the link that contains the element
            i = bisect.bisect_right(node, relative_index, key=lambda l: l.limit)
            link = node[i]
            # find the previous link's limit
            prev_limit = node[i - 1].limit if i > 0 else 0
            # calculate expected size for next round
            expected_size = link.limit - prev_limit
            # adjust relative index
            relative_index -= prev_limit
            # descend
            node = self.store.fetch(link.digest)

        # now `node` is a leaf block
        if (actual_size := node.element_count()) != expected_size:
            raise ValueError(f"Expected {expected_size} elements, got {actual_size}")
        return relative_index, node

    def collect_leaves(
        self, index: slice = slice(None)
    ) -> tuple[int, t.Sequence[Block]]:
        total_size = len(self)
        range_start, range_stop, _step = index.indices(total_size)
        if _step != 1:
            raise NotImplementedError("Step not supported")

        if range_start >= range_stop:
            return range_start, []

        if not isinstance(self.root, LinksBlock):
            return 0, [self.root]

        leaves: list[Block] = []

        def overlaps_query(block_start: int, block_size: int) -> bool:
            block_stop = block_start + block_size
            return block_start < range_stop and range_start < block_stop

        # Stack contains (block, global_start, expected_size) in DFS order.
        # Children are pushed in reverse so stack top = first child to visit.
        stack: list[tuple[Block, int, int]] = [(self.root, 0, total_size)]
        returned_start = 0

        while stack:
            block, block_start, expected_size = stack.pop()
            actual_size = block.element_count()
            if actual_size != expected_size:
                raise ValueError(
                    f"Expected {expected_size} elements, got {actual_size}"
                )

            if not isinstance(block, LinksBlock):
                if overlaps_query(block_start, expected_size):
                    if not leaves:
                        # mark the global start offset of the first leaf
                        returned_start = block_start
                    leaves.append(block)
                continue

            prev_limit = 0
            children: list[tuple[Block, int, int]] = []
            for link in block:
                child_size = link.limit - prev_limit
                child_start = block_start + prev_limit
                prev_limit = link.limit
                if not overlaps_query(child_start, child_size):
                    continue
                child = self.store.fetch(link.digest)
                children.append((child, child_start, child_size))

            stack.extend(reversed(children))

        return returned_start, leaves


class BytestringTree:
    def __init__(self, root: Block, store: BlockStore) -> None:
        self.tree = LinkTree(root, store)

    def to_bytes(self) -> bytes:
        _start, leaves = self.tree.collect_leaves()
        if not all(isinstance(leaf, DataBlock) for leaf in leaves):
            raise ValueError("Expected DataBlock leaves")
        data_leaves = t.cast(list[DataBlock], leaves)
        return b"".join(leaf.data for leaf in data_leaves)


class TreeArray(ABC, t.Sequence[T], t.Generic[T, ElemType, EntryType]):
    """A link-tree backed array.

    Each kind of array has the following types:
    * `EntryType`: The type of **entries** stored in the leaves of the link tree:
       - for DATA and SLOTS blocks, the entries are raw bytes;
       - for TABLE blocks, the entries are TABLE block entries, most typically blocks or links.
    * `ElemType`: The logical type of the tree elements:
       - for data arrays, fixed-size `bytes` chunks
       - for arrays of bytestrings, variable-size `bytes`
       - for table arrays, usually a certain kind of `Block`
    * `T`: The Python type that this tree represents. Converted by the `decode_element` callback.
      (e.g., for arrays of primitives, `T` would be `int` or `float` decoded from the bytes chunk)
    """

    def __init__(
        self,
        block: Block,
        store: BlockStore,
        decode_element: t.Callable[[ElemType], T],
    ) -> None:
        self.tree = LinkTree(block, store)
        self.decode_element = decode_element

    def __len__(self) -> int:
        return len(self.tree)

    @abstractmethod
    def entry_to_element(self, entry: EntryType) -> ElemType:
        """Convert a leaf block to a Python element."""
        raise NotImplementedError

    @abstractmethod
    def leaf_to_list(self, leaf: Block) -> t.Sequence[EntryType]:
        raise NotImplementedError

    def decode_entry(self, entry: EntryType) -> T:
        elem = self.entry_to_element(entry)
        return self.decode_element(elem)

    @t.overload
    def __getitem__(self, index: int) -> T: ...
    @t.overload
    def __getitem__(self, index: slice) -> t.Sequence[T]: ...

    def __getitem__(self, index: int | slice) -> T | t.Sequence[T]:
        if isinstance(index, int):
            # easy case: just find the leaf and return the element
            relative_index, leaf = self.tree.find_leaf(index)
            entries = self.leaf_to_list(leaf)
            return self.decode_entry(entries[relative_index])

        global_start, leaves = self.tree.collect_leaves(index)
        range_start, range_stop, _step = index.indices(len(self))
        if _step != 1:
            raise NotImplementedError("Step not supported")

        slice_start = range_start - global_start
        slice_stop = range_stop - global_start
        entries = []
        for leaf in leaves:
            entries.extend(self.leaf_to_list(leaf))
        return [self.decode_entry(elem) for elem in entries[slice_start:slice_stop]]

    def __eq__(self, other: t.Any) -> bool:
        if not isinstance(other, t.Sequence):
            return NotImplemented
        return all(a == b for a, b in zip(self, other))


class DataArray(TreeArray[T, bytes, bytes]):
    def __init__(
        self,
        root: Block,
        store: BlockStore,
        elem_size: int,
        elem_align: int,
        decode_element: t.Callable[[bytes], T] = lambda x: x,
    ) -> None:
        super().__init__(root, store, decode_element)
        self.elem_size = elem_size
        self.elem_align = elem_align

    def _verify_leaf(self, leaf: Block) -> DataBlock:
        if not isinstance(leaf, DataBlock):
            raise ValueError(f"Expected DATA leaf, got {type(leaf).__name__}")
        if leaf.elem_size != self.elem_size:
            raise ValueError(
                f"DATA block elem_size {leaf.elem_size} does not match "
                f"expected {self.elem_size}"
            )
        if leaf.elem_align != self.elem_align:
            raise ValueError(
                f"DATA block elem_align {leaf.elem_align} does not match "
                f"expected {self.elem_align}"
            )
        return leaf

    def leaf_to_list(self, leaf: Block) -> t.Sequence[bytes]:
        return list(self._verify_leaf(leaf))

    def entry_to_element(self, entry: bytes) -> bytes:
        return entry


class BytestringArray(TreeArray[T, bytes, bytes | Link | Block]):
    def __init__(
        self,
        root: Block,
        store: BlockStore,
        decode_element: t.Callable[[bytes], T] = lambda x: x,
    ) -> None:
        super().__init__(root, store, decode_element)
        self.store = store

    def leaf_to_list(self, leaf: Block) -> t.Sequence[bytes | Link | Block]:
        if isinstance(leaf, SlotsBlock):
            return leaf.get_entries()
        if isinstance(leaf, TableBlock):
            leaves: list[Link | Block] = []
            for entry in leaf:
                match entry:
                    case BlockEntry(block=block):
                        leaves.append(block)
                    case LinkEntry(link=link):
                        leaves.append(link)
                    case _:
                        raise ValueError(
                            f"Expected BlockEntry or LinkEntry, got {type(entry).__name__}"
                        )
            return leaves
        raise ValueError(
            f"Expected SlotsBlock or TableBlock, got {type(leaf).__name__}"
        )

    def entry_to_element(self, entry: bytes | Link | Block) -> bytes:
        if isinstance(entry, bytes):
            return entry
        if isinstance(entry, Link):
            block = self.store.fetch(entry.digest)
        else:
            block = entry
        bytestring = BytestringTree(block, self.store)
        return bytestring.to_bytes()


class TableArray(TreeArray[T, Block, Block | Link]):
    def __init__(
        self,
        root: Block,
        store: BlockStore,
        decode_element: t.Callable[[Block], T] = lambda x: x,
    ) -> None:
        super().__init__(root, store, decode_element)
        self.store = store

    def leaf_to_list(self, leaf: Block) -> t.Sequence[Block | Link]:
        if not isinstance(leaf, TableBlock):
            raise ValueError(f"Expected TableBlock, got {type(leaf).__name__}")
        result: list[Block | Link] = []
        for entry in leaf:
            match entry:
                case BlockEntry(block=block):
                    result.append(block)
                case LinkEntry(link=link):
                    result.append(link)
                case _:
                    raise ValueError(
                        f"Expected BlockEntry or LinkEntry, got {type(entry).__name__}"
                    )
        return result

    def entry_to_element(self, entry: Block | Link) -> Block:
        if isinstance(entry, Link):
            return self.store.fetch(entry.digest)
        else:
            return entry


# ============================================================
# Encode side: building arrays into blocks
# ============================================================


def build_bytestring_tree(data: bytes, store: BlockStore) -> Block:
    """Build a bytestring tree from a list of bytes."""
    if not data:
        return DataBlock.build(b"", elem_size=1, elem_align=1)

    blocks: list[Block] = []
    max_data_size = SIZE_MAX - 4  # block header + elem_info
    for i in range(0, len(data), max_data_size):
        chunk = data[i : i + max_data_size]
        block = DataBlock.build(chunk, elem_size=1, elem_align=1)
        blocks.append(block)
    return linktree_reduce(blocks, store)


def build_data_array(
    elements: list[bytes],
    elem_size: int,
    elem_align: int,
    store: BlockStore,
) -> Block:
    """Build a DATA array, possibly spanning multiple blocks as a link tree.

    All elements must be the same size. `elem_size` is the unpadded element size,
    used for the DATA block's elem_info header (and verified against elements).
    """
    if not elements:
        return DataBlock.build(b"", elem_size=elem_size, elem_align=elem_align)

    padded = align_up(elem_size, elem_align)
    start_offset = max(elem_align, 4)
    max_elems_per_block = (SIZE_MAX - start_offset) // padded

    if max_elems_per_block == 0:
        raise ValueError(
            f"Element size {elem_size} (padded {padded}) too large for block"
        )

    # Chunk into multiple blocks
    blocks: list[Block] = []
    for chunk in itertools.batched(elements, max_elems_per_block):
        block = DataBlock.build_array(list(chunk), align=elem_align)
        blocks.append(block)

    return linktree_reduce(blocks, store)


def build_bytestring_array(elements: t.Sequence[bytes], store: BlockStore) -> Block:
    """Build a SLOTS array, possibly spanning multiple blocks as a link tree."""
    if not elements:
        return SlotsBlock.build_slots([])

    # Pack elements into SLOTS blocks sequentially
    blocks: list[Block] = []
    current_items: list[bytes] = []
    current_block_size = 2 + 2  # header + sentinel

    def seal_current() -> None:
        nonlocal current_block_size
        current_block_size = 2 + 2  # header + sentinel
        if not current_items:
            return
        block = SlotsBlock.build_slots(current_items)
        blocks.append(block)
        current_items.clear()

    for elem in elements:
        # Check if this element can fit alone in a block:
        # header + offset + sentinel + element
        if 2 + 4 + len(elem) > SIZE_MAX:
            seal_current()
            bytestring_tree = build_bytestring_tree(elem, store)
            table = Table([BlockEntry(bytestring_tree)])
            blocks.append(table.build(store))
            continue

        # Check if adding this element and its offset would exceed block size
        if current_block_size + 2 + len(elem) > SIZE_MAX:
            # Seal current block
            seal_current()

        current_items.append(elem)
        current_block_size += 2 + len(elem)

    # Seal final block
    seal_current()

    return linktree_reduce(blocks, store)


def build_table_array(elements: t.Sequence[TableEntry], store: BlockStore) -> Block:
    """Build a TABLE array of complex elements.

    Uses the "always inline" algorithm: embed each element as a BLOCK entry
    when possible, fall back to LINK for elements too large to ever embed.
    """
    if not elements:
        return TableBlock.build([], b"")

    result_blocks: list[Block] = []
    current_table = Table([])

    def seal_current() -> None:
        nonlocal current_table
        if not current_table.entries:
            return
        result_blocks.append(current_table.build(store))
        current_table = Table([])

    for elem in elements:
        try:
            table_alone = Table([elem])
            table_alone.fit(store)
        except ValueError:
            # Too large to embed; use LINK
            elem = outlink(elem, store)

        # Does it fit in the current block?
        trial = Table(current_table.entries + [elem])
        try:
            trial.fit(store)
            current_table = trial  # keep outlinking decisions
        except ValueError:
            seal_current()
            current_table = Table([elem])

    seal_current()

    return linktree_reduce(result_blocks, store)


def linktree_reduce(leaf_blocks: t.Sequence[Block], store: BlockStore) -> Block:
    """Reduces a non-empty list to a single root block.

    Returns a single Block. If the list has just one element, it is returned.
    Otherwise, builds a link tree from the list and returns its root.
    """
    if not leaf_blocks:
        raise ValueError("Cannot build links tree from empty list")

    if len(leaf_blocks) == 1:
        return leaf_blocks[0]

    # How many links fit in one LINKS block?
    # LINKS block: 4 bytes header + 36 * n links
    max_links_per_block = (SIZE_MAX - 4) // Link.SIZE

    tail_size = len(leaf_blocks) % max_links_per_block
    if len(leaf_blocks) > max_links_per_block and tail_size > 0:
        # if there is a tail, that is:
        # * we have more than one full link-block worth of elements (len(leaf_blocks) > max_links_per_block)
        # * and the last block is not full (len(leaf_blocks) % max_links_per_block > 0)
        # then cut off the tail and push it up a level
        leaf_blocks, tail = leaf_blocks[:-tail_size], leaf_blocks[-tail_size:]
    else:
        tail = []

    links = [Link(store.store(entry), entry.element_count()) for entry in leaf_blocks]
    inner_blocks: list[Block] = []

    for chunk in itertools.batched(links, max_links_per_block):
        block = LinksBlock.build(limits_to_cumulative(chunk))
        inner_blocks.append(block)

    # ...reattach the tail for recursive call
    inner_blocks.extend(tail)

    if len(inner_blocks) == 1:
        return inner_blocks[0]

    return linktree_reduce(inner_blocks, store)
