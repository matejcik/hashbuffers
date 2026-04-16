from __future__ import annotations

import typing as t
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from ..codec import Block, TableBlock, TableEntry
from ..codec.table import NULL_ENTRY, TableEntry
from ..fitting import Table
from ..store import BlockStore
from .common import BlockDecoderType, FieldType, resolve_entry_to_block

T = t.TypeVar("T")
_UNSET = object()


@dataclass(frozen=True)
class StructField(t.Generic[T]):
    index: int
    name: str
    type: FieldType[T]
    required: bool = False


class LazyStructMapping(Mapping[str, t.Any]):
    """Mapping-like view over a TABLE struct with lazy field decode."""

    def __init__(
        self,
        fields: t.Collection[StructField[t.Any]],
        store: BlockStore,
        table: TableBlock,
    ) -> None:
        # self._fields = fields
        self._store = store
        self._table = table
        self._fields = {field.name: field for field in fields}
        self._values: dict[str, t.Any] = {field.name: _UNSET for field in fields}

    def _resolve(self, field: StructField[t.Any]) -> t.Any:
        cached = self._values[field.name]
        if cached is not _UNSET:
            return cached

        entry = self._table[field.index]
        value = field.type.decode_or_none(entry, self._store)
        if value is None and field.required:
            raise ValueError(f"Required field '{field.name}' is missing")
        self._values[field.name] = value
        return value

    def __getitem__(self, key: str) -> t.Any:
        field = self._fields.get(key)
        if field is None:
            raise KeyError(key)
        return self._resolve(field)

    def __iter__(self) -> Iterator[str]:
        return iter(self._fields.keys())

    def __len__(self) -> int:
        return len(self._fields)


class StructType(BlockDecoderType[Mapping[str, t.Any]]):
    def __init__(self, fields: t.Collection[StructField[t.Any]]) -> None:
        self.fields = fields
        names = set(field.name for field in fields)
        indices = set(field.index for field in fields)
        if len(indices) != len(self.fields) or len(names) != len(self.fields):
            raise ValueError("Duplicate field indices or names")

    def encode(self, value: Mapping[str, t.Any], store: BlockStore) -> TableEntry:
        entries: dict[int, TableEntry] = {}

        for name in value:
            if not any(field.name == name for field in self.fields):
                raise ValueError(f"Unknown field name: {name}")

        for field in self.fields:
            field_value = value.get(field.name)
            if field_value is None:
                if field.required:
                    raise ValueError(f"Required field '{field.name}' is missing")
                else:
                    continue
            entries[field.index] = field.type.encode(field_value, store)

        max_index = max(entries.keys(), default=-1)
        entries_list: list[TableEntry] = [NULL_ENTRY] * (max_index + 1)
        for index, entry in entries.items():
            entries_list[index] = entry
        table = Table(entries_list)
        return table.build_entry(store)

    def decode(self, entry: TableEntry, store: BlockStore) -> Mapping[str, t.Any]:
        block = resolve_entry_to_block(entry, store, expected_limit=1)
        decode_block = self.block_decoder(store)
        return decode_block(block)

    def block_decoder(
        self, store: BlockStore
    ) -> t.Callable[[Block], Mapping[str, t.Any]]:
        def decode_block(block: Block) -> Mapping[str, t.Any]:
            if not isinstance(block, TableBlock):
                raise ValueError(f"Expected TABLE block, got {type(block)}")
            return LazyStructMapping(self.fields, store, block)

        return decode_block
