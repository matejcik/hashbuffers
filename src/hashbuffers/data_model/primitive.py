import struct
from dataclasses import dataclass

from ..codec import TableBlock
from ..fitting import DirectEntry, TableEntry, int_inline_or_direct
from ..store import BlockStore
from .abc import FixedFieldType


@dataclass(frozen=True)
class PrimitiveInt(FixedFieldType[int]):
    size: int
    signed: bool

    def get_size(self) -> int:
        return self.size

    def get_alignment(self) -> int:
        return self.size

    def encode_bytes(self, value: int) -> bytes:
        return value.to_bytes(self.size, "little", signed=self.signed)

    def decode_bytes(self, data: bytes) -> int:
        return int.from_bytes(data, "little", signed=self.signed)

    def encode(self, value: int, store: BlockStore) -> TableEntry:
        return int_inline_or_direct(value, self.size, self.signed)

    def decode(self, table: TableBlock, index: int, store: BlockStore) -> int | None:
        return table.get_int(index, self.size, signed=self.signed)


@dataclass(frozen=True)
class PrimitiveFloat(FixedFieldType[float]):
    size: int

    def get_size(self) -> int:
        return self.size

    def get_alignment(self) -> int:
        return self.size

    @property
    def format(self) -> str:
        return "<f" if self.size == 4 else "<d"

    def encode_bytes(self, value: float) -> bytes:
        return struct.pack(self.format, value)

    def decode_bytes(self, data: bytes) -> float:
        return struct.unpack(self.format, data)[0]

    def encode(self, value: float, store: BlockStore) -> TableEntry:
        return DirectEntry(self.encode_bytes(value), self.size, 1)

    def decode(self, table: TableBlock, index: int, store: BlockStore) -> float | None:
        data = table.get_fixedsize(index, self.size)
        if data is None:
            return None
        return self.decode_bytes(data)


U8 = PrimitiveInt(1, False)
U16 = PrimitiveInt(2, False)
U32 = PrimitiveInt(4, False)
U64 = PrimitiveInt(8, False)
I8 = PrimitiveInt(1, True)
I16 = PrimitiveInt(2, True)
I32 = PrimitiveInt(4, True)
I64 = PrimitiveInt(8, True)
F32 = PrimitiveFloat(4)
F64 = PrimitiveFloat(8)
