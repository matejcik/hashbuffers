"""Test vectors from vectors_json/ directory.

Positive vectors: verify round-trip (encode → blockstore, blockstore → decode).
Negative vectors: verify that decoding raises an error.
"""

from __future__ import annotations

import json
import typing as t
from pathlib import Path

import pytest

from hashbuffers.data_model.abc import FieldType
from hashbuffers.data_model.array import (
    BlockArrayType,
    BytestringArrayType,
    BytestringType,
    DataArrayType,
    FixedArrayType,
)
from hashbuffers.data_model.struct import StructField, StructType
from hashbuffers.schema_json import LoadedSchema, load_schema
from hashbuffers.store import BlockStore

VECTORS_DIR = Path(__file__).parent.parent / "vectors_json"


def _load_vectors(name: str) -> list[dict[str, t.Any]]:
    return json.loads((VECTORS_DIR / name).read_text())


def _build_store(vector: dict[str, t.Any]) -> BlockStore:
    store = BlockStore(bytes.fromhex(vector["store_key"]))
    for digest_hex, block_hex in vector["store"].items():
        digest = bytes.fromhex(digest_hex)
        block_data = bytes.fromhex(block_hex)
        store.blocks[digest] = block_data
    return store


def _is_bytes_field(ft: FieldType) -> bool:
    """Check if a field type decodes to raw bytes (not str)."""
    if isinstance(ft, BytestringType):
        return True
    return False


def _find_element_type(ft: FieldType) -> FieldType | None:
    """Get the element type for array field types."""
    if isinstance(ft, (FixedArrayType, DataArrayType)):
        return ft.element_type
    if isinstance(ft, BytestringArrayType):
        return None  # handled specially
    if isinstance(ft, BlockArrayType):
        return ft.block_decoder_type
    return None


def _is_string_array(ft: FieldType) -> bool:
    if not isinstance(ft, BytestringArrayType):
        return False
    try:
        return isinstance(ft.adapter.decode(b""), str)
    except Exception:
        return False


def _is_bytes_array(ft: FieldType) -> bool:
    if not isinstance(ft, BytestringArrayType):
        return False
    return not _is_string_array(ft)


def _field_by_name(
    struct_fields: t.Collection[StructField],
    name: str,
) -> StructField | None:
    for f in struct_fields:
        if f.name == name:
            return f
    return None


def _decoded_to_json(
    mapping: t.Mapping[str, t.Any],
    struct_fields: t.Collection[StructField],
    schema: LoadedSchema,
) -> dict[str, t.Any]:
    """Convert a decoded mapping to the JSON message format."""
    result: dict[str, t.Any] = {}
    for key in mapping:
        value = mapping[key]
        if value is None:
            continue
        field = _field_by_name(struct_fields, key)
        assert field is not None
        result[key] = _value_to_json(value, field.type, schema)
    return result


def _value_to_json(
    value: t.Any,
    ft: FieldType,
    schema: LoadedSchema,
) -> t.Any:
    if _is_bytes_field(ft):
        assert isinstance(value, bytes)
        return value.hex()

    if isinstance(ft, StructType):
        return _decoded_to_json(value, ft.fields, schema)

    # Arrays
    elem_type = _find_element_type(ft)
    if elem_type is not None:
        return [_value_to_json(v, elem_type, schema) for v in value]
    if _is_bytes_array(ft):
        return [v.hex() for v in value]
    if _is_string_array(ft):
        return list(value)

    return value


def _json_to_encodable(
    message: dict[str, t.Any],
    struct_fields: t.Collection[StructField],
    schema: LoadedSchema,
) -> dict[str, t.Any]:
    """Convert a JSON message dict to types suitable for StructType.encode()."""
    result: dict[str, t.Any] = {}
    for key, value in message.items():
        field = _field_by_name(struct_fields, key)
        assert field is not None, f"Unknown field: {key}"
        result[key] = _json_value_to_encodable(value, field.type, schema)
    return result


def _json_value_to_encodable(
    value: t.Any,
    ft: FieldType,
    schema: LoadedSchema,
) -> t.Any:
    if _is_bytes_field(ft):
        assert isinstance(value, str)
        return bytes.fromhex(value)

    if isinstance(ft, StructType):
        assert isinstance(value, dict)
        return _json_to_encodable(value, ft.fields, schema)

    # Arrays
    elem_type = _find_element_type(ft)
    if elem_type is not None:
        return [_json_value_to_encodable(v, elem_type, schema) for v in value]
    if _is_bytes_array(ft):
        return [bytes.fromhex(v) for v in value]
    if _is_string_array(ft):
        return list(value)

    return value


# ---- Positive vectors ----

POSITIVE_VECTORS = _load_vectors("positive.json")


@pytest.mark.parametrize(
    "vector",
    POSITIVE_VECTORS,
    ids=[v["name"] for v in POSITIVE_VECTORS],
)
class TestPositiveVectors:
    def test_decode(self, vector: dict[str, t.Any]) -> None:
        schema = load_schema(vector["schema"])
        store = _build_store(vector)
        root_digest = bytes.fromhex(vector["root_digest"])
        root_bytes = store.blocks[root_digest]

        decoded = schema.decode_root(root_bytes, store)
        result = _decoded_to_json(decoded, schema.root.type.fields, schema)
        assert result == vector["message"]

    def test_encode_roundtrip(self, vector: dict[str, t.Any]) -> None:
        schema = load_schema(vector["schema"])
        store_key = bytes.fromhex(vector["store_key"])
        encodable = _json_to_encodable(
            vector["message"], schema.root.type.fields, schema
        )

        encode_store = BlockStore(store_key)
        entry = schema.root.type.encode(encodable, encode_store)
        root_bytes = entry.encode()
        encode_store.store_bytes(root_bytes)

        # decode our own encoding and verify it matches the expected message
        decoded = schema.decode_root(root_bytes, encode_store)
        result = _decoded_to_json(decoded, schema.root.type.fields, schema)
        assert result == vector["message"]

    def test_encode_exact(self, vector: dict[str, t.Any]) -> None:
        """Verify encoding produces byte-exact match with the test vector."""
        schema = load_schema(vector["schema"])
        store_key = bytes.fromhex(vector["store_key"])
        encodable = _json_to_encodable(
            vector["message"], schema.root.type.fields, schema
        )

        encode_store = BlockStore(store_key)
        entry = schema.root.type.encode(encodable, encode_store)
        root_bytes = entry.encode()
        root_digest = encode_store.store_bytes(root_bytes)

        assert root_digest.hex() == vector["root_digest"]
        expected_store = {
            bytes.fromhex(k): bytes.fromhex(v) for k, v in vector["store"].items()
        }
        assert encode_store.blocks == expected_store


# ---- Negative vectors ----

NEGATIVE_VECTORS = _load_vectors("negative.json")


@pytest.mark.parametrize(
    "vector",
    NEGATIVE_VECTORS,
    ids=[v["name"] for v in NEGATIVE_VECTORS],
)
class TestNegativeVectors:
    def test_decode_fails(self, vector: dict[str, t.Any]) -> None:
        schema = load_schema(vector["schema"])
        store = _build_store(vector)
        root_digest = bytes.fromhex(vector["root_digest"])
        root_bytes = store.blocks[root_digest]

        with pytest.raises((ValueError, Exception)):
            decoded = schema.decode_root(root_bytes, store)
            # force lazy evaluation of all fields
            _decoded_to_json(decoded, schema.root.type.fields, schema)
