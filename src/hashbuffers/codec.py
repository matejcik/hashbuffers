import abc
import typing as t
from dataclasses import dataclass
from enum import IntEnum
from io import BytesIO
from os import SEEK_CUR

SIZE_MAX = 0x1FFF  # 8191

from .util import pack_flat_array, padded_element_size, unpack_flat_array


class Reader(BytesIO):
    def __init__(self, data: bytes | bytearray | memoryview):
        super().__init__(data)

    def read_exact(self, n: int) -> bytes:
        data = self.read(n)
        if len(data) != n:
            raise IOError(f"Expected {n} bytes, got {len(data)}")
        return data

    def read_until(self, offset: int) -> bytes:
        if offset < self.tell():
            raise IOError(f"Offset {offset} is before current position {self.tell()}")
        data = self.read(offset - self.tell())
        if self.tell() != offset:
            raise IOError(
                f"Expected to read to offset {offset}, stopped at {self.tell()}"
            )
        return data

    def read_uint(self, n: int) -> int:
        return int.from_bytes(self.read_exact(n), "little")

    def done(self) -> None:
        if self.tell() != len(self.getbuffer()):
            raise IOError("Unparsed trailing data")

    def child(self, length: int | None = None) -> t.Self:
        mv = self.getbuffer()[self.tell() :]
        # skip
        if length is None:
            length = len(mv)
        if length < 0:
            raise ValueError(f"Invalid length: {length}")
        if length > len(mv):
            raise ValueError(
                f"Not enough data left: want {length} bytes, remaining {len(mv)}"
            )

        mv = mv[:length]
        # skip over the child data
        self.seek(length, SEEK_CUR)
        return self.__class__(mv)


class Writer(BytesIO):
    def write_uint(self, *, size: int, value: int) -> t.Self:
        self.write(value.to_bytes(size, "little"))
        return self


def _check_bounds(value: int, min: int, max: int) -> None:
    if not min <= value <= max:
        raise ValueError(f"Value {value} is out of bounds ({min}-{max})")


@dataclass
class Tagged16:
    parameters: int
    number: int

    @classmethod
    def decode(cls, data: bytes) -> t.Self:
        num = Reader(data).read_uint(2)
        parameters = (num >> 13) & 0x7
        number = num & SIZE_MAX
        return cls(parameters, number)

    def encode(self) -> bytes:
        _check_bounds(self.parameters, 0, 0x7)
        _check_bounds(self.number, 0, SIZE_MAX)
        encoded = self.parameters << 13 | self.number
        return Writer().write_uint(size=2, value=encoded).getvalue()


class BlockType(IntEnum):
    TABLE = 0b00
    DATA = 0b01
    SLOTS = 0b10
    LINKS = 0b11

    def encode(self, size: int) -> bytes:
        params = self.value << 1
        return Tagged16(params, size).encode()

    @classmethod
    def decode(cls, data: bytes) -> tuple[t.Self, int]:
        tagged = Tagged16.decode(data)
        if tagged.parameters & 0b001 != 0:
            raise ValueError("Reserved bit is set")
        return cls(tagged.parameters >> 1), tagged.number


class VTableEntryType(IntEnum):
    NULL = 0b000
    DIRECT4 = 0b010
    DIRECT8 = 0b011
    INLINE = 0b100
    BLOCK = 0b101
    LINK = 0b110


@dataclass
class VTableEntry:
    type: VTableEntryType
    offset: int

    @classmethod
    def decode(cls, data: bytes) -> t.Self:
        tagged = Tagged16.decode(data)
        return cls(VTableEntryType(tagged.parameters), tagged.number)

    def encode(self) -> bytes:
        return Tagged16(self.type.value, self.offset).encode()

    @classmethod
    def null(cls) -> t.Self:
        return cls(VTableEntryType.NULL, 0)

    @classmethod
    def inline(cls, value: int) -> t.Self:
        return cls(VTableEntryType.INLINE, value)

    @classmethod
    def direct4(cls, offset: int) -> t.Self:
        return cls(VTableEntryType.DIRECT4, offset)

    @classmethod
    def direct8(cls, offset: int) -> t.Self:
        return cls(VTableEntryType.DIRECT8, offset)

    @classmethod
    def block(cls, offset: int) -> t.Self:
        return cls(VTableEntryType.BLOCK, offset)

    @classmethod
    def link(cls, offset: int) -> t.Self:
        return cls(VTableEntryType.LINK, offset)


