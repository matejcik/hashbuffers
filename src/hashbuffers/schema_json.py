"""JSON schema format for HashBuffers.

Provides dumping HashBuffer class hierarchies to JSON and loading
JSON schemas back into data_model type hierarchies.
"""

from __future__ import annotations

from functools import cached_property
import json
import re
import typing as t
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum

from .codec import TableBlock
from .data_model.abc import FieldType, FixedFieldType
from .data_model.array import (
    BlockArrayType,
    BytestringArrayType,
    BytestringType,
    DataArrayType,
    FixedArrayType,
)
from .data_model.adapter import AdapterCodec
from .data_model.primitive import F32, F64, I8, I16, I32, I64, U8, U16, U32, U64
from .data_model.struct import StructField, StructType
from .schema import (
    Array,
    Bool,
    Bytes,
    EnumType,
    HashBuffer,
    String,
    _FixedAdapterFieldType,
    _HashBufferFieldType,
)
from .store import BlockStore

SCHEMA_VERSION = 1

_BUILTIN_TO_STR: dict[FieldType, str] = {
    U8: "u8",
    U16: "u16",
    U32: "u32",
    U64: "u64",
    I8: "i8",
    I16: "i16",
    I32: "i32",
    I64: "i64",
    F32: "f32",
    F64: "f64",
    Bool: "bool",
    Bytes: "bytes",
    String: "str",
}

_STR_TO_BUILTIN: dict[str, FieldType] = {v: k for k, v in _BUILTIN_TO_STR.items()}

# ---- Type string parsing ----

_BRACKET_RE = re.compile(r"\[(\d*)\]$")


def _parse_type_string(s: str) -> tuple[str, list[int | None]]:
    """Parse ``'u32[][4]'`` into ``('u32', [None, 4])``.

    Dimensions are ordered left-to-right (innermost first).
    """
    dims: list[int | None] = []
    while True:
        m = _BRACKET_RE.search(s)
        if m is None:
            break
        dim_str = m.group(1)
        dims.append(int(dim_str) if dim_str else None)
        s = s[: m.start()]
    dims.reverse()
    return s, dims


# ---- Dumper ----


class _SchemaWalker:
    def __init__(self) -> None:
        self.structs: dict[str, dict[str, t.Any]] = {}
        self.enums: dict[str, dict[str, t.Any]] = {}
        self._pending: list[type[HashBuffer]] = []

    def walk(self, root: type[HashBuffer]) -> dict[str, t.Any]:
        self._pending.append(root)
        while self._pending:
            cls = self._pending.pop()
            name = cls.__name__
            if name in self.structs:
                continue
            self.structs[name] = self._dump_struct(cls)

        result: dict[str, t.Any] = {
            "version": SCHEMA_VERSION,
            "root": root.__name__,
        }
        if self.enums:
            result["enums"] = self.enums
        result["structs"] = self.structs
        return result

    def _dump_struct(self, cls: type[HashBuffer]) -> dict[str, t.Any]:
        fields: list[dict[str, t.Any]] = []
        for name, fld in cls._hb_fields.items():
            ft = fld.field_type
            field_dict: dict[str, t.Any] = {
                "index": fld.index,
                "name": name,
                "type": self._format_type(ft),
            }
            if fld.required:
                field_dict["required"] = True
            fields.append(field_dict)
        return {"fields": fields}

    def _format_type(self, ft: FieldType[t.Any]) -> str:
        # Built-in singletons
        if ft is Bool:
            return "bool"
        if ft is String:
            return "str"
        if isinstance(ft, BytestringType):
            return "bytes"

        # Enum: _FixedAdapterFieldType wrapping an IntEnum
        if isinstance(ft, _FixedAdapterFieldType):
            decode_fn = ft.adapter.decode
            if isinstance(decode_fn, type) and issubclass(decode_fn, IntEnum):
                inner = ft.inner
                self._register_enum(decode_fn, inner)
                return decode_fn.__name__

        # Primitives
        if ft in _BUILTIN_TO_STR:
            return _BUILTIN_TO_STR[ft]

        # Struct reference
        if isinstance(ft, _HashBufferFieldType):
            cls = ft.hb_type
            if cls.__name__ not in self.structs:
                self._pending.append(cls)
            return cls.__name__

        # Fixed-size array
        if isinstance(ft, FixedArrayType):
            return f"{self._format_type(ft.element_type)}[{ft.count}]"

        # Variable-size arrays
        if isinstance(ft, DataArrayType):
            elem = self._format_type(ft.element_type)
            if ft.count is not None:
                return f"{elem}[{ft.count}]"
            return f"{elem}[]"

        if isinstance(ft, BytestringArrayType):
            is_string = False
            try:
                is_string = isinstance(ft.adapter.decode(b""), str)
            except Exception:
                pass
            base = "str" if is_string else "bytes"
            if ft.count is not None:
                return f"{base}[{ft.count}]"
            return f"{base}[]"

        if isinstance(ft, BlockArrayType):
            elem = self._format_type(ft.block_decoder_type)
            if ft.count is not None:
                return f"{elem}[{ft.count}]"
            return f"{elem}[]"

        raise TypeError(f"Cannot format field type: {ft!r}")

    def _register_enum(
        self, enum_cls: type[IntEnum], inner: FixedFieldType[int]
    ) -> None:
        name = enum_cls.__name__
        if name in self.enums:
            return
        repr_str = _BUILTIN_TO_STR.get(inner, "u8")
        self.enums[name] = {
            "repr": repr_str,
            "members": {m.name: m.value for m in enum_cls},
        }


