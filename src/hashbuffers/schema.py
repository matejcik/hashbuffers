"""Schema DSL for defining Hashbuffers data structures."""

from __future__ import annotations

import struct
import typing as t
from dataclasses import dataclass
from enum import Enum
from typing import dataclass_transform

from .arrays import (
    build_data_array,
    build_slots_array,
    build_table_array,
    decode_data_elements,
    decode_slots_entries,
    decode_table_entries,
)
from .codec import (
    DataBlock,
    Link,
    TableBlock,
)
from .fitting import (
    DirectData,
    IntField,
    TableField,
    fit_table,
)
from .store import BlockStore, StoredBlock

# ============================================================
# Primitive types
# ============================================================


class Primitive(Enum):
    """Primitive wire types. Each member is usable directly as a field type."""

    #       (size, alignment, signed, is_float)
    U8 = (1, 1, False, False)
    U16 = (2, 2, False, False)
    U32 = (4, 4, False, False)
    U64 = (8, 8, False, False)
    I8 = (1, 1, True, False)
    I16 = (2, 2, True, False)
    I32 = (4, 4, True, False)
    I64 = (8, 8, True, False)
    F32 = (4, 4, False, True)
    F64 = (8, 8, False, True)

    @property
    def size(self) -> int:
        return self.value[0]

    @property
    def alignment(self) -> int:
        return self.value[1]

    @property
    def signed(self) -> bool:
        return self.value[2]

    @property
    def is_float(self) -> bool:
        return self.value[3]

    def fits_inline(self, value: int) -> bool:
        """Check if an integer value fits in a 13-bit inline field."""
        if self.is_float:
            return False
        if self.signed:
            return -4096 <= value <= 4095
        else:
            return 0 <= value <= 8191

    # --- encode/decode as a table field ---

    def encode_value(self, value: int | float, store: BlockStore) -> TableField:
        """Encode a primitive value into a TableField."""
        if not self.is_float:
            return IntField(int(value), self.size, self.signed)
        # Float: always DIRECT
        data = _encode_primitive(self, value)
        return DirectData(data, self.alignment)

    def decode_value(self, table: TableBlock, index: int, store: BlockStore) -> t.Any:
        """Decode a primitive value from a TABLE block."""
        if self.is_float:
            data = table.get_fixedsize(index, self.size)
            if data is None:
                return None
            return _decode_primitive(self, data)
        return table.get_int(index, self.size, signed=self.signed)


# Module-level constants
U8 = Primitive.U8
U16 = Primitive.U16
U32 = Primitive.U32
U64 = Primitive.U64
I8 = Primitive.I8
I16 = Primitive.I16
I32 = Primitive.I32
I64 = Primitive.I64
F32 = Primitive.F32
F64 = Primitive.F64


# ============================================================
# Array types
# ============================================================


@dataclass(frozen=True)
class _FixedArray:
    """Fixed-count array of fixed-size elements. Stored as DIRECT on heap."""

    element: t.Any  # Primitive, _FixedArray, or Adapted wrapping a fixed-size type
    count: int

    @property
    def size(self) -> int:
        elem_size = fixed_size(self.element)
        elem_align = alignment_of(self.element)
        padded = DataBlock.padded_elem_size(elem_size, elem_align)
        return padded * self.count

    def encode_value(self, value: list, store: BlockStore) -> TableField:
        """Encode a fixed-size array as DIRECT bytes on heap."""
        if len(value) != self.count:
            raise ValueError(
                f"FixedArray expects {self.count} elements, got {len(value)}"
            )
        data = _encode_fixed_array(self, value)
        return DirectData(data, alignment_of(self.element))

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> list | None:
        """Decode a fixed-size array from a TABLE block."""
        data = table.get_fixedsize(index, self.size)
        if data is None:
            return None
        return _decode_fixed_array(self, data)


