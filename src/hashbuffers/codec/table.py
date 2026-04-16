from __future__ import annotations

import abc
import typing as t
from dataclasses import dataclass
from enum import IntEnum

from ..util import bit_length
from .base import SIZE_MAX, Block, BlockType, Tagged16
from .links import Link


class TableEntryType(IntEnum):
    NULL = 0b000
    DIRECTDATA = 0b001
    DIRECT4 = 0b010
    DIRECT8 = 0b011
    INLINE = 0b100
    BLOCK = 0b101
    LINK = 0b110


@dataclass
class TableEntryRaw:
    type: TableEntryType
    offset: int

    @classmethod
    def decode(cls, data: bytes) -> t.Self:
        tagged = Tagged16.decode(data)
        return cls(TableEntryType(tagged.parameters), tagged.number)

    def encode(self) -> bytes:
        return Tagged16(self.type.value, self.offset).encode()


class TableEntry(abc.ABC):
    TYPES: t.ClassVar[tuple[TableEntryType, ...]]
    TYPE_MAP: t.ClassVar[dict[TableEntryType, type[TableEntry]]] = {}

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "TYPES"):
            for type in cls.TYPES:
                cls.TYPE_MAP[type] = cls

    @abc.abstractmethod
    def alignment(self) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def size(self) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def to_entry_raw(self, offset: int) -> TableEntryRaw:
        raise NotImplementedError

    @abc.abstractmethod
    def encode(self) -> bytes:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def from_table(cls, table: TableBlock, entry_raw: TableEntryRaw) -> t.Self:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def validate(cls, table: TableBlock, entry_raw: TableEntryRaw) -> None:
        raise NotImplementedError


class IntValue(abc.ABC):
    @abc.abstractmethod
    def to_int(self, size: int, signed: bool) -> int:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def from_int(cls, value: int, signed: bool) -> t.Self:
        raise NotImplementedError


class InlineEntryBase(TableEntry):
    def alignment(self) -> int:
        return 0

    def size(self) -> int:
        return 0

    @classmethod
    def validate(cls, table: TableBlock, entry_raw: TableEntryRaw) -> None:
        # nothing to validate
        return

    def encode(self) -> bytes:
        return b""


@dataclass
class InlineIntEntry(InlineEntryBase, IntValue):
    value: int

    TYPES = (TableEntryType.INLINE,)

    @staticmethod
    def fits(value: int, signed: bool) -> bool:
        if not signed and value < 0:
            return False
        return bit_length(value, signed) <= 13

    @classmethod
    def from_int(cls, value: int, signed: bool) -> t.Self:
        if not cls.fits(value, signed):
            raise ValueError(f"Value {value} is too large for inline int")
        return cls(value & SIZE_MAX)

    def to_int(self, size: int, signed: bool) -> int:
        if signed:
            BIT13 = 1 << 12
            return self.value - (BIT13 << 1) if self.value & BIT13 else self.value
        return self.value

    def to_entry_raw(self, offset: int) -> TableEntryRaw:
        return TableEntryRaw(TableEntryType.INLINE, self.value & SIZE_MAX)

    @classmethod
    def from_table(cls, table: TableBlock, entry_raw: TableEntryRaw) -> t.Self:
        assert entry_raw.type == TableEntryType.INLINE
        return cls(entry_raw.offset)


class NullEntry(InlineEntryBase):
    TYPES = (TableEntryType.NULL,)

    def to_entry_raw(self, offset: int) -> TableEntryRaw:
        return TableEntryRaw(TableEntryType.NULL, 0)

    @classmethod
    def from_table(cls, table: TableBlock, entry_raw: TableEntryRaw) -> NullEntry:
        assert entry_raw.type == TableEntryType.NULL
        return NULL_ENTRY


NULL_ENTRY = NullEntry()


