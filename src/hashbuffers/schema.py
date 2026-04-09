"""Schema DSL for defining Hashbuffers data structures."""

from __future__ import annotations

import struct
import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import dataclass_transform, overload

from .arrays import (
    DataArray,
    build_bytestring_array,
    build_data_array,
    build_table_array,
)
from .codec import (
    SIZE_MAX,
    Block,
    DataBlock,
    Link,
    SlotsBlock,
    TableBlock,
    decode_block,
)
from .fitting import BlockEntry, DirectEntry, TableEntry, int_inline_or_direct
from .store import BlockStore

# ============================================================
# Primitive types
# ============================================================

T = t.TypeVar("T")
U = t.TypeVar("U")


class Codec(t.Generic[T], ABC):
    """Generic encoder that can:

    * take a Python value of type T and encode it as an entry in a TABLE struct
    * take a TABLE block entry and decode it into a Python value of type T
    """

    @abstractmethod
    def encode_value(self, value: T, store: BlockStore) -> TableEntry:
        raise NotImplementedError

    @abstractmethod
    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> T | None:
        raise NotImplementedError


class BlockCodec(Codec[T]):
    """Codec that can store the value of type T in a stand-alone block."""

    @abstractmethod
    def encode_block(self, value: T, store: BlockStore) -> BlockEntry:
        raise NotImplementedError

    @abstractmethod
    def decode_block(self, block: Block, store: BlockStore) -> T:
        raise NotImplementedError


