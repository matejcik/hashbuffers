from dataclasses import dataclass
from enum import IntEnum
from io import BytesIO

from os import SEEK_CUR
import typing as t

SIZE_MAX = 0x1FFF  # 8191


class Encodable(t.Protocol):
    def encode(self) -> bytes: ...


class Decodable(t.Protocol):
    @classmethod
    def decode(cls, data: bytes) -> t.Self: ...


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
    STRUCT = 0b00
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
    INLINE = 0b100
    DIRECT = 0b101
    BLOCK = 0b110
    LINK = 0b111


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


@dataclass
class Link:
    digest: bytes
    limit: int

    SIZE: t.ClassVar[int] = 36

    @classmethod
    def decode(cls, data: bytes, exact: bool = True) -> t.Self:
        r = Reader(data)
        digest = r.read_exact(32)
        limit = r.read_uint(4)
        if exact:
            r.done()
        return cls(digest, limit)

    def encode(self) -> bytes:
        if len(self.digest) != 32:
            raise ValueError("Invalid digest length")
        _check_bounds(self.limit, 0, 0xFFFF_FFFF)
        w = Writer()
        w.write(self.digest)
        w.write_uint(size=4, value=self.limit)
        return w.getvalue()


@dataclass
class Block:
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

    def _encode_without_validation(self) -> bytes:
        raise NotImplementedError

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        raise NotImplementedError

    @classmethod
    def decode(cls, data: bytes) -> t.Self:
        block = cls._decode_without_validation(data)
        block.validate()
        return block