@dataclass
class Link:
    digest: bytes
    limit: int

    SIZE: t.ClassVar[int] = 36

    @classmethod
    def decode(cls, data: bytes) -> t.Self:
        r = Reader(data)
        digest = r.read_exact(32)
        limit = r.read_uint(4)
        r.done()
        return cls(digest, limit)

    def encode(self) -> bytes:
        if len(self.digest) != 32:
            raise ValueError("Invalid digest length")
        _check_bounds(self.limit, 1, 0xFFFF_FFFF)
        w = Writer()
        w.write(self.digest)
        w.write_uint(size=4, value=self.limit)
        return w.getvalue()


@dataclass
class Block(abc.ABC):
    block_type: BlockType
    size: int

    BLOCK_TYPE: t.ClassVar[BlockType]

    def _start_encode(self) -> Writer:
        w = Writer()
        w.write(self.block_type.encode(self.size))
        return w

    @classmethod
    def _start_decode(cls, data: bytes) -> tuple[Reader, int]:
        r = Reader(data)
        block_type, size = BlockType.decode(r.read_exact(2))
        if block_type != cls.BLOCK_TYPE:
            raise ValueError(f"Expected {cls.BLOCK_TYPE} block, got {block_type}")
        return r, size

    @abc.abstractmethod
    def compute_size(self) -> int:
        raise NotImplementedError

    def validate(self) -> None:
        if self.size != self.compute_size():
            raise ValueError(
                f"Computed size {self.compute_size()} does not match declared size {self.size}"
            )
        _check_bounds(self.size, 2, SIZE_MAX)

    def encode(self) -> bytes:
        self.size = self.compute_size()
        self.validate()
        return self._encode_without_validation()

    @abc.abstractmethod
    def _encode_without_validation(self) -> bytes:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        raise NotImplementedError

    @classmethod
    def decode(cls, data: bytes) -> t.Self:
        block = cls._decode_without_validation(data)
        block.validate()
        return block