def dump_schema(root: type[HashBuffer]) -> dict[str, t.Any]:
    """Dump a HashBuffer class hierarchy to a JSON-serializable dict."""
    return _SchemaWalker().walk(root)


def dump_schema_json(root: type[HashBuffer], **kwargs: t.Any) -> str:
    """Dump a HashBuffer class hierarchy to a JSON string."""
    return json.dumps(dump_schema(root), **kwargs)


# ---- Loader ----


@dataclass(frozen=True)
class FieldConstraints:
    max_size: int | None = None


@dataclass(frozen=True)
class Struct:
    type: StructType
    field_constraints: dict[str, FieldConstraints]


@dataclass(frozen=True)
class JsonEnum:
    repr: FixedFieldType[int]
    members: dict[str, int]

    def decode(self, value: int) -> str:
        return next(name for name, v in self.members.items() if v == value)

    def encode(self, value: str) -> int:
        return self.members[value]

    @cached_property
    def field_type(self) -> FixedFieldType[str]:
        adapter = AdapterCodec(self.encode, self.decode)
        return _FixedAdapterFieldType(self.repr, adapter)


@dataclass
class LoadedSchema:
    """Result of loading a JSON schema."""

    root_name: str
    structs: dict[str, Struct]
    enums: dict[str, JsonEnum]

    @property
    def root(self) -> Struct:
        return self.structs[self.root_name]

    def decode_root(self, data: bytes, store: BlockStore) -> Mapping[str, t.Any]:
        table = TableBlock.decode(data)
        return self.root.type.block_decoder(store)(table)


def _collect_struct_refs(type_str: str, enum_names: set[str]) -> set[str]:
    base, _dims = _parse_type_string(type_str)
    if base in _STR_TO_BUILTIN:
        return set()
    if base in enum_names:
        return set()
    return {base}


def _topo_sort(structs: dict[str, t.Any], enum_names: set[str]) -> list[str]:
    deps: dict[str, set[str]] = {}
    for name, struct_def in structs.items():
        refs: set[str] = set()
        for fld in struct_def["fields"]:
            refs |= _collect_struct_refs(fld["type"], enum_names)
        for ref in refs:
            if ref not in structs:
                raise ValueError(f"Unknown type: {ref!r}")
        deps[name] = refs

    order: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"Circular struct dependency involving '{name}'")
        visiting.add(name)
        for dep in deps.get(name, set()):
            visit(dep)
        visiting.remove(name)
        visited.add(name)
        order.append(name)

    for name in structs:
        visit(name)
    return order


