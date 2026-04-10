"""Tests for JSON schema dump and load."""

from __future__ import annotations

import typing as t
from enum import IntEnum

import pytest

from hashbuffers.data_model.array import (
    BlockArrayType,
    BytestringArrayType,
    DataArrayType,
    FixedArrayType,
)
from hashbuffers.data_model.primitive import U8, U16, U32
from hashbuffers.data_model.struct import StructType
from hashbuffers.schema import (
    Array,
    Bool,
    Bytes,
    EnumType,
    Field,
    HashBuffer,
    String,
)
from hashbuffers.schema_json import (
    FieldConstraints,
    LoadedSchema,
    _parse_type_string,
    dump_schema,
    dump_schema_json,
    load_schema,
    load_schema_json,
)
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


# ---- Test schemas ----


class Color(IntEnum):
    RED = 0
    GREEN = 1
    BLUE = 2


class Priority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Inner(HashBuffer):
    value: int | None = Field(0, U8)


class Outer(HashBuffer):
    name: str | None = Field(0, String)
    inner: Inner | None = Field(1, Inner)
    data: bytes | None = Field(2, Bytes)


class WithArrays(HashBuffer):
    ids: t.Sequence[int] | None = Field(0, Array(U32))
    vec3: t.Sequence[int] | None = Field(1, Array(U32, count=3))
    items: t.Sequence[Inner] | None = Field(2, Array(Inner))
    names: t.Sequence[str] | None = Field(3, Array(String))
    blobs: t.Sequence[bytes] | None = Field(4, Array(Bytes))


class WithEnum(HashBuffer):
    color: Color | None = Field(0, EnumType(Color))
    priority: Priority | None = Field(1, EnumType(Priority, repr=U16))


class WithRequired(HashBuffer):
    name: bytes = Field(0, Bytes, required=True)
    value: int = Field(1, U32, required=True)


class WithBool(HashBuffer):
    flag: bool | None = Field(0, Bool)
    count: int | None = Field(1, U16)


class NestedArrays(HashBuffer):
    matrix: t.Sequence[t.Any] | None = Field(0, Array(Array(U32), count=4))


# ---- Type string parsing tests ----


class TestParseTypeString:
    def test_simple(self):
        assert _parse_type_string("u32") == ("u32", [])

    def test_variable_array(self):
        assert _parse_type_string("u32[]") == ("u32", [None])

    def test_fixed_array(self):
        assert _parse_type_string("u32[3]") == ("u32", [3])

    def test_nested_array(self):
        assert _parse_type_string("u32[][4]") == ("u32", [None, 4])

    def test_struct_array(self):
        assert _parse_type_string("Inner[]") == ("Inner", [None])

    def test_deep_nesting(self):
        assert _parse_type_string("u8[][3][2]") == ("u8", [None, 3, 2])


# ---- Dumper tests ----


class TestDumpSchema:
    def test_simple_struct(self):
        schema = dump_schema(Inner)
        assert schema["version"] == 1
        assert schema["root"] == "Inner"
        assert "enums" not in schema
        fields = schema["structs"]["Inner"]["fields"]
        assert len(fields) == 1
        assert fields[0] == {"index": 0, "name": "value", "type": "u8"}

    def test_nested_struct(self):
        schema = dump_schema(Outer)
        assert schema["root"] == "Outer"
        assert "Inner" in schema["structs"]
        assert "Outer" in schema["structs"]

        outer_fields = {f["name"]: f for f in schema["structs"]["Outer"]["fields"]}
        assert outer_fields["name"]["type"] == "str"
        assert outer_fields["inner"]["type"] == "Inner"
        assert outer_fields["data"]["type"] == "bytes"

    def test_arrays(self):
        schema = dump_schema(WithArrays)
        fields = {f["name"]: f for f in schema["structs"]["WithArrays"]["fields"]}
        assert fields["ids"]["type"] == "u32[]"
        assert fields["vec3"]["type"] == "u32[3]"
        assert fields["items"]["type"] == "Inner[]"
        assert fields["names"]["type"] == "str[]"
        assert fields["blobs"]["type"] == "bytes[]"

    def test_enum(self):
        schema = dump_schema(WithEnum)
        assert "enums" in schema
        assert "Color" in schema["enums"]
        assert schema["enums"]["Color"]["repr"] == "u8"
        assert schema["enums"]["Color"]["members"] == {
            "RED": 0,
            "GREEN": 1,
            "BLUE": 2,
        }
        assert "Priority" in schema["enums"]
        assert schema["enums"]["Priority"]["repr"] == "u16"

        fields = {f["name"]: f for f in schema["structs"]["WithEnum"]["fields"]}
        assert fields["color"]["type"] == "Color"
        assert fields["priority"]["type"] == "Priority"

    def test_required(self):
        schema = dump_schema(WithRequired)
        fields = {f["name"]: f for f in schema["structs"]["WithRequired"]["fields"]}
        assert fields["name"].get("required") is True
        assert fields["value"].get("required") is True

    def test_bool(self):
        schema = dump_schema(WithBool)
        fields = {f["name"]: f for f in schema["structs"]["WithBool"]["fields"]}
        assert fields["flag"]["type"] == "bool"
        assert fields["count"]["type"] == "u16"

    def test_nested_arrays(self):
        schema = dump_schema(NestedArrays)
        fields = {f["name"]: f for f in schema["structs"]["NestedArrays"]["fields"]}
        assert fields["matrix"]["type"] == "u32[][4]"

    def test_json_roundtrip(self):
        json_str = dump_schema_json(Outer, indent=2)
        data = load_schema_json(json_str)
        assert isinstance(data, LoadedSchema)
        assert data.root_name == "Outer"