@dataclass
class TableBlock(Block):
    size: int
    vtable: list[VTableEntry]
    heap: bytes

    reserved_bits: int = 0

    BLOCK_TYPE = BlockType.TABLE

    # block header + vtable header + one entry = 6 bytes overhead
    HEAP_MAX_SIZE: t.ClassVar[int] = SIZE_MAX - 6

    def compute_size(self) -> int:
        return self.heap_start(len(self.vtable)) + len(self.heap)

    @classmethod
    def build(cls, vtable: list[VTableEntry], heap: bytes) -> t.Self:
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

    def _get_block(self, offset: int) -> Block:
        data = self.get_heap_data(offset)
        return decode_block(data, exact=False)

    def _get_link(self, offset: int) -> Link:
        data = self.get_heap_data(offset)
        return Link.decode(data[: Link.SIZE])

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        vtable_header = Tagged16(0, len(self.vtable))
        w.write(vtable_header.encode())
        for entry in self.vtable:
            w.write(entry.encode())
        w.write(self.heap)
        return w.getvalue()

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
            if entry.type in (VTableEntryType.NULL, VTableEntryType.INLINE):
                continue
            if entry.offset < heap_start or entry.offset >= self.size:
                raise ValueError(
                    f"Vtable entry offset {entry.offset} is out of bounds ({heap_start}-{self.size})"
                )
            if entry.type == VTableEntryType.DIRECT4:
                if entry.offset > self.size - 4:
                    raise ValueError(
                        f"DIRECT4 at offset {entry.offset} doesn't fit in block"
                    )
                if entry.offset % 4 != 0:
                    raise ValueError(
                        f"DIRECT4 at offset {entry.offset} is not 4-aligned"
                    )
            elif entry.type == VTableEntryType.DIRECT8:
                if entry.offset > self.size - 8:
                    raise ValueError(
                        f"DIRECT8 at offset {entry.offset} doesn't fit in block"
                    )
                if entry.offset % 8 != 0:
                    raise ValueError(
                        f"DIRECT8 at offset {entry.offset} is not 8-aligned"
                    )
            elif entry.type == VTableEntryType.LINK:
                if entry.offset > self.size - Link.SIZE:
                    raise ValueError(
                        f"LINK at offset {entry.offset} doesn't fit in block (need {Link.SIZE} bytes)"
                    )
                if entry.offset % 4 != 0:
                    raise ValueError(f"LINK at offset {entry.offset} is not 4-aligned")
                link = self._get_link(entry.offset)
                if link.limit == 0:
                    raise ValueError("Link limit must not be 0")
            elif entry.type == VTableEntryType.BLOCK:
                if entry.offset > self.size - 2:
                    raise ValueError(
                        f"BLOCK at offset {entry.offset} doesn't fit in block"
                    )
                if entry.offset % 2 != 0:
                    raise ValueError(f"BLOCK at offset {entry.offset} is not 2-aligned")
                sub_block = self._get_block(entry.offset)
                if entry.offset + sub_block.size > self.size:
                    raise ValueError(
                        f"Sub-block at offset {entry.offset} with size {sub_block.size} exceeds parent block"
                    )
                if isinstance(sub_block, TableBlock):
                    sub_align = sub_block.alignment()
                    if entry.offset % sub_align != 0:
                        raise ValueError(
                            f"BLOCK at offset {entry.offset} is not aligned "
                            f"to sub-block's alignment requirement {sub_align}"
                        )

    def alignment(self) -> int:
        """Compute alignment requirement from vtable entries (spec algorithm)."""
        max_align = 2
        for entry in self.vtable:
            if entry.type == VTableEntryType.DIRECT4:
                max_align = max(max_align, 4)
            elif entry.type == VTableEntryType.DIRECT8:
                max_align = max(max_align, 8)
            elif entry.type == VTableEntryType.LINK:
                max_align = max(max_align, 4)
            elif entry.type == VTableEntryType.BLOCK:
                sub_block = self._get_block(entry.offset)
                if isinstance(sub_block, TableBlock):
                    max_align = max(max_align, sub_block.alignment())
        return max_align

    @staticmethod
    def _sign_extend_13bit(value: int) -> int:
        BIT13 = 1 << 12
        return value - (BIT13 << 1) if value & BIT13 else value

    def get_int(self, index: int, size: int, signed: bool = False) -> int | None:
        if size not in (1, 2, 4, 8):
            raise ValueError(f"Invalid size: {size} (must be 1, 2, 4, or 8)")
        if size == 2:
            # no dedicated u16 storage, upgrade to u32 / DIRECT4
            size = 4
        try:
            entry = self.vtable[index]
        except IndexError:
            return None
        match entry.type:
            case VTableEntryType.NULL:
                return None
            case VTableEntryType.INLINE:
                return self._sign_extend_13bit(entry.offset) if signed else entry.offset
            case VTableEntryType.DIRECT4:
                if size < 4:
                    raise ValueError(f"DIRECT4 is oversized for {size}-byte integer")
                data = self.get_heap_data(entry.offset, 4)
                return int.from_bytes(data, "little", signed=signed)
            case VTableEntryType.DIRECT8:
                if size < 8:
                    raise ValueError(f"DIRECT8 is oversized for {size}-byte integer")
                data = self.get_heap_data(entry.offset, 8)
                return int.from_bytes(data, "little", signed=signed)
            case _:
                raise ValueError(f"Expected integer, got {entry.type}")

    def get_fixedsize(self, index: int, size: int) -> bytes | None:
        if size not in (4, 8):
            raise ValueError(f"Invalid size: {size}")
        try:
            entry = self.vtable[index]
        except IndexError:
            return None
        if entry.type == VTableEntryType.NULL:
            return None
        if size == 4 and entry.type == VTableEntryType.DIRECT4:
            return self.get_heap_data(entry.offset, 4)
        if size == 8 and entry.type == VTableEntryType.DIRECT8:
            return self.get_heap_data(entry.offset, 8)
        raise ValueError(f"Invalid entry type for size {size}: {entry.type}")

    def get_block(self, index: int) -> Block | Link | None:
        try:
            entry = self.vtable[index]
        except IndexError:
            return None
        match entry.type:
            case VTableEntryType.NULL:
                return None
            case VTableEntryType.LINK:
                return self._get_link(entry.offset)
            case VTableEntryType.BLOCK:
                return self._get_block(entry.offset)
            case _:
                raise ValueError(f"Expected block, got {entry.type}")

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        vtable_header = Tagged16.decode(r.read_exact(2))
        reserved_bits = vtable_header.parameters
        vtable_r = r.child(vtable_header.number * 2)
        vtable = [
            VTableEntry.decode(vtable_r.read_exact(2))
            for _ in range(vtable_header.number)
        ]
        vtable_r.done()
        heap = r.read_until(size)
        r.done()
        return cls(cls.BLOCK_TYPE, size, vtable, heap, reserved_bits=reserved_bits)