def _resolve_base_type(
    name: str,
    structs: dict[str, Struct],
    enums: dict[str, JsonEnum],
) -> FieldType[t.Any]:
    if name in _STR_TO_BUILTIN:
        return _STR_TO_BUILTIN[name]
    if name in enums:
        return enums[name].field_type
    if name in structs:
        return structs[name].type
    raise ValueError(f"Unknown type: {name!r}")


def _build_field_type(
    type_str: str,
    structs: dict[str, Struct],
    enums: dict[str, JsonEnum],
) -> FieldType[t.Any]:
    base_name, dims = _parse_type_string(type_str)
    current: FieldType[t.Any] = _resolve_base_type(base_name, structs, enums)
    for dim in dims:
        current = Array(current, count=dim)
    return current


def _count_array_levels(type_str: str) -> int:
    _base, dims = _parse_type_string(type_str)
    return len(dims)


def load_schema(data: dict[str, t.Any]) -> LoadedSchema:
    """Load a JSON schema dict into a LoadedSchema."""
    version = data.get("version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema version: {version} (expected {SCHEMA_VERSION})"
        )

    root_name = data["root"]
    enum_defs: dict[str, t.Any] = data.get("enums", {})
    struct_defs: dict[str, t.Any] = data["structs"]

    if root_name not in struct_defs:
        raise ValueError(f"Root struct {root_name!r} not found in structs")

    # 1. Build enums
    enums: dict[str, JsonEnum] = {}
    for name, edef in enum_defs.items():
        members = edef["members"]
        repr_str = edef.get("repr", "u8")
        repr_type = _STR_TO_BUILTIN.get(repr_str)
        if repr_type is None:
            raise ValueError(f"Unknown enum repr type: {repr_str!r}")
        if not isinstance(repr_type, FixedFieldType):
            raise ValueError(
                f"Enum repr type must be a fixed field type: {repr_type!r}"
            )
        enums[name] = JsonEnum(repr_type, members)

    # 2. Topological sort
    enum_names = set(enum_defs.keys())
    order = _topo_sort(struct_defs, enum_names)

    # 3. Build struct types
    structs: dict[str, Struct] = {}

    for struct_name in order:
        sdef = struct_defs[struct_name]
        struct_fields: list[StructField[t.Any]] = []
        field_constraints: dict[str, FieldConstraints] = {}
        for fdef in sdef["fields"]:
            type_str: str = fdef["type"]
            ft = _build_field_type(type_str, structs, enums)
            required: bool = fdef.get("required", False)

            max_size = fdef.get("max_size")
            if max_size is not None:
                n_levels = _count_array_levels(type_str)
                if n_levels == 0:
                    raise ValueError(
                        f"max_size on non-array field "
                        f"'{struct_name}.{fdef['name']}'"
                    )
                if n_levels > 1:
                    raise ValueError(
                        f"max_size on multi-level array "
                        f"'{struct_name}.{fdef['name']}' "
                        f"(type '{type_str}'): use type aliases "
                        f"for per-level constraints"
                    )

            field_constraints[fdef["name"]] = FieldConstraints(max_size=max_size)
            struct_fields.append(
                StructField(
                    index=fdef["index"],
                    name=fdef["name"],
                    type=ft,
                    required=required,
                )
            )
        structs[struct_name] = Struct(StructType(struct_fields), field_constraints)

    return LoadedSchema(root_name=root_name, structs=structs, enums=enums)


def load_schema_json(json_str: str) -> LoadedSchema:
    """Load a JSON schema string into a LoadedSchema."""
    return load_schema(json.loads(json_str))
