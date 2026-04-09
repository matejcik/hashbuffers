import typing as t
from abc import ABC, abstractmethod

from ..codec import Block, TableBlock
from ..fitting import TableEntry
from ..store import BlockStore

T = t.TypeVar("T")


class FieldType(ABC, t.Generic[T]):
    @abstractmethod
    def encode(self, value: T, store: BlockStore) -> TableEntry:
        raise NotImplementedError

    @abstractmethod
    def decode(self, table: TableBlock, index: int, store: BlockStore) -> T | None:
        raise NotImplementedError


class FixedFieldType(FieldType[T]):
    @abstractmethod
    def get_size(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_alignment(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def encode_bytes(self, value: T) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def decode_bytes(self, data: bytes) -> T:
        raise NotImplementedError


class BlockDecoderType(FieldType[T], t.Generic[T]):
    @abstractmethod
    def block_decoder(self, store: BlockStore) -> t.Callable[[Block], T]:
        raise NotImplementedError