@dataclass
class DataBlock(Block):
    data: bytes

    BLOCK_TYPE = BlockType.DATA

    @classmethod
    def build(cls, data: bytes, *, align: int = 1) -> t.Self:
        start_offset = max(align, 2)
        pad_size = start_offset - 2
        size = start_offset + len(data)
        padding = b"\x00" * pad_size
        return cls(cls.BLOCK_TYPE, size, padding + data)

    def compute_size(self) -> int:
        return 2 + len(self.data)

    @classmethod
    def build_array(cls, data: t.Sequence[bytes], *, align: int = 1) -> t.Self:
        array_data = pack_flat_array(data, align)
        return cls.build(array_data, align=align)

    def get_data(self, *, align: int = 1) -> memoryview:
        pad_size = max(align, 2) - 2
        return memoryview(self.data)[pad_size:]

    def get_array(self, elem_size: int, *, align: int = 1) -> list[memoryview]:
        data = self.get_data(align=align)
        return unpack_flat_array(data, elem_size, align)

    def array_length(self, elem_size: int, *, align: int = 1) -> int:
        return len(self.get_data(align=align)) // padded_element_size(elem_size, align)

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        w.write(self.data)
        return w.getvalue()

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        data = r.read_until(size)
        r.done()
        return cls(cls.BLOCK_TYPE, size, data)