@dataclass
class DirectFixedEntry(TableEntry, IntValue):
    data: bytes

    TYPES = TableEntryType.DIRECT4, TableEntryType.DIRECT8

    DIRECT_SIZES: t.ClassVar[dict[TableEntryType, int]] = {
        TableEntryType.DIRECT4: 4,
        TableEntryType.DIRECT8: 8,
    }

    def __post_init__(self) -> None:
        if len(self.data) not in self.DIRECT_SIZES.values():
            raise ValueError(f"Invalid data size: {len(self.data)} (must be 4 or 8)")

    def alignment(self) -> int:
        return len(self.data)

    def size(self) -> int:
        return len(self.data)

    def to_entry_raw(self, offset: int) -> TableEntryRaw:
        for typ, length in self.DIRECT_SIZES.items():
            if len(self.data) == length:
                return TableEntryRaw(typ, offset)
        # should not have been possible to construct this entry
        raise RuntimeError("Unsupported direct size")

    @classmethod
    def size_of(cls, entry_raw: TableEntryRaw) -> int:
        try:
            return cls.DIRECT_SIZES[entry_raw.type]
        except KeyError:
            raise ValueError(f"Unsupported direct type: {entry_raw.type}") from None

    @classmethod
    def validate(cls, table: TableBlock, entry_raw: TableEntryRaw) -> None:
        size = cls.size_of(entry_raw)
        table._check_bounds_and_align(entry_raw.offset, size, size)

    @classmethod
    def from_table(cls, table: TableBlock, entry_raw: TableEntryRaw) -> t.Self:
        size = cls.size_of(entry_raw)
        data = table.get_heap_data(entry_raw.offset, size)
        return cls(bytes(data))

    @classmethod
    def from_int(cls, value: int, signed: bool) -> t.Self:
        if not signed and value < 0:
            raise OverflowError(f"Value {value} is negative for signed type")
        if bit_length(value, signed) > 32:
            return cls(value.to_bytes(8, "little", signed=signed))
        return cls(value.to_bytes(4, "little", signed=signed))

    def to_int(self, size: int, signed: bool) -> int:
        if size == 1 or (size < 8 and len(self.data) == 8):
            raise ValueError(
                f"Encoding too big (using {len(self.data)} bytes for {size} bytes type)"
            )
        return int.from_bytes(self.data, "little", signed=signed)

    def encode(self) -> bytes:
        return self.data


@dataclass
class DirectDataEntry(TableEntry):
    data: bytes

    TYPES = (TableEntryType.DIRECTDATA,)

    def alignment(self) -> int:
        return 2

    def size(self) -> int:
        return 2 + len(self.data)

    def to_entry_raw(self, offset: int) -> TableEntryRaw:
        return TableEntryRaw(TableEntryType.DIRECTDATA, offset)

    @classmethod
    def from_table(cls, table: TableBlock, entry_raw: TableEntryRaw) -> t.Self:
        assert entry_raw.type == TableEntryType.DIRECTDATA
        header_data = table.get_heap_data(entry_raw.offset, 2)
        header = Tagged16.decode(bytes(header_data))
        if header.parameters != 0:
            raise ValueError(
                f"DIRECTDATA header params {header.parameters} are not zero"
            )
        data = table.get_heap_data(entry_raw.offset + 2, header.number)
        return cls(bytes(data))

    @classmethod
    def validate(cls, table: TableBlock, entry_raw: TableEntryRaw) -> None:
        assert entry_raw.type == TableEntryType.DIRECTDATA
        table._check_bounds_and_align(entry_raw.offset, 2, 2)
        header_data = table.get_heap_data(entry_raw.offset, 2)
        header = Tagged16.decode(bytes(header_data))
        if header.parameters != 0:
            raise ValueError(
                f"DIRECTDATA header params {header.parameters} are not zero"
            )
        table._check_bounds_and_align(entry_raw.offset + 2, header.number, 1)

    def encode(self) -> bytes:
        header = Tagged16(0, len(self.data)).encode()
        return header + self.data


@dataclass
class LinkEntry(TableEntry):
    link: Link

    TYPES = (TableEntryType.LINK,)

    def alignment(self) -> int:
        return Link.ALIGNMENT

    def size(self) -> int:
        return Link.SIZE

    def to_entry_raw(self, offset: int) -> TableEntryRaw:
        return TableEntryRaw(TableEntryType.LINK, offset)

    @classmethod
    def from_table(cls, table: TableBlock, entry_raw: TableEntryRaw) -> t.Self:
        assert entry_raw.type == TableEntryType.LINK
        data = table.get_heap_data(entry_raw.offset, Link.SIZE)
        return cls(Link.decode(bytes(data)))

    @classmethod
    def validate(cls, table: TableBlock, entry_raw: TableEntryRaw) -> None:
        assert entry_raw.type == TableEntryType.LINK
        table._check_bounds_and_align(entry_raw.offset, Link.SIZE, Link.ALIGNMENT)
        link = cls.from_table(table, entry_raw)
        if link.link.limit == 0:
            raise ValueError("Link limit must not be 0")

    def encode(self) -> bytes:
        return self.link.encode()


@dataclass
class BlockEntry(TableEntry):
    block: Block

    TYPES = (TableEntryType.BLOCK,)

    def alignment(self) -> int:
        return self.block.alignment()

    def size(self) -> int:
        return self.block.size

    def to_entry_raw(self, offset: int) -> TableEntryRaw:
        return TableEntryRaw(TableEntryType.BLOCK, offset)

    @classmethod
    def from_table(cls, table: TableBlock, entry_raw: TableEntryRaw) -> t.Self:
        from . import decode_block

        assert entry_raw.type == TableEntryType.BLOCK
        data = table.get_heap_data(entry_raw.offset)
        return cls(decode_block(bytes(data), exact=False))

    @classmethod
    def validate(cls, table: TableBlock, entry_raw: TableEntryRaw) -> None:
        assert entry_raw.type == TableEntryType.BLOCK
        table._check_bounds_and_align(entry_raw.offset, 2, 2)
        sub_block = cls.from_table(table, entry_raw)
        table._check_bounds_and_align(
            entry_raw.offset, sub_block.size(), sub_block.alignment()
        )

    def encode(self) -> bytes:
        return self.block.encode()