@dataclass
class StructBlock(Block):
    size: int
    vtable: list[VTableEntry]
    heap: bytes

    reserved_bits: int = 0

    BLOCK_TYPE = BlockType.STRUCT

    def compute_size(self) -> int:
        return self.heap_start + len(self.heap)

    @classmethod
    def build(cls, vtable: list[VTableEntry], heap: bytes) -> t.Self:
        new = cls(BlockType.STRUCT, 0, vtable, heap)
        new.size = new.compute_size()
        return new

    @property
    def heap_start(self) -> int:
        return 4 + 2 * len(self.vtable)

    def get_heap_data(self, offset: int, length: int | None = None) -> bytes:
        if length is None:
            length = self.size - offset
        if offset < self.heap_start or length < 0 or offset + length > self.size:
            raise ValueError("Heap read out of bounds")
        offset -= self.heap_start
        return self.heap[offset : offset + length]

    def _get_block(self, offset: int) -> Block:
        data = self.get_heap_data(offset)
        return decode_block(data, exact=False)

    def _get_link(self, offset: int) -> Link:
        data = self.get_heap_data(offset)
        return Link.decode(data, exact=False)

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
        if self.reserved_bits != 0:
            raise ValueError(f"Reserved bits {self.reserved_bits} are not zero")
        for entry in self.vtable:
            if entry.type in (VTableEntryType.NULL, VTableEntryType.INLINE):
                continue
            if entry.offset < self.heap_start or entry.offset >= self.size:
                raise ValueError(
                    f"Vtable entry offset {entry.offset} is out of bounds ({self.heap_start}-{self.size})"
                )
            if entry.type == VTableEntryType.LINK:
                self._get_link(entry.offset)
            if entry.type == VTableEntryType.BLOCK:
                self._get_block(entry.offset)

    @staticmethod
    def _sign_extend_13bit(value: int) -> int:
        BIT13 = 1 << 12
        return value - (BIT13 << 1) if value & BIT13 else value

    def get_int(self, index: int, size: int, signed: bool = False) -> int | None:
        try:
            entry = self.vtable[index]
        except IndexError:
            return None
        match entry.type:
            case VTableEntryType.NULL:
                return None
            case VTableEntryType.INLINE:
                return self._sign_extend_13bit(entry.offset) if signed else entry.offset
            case VTableEntryType.DIRECT:
                data = self.get_heap_data(entry.offset, size)
                return int.from_bytes(data, "little", signed=signed)
            case _:
                raise ValueError(f"Expected integer, got {entry.type}")

    def get_fixedsize(self, index: int, size: int) -> bytes | None:
        try:
            entry = self.vtable[index]
        except IndexError:
            return None
        match entry.type:
            case VTableEntryType.NULL:
                return None
            case VTableEntryType.DIRECT:
                return self.get_heap_data(entry.offset, size)
            case _:
                raise ValueError(f"Expected fixed-size, got {entry.type}")

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

    @staticmethod
    def padded_elem_size(elem_size: int, align: int = 1) -> int:
        # round up to the nearest multiple of align
        return (elem_size + align - 1) & ~(align - 1)

    @classmethod
    def build_array(cls, data: list[bytes], *, align: int = 1) -> t.Self:
        if not data:
            return cls.build(b"", align=align)
        if not all(len(elem) == len(data[0]) for elem in data):
            raise ValueError("All elements must have the same length")
        padded_elem_size = cls.padded_elem_size(len(data[0]), align)
        padding_size = padded_elem_size - len(data[0])
        elem_padding = b"\x00" * padding_size
        return cls.build(b"".join(elem + elem_padding for elem in data), align=align)

    def get_data(self, *, align: int = 1) -> memoryview:
        pad_size = max(align, 2) - 2
        return memoryview(self.data)[pad_size:]

    def get_array(self, elem_size: int, *, align: int = 1) -> list[bytes]:
        data = self.get_data(align=align)
        padded_elem_size = self.padded_elem_size(elem_size, align)
        if len(data) % padded_elem_size != 0:
            raise ValueError(
                f"Data length {len(data)} is not divisible by padded element size {padded_elem_size}"
            )
        elems_untrimmed = [
            data[i : i + padded_elem_size]
            for i in range(0, len(data), padded_elem_size)
        ]
        return [bytes(elem[:elem_size]) for elem in elems_untrimmed]

    def array_length(self, elem_size: int, *, align: int = 1) -> int:
        return len(self.get_data(align=align)) // self.padded_elem_size(
            elem_size, align
        )

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
    raw_entries: bool
    offsets: list[int]
    heap: bytes

    reserved_bits: int = 0

    RAW_ENTRIES_BIT: t.ClassVar[int] = 0b100
    BLOCK_TYPE = BlockType.SLOTS

    def compute_size(self) -> int:
        return 4 + 2 * len(self.offsets) + len(self.heap)

    @staticmethod
    def heap_start(offsets: list[int]) -> int:
        return 4 + 2 * len(offsets)

    @classmethod
    def build(cls, raw_entries: bool, offsets: list[int], heap: bytes) -> t.Self:
        new = cls(cls.BLOCK_TYPE, 0, raw_entries, offsets, heap)
        new.size = new.compute_size()
        return new

    @classmethod
    def build_raw(cls, items: list[bytes]) -> t.Self:
        offsets = []
        heap = bytearray()
        for item in items:
            offsets.append(len(heap))
            heap.extend(item)
        offsets.append(len(heap))
        heap_start = cls.heap_start(offsets)
        return cls.build(True, [off + heap_start for off in offsets], bytes(heap))

    @classmethod
    def build_blocks(cls, items: list[Block]) -> t.Self:
        offsets = []
        heap = bytearray()
        for item in items:
            offsets.append(len(heap))
            heap.extend(item.encode())
        heap_start = cls.heap_start(offsets)
        return cls.build(False, [off + heap_start for off in offsets], bytes(heap))

    def check_index(self, index: int) -> None:
        index_max = len(self.offsets) - 1
        if self.raw_entries:
            index_max -= 1
        _check_bounds(index, 0, index_max)

    def get_block(self, index: int) -> Block:
        if self.raw_entries:
            raise ValueError("Raw entries are not blocks")
        heap_start = self.heap_start(self.offsets)
        self.check_index(index)
        offset = self.offsets[index]
        _check_bounds(offset, heap_start, self.size - 2)
        heap_offset = offset - heap_start
        return decode_block(self.heap[heap_offset:], exact=False)

    def get_raw_entry(self, index: int) -> bytes:
        if not self.raw_entries:
            raise ValueError("Raw entries are not raw entries")
        self.check_index(index)
        heap_start = self.heap_start(self.offsets)
        start = self.offsets[index]
        _check_bounds(start, heap_start, self.size)
        end = self.offsets[index + 1]
        _check_bounds(end, start, self.size)
        return self.heap[start - heap_start : end - heap_start]

    def _encode_without_validation(self) -> bytes:
        if self.raw_entries:
            params = self.RAW_ENTRIES_BIT
            count = len(self.offsets) - 1
        else:
            params = 0
            count = len(self.offsets)

        w = self._start_encode()
        slot_header = Tagged16(params | self.reserved_bits, count)
        w.write(slot_header.encode())
        for off in self.offsets:
            w.write_uint(size=2, value=off)
        w.write(self.heap)
        return w.getvalue()

    def validate(self) -> None:
        super().validate()
        heap_start = self.heap_start(self.offsets)
        if self.reserved_bits != 0:
            raise ValueError(f"Reserved bits {self.reserved_bits} are not zero")
        if self.raw_entries:
            if self.offsets[0] != heap_start:
                raise ValueError(
                    f"First offset {self.offsets[0]} is not equal to heap start {heap_start}"
                )
            if self.offsets[-1] != self.size:
                raise ValueError(
                    f"Sentinel offset {self.offsets[-1]} is not equal to block size {self.size}"
                )
            if any(
                self.offsets[i] > self.offsets[i + 1]
                for i in range(len(self.offsets) - 1)
            ):
                raise ValueError("Offsets are not non-decreasing")
        else:
            for i in range(len(self.offsets)):
                self.get_block(i)

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        slot_header = Tagged16.decode(r.read_exact(2))
        raw_entries = bool(slot_header.parameters & 0b100)
        reserved_bits = slot_header.parameters & 0b11
        count = slot_header.number
        if raw_entries:
            count += 1
        offsets_r = r.child(count * 2)
        offsets = [offsets_r.read_uint(2) for _ in range(count)]
        offsets_r.done()
        heap = r.read_until(size)
        r.done()
        return cls(
            cls.BLOCK_TYPE,
            size,
            raw_entries,
            offsets,
            heap,
            reserved_bits=reserved_bits,
        )


