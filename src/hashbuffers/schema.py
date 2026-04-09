"""Schema DSL for defining HashBuffer data structures.

This module is intentionally thin: it composes `data_model` field types and adds
descriptor-backed HashBuffer classes with lazy LINK resolution.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass
from enum import IntEnum
from typing import dataclass_transform

from .codec import Block, Link, TableBlock, VTableEntryType
from .data_model.abc import BlockDecoderType, FieldType, FixedFieldType
from .data_model.adapter import AdapterCodec
from .data_model.array import (
    BlockArrayType,
    BytestringArrayType,
    BytestringType,
    DataArrayType,
    FixedArrayType,
)
from .data_model.primitive import F32, F64, I8, I16, I32, I64, U8, U16, U32, U64
from .data_model.struct import StructField, StructType
from .fitting import BlockEntry, TableEntry
from .store import BlockStore

__all__ = [
    "U8",
    "U16",
    "U32",
    "U64",
    "I8",
    "I16",
    "I32",
    "I64",
    "F32",
    "F64",
    "Bool",
    "Bytes",
    "String",
    "Array",
    "EnumType",
    "Field",
    "HashBuffer",
]

T = t.TypeVar("T")
U = t.TypeVar("U")
HB = t.TypeVar("HB", bound="HashBuffer")
E = t.TypeVar("E", bound=IntEnum)


class _UnsetType:
    pass


_UNSET = _UnsetType()


@dataclass
class _LazyValue(t.Generic[T]):
    resolve: t.Callable[[], T]
    value: T | _UnsetType = _UNSET

    def get(self) -> T:
        if isinstance(self.value, _UnsetType):
            self.value = self.resolve()
        return self.value


def _table_entry_type(table: TableBlock, index: int) -> VTableEntryType | None:
    if index < 0 or index >= len(table.vtable):
        return None
    return table.vtable[index].type


@dataclass(frozen=True)
class _AdapterFieldType(FieldType[T], t.Generic[T, U]):
    inner: FieldType[U]
    adapter: AdapterCodec[T, U]

    def encode(self, value: T, store: BlockStore) -> TableEntry:
        return self.inner.encode(self.adapter.encode(value), store)

    def decode(self, table: TableBlock, index: int, store: BlockStore) -> T | None:
        value = self.inner.decode(table, index, store)
        if value is None:
            return None
        return self.adapter.decode(value)


class _FixedAdapterFieldType(
    _AdapterFieldType[T, U], FixedFieldType[T], t.Generic[T, U]
):
    inner: FixedFieldType[U]

    def __init__(self, inner: FixedFieldType[U], adapter: AdapterCodec[T, U]) -> None:
        super().__init__(inner, adapter)

    def get_size(self) -> int:
        return self.inner.get_size()

    def get_alignment(self) -> int:
        return self.inner.get_alignment()

    def encode_bytes(self, value: T) -> bytes:
        return self.inner.encode_bytes(self.adapter.encode(value))

    def decode_bytes(self, data: bytes) -> T:
        return self.adapter.decode(self.inner.decode_bytes(data))


@dataclass(frozen=True)
class _HashBufferFieldType(BlockDecoderType[HB]):
    hb_type: type[HB]

    def encode(self, value: HB, store: BlockStore) -> TableEntry:
        return value._encode_table_entry(store)

    def decode(self, table: TableBlock, index: int, store: BlockStore) -> HB | None:
        block = table.get_block(index)
        if block is None:
            return None
        if isinstance(block, Link):
            if block.limit != 1:
                raise ValueError(f"Expected LINK with limit 1, got {block.limit}")
            block = store.fetch(block.digest)
        return self.block_decoder(store)(block)

    def block_decoder(self, store: BlockStore) -> t.Callable[[Block], HB]:
        def decode_block(block: Block) -> HB:
            if not isinstance(block, TableBlock):
                raise ValueError(f"Expected TABLE block, got {type(block)}")
            return self.hb_type._decode_from_table(block, store)

        return decode_block


def _field_type_from_annotation(typ: t.Any) -> FieldType:
    if isinstance(typ, FieldType):
        return typ
    if isinstance(typ, type) and issubclass(typ, HashBuffer):
        return _HashBufferFieldType(typ)
    raise TypeError(f"Unsupported field type: {typ!r}")


def _adapt(
    inner: FieldType[U],
    *,
    encode: t.Callable[[T], U],
    decode: t.Callable[[U], T],
) -> FieldType[T]:
    adapter = AdapterCodec(encode, decode)
    if isinstance(inner, FixedFieldType):
        return _FixedAdapterFieldType(inner, adapter)
    return _AdapterFieldType(inner, adapter)


def EnumType(enum_cls: type[E], repr: FieldType[int] = U8) -> FieldType[E]:
    return _adapt(repr, encode=lambda value: value.value, decode=enum_cls)


Bool: FieldType[bool] = _adapt(U8, encode=int, decode=bool)
Bytes: FieldType[bytes] = BytestringType()
String: FieldType[str] = _adapt(
    Bytes,
    encode=lambda value: value.encode("utf-8"),
    decode=lambda value: value.decode("utf-8"),
)


def Array(element: t.Any, *, count: int | None = None) -> FieldType[t.Any]:
    element_type = _field_type_from_annotation(element)
    if count is not None and isinstance(element_type, FixedFieldType):
        try:
            return FixedArrayType(element_type, count)
        except ValueError:
            # count is too large for fixed-size element type
            # pass through to variable-size array
            pass

    if isinstance(element_type, _AdapterFieldType) and isinstance(
        element_type.inner, BytestringType
    ):
        return BytestringArrayType(adapter=element_type.adapter, count=count)

    if isinstance(element_type, _HashBufferFieldType):
        return BlockArrayType(element_type, count=count)
    if isinstance(element_type, FixedFieldType):
        return DataArrayType(element_type, count=count)
    if isinstance(element_type, BytestringType):
        return BytestringArrayType(count=count)
    if isinstance(element_type, BlockDecoderType):
        return BlockArrayType(element_type, count=count)
    raise TypeError(f"Cannot create array of {element!r}")


class Field(t.Generic[T]):
    """Descriptor declaring one HashBuffer field."""

    def __init__(
        self,
        index: int,
        type: t.Any,
        *,
        required: bool = False,
    ) -> None:
        self.index = index
        self.type = type
        self.required = required
        self.name: str | None = None
        self._field_type: FieldType[T] | None = None

    def __set_name__(self, owner: type["HashBuffer"], name: str) -> None:
        self.name = name

    def bind(self) -> None:
        self._field_type = _field_type_from_annotation(self.type)

    @property
    def field_type(self) -> FieldType[T]:
        if self._field_type is None:
            raise RuntimeError("Field is not bound")
        return self._field_type

    def _normalize(self, value: T) -> T | None:
        if value is None:
            if self.required:
                raise ValueError(f"Required field '{self.name}' is missing")
            return None
        return value

    def _decode_now(self, table: TableBlock, store: BlockStore) -> T | None:
        assert self.name is not None
        value = self.field_type.decode(table, self.index, store)
        if value is None and self.required:
            raise ValueError(
                f"Required field '{self.name}' (index {self.index}) is missing"
            )
        return value

    def _decode_maybe_lazy(self, table: TableBlock, store: BlockStore) -> t.Any:
        entry_type = _table_entry_type(table, self.index)
        if entry_type == VTableEntryType.LINK:
            return _LazyValue(lambda: self._decode_now(table, store))
        return self._decode_now(table, store)

    @t.overload
    def __get__(self, instance: HashBuffer, owner: type[HashBuffer]) -> T | None: ...
    @t.overload
    def __get__(self, instance: None, owner: type[HashBuffer]) -> t.Self: ...

    def __get__(
        self, instance: HashBuffer | None, owner: type[HashBuffer]
    ) -> T | None | t.Self:
        if instance is None:
            return self
        assert self.name is not None
        raw = instance._hb_state.get(self.name, _UNSET)
        if isinstance(raw, _LazyValue):
            value = raw.get()
            instance._hb_state[self.name] = value
            return value
        if raw is _UNSET:
            return None
        return raw

    def __set__(self, instance: HashBuffer, value: T) -> None:
        assert self.name is not None
        instance._hb_state[self.name] = self._normalize(value)


@dataclass_transform()
class HashBuffer:
    _hb_fields: t.ClassVar[dict[str, Field[t.Any]]] = {}
    _hb_struct: t.ClassVar[StructType]

    _hb_state: dict[str, t.Any]

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__init_subclass__(**kwargs)

        fields: dict[str, Field[t.Any]] = {}
        seen_indices: dict[int, str] = {}
        struct_fields: list[StructField[t.Any]] = []
        max_index = -1

        for attr_name, attr_value in cls.__dict__.items():
            if not isinstance(attr_value, Field):
                continue
            attr_value.bind()
            if attr_value.index in seen_indices:
                raise ValueError(
                    f"Duplicate field index {attr_value.index}: "
                    f"fields '{seen_indices[attr_value.index]}' and '{attr_name}'"
                )
            seen_indices[attr_value.index] = attr_name
            fields[attr_name] = attr_value
            struct_fields.append(
                StructField(
                    index=attr_value.index,
                    name=attr_name,
                    type=attr_value.field_type,
                    required=attr_value.required,
                )
            )
            max_index = max(max_index, attr_value.index)

        cls._hb_fields = fields
        cls._hb_struct = StructType(struct_fields)

    def __init__(self, **kwargs: t.Any) -> None:
        self._hb_state = {name: None for name in self._hb_fields}
        unknown = set(kwargs) - set(self._hb_fields)
        if unknown:
            unknown_names = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown field(s): {unknown_names}")
        for name, value in kwargs.items():
            setattr(self, name, value)

    def _encode_table_entry(self, store: BlockStore) -> BlockEntry:
        mapping = {name: getattr(self, name) for name in self._hb_fields}
        entry = self._hb_struct.encode(mapping, store)
        if not isinstance(entry, BlockEntry):
            raise ValueError("Root struct must encode as a block entry")
        if not isinstance(entry.block, TableBlock):
            raise ValueError(
                f"Root struct must encode to TABLE, got {type(entry.block)}"
            )
        return entry

    def encode(self, store: BlockStore) -> bytes:
        entry = self._encode_table_entry(store)
        return entry.encode()

    @classmethod
    def _decode_from_table(cls, table: TableBlock, store: BlockStore) -> t.Self:
        self = cls.__new__(cls)
        self._hb_state = {}

        for name, field in cls._hb_fields.items():
            self._hb_state[name] = field._decode_maybe_lazy(table, store)

        return self

    @classmethod
    def decode(cls, data: bytes, store: BlockStore) -> t.Self:
        table = TableBlock.decode(data)
        return cls._decode_from_table(table, store)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        for name in self._hb_fields:
            if getattr(self, name) != getattr(other, name):
                return False
        return True

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{name}={getattr(self, name)!r}" for name in self._hb_fields
        )
        return f"{self.__class__.__name__}({fields})"