@dataclass
class SlotsBlock(Block):
    offsets: list[int]
    heap: bytes

    BLOCK_TYPE = BlockType.SLOTS

    # block header + first offset + sentinel = 6 bytes overhead
    MAX_ELEMENT_SIZE: t.ClassVar[int] = SIZE_MAX - 6

    def compute_size(self) -> int:
        return 2 + 2 * len(self.offsets) + len(self.heap)

    @staticmethod
    def heap_start(offsets_len: int) -> int:
        return 2 + 2 * offsets_len

    @classmethod
    def build(cls, offsets: list[int], heap: bytes) -> t.Self:
        new = cls(cls.BLOCK_TYPE, 0, offsets, heap)
        new.size = new.compute_size()
        return new

    @classmethod
    def build_slots(cls, items: list[bytes]) -> t.Self:
        offsets = []
        heap = bytearray()
        for item in items:
            offsets.append(len(heap))
            heap.extend(item)
        offsets.append(len(heap))
        heap_start = cls.heap_start(len(offsets))
        return cls.build([off + heap_start for off in offsets], bytes(heap))

    def element_count(self) -> int:
        return len(self.offsets) - 1

    def get_entry(self, index: int) -> bytes:
        _check_bounds(index, 0, self.element_count() - 1)
        heap_start = self.heap_start(len(self.offsets))
        start = self.offsets[index]
        end = self.offsets[index + 1]
        return self.heap[start - heap_start : end - heap_start]

    def get_entries(self) -> list[bytes]:
        return [self.get_entry(i) for i in range(self.element_count())]

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        for off in self.offsets:
            w.write_uint(size=2, value=off)
        w.write(self.heap)
        return w.getvalue()

    def validate(self) -> None:
        super().validate()
        if self.size < 4:
            raise ValueError("SLOTS block must be at least 4 bytes")
        if not self.offsets:
            raise ValueError("SLOTS block must have at least one offset (sentinel)")
        first = self.offsets[0]
        if first < 4:
            raise ValueError(f"First offset {first} must be at least 4")
        if first % 2 != 0:
            raise ValueError(f"First offset {first} must be divisible by 2")
        if first > self.size:
            raise ValueError(f"First offset {first} exceeds block size {self.size}")
        expected_count = (first - 2) // 2
        if len(self.offsets) != expected_count:
            raise ValueError(
                f"Offset count {len(self.offsets)} does not match expected {expected_count}"
            )
        if any(
            self.offsets[i] > self.offsets[i + 1] for i in range(len(self.offsets) - 1)
        ):
            raise ValueError("Offsets are not non-decreasing")
        if self.offsets[-1] != self.size:
            raise ValueError(
                f"Sentinel offset {self.offsets[-1]} is not equal to block size {self.size}"
            )

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        if size < 4:
            raise ValueError("SLOTS block too small")
        first_offset = r.read_uint(2)
        if first_offset < 4 or first_offset % 2 != 0:
            raise ValueError(f"Invalid first offset {first_offset}")
        offset_count = (first_offset - 2) // 2
        offsets = [first_offset]
        for _ in range(offset_count - 1):
            offsets.append(r.read_uint(2))
        heap = r.read_until(size)
        r.done()
        return cls(cls.BLOCK_TYPE, size, offsets, heap)


@dataclass
class LinksBlock(Block):
    links: list[Link]

    reserved_bits: int = 0

    BLOCK_TYPE = BlockType.LINKS

    def compute_size(self) -> int:
        return 4 + 36 * len(self.links)

    @classmethod
    def build(cls, links: list[Link]) -> t.Self:
        size = 4 + 36 * len(links)
        if size > SIZE_MAX:
            raise ValueError(f"Links block exceeds {SIZE_MAX} bytes (size: {size})")
        return cls(cls.BLOCK_TYPE, size, links)

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        w.write_uint(size=2, value=self.reserved_bits)
        for link in self.links:
            w.write(link.encode())
        return w.getvalue()

    def validate(self) -> None:
        super().validate()
        if self.reserved_bits != 0:
            raise ValueError(f"Reserved bits {self.reserved_bits} are not zero")
        if not self.links:
            raise ValueError("LINKS block must have at least one link")
        if any(
            self.links[i].limit >= self.links[i + 1].limit
            for i in range(len(self.links) - 1)
        ):
            raise ValueError("Links must be strictly increasing")
        if any(link.limit == 0 for link in self.links):
            raise ValueError("Links must not have limit 0")

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        reserved_bits = r.read_uint(2)
        data_size = size - 4
        if data_size % Link.SIZE != 0:
            raise ValueError(
                f"LINKS block data length is not a multiple of link size ({Link.SIZE})"
            )

        links = []
        for _ in range(data_size // Link.SIZE):
            links.append(Link.decode(r.read_exact(Link.SIZE)))
        r.done()

        return cls(cls.BLOCK_TYPE, size, links, reserved_bits=reserved_bits)


def decode_block(data: bytes, exact: bool = True) -> Block:
    header = Reader(data).read_exact(2)
    block_type, size = BlockType.decode(header)
    if not exact:
        data = data[:size]
    if block_type == BlockType.TABLE:
        return TableBlock.decode(data)
    if block_type == BlockType.DATA:
        return DataBlock.decode(data)
    if block_type == BlockType.SLOTS:
        return SlotsBlock.decode(data)
    if block_type == BlockType.LINKS:
        return LinksBlock.decode(data)
    raise ValueError(f"Unknown block type: {block_type}")