# ---- Loader tests ----


class TestLoadSchema:
    def test_simple(self):
        data = {
            "version": 1,
            "root": "Simple",
            "structs": {
                "Simple": {
                    "fields": [
                        {"index": 0, "name": "x", "type": "u32"},
                        {"index": 1, "name": "y", "type": "i16"},
                    ]
                }
            },
        }
        loaded = load_schema(data)
        assert loaded.root_name == "Simple"
        assert "Simple" in loaded.structs
        st = loaded.structs["Simple"]
        assert isinstance(st.type, StructType)
        assert len(st.type.fields) == 2

    def test_nested_structs(self):
        data = {
            "version": 1,
            "root": "Outer",
            "structs": {
                "Inner": {"fields": [{"index": 0, "name": "value", "type": "u8"}]},
                "Outer": {
                    "fields": [
                        {"index": 0, "name": "name", "type": "str"},
                        {"index": 1, "name": "inner", "type": "Inner"},
                    ]
                },
            },
        }
        loaded = load_schema(data)
        assert "Inner" in loaded.structs
        assert "Outer" in loaded.structs

    def test_enums(self):
        data = {
            "version": 1,
            "root": "Root",
            "enums": {
                "Color": {
                    "repr": "u8",
                    "members": {"RED": 0, "GREEN": 1, "BLUE": 2},
                }
            },
            "structs": {
                "Root": {"fields": [{"index": 0, "name": "color", "type": "Color"}]}
            },
        }
        loaded = load_schema(data)
        assert "Color" in loaded.enums
        assert loaded.enums["Color"].members == {"RED": 0, "GREEN": 1, "BLUE": 2}

    def test_enum_default_repr(self):
        data = {
            "version": 1,
            "root": "Root",
            "enums": {
                "Status": {"members": {"ON": 1, "OFF": 0}},
            },
            "structs": {
                "Root": {"fields": [{"index": 0, "name": "status", "type": "Status"}]}
            },
        }
        loaded = load_schema(data)
        assert "Status" in loaded.enums

    def test_arrays(self):
        data = {
            "version": 1,
            "root": "Root",
            "structs": {
                "Root": {
                    "fields": [
                        {"index": 0, "name": "ids", "type": "u32[]"},
                        {"index": 1, "name": "vec", "type": "u32[3]"},
                        {"index": 2, "name": "names", "type": "str[]"},
                        {"index": 3, "name": "blobs", "type": "bytes[]"},
                    ]
                }
            },
        }
        loaded = load_schema(data)
        st = loaded.structs["Root"]
        fields = {f.name: f for f in st.type.fields}
        assert isinstance(fields["ids"].type, DataArrayType)
        assert isinstance(fields["vec"].type, FixedArrayType)
        assert isinstance(fields["names"].type, BytestringArrayType)
        assert isinstance(fields["blobs"].type, BytestringArrayType)

    def test_block_array(self):
        data = {
            "version": 1,
            "root": "Root",
            "structs": {
                "Item": {"fields": [{"index": 0, "name": "id", "type": "u16"}]},
                "Root": {
                    "fields": [
                        {"index": 0, "name": "items", "type": "Item[]"},
                    ]
                },
            },
        }
        loaded = load_schema(data)
        st = loaded.structs["Root"]
        fields = {f.name: f for f in st.type.fields}
        assert isinstance(fields["items"].type, BlockArrayType)

    def test_max_size_single_level(self):
        data = {
            "version": 1,
            "root": "Root",
            "structs": {
                "Root": {
                    "fields": [
                        {
                            "index": 0,
                            "name": "items",
                            "type": "u32[]",
                            "max_size": 128,
                        },
                    ]
                }
            },
        }
        loaded = load_schema(data)
        expected_constraints = FieldConstraints(max_size=128)
        assert loaded.structs["Root"].field_constraints["items"] == expected_constraints

    def test_max_size_multi_level_error(self):
        data = {
            "version": 1,
            "root": "Root",
            "structs": {
                "Root": {
                    "fields": [
                        {
                            "index": 0,
                            "name": "matrix",
                            "type": "u32[][]",
                            "max_size": 10,
                        },
                    ]
                }
            },
        }
        with pytest.raises(ValueError, match="multi-level array"):
            load_schema(data)

    def test_max_size_non_array_error(self):
        data = {
            "version": 1,
            "root": "Root",
            "structs": {
                "Root": {
                    "fields": [
                        {
                            "index": 0,
                            "name": "x",
                            "type": "u32",
                            "max_size": 10,
                        },
                    ]
                }
            },
        }
        with pytest.raises(ValueError, match="non-array"):
            load_schema(data)

    def test_bad_version(self):
        with pytest.raises(ValueError, match="Unsupported schema version"):
            load_schema({"version": 99, "root": "X", "structs": {}})

    def test_unknown_type(self):
        data = {
            "version": 1,
            "root": "Root",
            "structs": {
                "Root": {"fields": [{"index": 0, "name": "x", "type": "Nonexistent"}]}
            },
        }
        with pytest.raises(ValueError, match="Unknown type"):
            load_schema(data)

    def test_circular_dependency(self):
        data = {
            "version": 1,
            "root": "A",
            "structs": {
                "A": {"fields": [{"index": 0, "name": "b", "type": "B"}]},
                "B": {"fields": [{"index": 0, "name": "a", "type": "A"}]},
            },
        }
        with pytest.raises(ValueError, match="Circular"):
            load_schema(data)

    def test_missing_root(self):
        data = {
            "version": 1,
            "root": "Missing",
            "structs": {
                "Actual": {"fields": [{"index": 0, "name": "x", "type": "u8"}]}
            },
        }
        with pytest.raises(ValueError, match="not found"):
            load_schema(data)


