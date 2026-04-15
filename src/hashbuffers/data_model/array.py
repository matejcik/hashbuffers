import typing as t
from abc import abstractmethod
from dataclasses import dataclass

from ..arrays import (
    BytestringArray,
    BytestringTree,
    DataArray,
    TableArray,
    build_bytestring_array,
    build_bytestring_tree,
    build_data_array,
    build_table_array,
)
from ..codec import SIZE_MAX, Block, DataBlock, Link, TableBlock, VTableEntryType
from ..fitting import BlockEntry, TableEntry
from ..store import BlockStore
from ..util import pack_flat_array, padded_element_size, unpack_flat_array
from .abc import BlockDecoderType, FieldType, FixedFieldType
from .adapter import AdapterCodec

T = t.TypeVar("T")


class FixedArrayType(FixedFieldType[t.Sequence[T]]):
    element_type: FixedFieldType[T]
    count: int

    def __init__(self, element_type: FixedFieldType[T], count: int) -> None:
        padded_size = padded_element_size(
            element_type.get_size(), element_type.get_alignment()
        )
        start_offset = max(element_type.get_alignment(), 2)
        if not 0 <= (count * padded_size) <= (SIZE_MAX - start_offset):
            raise ValueError(f"Array size {count * padded_size} is too large")

        self.element_type = element_type
        self.count = count

    def get_size(self) -> int:
        return self.element_type.get_size() * self.count

    def get_alignment(self) -> int:
        return self.element_type.get_alignment()

    def encode_bytes(self, value: t.Sequence[T]) -> bytes:
        if len(value) != self.count:
            raise ValueError(
                f"FixedArray expects {self.count} elements, got {len(value)}"
            )
        byte_values = [self.element_type.encode_bytes(v) for v in value]
        return pack_flat_array(byte_values, self.get_alignment())

    def decode_bytes(self, data: bytes) -> t.Sequence[T]:
        if len(data) != self.get_size():
            raise ValueError(f"Expected {self.get_size()} bytes, got {len(data)}")
        byte_values = unpack_flat_array(
            data, self.element_type.get_size(), self.get_alignment()
        )
        return [self.element_type.decode_bytes(v) for v in byte_values]

    def encode(self, value: t.Sequence[T], store: BlockStore) -> TableEntry:
        data_block = DataBlock.build(
            self.encode_bytes(value), align=self.get_alignment()
        )
        return BlockEntry.from_data(data_block, self.get_alignment(), self.count)

    def decode(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> t.Sequence[T] | None:
        # TODO nicer interface for table.get
        entry = table.vtable[index]
        if entry.type == VTableEntryType.BLOCK:
            block = table.get_block(index)
            if not isinstance(block, DataBlock):
                raise ValueError(f"Expected DATA block, got {type(block)}")
            align = self.get_alignment()
            if entry.offset % align != 0:
                raise ValueError(
                    f"Fixed array BLOCK entry at offset {entry.offset} "
                    f"is not aligned to element alignment {align}"
                )
            data = bytes(block.get_data(align=self.get_alignment()))
            return self.decode_bytes(data)

        if entry.type == VTableEntryType.LINK:
            link = table.get_block(index)
            assert isinstance(link, Link)
            if link.limit != self.count:
                raise ValueError(
                    f"FixedArray expects {self.count} elements, got {link.limit}"
                )
            block = store.fetch(link.digest)
            if not isinstance(block, DataBlock):
                raise ValueError(f"Expected DATA block, got {type(block)}")
            data = bytes(block.get_data(align=self.get_alignment()))
            return self.decode_bytes(data)

        raise ValueError(f"Expected BLOCK or LINK entry, got {entry.type}")


class BytestringType(FieldType[bytes]):
    def encode(self, value: bytes, store: BlockStore) -> TableEntry:
        return build_bytestring_tree(value, store)

    def decode(self, table: TableBlock, index: int, store: BlockStore) -> bytes | None:
        block_or_link = table.get_block(index)
        if block_or_link is None:
            return None
        if isinstance(block_or_link, Link):
            block = store.fetch(block_or_link.digest)
        else:
            block = block_or_link
        return BytestringTree(block, store).to_bytes()


@dataclass(frozen=True)
class VarArrayType(BlockDecoderType[t.Sequence[T]]):
    count: int | None

    def check_count(self, actual_count: int) -> None:
        if self.count is not None and actual_count != self.count:
            raise ValueError(f"Array expects {self.count} elements, got {actual_count}")

    def decode(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> t.Sequence[T] | None:
        block = table.get_block(index)
        if block is None:
            return None
        if isinstance(block, Link):
            self.check_count(block.limit)
            block = store.fetch(block.digest)
        array = self.block_decoder(store)(block)
        self.check_count(len(array))
        return array

    def block_decoder(self, store: BlockStore) -> t.Callable[[Block], t.Sequence[T]]:
        def decode_block(block: Block) -> t.Sequence[T]:
            array = self.to_array(block, store)
            self.check_count(len(array))
            return array

        return decode_block

    @abstractmethod
    def to_array(self, block: Block, store: BlockStore) -> t.Sequence[T]:
        raise NotImplementedError


class BytestringArrayType(VarArrayType[T]):
    def __init__(
        self,
        count: int | None = None,
        *,
        adapter: AdapterCodec[T, bytes] = AdapterCodec.identity(),
    ) -> None:
        super().__init__(count)
        self.adapter = adapter

    def encode(self, value: t.Sequence[T], store: BlockStore) -> TableEntry:
        self.check_count(len(value))
        return build_bytestring_array([self.adapter.encode(v) for v in value], store)

    def to_array(self, block: Block, store: BlockStore) -> t.Sequence[T]:
        return BytestringArray(block, store, decode_element=self.adapter.decode)


class DataArrayType(VarArrayType[T]):
    element_type: FixedFieldType[T]

    def __init__(
        self, element_type: FixedFieldType[T], count: int | None = None
    ) -> None:
        super().__init__(count)
        self.element_type = element_type

    def encode(self, value: t.Sequence[T], store: BlockStore) -> TableEntry:
        self.check_count(len(value))
        byte_values = [self.element_type.encode_bytes(v) for v in value]
        return build_data_array(byte_values, self.element_type.get_alignment(), store)

    def to_array(self, block: Block, store: BlockStore) -> t.Sequence[T]:
        return DataArray(
            block,
            store,
            self.element_type.get_size(),
            self.element_type.get_alignment(),
            decode_element=self.element_type.decode_bytes,
        )


class BlockArrayType(VarArrayType[T]):
    block_decoder_type: BlockDecoderType[T]

    def __init__(
        self, block_decoder_type: BlockDecoderType[T], count: int | None = None
    ) -> None:
        super().__init__(count)
        self.block_decoder_type = block_decoder_type

    def encode(self, value: t.Sequence[T], store: BlockStore) -> TableEntry:
        self.check_count(len(value))
        blocks = [self.block_decoder_type.encode(v, store) for v in value]
        return build_table_array(blocks, store)

    def to_array(self, block: Block, store: BlockStore) -> t.Sequence[T]:
        return TableArray(
            block,
            store,
            decode_element=self.block_decoder_type.block_decoder(store),
        )
