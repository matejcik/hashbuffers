import typing as t
from abc import ABC, abstractmethod

from ..codec import Block, TableEntry
from ..codec.table import BlockEntry, LinkEntry, NullEntry
from ..store import BlockStore

T = t.TypeVar("T")


class FieldType(ABC, t.Generic[T]):
    @abstractmethod
    def encode(self, value: T, store: BlockStore) -> TableEntry:
        raise NotImplementedError

    @abstractmethod
    def decode(self, entry: TableEntry, store: BlockStore) -> T:
        raise NotImplementedError

    def decode_or_none(self, entry: TableEntry, store: BlockStore) -> T | None:
        if isinstance(entry, NullEntry):
            return None
        return self.decode(entry, store)


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


def resolve_entry_to_block(
    entry: TableEntry, store: BlockStore, expected_limit: int | None = None
) -> Block:
    match entry:
        case BlockEntry(block=block):
            return block
        case LinkEntry(link=link):
            if expected_limit is not None and link.limit != expected_limit:
                raise ValueError(
                    f"Expected LINK with limit {expected_limit}, got {link.limit}"
                )
            return store.fetch(link.digest)
        case _:
            raise ValueError(
                f"Could not resolve entry to block: {type(entry).__name__}"
            )