@dataclass
class TableBlock(Block):
    size: int
    vtable: list[TableEntryRaw]
    heap: bytes

    reserved_bits: int = 0

    BLOCK_TYPE = BlockType.TABLE

    # block header + vtable header + one entry = 6 bytes overhead
    HEAP_MAX_SIZE: t.ClassVar[int] = SIZE_MAX - 6

    def compute_size(self) -> int:
        return self.heap_start(len(self.vtable)) + len(self.heap)

    def element_count(self) -> int:
        return len(self.vtable)

    @classmethod
    def build(cls, vtable: list[TableEntryRaw], heap: bytes) -> t.Self:
        new = cls(BlockType.TABLE, 0, vtable, heap)
        new.size = new.compute_size()
        return new

    @staticmethod
    def heap_start(vtable_count: int) -> int:
        return 4 + 2 * vtable_count

    def get_heap_data(self, offset: int, length: int | None = None) -> bytes:
        heap_start = self.heap_start(len(self.vtable))
        if length is None:
            length = self.size - offset
        if offset < heap_start or length < 0 or offset + length > self.size:
            raise ValueError("Heap read out of bounds")
        offset -= heap_start
        return self.heap[offset : offset + length]

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        vtable_header = Tagged16(0, len(self.vtable))
        w.write(vtable_header.encode())
        for entry in self.vtable:
            w.write(entry.encode())
        w.write(self.heap)
        return w.getvalue()

    def _check_bounds_and_align(self, offset: int, length: int, align: int) -> None:
        heap_start = self.heap_start(len(self.vtable))
        start_offset = offset
        end_offset = start_offset + length
        if not heap_start <= start_offset <= end_offset <= self.size:
            raise ValueError("Heap read out of bounds")
        if start_offset % align != 0:
            raise ValueError(f"Offset {start_offset} is not {align}-aligned")

    def get_entry(self, index: int) -> TableEntry:
        if index < 0:
            raise IndexError("Negative index")
        if index >= len(self.vtable):
            return NULL_ENTRY
        entry_raw = self.vtable[index]
        cls = TableEntry.TYPE_MAP[entry_raw.type]
        return cls.from_table(self, entry_raw)

    def validate(self) -> None:
        super().validate()
        heap_start = self.heap_start(len(self.vtable))
        if self.size < 4:
            raise ValueError("TABLE block must be at least 4 bytes")
        if self.reserved_bits != 0:
            raise ValueError(f"Reserved bits {self.reserved_bits} are not zero")
        if heap_start > self.size:
            raise ValueError(
                f"Entry count overflows block: heap_start {heap_start} > size {self.size}"
            )
        for entry in self.vtable:
            TableEntry.TYPE_MAP[entry.type].validate(self, entry)

    def alignment(self) -> int:
        """Compute alignment requirement from vtable entries (spec algorithm)."""
        max_align = 2
        for entry in self.vtable:
            cls = TableEntry.TYPE_MAP[entry.type]
            val = cls.from_table(self, entry)
            max_align = max(max_align, val.alignment())
        return max_align

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        vtable_header = Tagged16.decode(r.read_exact(2))
        reserved_bits = vtable_header.parameters
        vtable_r = r.child(vtable_header.number * 2)
        vtable = [
            TableEntryRaw.decode(vtable_r.read_exact(2))
            for _ in range(vtable_header.number)
        ]
        vtable_r.done()
        heap = r.read_until(size)
        r.done()
        return cls(cls.BLOCK_TYPE, size, vtable, heap, reserved_bits=reserved_bits)

    def __len__(self) -> int:
        return self.element_count()

    @t.overload
    def __getitem__(self, index: int) -> TableEntry: ...
    @t.overload
    def __getitem__(self, index: slice) -> t.Sequence[TableEntry]: ...

    def __getitem__(self, index: int | slice) -> TableEntry | t.Sequence[TableEntry]:
        if isinstance(index, int):
            return self.get_entry(index)
        else:
            start, stop, step = index.indices(self.element_count())
            return [self.get_entry(i) for i in range(start, stop, step)]

    def __iter__(self) -> t.Iterator[TableEntry]:
        for entry_raw in self.vtable:
            cls = TableEntry.TYPE_MAP[entry_raw.type]
            yield cls.from_table(self, entry_raw)