@dataclass(frozen=True)
class _VarArray:
    """Variable-length array. Wire encoding depends on element type."""

    element: t.Any  # any type: Primitive, _FixedArray, _VarArray, Adapted, HashBuffer

    def _unwrap_adapted(self) -> tuple[t.Any, list[Adapted]]:
        """Unwrap all Adapted layers, returning (wire_elem, adapter_chain)."""
        elem = self.element
        chain: list[Adapted] = []
        while isinstance(elem, Adapted):
            chain.append(elem)
            elem = elem.wire_type
        return elem, chain

    def encode_value(self, value: list, store: BlockStore) -> TableField:
        """Encode a variable-length array."""
        wire_elem, adapters = self._unwrap_adapted()

        # Apply adapters to convert Python values to wire values
        wire_values = value
        for adapter in adapters:
            wire_values = [adapter.py_encode(v) for v in wire_values]

        if not wire_values:
            # Empty array: build an empty block of the appropriate type
            if is_fixed_size(wire_elem):
                sb = build_data_array([], alignment_of(wire_elem), store)
            elif _is_hashbuffer(wire_elem):
                sb = build_table_array([], store)
            else:
                sb = build_slots_array([], store)
            return sb

        if is_fixed_size(wire_elem):
            return self._encode_data(wire_elem, wire_values, store)
        elif _is_hashbuffer(wire_elem):
            return self._encode_table(wire_elem, wire_values, store)
        else:
            return self._encode_slots(wire_elem, wire_values, store)

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> list | None:
        """Decode a variable-length array from a TABLE block."""
        data = _resolve_block_or_link(table, index, store)
        if data is None:
            return None
        return self._decode_from_data(data, store)

    def _decode_from_data(self, data: bytes, store: BlockStore) -> list:
        """Decode from raw block data (used by decode_value and SLOTS decode)."""
        wire_elem, adapters = self._unwrap_adapted()

        if is_fixed_size(wire_elem):
            result = self._decode_data(wire_elem, data, store)
        elif _is_hashbuffer(wire_elem):
            result = self._decode_table(wire_elem, data, store)
        else:
            result = self._decode_slots(wire_elem, data, store)

        # Apply adapters in reverse to convert wire values to Python values
        for adapter in reversed(adapters):
            result = [adapter.py_decode(v) for v in result]
        return result

    # --- Encode helpers (one per array representation) ---

    @staticmethod
    def _encode_data(
        wire_elem: t.Any, wire_values: list, store: BlockStore
    ) -> TableField:
        if isinstance(wire_elem, _FixedArray):
            elements = [_encode_fixed_array(wire_elem, v) for v in wire_values]
        elif isinstance(wire_elem, Primitive):
            elements = [_encode_primitive(wire_elem, v) for v in wire_values]
        else:
            raise TypeError(f"Cannot encode as DATA array: {wire_elem}")
        sb = build_data_array(elements, alignment_of(wire_elem), store)
        return sb

    @staticmethod
    def _encode_table(
        wire_elem: t.Any, wire_values: list, store: BlockStore
    ) -> TableField:
        encoded = [v.encode(store) for v in wire_values]
        sb = build_table_array(encoded, store)
        return sb

    @staticmethod
    def _encode_slots(
        wire_elem: t.Any, wire_values: list, store: BlockStore
    ) -> TableField:
        slot_bytes: list[bytes] = []
        for v in wire_values:
            if isinstance(wire_elem, _VarArray):
                tf = wire_elem.encode_value(v, store)
                assert isinstance(tf, StoredBlock)
                slot_bytes.append(tf.data)
            elif _is_hashbuffer(wire_elem):
                slot_bytes.append(v.encode(store).data)
            else:
                raise TypeError(f"Cannot encode as SLOTS element: {wire_elem}")
        sb = build_slots_array(slot_bytes, store)
        return sb

    # --- Decode helpers (one per array representation) ---

    @staticmethod
    def _decode_data(wire_elem: t.Any, data: bytes, store: BlockStore) -> list:
        elem_size = fixed_size(wire_elem)
        elem_align = alignment_of(wire_elem)
        raw_elems = decode_data_elements(data, elem_size, elem_align, store)
        if isinstance(wire_elem, _FixedArray):
            return [_decode_fixed_array(wire_elem, e) for e in raw_elems]
        elif isinstance(wire_elem, Primitive):
            return [_decode_primitive(wire_elem, e) for e in raw_elems]
        else:
            raise TypeError(f"Cannot decode DATA array of: {wire_elem}")

    @staticmethod
    def _decode_table(wire_elem: t.Any, data: bytes, store: BlockStore) -> list:
        entries = decode_table_entries(data, store)
        result: list[t.Any] = []
        for entry in entries:
            if isinstance(entry, Link):
                sb = store[entry.digest]
                result.append(wire_elem.decode(sb.data, store))
            else:
                result.append(wire_elem.decode(entry.encode(), store))
        return result

    @staticmethod
    def _decode_slots(wire_elem: t.Any, data: bytes, store: BlockStore) -> list:
        raw_slots = decode_slots_entries(data, store)
        if isinstance(wire_elem, _VarArray):
            return [wire_elem._decode_from_data(s, store) for s in raw_slots]
        elif _is_hashbuffer(wire_elem):
            return [wire_elem.decode(s, store) for s in raw_slots]
        else:
            raise TypeError(f"Cannot decode SLOTS element: {wire_elem}")


