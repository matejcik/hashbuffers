"""Bridge between trezorlib protobuf MessageType classes and Hashbuffers."""

from __future__ import annotations

import typing as t
from enum import IntEnum

from .codec import Block, Link, TableBlock
from .data_model.abc import BlockDecoderType, FieldType
from .fitting import NULL_ENTRY, BlockEntry, Table, TableEntry
from .schema import (
    I32,
    I64,
    U8,
    U16,
    U32,
    U64,
    Array,
    Bool,
    Bytes,
    EnumType,
    String,
)
from .store import BlockStore

if t.TYPE_CHECKING:
    from trezorlib import protobuf

    MT = t.TypeVar("MT", bound=protobuf.MessageType)


def _resolve_block_or_link(
    table: TableBlock, index: int, store: BlockStore
) -> Block | None:
    result = table.get_block(index)
    if result is None:
        return None
    if isinstance(result, Link):
        return store.fetch(result.digest)
    return result


class _MessageRef(BlockDecoderType["protobuf.MessageType"]):
    def __init__(self, msg_type: type["protobuf.MessageType"]) -> None:
        self.msg_type = msg_type

    def encode(self, value: "protobuf.MessageType", store: BlockStore) -> TableEntry:
        return _serialize_entry(value, store)

    def decode(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> "protobuf.MessageType | None":
        block = _resolve_block_or_link(table, index, store)
        if block is None:
            return None
        return self.block_decoder(store)(block)

    def block_decoder(
        self, store: BlockStore
    ) -> t.Callable[[Block], "protobuf.MessageType"]:
        def decode_block(block: Block) -> "protobuf.MessageType":
            if not isinstance(block, TableBlock):
                raise ValueError(f"Expected TABLE block, got {type(block)}")
            return _deserialize_from_table(self.msg_type, block, store)

        return decode_block


# Protobuf type name -> hashbuffers schema type
_PROTO_TYPE_MAP: dict[str, FieldType[t.Any]] = {
    "uint32": U32,
    "uint64": U64,
    "sint32": I32,
    "sint64": I64,
    "bool": Bool,
    "bytes": Bytes,
    "string": String,
}


def _hb_type_for_field(field: "protobuf.Field") -> FieldType[t.Any]:
    hb_type = _PROTO_TYPE_MAP.get(field.proto_type)
    if hb_type is not None:
        if field.repeated:
            return Array(hb_type)
        return hb_type

    py_type = field.py_type

    if issubclass(py_type, IntEnum):
        max_val = max(e.value for e in py_type) if len(py_type) > 0 else 0
        if max_val < 256:
            repr_prim = U8
        elif max_val < 65536:
            repr_prim = U16
        else:
            repr_prim = U32
        enum_type = EnumType(py_type, repr_prim)
        if field.repeated:
            return Array(enum_type)
        return enum_type

    msg_type = _MessageRef(py_type)
    if field.repeated:
        return Array(msg_type)
    return msg_type


def _serialize_entry(msg: "protobuf.MessageType", store: BlockStore) -> BlockEntry:
    mtype = msg.__class__
    if not mtype.FIELDS:
        return Table([]).build_entry(store)

    max_tag = max(mtype.FIELDS.keys())
    entries: list[TableEntry] = [NULL_ENTRY] * (max_tag + 1)

    for ftag, field in mtype.FIELDS.items():
        value = getattr(msg, field.name, None)
        if field.repeated and value is None:
            value = []
        if value is None:
            continue
        if field.repeated and not value:
            continue
        entries[ftag] = _hb_type_for_field(field).encode(value, store)

    return Table(entries).build_entry(store)


def serialize(msg: "protobuf.MessageType", store: BlockStore) -> bytes:
    entry = _serialize_entry(msg, store)
    return entry.encode()


def _deserialize_from_table(
    msg_type: type[MT], table: TableBlock, store: BlockStore
) -> MT:
    kwargs: dict[str, t.Any] = {}

    for ftag, field in msg_type.FIELDS.items():
        value = _hb_type_for_field(field).decode(table, ftag, store)

        if value is None:
            if field.repeated:
                kwargs[field.name] = []
            elif field.required:
                raise ValueError(
                    f"Required field '{field.name}' (tag {ftag}) is missing"
                )
            elif field.default is not None:
                kwargs[field.name] = field.default
            continue

        kwargs[field.name] = value

    return msg_type(**kwargs)


def deserialize(msg_type: type[MT], data: bytes, store: BlockStore) -> MT:
    table = TableBlock.decode(data)
    return _deserialize_from_table(msg_type, table, store)