class SmallFixedCodec(BlockCodec[T]):
    """Codec for a small (smaller than block size) fixed-size value.

    Builds on BlockCodec -- every fixed-size value must be encodable as a stand-alone DATA block.
    In addition to BlockCodec, it can report its size and alignment, and encode to / decode from
    raw bytes, so that we can build fixed-size arrays of it.
    """

    @property
    @abstractmethod
    def size(self) -> int:
        raise NotImplementedError

    @property
    @abstractmethod
    def alignment(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def to_bytes(self, value: T) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def from_bytes(self, data: bytes | memoryview) -> T:
        raise NotImplementedError

    def encode_block(self, value: T, store: BlockStore) -> BlockEntry:
        data = self.to_bytes(value)
        block = DataBlock.build(data, align=self.alignment)
        return BlockEntry.from_data(block, self.alignment, 1)

    def decode_block(self, block: Block, store: BlockStore) -> T:
        if not isinstance(block, DataBlock):
            raise ValueError(f"SmallFixedCodec expects DATA block, got {block}")
        if block.size != self.size:
            raise ValueError(
                f"SmallFixedCodec expects {self.size} bytes, got {block.size}"
            )
        return self.from_bytes(block.data)


@dataclass(frozen=True)
class PrimitiveInt(SmallFixedCodec[int]):
    _size: int
    _signed: bool

    @property
    def size(self) -> int:
        return self._size

    @property
    def alignment(self) -> int:
        return self.size

    def to_bytes(self, value: int) -> bytes:
        return value.to_bytes(self.size, "little", signed=self._signed)

    def from_bytes(self, data: bytes | memoryview) -> int:
        return int.from_bytes(data, "little", signed=self._signed)

    # --- miscellaneous ---

    def fits_inline(self, value: int) -> bool:
        """Check if an integer value fits in a 13-bit inline field."""
        if self._signed:
            return -4096 <= value <= 4095
        else:
            return 0 <= value <= 8191

    # --- FieldCodec protocol ---

    def encode_value(self, value: int, store: BlockStore) -> TableEntry:
        """Encode a primitive value into a TableField."""
        return int_inline_or_direct(value, self.size, self._signed)

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> int | None:
        """Decode a primitive value from a TABLE block."""
        return table.get_int(index, self.size, signed=self._signed)


@dataclass(frozen=True)
class PrimitiveFloat(SmallFixedCodec[float]):
    _size: int

    # --- FixedCodec protocol ---

    @property
    def size(self) -> int:
        return self._size

    @property
    def alignment(self) -> int:
        return self._size

    def to_bytes(self, value: float) -> bytes:
        return struct.pack(self.struct_format, value)

    def from_bytes(self, data: bytes | memoryview) -> float:
        return struct.unpack(self.struct_format, data)[0]

    # --- miscellaneous ---

    @property
    def struct_format(self) -> str:
        return "<f" if self._size == 4 else "<d"

    # --- FieldCodec protocol ---

    def encode_value(self, value: float, store: BlockStore) -> TableEntry:
        """Encode a primitive value into a TableField."""
        data = self.to_bytes(value)
        return DirectEntry(data, self.alignment, 1)

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> float | None:
        """Decode a primitive value from a TABLE block."""
        data = table.get_fixedsize(index, self.size)
        if data is None:
            return None
        return self.from_bytes(data)


# Module-level constants
U8 = PrimitiveInt(1, False)
U16 = PrimitiveInt(2, False)
U32 = PrimitiveInt(4, False)
U64 = PrimitiveInt(8, False)
I8 = PrimitiveInt(1, True)
I16 = PrimitiveInt(2, True)
I32 = PrimitiveInt(4, True)
I64 = PrimitiveInt(8, True)
F32 = PrimitiveFloat(4)
F64 = PrimitiveFloat(8)


# ============================================================
# Adapters
# ============================================================

PythonType = t.TypeVar("PythonType")
WireType = t.TypeVar("WireType")


@dataclass(frozen=True)
class Adapter(t.Generic[PythonType, WireType]):
    """Semantic adapter: transforms between Python type P and wire type W.

    The wire_type handles serialization; the adapter handles semantics.
    """

    wire_codec: Codec[WireType]
    py_encode: t.Callable[[PythonType], WireType]  # Python value → wire value
    py_decode: t.Callable[[WireType], PythonType]  # wire value → Python value

    @classmethod
    def adapt(
        cls,
        adapter: "Adapter[T, WireType]",
        encode: t.Callable[[PythonType], T],
        decode: t.Callable[[T], PythonType],
    ) -> t.Self:
        def adapt_encode(value: PythonType) -> WireType:
            return adapter.py_encode(encode(value))

        def adapt_decode(value: WireType) -> PythonType:
            return decode(adapter.py_decode(value))

        return cls(adapter.wire_codec, adapt_encode, adapt_decode)


# Predefined adapters
Bool = Adapter(U8, int, bool)


def identity_adapter(codec: Codec[T]) -> Adapter[T, T]:
    return Adapter(codec, lambda v: v, lambda v: v)


def EnumType(enum_cls: type[Enum], repr: PrimitiveInt = U8) -> Adapter:
    """Create an adapter for an Enum type stored as a primitive."""
    return Adapter(repr, lambda e: e.value, enum_cls)


# ============================================================
# Array types
# ============================================================


@dataclass(frozen=True, kw_only=True)
class _SmallFixedArray(SmallFixedCodec[list[T]], t.Generic[T, U]):
    """Small (smaller than block size) fixed-count array of fixed-size elements.
    Stored as DIRECT on heap.
    """

    element_codec: SmallFixedCodec[U]
    element_adapter: Adapter[T, U]
    count: int

    @t.overload
    @classmethod
    def new(
        cls, codec: SmallFixedCodec[U], /, count: int
    ) -> "_SmallFixedArray[U, U]": ...

    @t.overload
    @classmethod
    def new(cls, adapter: Adapter[T, U], /, count: int) -> "_SmallFixedArray[T, U]": ...

    @classmethod
    def new(
        cls, codec_or_adapter: SmallFixedCodec | Adapter, /, count: int
    ) -> "_SmallFixedArray":
        if isinstance(codec_or_adapter, Codec):
            return cls(
                element_codec=codec_or_adapter,
                element_adapter=Adapter(codec_or_adapter, lambda v: v, lambda v: v),
                count=count,
            )

        if not isinstance(codec_or_adapter.wire_codec, SmallFixedCodec):
            raise TypeError(
                f"Expected SmallFixedCodec, got {codec_or_adapter.wire_codec}"
            )
        return cls(
            element_codec=codec_or_adapter.wire_codec,
            element_adapter=codec_or_adapter,
            count=count,
        )

    @property
    def padded_element_size(self) -> int:
        return DataBlock.padded_elem_size(
            self.element_codec.size,
            self.element_codec.alignment,
        )

    @property
    def size(self) -> int:
        return self.padded_element_size * self.count

    @property
    def alignment(self) -> int:
        return self.element_codec.alignment

    def to_bytes(self, value: list[T]) -> bytes:
        if len(value) != self.count:
            raise ValueError(
                f"FixedArray expects {self.count} elements, got {len(value)}"
            )
        wire_value = [self.element_adapter.py_encode(v) for v in value]
        return b"".join(self.element_codec.to_bytes(v) for v in wire_value)

    def from_bytes(self, data: bytes | memoryview) -> list[T]:
        if len(data) != self.size:
            raise ValueError(f"FixedArray expects {self.size} bytes, got {len(data)}")
        chunks = [
            data[
                i * self.padded_element_size : (i * self.padded_element_size)
                + self.element_codec.size
            ]
            for i in range(self.count)
        ]
        elems = [self.element_codec.from_bytes(chunk) for chunk in chunks]
        return [self.element_adapter.py_decode(v) for v in elems]

    def encode_value(self, value: list[T], store: BlockStore) -> TableEntry:
        """Encode a fixed-size array as DIRECT bytes on heap."""
        if len(value) != self.count:
            raise ValueError(
                f"FixedArray expects {self.count} elements, got {len(value)}"
            )
        data = self.to_bytes(value)
        return DirectEntry(data, self.alignment, self.count)

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> list[T] | None:
        """Decode a fixed-size array from a TABLE block."""
        data = table.get_fixedsize(index, self.size)
        if data is None:
            return None
        return self.from_bytes(data)


@dataclass(frozen=True, kw_only=True)
class _VarArray(BlockCodec[t.Sequence[T]], t.Generic[T, U]):
    """Base for variable-length array types."""

    element_adapter: Adapter[T, U]
    count: int | None = None

    def encode_value(self, value: t.Sequence[T], store: BlockStore) -> TableEntry:
        return self.encode_block(value, store)

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> t.Sequence[T] | None:
        block = _resolve_block_or_link(table, index, store)
        if block is None:
            return None
        return self.decode_block(block, store)


@dataclass(frozen=True, kw_only=True)
class _DataArray(_VarArray[T, U]):
    """Variable-length array of fixed-size elements. DATA representation."""

    element_codec: SmallFixedCodec[U]

    def to_byte_array(self, value: t.Sequence[T]) -> list[bytes]:
        if self.count is not None and len(value) != self.count:
            raise ValueError(
                f"_DataArray expects {self.count} elements, got {len(value)}"
            )
        adapted = [self.element_adapter.py_encode(v) for v in value]
        elements = [self.element_codec.to_bytes(v) for v in adapted]
        return elements

    def from_byte_array(self, data: t.Sequence[bytes]) -> t.Sequence[T]:
        if self.count is not None and len(data) != self.count:
            raise ValueError(
                f"_DataArray expects {self.count} elements, got {len(data)}"
            )
        adapted = [self.element_codec.from_bytes(v) for v in data]
        return [self.element_adapter.py_decode(v) for v in adapted]

    def encode_block(self, value: t.Sequence[T], store: BlockStore) -> BlockEntry:
        elements = self.to_byte_array(value)
        return build_data_array(elements, self.element_codec.alignment, store)

    def decode_block(self, block: Block, store: BlockStore) -> t.Sequence[T]:
        array = DataArray(
            block, store, self.element_codec.size, self.element_codec.alignment
        )
        return self.from_byte_array(array[:])


@dataclass(frozen=True, kw_only=True)
class _BytestringArray(_VarArray[T, bytes]):
    """Variable-length array of byte strings. SLOTS representation with TABLE fallback."""

    def encode_block(self, value: list[T], store: BlockStore) -> BlockEntry:
        byte_array = [self.element_adapter.py_encode(v) for v in value]
        return build_bytestring_array(byte_array, store)

    def decode_block(self, block: Block, store: BlockStore) -> list[T]:

        leaves = collect_leaves(block, store)
        if not leaves:
            return self._adapt_decode([])
        first = leaves[0]
        if isinstance(first, TableBlock):
            # TABLE representation: each entry is a DATA block of U8
            entries = decode_table_entries(block, store)
            result: list[bytes] = []
            for entry in entries:
                repr = DataRepr(1, 1)
                raw = decode_leaves(entry, repr, store)
                result.append(bytes(b[0] for b in raw))
            return self._adapt_decode(t.cast(list[T], result))
        else:
            # SLOTS representation
            raw_slots = decode_slots_entries(block, store)
            return self._adapt_decode(t.cast(list[T], raw_slots))


@dataclass(frozen=True, kw_only=True)
class _BlockArray(_VarArray[T, T]):
    """Variable-length array of block-encoded elements (structs or nested arrays).
    TABLE representation."""

    element: t.Any  # HashBuffer class or BlockCodec instance

    def encode_block(self, value: list[T], store: BlockStore) -> StoredBlock:
        value = self._adapt_encode(value)
        sbs: list[StoredBlock] = []
        for v in value:
            if _is_hashbuffer(self.element):
                sbs.append(t.cast(HashBuffer, v).encode(store))
            elif isinstance(self.element, BlockCodec):
                sbs.append(self.element.encode_block(v, store))
            else:
                raise TypeError(f"Cannot encode element: {self.element}")
        return build_table_array(sbs, store)

    def decode_block(self, block: Block, store: BlockStore) -> list[T] | None:
        entries = decode_table_entries(block, store)
        result: list[t.Any] = []
        for entry in entries:
            if _is_hashbuffer(self.element):
                result.append(self.element.decode(entry.encode(), store))
            elif isinstance(self.element, BlockCodec):
                result.append(self.element.decode_block(entry, store))
            else:
                raise TypeError(f"Cannot decode element: {self.element}")
        return self._adapt_decode(result)


def Array(element: Codec[T], *, count: int | None = None) -> t.Any:
    return
    """Create an array type.

    Array(U32)           — variable-length array of u32
    Array(U32, count=3)  — fixed-size array of 3 u32s
    Array(Array(U32))    — variable array of variable arrays (no wrapper struct needed)
    """
    # Detect adaptation level
    is_adapted = hasattr(element, "unadapt") and element.unadapt() is not element
    adapter: Adapter | None = element if is_adapted else None  # type: ignore
    wire = element.unadapt() if is_adapted else element

    if count is not None:
        if not isinstance(wire, FixedCodec):
            raise TypeError(
                f"Fixed-count array requires fixed-size element type, got {wire}"
            )
        return _FixedArray(element=wire, element_adapter=adapter, count=count)

    # Variable-length: dispatch based on wire type
    if isinstance(wire, FixedCodec):
        return _DataArray(element=wire, element_adapter=adapter)

    # Bytes array: element is Adapted wrapping a DataArray of U8
    if isinstance(wire, _DataArray) and wire.element == U8:
        # Determine the right adapter for _BytesArray:
        # - Array(Bytes): adapter=Bytes, but _BytesArray works with bytes directly → no adapter
        # - Array(String): adapter=String, py_encode converts str→bytes → keep adapter
        if (
            is_adapted
            and isinstance(adapter, Adapter)
            and isinstance(adapter.wire_type, Adapter)
        ):
            # Outer adapter wrapping Bytes (e.g., String wrapping Bytes)
            return _BytesArray(element_adapter=adapter)
        else:
            # Direct Bytes adapter or _RawBytes → no adapter needed
            return _BytesArray()

    # HashBuffer (struct) arrays
    if _is_hashbuffer(element):
        return _BlockArray(element=element)

    # BlockCodec arrays (nested variable arrays, etc.)
    if isinstance(element, BlockCodec):
        return _BlockArray(element=element, element_adapter=adapter)

    raise TypeError(f"Cannot create array of: {element}")


# Convenience aliases (Bytes defined after Adapted class below)
_RawBytes = Array(U8)  # raw wire type: list[int]
Bytes = Adapter(_RawBytes, list, bytes)  # Python bytes ↔ wire list[int]
String = Adapter.adapt(Bytes, lambda s: s.encode("utf-8"), lambda b: b.decode("utf-8"))


# ============================================================
# Type helpers
# ============================================================


def _wire_type(typ: t.Any) -> t.Any:
    """Get the underlying wire type, unwrapping Adapted."""
    if isinstance(typ, Adapter):
        return _wire_type(typ.wire_type)
    return typ


def is_fixed_size(typ: t.Any) -> bool:
    """Check if a type has a known fixed byte size."""
    typ = _wire_type(typ)
    return isinstance(typ, FixedCodec)


def alignment_of(typ: t.Any) -> int:
    """Get the alignment requirement of a type."""
    typ = _wire_type(typ)
    if isinstance(typ, FixedCodec):
        return typ.alignment
    if isinstance(typ, _FixedArray):
        return alignment_of(typ.element)
    if isinstance(typ, _VarArray):
        if isinstance(typ, _DataArray):
            return max(typ.element.alignment, 2)
        return 2
    if _is_hashbuffer(typ):
        return 2
    raise TypeError(f"Cannot determine alignment for: {typ}")


def _is_hashbuffer(typ: t.Any) -> t.TypeGuard[type[HashBuffer]]:
    """Check if a type is a HashBuffer subclass."""
    return isinstance(typ, type) and issubclass(typ, HashBuffer)


# ============================================================
# Block-level encode/decode helpers
# ============================================================


def _resolve_block_or_link(
    table: TableBlock, index: int, store: BlockStore
) -> Block | None:
    """Get block from a BLOCK or LINK vtable entry, resolving links."""
    result = table.get_block(index)
    if result is None:
        return None
    if isinstance(result, Link):
        return store.fetch(result.digest)
    return result


# ============================================================
# Field descriptor
# ============================================================


class Field:
    """Universal field descriptor.

    Usage:
        class MyStruct(HashBuffer):
            x: int | None = Field(0, U32)
            name: bytes | None = Field(1, Bytes)
            items: list[int] | None = Field(2, Array(U32))
            sub: SubStruct | None = Field(3, SubStruct)
            flag: bool | None = Field(4, Bool)
    """

    # __new__ returns Any so that `x: T = Field(...)` passes type-checker
    # assignment checks (same pattern used by attrs, dataclasses.field, etc.)
    def __new__(cls, *args: t.Any, **kwargs: t.Any) -> t.Any:
        return super().__new__(cls)

    def __init__(
        self,
        index: int,
        type: t.Any,
        *,
        required: bool = False,
        count: int | None = None,
    ) -> None:
        self.index = index
        self.type = type
        self.required = required
        self.count = count  # schema-level validation for variable arrays


# ============================================================
# HashBuffer base class
# ============================================================


@dataclass_transform()
class HashBuffer:
    """Base class for schema-defined Hashbuffers data structures.

    Usage:
        class MyStruct(HashBuffer):
            foo = Field(0, U32)
            bar = Field(1, Bytes)

        obj = MyStruct(foo=42, bar=b"hello")
        sb = obj.encode(store)
        decoded = MyStruct.decode(sb.data, store)
    """

    _hb_fields: t.ClassVar[dict[str, Field]] = {}
    _hb_max_index: t.ClassVar[int] = 0

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__init_subclass__(**kwargs)
        fields: dict[str, Field] = {}
        seen_indices: dict[int, str] = {}
        for attr_name, attr_value in cls.__dict__.items():
            if isinstance(attr_value, Field):
                if attr_value.index in seen_indices:
                    raise ValueError(
                        f"Duplicate field index {attr_value.index}: "
                        f"fields '{seen_indices[attr_value.index]}' and '{attr_name}'"
                    )
                seen_indices[attr_value.index] = attr_name
                fields[attr_name] = attr_value
        cls._hb_fields = fields
        cls._hb_max_index = max(f.index for f in fields.values()) + 1 if fields else 0

    def __init__(self, **kwargs: t.Any) -> None:
        for name, descriptor in self._hb_fields.items():
            value = kwargs.get(name)
            setattr(self, name, value)

    def encode(self, store: BlockStore) -> StoredBlock:
        """Encode this struct into blocks, store them, return StoredBlock."""
        fields: list[TableField] = [None] * self._hb_max_index

        for name, descriptor in self._hb_fields.items():
            value = getattr(self, name, None)
            fields[descriptor.index] = _encode_field(descriptor, value, store)

        return fit_table(fields, store)

    @classmethod
    def decode(cls, data: bytes, store: BlockStore) -> t.Self:
        """Decode a struct from block bytes."""
        table = TableBlock.decode(data)

        kwargs: dict[str, t.Any] = {}
        for name, descriptor in cls._hb_fields.items():
            value = _decode_field(descriptor, table, store)
            if value is None and descriptor.required:
                raise ValueError(
                    f"Required field '{name}' (index {descriptor.index}) is missing"
                )
            kwargs[name] = value

        return cls(**kwargs)

    @classmethod
    def encode_as_field(cls, value: HashBuffer, store: BlockStore) -> TableField:
        """Encode a HashBuffer value as a TABLE field (for use as nested struct)."""
        return value.encode(store)

    @classmethod
    def decode_as_field(
        cls, table: TableBlock, index: int, store: BlockStore
    ) -> t.Self | None:
        """Decode a HashBuffer from a TABLE field."""
        block = _resolve_block_or_link(table, index, store)
        if block is None:
            return None
        return cls.decode(block.encode(), store)

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


# ============================================================
# Field encode/decode dispatch
# ============================================================


def _encode_field(descriptor: Field, value: t.Any, store: BlockStore) -> TableField:
    """Encode a field value into a TableField."""
    if value is None:
        if descriptor.required:
            raise ValueError(f"Required field at index {descriptor.index} is None")
        return None

    typ = descriptor.type
    if _is_hashbuffer(typ):
        return typ.encode_as_field(value, store)
    return typ.encode_value(value, store)


def _decode_field(descriptor: Field, table: TableBlock, store: BlockStore) -> t.Any:
    """Decode a field value from a TABLE block."""
    typ = descriptor.type

    if _is_hashbuffer(typ):
        result = typ.decode_as_field(table, descriptor.index, store)
    else:
        result = typ.decode_value(table, descriptor.index, store)

    # Validate array count constraint
    if (
        descriptor.count is not None
        and result is not None
        and isinstance(result, list)
        and len(result) != descriptor.count
    ):
        raise ValueError(
            f"Array count mismatch: schema expects {descriptor.count}, got {len(result)}"
        )
    return result
