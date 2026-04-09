from __future__ import annotations

import typing as t

from .data_model.abc import FieldType
from .data_model.primitive import PrimitiveInt
from .store import BlockStore

_PyT = t.TypeVar("_PyT")
_HBT = t.TypeVar("_HBT", bound="HashBuffer")

U8: PrimitiveInt
U16: PrimitiveInt
U32: PrimitiveInt
U64: PrimitiveInt
I8: PrimitiveInt
I16: PrimitiveInt
I32: FieldType[int]
I64: FieldType[int]
F32: FieldType[float]
F64: FieldType[float]

Bool: FieldType[bool]
Bytes: FieldType[bytes]
String: FieldType[str]

@t.overload
def Array(
    element: FieldType[_PyT], *, count: None = ...
) -> FieldType[t.Sequence[_PyT]]: ...
@t.overload
def Array(element: FieldType[_PyT], *, count: int) -> FieldType[t.Sequence[_PyT]]: ...
@t.overload
def Array(element: type[_HBT], *, count: None = ...) -> FieldType[t.Sequence[_HBT]]: ...
@t.overload
def Array(element: type[_HBT], *, count: int) -> FieldType[t.Sequence[_HBT]]: ...
def EnumType(enum_cls: type[_PyT], repr: FieldType[int] = ...) -> FieldType[_PyT]: ...
def is_fixed_size(typ: t.Any) -> bool: ...
def fixed_size(typ: t.Any) -> int: ...
def alignment_of(typ: t.Any) -> int: ...

class Field:
    index: int
    required: bool

    @t.overload
    def __new__(
        cls,
        index: int,
        type: FieldType[_PyT],
        *,
        required: t.Literal[True],
    ) -> _PyT: ...
    @t.overload
    def __new__(
        cls,
        index: int,
        type: FieldType[_PyT],
        *,
        required: bool = ...,
    ) -> _PyT | None: ...
    @t.overload
    def __new__(
        cls,
        index: int,
        type: type[_HBT],
        *,
        required: t.Literal[True],
    ) -> _HBT: ...
    @t.overload
    def __new__(
        cls,
        index: int,
        type: type[_HBT],
        *,
        required: bool = ...,
    ) -> _HBT | None: ...

@t.dataclass_transform()
class HashBuffer:
    _hb_fields: t.ClassVar[dict[str, Field]]
    _hb_max_index: t.ClassVar[int]

    def __init_subclass__(cls, **kwargs: t.Any) -> None: ...
    def __init__(self, **kwargs: t.Any) -> None: ...
    def encode(self, store: BlockStore) -> bytes: ...
    @classmethod
    def decode(cls, data: bytes, store: BlockStore) -> t.Self: ...
    def __eq__(self, other: object) -> bool: ...
    def __repr__(self) -> str: ...
