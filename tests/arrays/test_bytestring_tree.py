"""Unit tests for BytestringTree — leaf_length, to_bytes."""

import pytest

from hashbuffers.arrays import BytestringTree, build_bytestring_tree
from hashbuffers.codec import SIZE_MAX, DataBlock, SlotsBlock
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


class TestBytestringTreeLeafLength:
    def test_data_block(self):
        block = DataBlock.build(b"hello")
        assert BytestringTree.leaf_length(block) == 5

    def test_rejects_non_data_block(self):
        block = SlotsBlock.build_slots([b"a"])
        with pytest.raises(ValueError, match="DataBlock"):
            BytestringTree.leaf_length(block)


class TestBytestringTreeToBytes:
    def test_empty(self, store):
        entry = build_bytestring_tree(b"", store)
        tree = BytestringTree(entry.block, store)
        assert tree.to_bytes() == b""

    def test_small(self, store):
        entry = build_bytestring_tree(b"hello world", store)
        tree = BytestringTree(entry.block, store)
        assert tree.to_bytes() == b"hello world"

    def test_large_spanning_blocks(self, store):
        data = b"x" * (SIZE_MAX * 2)
        entry = build_bytestring_tree(data, store)
        tree = BytestringTree(entry.block, store)
        assert tree.to_bytes() == data
