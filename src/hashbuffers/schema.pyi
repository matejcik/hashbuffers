"""Type stubs for hashbuffers.schema.

These stubs encode the mapping from wire types to Python types so that:
  - Field(0, U8)              → int | None
  - Field(0, U8, required=True) → int
  - Field(0, Bytes)           → bytes | None
  - Field(0, Array(U32))      → list[int] | None
  - Field(0, Inner)           → Inner | None   (HashBuffer subclass)

Mismatches like `foo: str = Field(0, U8)` are flagged by the type checker.
"""

from __future__ import annotations

import typing as t

from .store import BlockStore, StoredBlock

# =============================================================
# TypeVars
# =============================================================

_PyT = t.TypeVar("_PyT")
_HBT = t.TypeVar("_HBT", bound="HashBuffer")

# =============================================================
# Wire type hierarchy (type-checker only)
#
# _Wire[T] is a marker: "this schema type maps to Python type T".
# _ListWire[T] extends _Wire[list[T]], so Array(...) results compose.
# =============================================================

class _Wire(t.Generic[_PyT]):
    """Base marker for schema wire types.  _PyT is the Python decode type."""

    ...

class _ListWire(_Wire[list[_PyT]], t.Generic[_PyT]):
    """Wire type for arrays (var or fixed); maps to list[_PyT]."""

    element: t.Any

# =============================================================
# Primitive types
# =============================================================

class Primitive:
    @property
    def size(self) -> int: ...
    @property
    def alignment(self) -> int: ...
    @property
    def signed(self) -> bool: ...
    @property
    def is_float(self) -> bool: ...
    def fits_inline(self, value: int) -> bool: ...
    def encode_value(self, value: int | float, store: BlockStore) -> t.Any: ...
    def decode_value(self, table: t.Any, index: int, store: BlockStore) -> t.Any: ...

U8: _Wire[int]
U16: _Wire[int]
U32: _Wire[int]
U64: _Wire[int]
I8: _Wire[int]
I16: _Wire[int]
I32: _Wire[int]
I64: _Wire[int]
F32: _Wire[float]
F64: _Wire[float]

# =============================================================
# Array constructor
# =============================================================

@t.overload
def Array(element: _Wire[_PyT], *, count: None = ...) -> _ListWire[_PyT]: ...
@t.overload
def Array(element: _Wire[_PyT], *, count: int) -> _ListWire[_PyT]: ...
@t.overload
def Array(element: type[_HBT], *, count: None = ...) -> _ListWire[_HBT]: ...
@t.overload
def Array(element: type[_HBT], *, count: int) -> _ListWire[_HBT]: ...

# =============================================================
# Adapter types
# =============================================================

class Adapted(t.Generic[_PyT]):
    wire_type: t.Any
    py_encode: t.Callable[..., t.Any]
    py_decode: t.Callable[..., t.Any]
    def __init__(
        self,
        wire_type: t.Any,
        py_encode: t.Callable[..., t.Any],
        py_decode: t.Callable[..., t.Any],
    ) -> None: ...
    def encode_value(self, value: _PyT, store: BlockStore) -> t.Any: ...
    def decode_value(
        self, table: t.Any, index: int, store: BlockStore
    ) -> _PyT | None: ...

Bool: _Wire[bool]
Bytes: _Wire[bytes]
String: _Wire[str]

def EnumType(enum_cls: type[_PyT], repr: t.Any = ...) -> _Wire[_PyT]: ...

# =============================================================
# Type helpers
# =============================================================

def is_fixed_size(typ: t.Any) -> bool: ...
def fixed_size(typ: t.Any) -> int: ...
def alignment_of(typ: t.Any) -> int: ...

# =============================================================
# Field descriptor
#
# __new__ is overloaded to return the Python type (not Field itself),
# so `x: int | None = Field(0, U8)` passes and `x: str = Field(0, U8)` fails.
# =============================================================

class Field:
    index: int
    required: bool
    count: int | None

    # _Wire[T], required=True  →  T
    @t.overload
    def __new__(
        cls,
        index: int,
        type: _Wire[_PyT],
        *,
        required: t.Literal[True],
        default: t.Any = ...,
        count: int | None = ...,
    ) -> _PyT: ...
    # _Wire[T], required=False (default)  →  T | None
    @t.overload
    def __new__(
        cls,
        index: int,
        type: _Wire[_PyT],
        *,
        required: bool = ...,
        default: t.Any = ...,
        count: int | None = ...,
    ) -> _PyT | None: ...
    # HashBuffer subclass, required=True  →  T
    @t.overload
    def __new__(
        cls,
        index: int,
        type: type[_HBT],
        *,
        required: t.Literal[True],
        default: t.Any = ...,
        count: int | None = ...,
    ) -> _HBT: ...
    # HashBuffer subclass, required=False  →  T | None
    @t.overload
    def __new__(
        cls,
        index: int,
        type: type[_HBT],
        *,
        required: bool = ...,
        default: t.Any = ...,
        count: int | None = ...,
    ) -> _HBT | None: ...
    # Fallback for custom Adapted instances, etc.  →  Any
    @t.overload
    def __new__(
        cls,
        index: int,
        type: t.Any,
        *,
        required: t.Literal[True],
        default: t.Any = ...,
        count: int | None = ...,
    ) -> t.Any: ...
    @t.overload
    def __new__(
        cls,
        index: int,
        type: t.Any,
        *,
        required: bool = ...,
        default: t.Any = ...,
        count: int | None = ...,
    ) -> t.Any: ...

# =============================================================
# HashBuffer base class
# =============================================================

@t.dataclass_transform()
class HashBuffer:
    _hb_fields: t.ClassVar[dict[str, t.Any]]
    _hb_max_index: t.ClassVar[int]

    def __init_subclass__(cls, **kwargs: t.Any) -> None: ...
    def __init__(self, **kwargs: t.Any) -> None: ...
    def encode(self, store: BlockStore) -> StoredBlock: ...
    @classmethod
    def decode(cls, data: bytes, store: BlockStore) -> t.Self: ...
    @classmethod
    def encode_as_field(cls, value: HashBuffer, store: BlockStore) -> t.Any: ...
    @classmethod
    def decode_as_field(
        cls, table: t.Any, index: int, store: BlockStore
    ) -> t.Self | None: ...
    def __eq__(self, other: object) -> bool: ...
    def __repr__(self) -> str: ...