def Array(element: t.Any, *, count: int | None = None) -> _FixedArray | _VarArray:
    """Create an array type.

    Array(U32)           — variable-length array of u32
    Array(U32, count=3)  — fixed-size array of 3 u32s
    Array(Array(U32))    — variable array of variable arrays (no wrapper struct needed)
    """
    if count is not None:
        if not is_fixed_size(element):
            raise TypeError(
                f"Fixed-count array requires fixed-size element type, got {element}"
            )
        return _FixedArray(element, count)
    return _VarArray(element)


# Convenience aliases (Bytes defined after Adapted class below)
_RawBytes = Array(U8)  # raw wire type: list[int]


# ============================================================
# Adapters
# ============================================================


@dataclass(frozen=True)
class Adapted:
    """Semantic adapter: transforms between Python type P and wire type W.

    The wire_type handles serialization; the adapter handles semantics.
    """

    wire_type: t.Any  # underlying wire type
    py_encode: t.Callable  # Python value → wire value
    py_decode: t.Callable  # wire value → Python value

    def encode_value(self, value: t.Any, store: BlockStore) -> TableField:
        wire_val = self.py_encode(value)
        return self.wire_type.encode_value(wire_val, store)

    def decode_value(self, table: TableBlock, index: int, store: BlockStore) -> t.Any:
        wire_val = self.wire_type.decode_value(table, index, store)
        if wire_val is None:
            return None
        return self.py_decode(wire_val)


# Predefined adapters
Bool = Adapted(U8, int, bool)
Bytes = Adapted(_RawBytes, list, bytes)  # Python bytes ↔ wire list[int]
String = Adapted(Bytes, lambda s: s.encode("utf-8"), lambda b: b.decode("utf-8"))


def EnumType(enum_cls: type[Enum], repr: Primitive = U8) -> Adapted:
    """Create an adapter for an Enum type stored as a primitive."""
    return Adapted(repr, lambda e: e.value, enum_cls)


# ============================================================
# Type helpers
# ============================================================


def _wire_type(typ: t.Any) -> t.Any:
    """Get the underlying wire type, unwrapping Adapted."""
    if isinstance(typ, Adapted):
        return _wire_type(typ.wire_type)
    return typ


def is_fixed_size(typ: t.Any) -> bool:
    """Check if a type has a known fixed byte size."""
    typ = _wire_type(typ)
    if isinstance(typ, Primitive):
        return True
    if isinstance(typ, _FixedArray):
        return True
    return False