@dataclass
class LinksBlock(Block):
    leaf_parent: bool
    links: list[Link]

    reserved_bits: int = 0

    BLOCK_TYPE = BlockType.LINKS

    LEAF_PARENT_BIT: t.ClassVar[int] = 1 << 15

    def compute_size(self) -> int:
        return 4 + 36 * len(self.links)

    @classmethod
    def build(cls, leaf_parent: bool, links: list[Link]) -> "LinksBlock":
        size = 4 + 36 * len(links)
        if size > SIZE_MAX:
            raise ValueError(f"Links block exceeds {SIZE_MAX} bytes (size: {size})")
        return cls(cls.BLOCK_TYPE, size, leaf_parent, links)

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        params = self.LEAF_PARENT_BIT * self.leaf_parent
        w.write_uint(size=2, value=params | self.reserved_bits)
        for link in self.links:
            w.write(link.encode())
        return w.getvalue()

    def validate(self) -> None:
        super().validate()
        if self.reserved_bits != 0:
            raise ValueError(f"Reserved bits {self.reserved_bits} are not zero")
        if self.leaf_parent:
            if any(link.limit != 0 for link in self.links):
                raise ValueError("Leaf parent links must have limit 0")
        else:
            if not self.links:
                raise ValueError("Inner node must have at least one link")
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
        params = r.read_uint(2)
        leaf_parent = bool(params & cls.LEAF_PARENT_BIT)
        reserved_bits = params & ~cls.LEAF_PARENT_BIT
        data_size = size - 4
        if data_size % Link.SIZE != 0:
            raise ValueError(
                f"LINKS block data length is not a multiple of link size ({Link.SIZE})"
            )

        links = []
        for _ in range(data_size // Link.SIZE):
            links.append(Link.decode(r.read_exact(Link.SIZE)))
        r.done()

        return cls(
            cls.BLOCK_TYPE, size, leaf_parent, links, reserved_bits=reserved_bits
        )


def decode_block(data: bytes, exact: bool = True) -> Block:
    header = Reader(data).read_exact(2)
    block_type, size = BlockType.decode(header)
    if not exact:
        data = data[:size]
    if block_type == BlockType.STRUCT:
        return StructBlock.decode(data)
    if block_type == BlockType.DATA:
        return DataBlock.decode(data)
    if block_type == BlockType.SLOTS:
        return SlotsBlock.decode(data)
    if block_type == BlockType.LINKS:
        return LinksBlock.decode(data)
    raise ValueError(f"Unknown block type: {block_type}")
