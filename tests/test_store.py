"""Unit tests for hashbuffers.store — BlockStore."""

import pytest

from hashbuffers.codec import DataBlock
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestBlockStore:
    def test_store_and_fetch_roundtrip(self, store):
        block = DataBlock.build(b"hello")
        digest = store.store(block)
        fetched = store.fetch(digest)
        assert isinstance(fetched, DataBlock)
        assert fetched.get_data() == b"hello"

    def test_fetch_nonexistent_raises(self, store):
        with pytest.raises(KeyError):
            store.fetch(b"\x00" * 32)

    def test_contains_true_after_store(self, store):
        block = DataBlock.build(b"data")
        digest = store.store(block)
        assert digest in store

    def test_contains_false_before_store(self, store):
        assert b"\x00" * 32 not in store

    def test_len_empty(self, store):
        assert len(store) == 0

    def test_len_after_stores(self, store):
        block1 = DataBlock.build(b"one")
        block2 = DataBlock.build(b"two")
        store.store(block1)
        store.store(block2)
        assert len(store) == 2

    def test_fetch_corrupted_raises(self, store):
        block = DataBlock.build(b"data")
        digest = store.store(block)
        # Tamper with stored data
        store.blocks[digest] = b"\xff" * len(store.blocks[digest])
        with pytest.raises(ValueError, match="HMAC verification failed"):
            store.fetch(digest)