def fixed_size(typ: t.Any) -> int:
    """Get the fixed byte size of a type. Raises if not fixed-size."""
    typ = _wire_type(typ)
    if isinstance(typ, Primitive):
        return typ.size
    if isinstance(typ, _FixedArray):
        return typ.size
    raise TypeError(f"Type {typ} is not fixed-size")


def alignment_of(typ: t.Any) -> int:
    """Get the alignment requirement of a type."""
    typ = _wire_type(typ)
    if isinstance(typ, Primitive):
        return typ.alignment
    if isinstance(typ, _FixedArray):
        return alignment_of(typ.element)
    if isinstance(typ, _VarArray):
        elem = _wire_type(typ.element)
        if is_fixed_size(elem):
            return max(alignment_of(elem), 2)
        return 2
    if _is_hashbuffer(typ):
        return 2
    raise TypeError(f"Cannot determine alignment for: {typ}")


def _is_hashbuffer(typ: t.Any) -> bool:
    """Check if a type is a HashBuffer subclass."""
    return isinstance(typ, type) and issubclass(typ, HashBuffer)


# ============================================================
# Primitive encode/decode helpers
# ============================================================


def _encode_primitive(prim: Primitive, value: int | float) -> bytes:
    """Encode a primitive value to bytes."""
    if prim.is_float:
        fmt = "<f" if prim == Primitive.F32 else "<d"
        return struct.pack(fmt, value)
    return int(value).to_bytes(prim.size, "little", signed=prim.signed)


def _decode_primitive(prim: Primitive, data: bytes) -> int | float:
    """Decode a primitive value from bytes."""
    if prim.is_float:
        fmt = "<f" if prim == Primitive.F32 else "<d"
        return struct.unpack(fmt, data)[0]
    return int.from_bytes(data, "little", signed=prim.signed)


def _encode_fixed_array(fat: _FixedArray, value: list) -> bytes:
    """Encode a fixed-size array to flat bytes."""
    if len(value) != fat.count:
        raise ValueError(f"FixedArray expects {fat.count} elements, got {len(value)}")
    elem = fat.element
    if isinstance(elem, Adapted):
        # Adapt each element, then encode via wire type
        wire_elem = _wire_type(elem)
        adapted = [elem.py_encode(v) for v in value]
        return _encode_fixed_array(_FixedArray(wire_elem, fat.count), adapted)
    if isinstance(elem, Primitive):
        return b"".join(_encode_primitive(elem, v) for v in value)
    if isinstance(elem, _FixedArray):
        return b"".join(_encode_fixed_array(elem, v) for v in value)
    raise TypeError(f"Cannot encode fixed array of: {elem}")


def _decode_fixed_array(fat: _FixedArray, data: bytes) -> list:
    """Decode a fixed-size array from flat bytes."""
    elem = fat.element
    if isinstance(elem, Adapted):
        wire_elem = _wire_type(elem)
        wire_result = _decode_fixed_array(_FixedArray(wire_elem, fat.count), data)
        return [elem.py_decode(v) for v in wire_result]
    if isinstance(elem, Primitive):
        return [
            _decode_primitive(elem, data[i * elem.size : (i + 1) * elem.size])
            for i in range(fat.count)
        ]
    if isinstance(elem, _FixedArray):
        return [
            _decode_fixed_array(elem, data[i * elem.size : (i + 1) * elem.size])
            for i in range(fat.count)
        ]
    raise TypeError(f"Cannot decode fixed array of: {elem}")


# ============================================================
# Block-level encode/decode helpers
# ============================================================


def _resolve_block_or_link(
    table: TableBlock, index: int, store: BlockStore
) -> bytes | None:
    """Get block data from a BLOCK or LINK entry."""
    result = table.get_block(index)
    if result is None:
        return None
    if isinstance(result, Link):
        sb = store[result.digest]
        return sb.data
    return result.encode()


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
        data = _resolve_block_or_link(table, index, store)
        if data is None:
            return None
        return cls.decode(data, store)

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