# ---- Wire-format round-trip tests ----


class TestRoundTrip:
    """Dump a HashBuffer class to JSON, load it, and verify wire compatibility."""

    def test_simple_struct(self, store: BlockStore):
        schema = dump_schema(Inner)
        loaded = load_schema(schema)

        original = Inner(value=42)
        wire = original.encode(store)

        decoded = loaded.decode_root(wire, store)
        assert decoded["value"] == 42

    def test_nested_struct(self, store: BlockStore):
        schema = dump_schema(Outer)
        loaded = load_schema(schema)

        original = Outer(name="hello", inner=Inner(value=7), data=b"\x01\x02")
        wire = original.encode(store)

        decoded = loaded.decode_root(wire, store)
        assert decoded["name"] == "hello"
        assert decoded["inner"]["value"] == 7
        assert decoded["data"] == b"\x01\x02"

    def test_arrays(self, store: BlockStore):
        schema = dump_schema(WithArrays)
        loaded = load_schema(schema)

        original = WithArrays(
            ids=[1, 2, 3],
            vec3=[10, 20, 30],
            items=[Inner(value=1), Inner(value=2)],
            names=["foo", "bar"],
            blobs=[b"\x00", b"\xff"],
        )
        wire = original.encode(store)

        decoded = loaded.decode_root(wire, store)
        assert list(decoded["ids"]) == [1, 2, 3]
        assert list(decoded["vec3"]) == [10, 20, 30]
        assert decoded["items"][0]["value"] == 1
        assert decoded["items"][1]["value"] == 2
        assert list(decoded["names"]) == ["foo", "bar"]
        assert list(decoded["blobs"]) == [b"\x00", b"\xff"]

    def test_enum(self, store: BlockStore):
        schema = dump_schema(WithEnum)
        loaded = load_schema(schema)

        original = WithEnum(color=Color.GREEN, priority=Priority.HIGH)
        wire = original.encode(store)

        decoded = loaded.decode_root(wire, store)
        assert decoded["color"] == "GREEN"
        assert decoded["priority"] == "HIGH"

    def test_bool(self, store: BlockStore):
        schema = dump_schema(WithBool)
        loaded = load_schema(schema)

        original = WithBool(flag=True, count=42)
        wire = original.encode(store)

        decoded = loaded.decode_root(wire, store)
        assert decoded["flag"] is True
        assert decoded["count"] == 42

    def test_required_fields(self, store: BlockStore):
        schema = dump_schema(WithRequired)
        loaded = load_schema(schema)

        original = WithRequired(name=b"test", value=99)
        wire = original.encode(store)

        decoded = loaded.decode_root(wire, store)
        assert decoded["name"] == b"test"
        assert decoded["value"] == 99

    def test_full_json_roundtrip(self, store: BlockStore):
        """Dump to JSON string, load back, verify wire compat."""
        json_str = dump_schema_json(Outer, indent=2)
        loaded = load_schema_json(json_str)

        original = Outer(name="world", inner=Inner(value=255), data=b"abcd")
        wire = original.encode(store)

        decoded = loaded.decode_root(wire, store)
        assert decoded["name"] == "world"
        assert decoded["inner"]["value"] == 255
        assert decoded["data"] == b"abcd"
