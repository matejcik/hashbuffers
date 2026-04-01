"""Shared fixtures and schema classes for schema tests."""

import pytest

from hashbuffers.schema import (
    I16,
    U8,
    U16,
    U32,
    Array,
    Bytes,
    Field,
    HashBuffer,
)
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


# --- Reusable schema classes ---


class SimpleStruct(HashBuffer):
    x: int | None = Field(0, U32)
    y: int | None = Field(1, I16)


class Inner(HashBuffer):
    value: int | None = Field(0, U8)


class Outer(HashBuffer):
    name: bytes | None = Field(0, Bytes)
    inner: Inner | None = Field(1, Inner)


class Item(HashBuffer):
    id: int | None = Field(0, U16)
    data: bytes | None = Field(1, Bytes)


class Container(HashBuffer):
    items: list[Item] | None = Field(0, Array(Item))


class RequiredStruct(HashBuffer):
    name: bytes = Field(0, Bytes, required=True)
    value: int = Field(1, U32, required=True)


class ArrayStruct(HashBuffer):
    values: list[int] | None = Field(0, Array(U32))


class BlobStruct(HashBuffer):
    data: bytes | None = Field(0, Bytes)


class StringsStruct(HashBuffer):
    strings: list[bytes] | None = Field(0, Array(Bytes))


class AllOptional(HashBuffer):
    a: int | None = Field(0, U32)
    b: bytes | None = Field(1, Bytes)
    c: list[int] | None = Field(2, Array(U8))
    d: list[bytes] | None = Field(3, Array(Bytes))


Vec3 = Array(U32, count=3)
