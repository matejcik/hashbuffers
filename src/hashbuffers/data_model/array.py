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
from ..codec import SIZE_MAX, Block, DataBlock
from ..codec.table import BlockEntry, DirectDataEntry, TableEntry
from ..store import BlockStore
from ..util import align_up, pack_flat_array, unpack_flat_array
from .adapter import AdapterCodec
from .common import BlockDecoderType, FieldType, FixedFieldType, resolve_entry_to_block

T = t.TypeVar("T")


class FixedArrayType(FixedFieldType[t.Sequence[T]]):
    element_type: FixedFieldType[T]
    count: int

    def __init__(self, element_type: FixedFieldType[T], count: int) -> None:
        padded_size = align_up(element_type.get_size(), element_type.get_alignment())
        start_offset = max(element_type.get_alignment(), 4)
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
        encoded = self.encode_bytes(value)
        if self.get_alignment() == 1 and 2 + len(encoded) <= SIZE_MAX:
            return DirectDataEntry(encoded)
        data_block = DataBlock.build(
            encoded,
            elem_size=self.element_type.get_size(),
            elem_align=self.get_alignment(),
        )
        return BlockEntry(data_block)

    def _verify_data_block(self, block: DataBlock) -> None:
        expected_size = self.element_type.get_size()
        expected_align = self.get_alignment()
        if block.elem_size != expected_size:
            raise ValueError(
                f"DATA block elem_size {block.elem_size} does not match "
                f"expected {expected_size}"
            )
        if block.elem_align != expected_align:
            raise ValueError(
                f"DATA block elem_align {block.elem_align} does not match "
                f"expected {expected_align}"
            )

    def decode(self, entry: TableEntry, store: BlockStore) -> t.Sequence[T]:
        if isinstance(entry, DirectDataEntry):
            return self.decode_bytes(entry.data)
        block = resolve_entry_to_block(entry, store, expected_limit=self.count)
        if not isinstance(block, DataBlock):
            raise ValueError(f"Expected DATA block, got {type(block)}")
        self._verify_data_block(block)
        data = bytes(block.get_data())
        return self.decode_bytes(data)


class BytestringType(FieldType[bytes]):
    def encode(self, value: bytes, store: BlockStore) -> TableEntry:
        # DIRECTDATA header is 2 bytes, so max payload is SIZE_MAX - 2
        # but also need to fit on heap, so fitting will outlink if needed
        if 2 + len(value) <= SIZE_MAX:
            return DirectDataEntry(value)
        block = build_bytestring_tree(value, store)
        return BlockEntry(block)

    def decode(self, entry: TableEntry, store: BlockStore) -> bytes:
        if isinstance(entry, DirectDataEntry):
            return entry.data
        block = resolve_entry_to_block(entry, store)
        return BytestringTree(block, store).to_bytes()


@dataclass(frozen=True)
class VarArrayType(BlockDecoderType[t.Sequence[T]]):
    count: int | None

    def check_count(self, actual_count: int) -> None:
        if self.count is not None and actual_count != self.count:
            raise ValueError(f"Array expects {self.count} elements, got {actual_count}")

    def decode(self, entry: TableEntry, store: BlockStore) -> t.Sequence[T]:
        block = resolve_entry_to_block(entry, store)
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
        block = build_bytestring_array([self.adapter.encode(v) for v in value], store)
        return BlockEntry(block)

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
        block = build_data_array(
            byte_values,
            self.element_type.get_size(),
            self.element_type.get_alignment(),
            store,
        )
        return BlockEntry(block)

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
        block = build_table_array(blocks, store)
        return BlockEntry(block)

    def to_array(self, block: Block, store: BlockStore) -> t.Sequence[T]:
        return TableArray(
            block,
            store,
            decode_element=self.block_decoder_type.block_decoder(store),
        )
