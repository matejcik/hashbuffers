"""Bridge between trezorlib protobuf MessageType classes and Hashbuffers.

Provides functions to serialize protobuf MessageType instances into Hashbuffers
wire format and deserialize them back.

Usage:
    from trezorlib import messages, protobuf
    from hashbuffers.trezorproto import serialize, deserialize
    from hashbuffers.store import BlockStore

    store = BlockStore(key)
    msg = messages.SignTx(outputs_count=1, inputs_count=2, coin_name="Bitcoin")
    sb = serialize(msg, store)

    decoded = deserialize(messages.SignTx, sb.data, store)
    assert decoded.outputs_count == 1
"""

from __future__ import annotations

import typing as t
from enum import IntEnum

from .codec import Link, TableBlock
from .fitting import TableField, fit_table
from .schema import (
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
from .store import BlockStore, StoredBlock

if t.TYPE_CHECKING:
    from trezorlib import protobuf


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


# Protobuf type name -> hashbuffers schema type
_PROTO_TYPE_MAP: dict[str, t.Any] = {
    "uint32": U32,
    "uint64": U64,
    "sint32": U32,  # protobuf sint uses zigzag; we store as plain uint
    "sint64": U64,
    "bool": Bool,
    "bytes": Bytes,
    "string": String,
}


def _hb_type_for_field(field: protobuf.Field) -> t.Any:
    """Map a protobuf Field to its hashbuffers schema type."""
    # Check builtins first
    hb_type = _PROTO_TYPE_MAP.get(field.proto_type)
    if hb_type is not None:
        if field.repeated:
            return Array(hb_type)
        return hb_type

    # Resolve the Python type — this handles enums and message types
    py_type = field.py_type

    if issubclass(py_type, IntEnum):
        # Pick a representation size based on the enum's max value
        max_val = max(e.value for e in py_type) if len(py_type) > 0 else 0
        if max_val < 256:
            repr_prim = U8
        elif max_val < 65536:
            repr_prim = U16
        else:
            repr_prim = U32
        hb_type = EnumType(py_type, repr_prim)
        if field.repeated:
            return Array(hb_type)
        return hb_type

    # Must be a MessageType subclass — handle recursively
    if field.repeated:
        return _MessageArray(py_type)
    return _MessageRef(py_type)


class _MessageRef:
    """Wrapper for a nested MessageType, implementing the encode_value/decode_value
    protocol expected by the schema field dispatch."""

    def __init__(self, msg_type: type[protobuf.MessageType]) -> None:
        self.msg_type = msg_type

    def encode_value(
        self, value: protobuf.MessageType, store: BlockStore
    ) -> TableField:
        sb = serialize(value, store)
        return sb

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> protobuf.MessageType | None:
        data = _resolve_block_or_link(table, index, store)
        if data is None:
            return None
        return deserialize(self.msg_type, data, store)


class _MessageArray:
    """Wrapper for a repeated MessageType field."""

    def __init__(self, msg_type: type[protobuf.MessageType]) -> None:
        self.msg_type = msg_type
        self._ref = _MessageRef(msg_type)

    def encode_value(self, value: list, store: BlockStore) -> TableField:
        from .arrays import build_table_array

        if not value:
            from .codec import TableBlock as TB

            block = TB.build([], b"")
            sb = store.store(block.encode(), limit=0, alignment=2)
            return sb

        encoded = [serialize(v, store) for v in value]
        sb = build_table_array(encoded, store)
        return sb

    def decode_value(
        self, table: TableBlock, index: int, store: BlockStore
    ) -> list | None:
        from .arrays import decode_table_entries
        from .codec import Link

        data = _resolve_block_or_link(table, index, store)
        if data is None:
            return None

        entries = decode_table_entries(data, store)
        result: list = []
        for entry in entries:
            if isinstance(entry, Link):
                sb = store[entry.digest]
                result.append(deserialize(self.msg_type, sb.data, store))
            else:
                result.append(deserialize(self.msg_type, entry.encode(), store))
        return result


def _encode_field_value(
    field: protobuf.Field,
    value: t.Any,
    store: BlockStore,
) -> TableField:
    """Encode a single protobuf field value into a TableField."""
    hb_type = _hb_type_for_field(field)

    if isinstance(hb_type, (_MessageRef, _MessageArray)):
        return hb_type.encode_value(value, store)

    return hb_type.encode_value(value, store)


def _decode_field_value(
    field: protobuf.Field,
    table: TableBlock,
    index: int,
    store: BlockStore,
) -> t.Any:
    """Decode a single protobuf field value from a TableBlock."""
    hb_type = _hb_type_for_field(field)

    if isinstance(hb_type, (_MessageRef, _MessageArray)):
        return hb_type.decode_value(table, index, store)

    return hb_type.decode_value(table, index, store)


def serialize(msg: protobuf.MessageType, store: BlockStore) -> StoredBlock:
    """Serialize a protobuf MessageType instance into Hashbuffers.

    Protobuf field tags become table indices. Fields are encoded according to
    their proto_type using the hashbuffers schema type system.

    Returns a StoredBlock containing the root TABLE block.
    """
    mtype = msg.__class__
    if not mtype.FIELDS:
        return fit_table([], store)

    max_tag = max(mtype.FIELDS.keys())
    fields: list[TableField] = [None] * (max_tag + 1)

    for ftag, field in mtype.FIELDS.items():
        value = getattr(msg, field.name, None)

        # Handle repeated fields: None means empty list
        if field.repeated and value is None:
            value = []

        # Skip None for non-repeated fields
        if value is None:
            continue

        # Skip empty repeated fields (encode as NULL)
        if field.repeated and not value:
            continue

        fields[ftag] = _encode_field_value(field, value, store)

    return fit_table(fields, store)


def deserialize(
    msg_type: type[protobuf.MessageType],
    data: bytes,
    store: BlockStore,
) -> protobuf.MessageType:
    """Deserialize a Hashbuffers TABLE block into a protobuf MessageType instance.

    Args:
        msg_type: The protobuf MessageType class to decode into.
        data: Raw bytes of the root TABLE block.
        store: BlockStore for resolving links.

    Returns:
        An instance of msg_type populated with decoded field values.
    """
    table = TableBlock.decode(data)

    kwargs: dict[str, t.Any] = {}
    for ftag, field in msg_type.FIELDS.items():
        value = _decode_field_value(field, table, ftag, store)

        if value is None:
            if field.repeated:
                kwargs[field.name] = []
            elif field.required:
                raise ValueError(
                    f"Required field '{field.name}' (tag {ftag}) is missing"
                )
            elif field.default is not None:
                kwargs[field.name] = field.default
            # else: leave it out, MessageType.__init__ handles defaults
            continue

        kwargs[field.name] = value

    return msg_type(**kwargs)
