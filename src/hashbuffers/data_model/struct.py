from __future__ import annotations

from dataclasses import dataclass
import typing as t

from .abc import BlockDecoderType, FieldType
from ..store import BlockStore
from ..fitting import NULL_ENTRY, TableEntry, Table
from ..codec import Block, Link, TableBlock

T = t.TypeVar("T")


@dataclass(frozen=True)
class StructField(t.Generic[T]):
    index: int
    name: str
    type: FieldType[T]
    required: bool = False


class StructType(BlockDecoderType[dict[str, t.Any]]):
    def __init__(self, fields: t.Collection[StructField[t.Any]]) -> None:
        self.fields = fields
        names = set(field.name for field in fields)
        indices = set(field.index for field in fields)
        if len(indices) != len(self.fields) or len(names) != len(self.fields):
            raise ValueError("Duplicate field indices or names")

    def encode(self, value: dict[str, t.Any], store: BlockStore) -> TableEntry:
        max_index = max(e.index for e in self.fields)
        entries: list[TableEntry] = [NULL_ENTRY] * (max_index + 1)

        for name in value:
            if not any(field.name == name for field in self.fields):
                raise ValueError(f"Unknown field name: {name}")

        for field in self.fields:
            field_value = value.get(field.name)
            if field_value is None:
                if field.required:
                    raise ValueError(f"Field {field.name} is required")
                else:
                    continue
            entries[field.index] = field.type.encode(field_value, store)
        table = Table(entries)
        return table.build_entry(store)

    def decode(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> dict[str, t.Any] | None:
        block = table.get_block(index)
        if block is None:
            return None

        if isinstance(block, Link):
            if block.limit != 1:
                raise ValueError(f"Expected LINK with limit 1, got {block.limit}")
            block = store.fetch(block.digest)

        decode_block = self.block_decoder(store)
        return decode_block(block)

    def block_decoder(self, store: BlockStore) -> t.Callable[[Block], dict[str, t.Any]]:
        def decode_block(block: Block) -> dict[str, t.Any]:
            if not isinstance(block, TableBlock):
                raise ValueError(f"Expected TABLE block, got {type(block)}")

            result: dict[str, t.Any] = {}
            for field in self.fields:
                field_value = field.type.decode(block, field.index, store)
                if field_value is None and field.required:
                    raise ValueError(f"Field {field.name} is required")
                result[field.name] = field_value
            return result

        return decode_block
