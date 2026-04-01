"""Tests for BlockStore and StoredBlock."""

import hashlib
import hmac

import pytest

from hashbuffers.codec import DataBlock, Link
from hashbuffers.store import BlockStore, StoredBlock


def test_store_and_retrieve():
    key = b"test-key-32-bytes-padding-needed"
    store = BlockStore(key)
    block = DataBlock.build(b"hello").encode()
    sb = store.store(block, limit=1)

    assert sb.data == block
    assert sb.alignment == 2
    assert sb.link.limit == 1
    assert sb.link.digest == hmac.new(key, block, hashlib.sha256).digest()

    retrieved = store[sb.link.digest]
    assert retrieved == sb


def test_store_custom_alignment():
    store = BlockStore(b"key")
    block = DataBlock.build(b"x", align=4).encode()
    sb = store.store(block, limit=1, alignment=4)
    assert sb.alignment == 4


def test_getitem_missing_raises():
    store = BlockStore(b"key")
    with pytest.raises(KeyError):
        store[b"\x00" * 32]


def test_contains():
    store = BlockStore(b"key")
    block = DataBlock.build(b"x").encode()
    sb = store.store(block, limit=1)
    assert sb.link.digest in store
    assert b"\x00" * 32 not in store


def test_len():
    store = BlockStore(b"key")
    assert len(store) == 0
    block = DataBlock.build(b"x").encode()
    store.store(block, limit=1)
    assert len(store) == 1
