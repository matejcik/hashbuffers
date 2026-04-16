import abc
import typing as t
from dataclasses import dataclass
from enum import IntEnum

from .io import Reader, Writer

SIZE_MAX = 0x1FFF

T = t.TypeVar("T")


def check_bounds(value: int, min: int, max: int) -> None:
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
        check_bounds(self.parameters, 0, 0x7)
        check_bounds(self.number, 0, SIZE_MAX)
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


@dataclass
class Block(abc.ABC, t.Sequence[T]):
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

    @abc.abstractmethod
    def alignment(self) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def element_count(self) -> int:
        raise NotImplementedError

    def validate(self) -> None:
        if self.size != self.compute_size():
            raise ValueError(
                f"Computed size {self.compute_size()} does not match declared size {self.size}"
            )
        check_bounds(self.size, 2, SIZE_MAX)

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
