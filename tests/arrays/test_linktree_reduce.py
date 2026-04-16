"""Unit tests for linktree_reduce — empty, single, multi, tail optimization."""

import pytest

from hashbuffers.arrays import linktree_reduce
from hashbuffers.codec import SIZE_MAX, DataBlock, LinksBlock
from hashbuffers.fitting import BlockEntry
from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")


def make_leaf_block(data: bytes = b"x") -> DataBlock:
    return DataBlock.build(data, elem_size=1, elem_align=1)


class TestLinktreeReduce:
    def test_empty_raises(self, store):
        with pytest.raises(ValueError, match="empty"):
            linktree_reduce([], store)

    def test_single_passthrough(self, store):
        block = make_leaf_block()
        result = linktree_reduce([block], store)
        assert result is block

    def test_two_elements(self, store):
        blocks = [make_leaf_block(b"a"), make_leaf_block(b"b")]
        result = linktree_reduce(blocks, store)
        assert isinstance(result, LinksBlock)

    def test_many_elements(self, store):
        blocks = [make_leaf_block(b"x") for _ in range(50)]
        result = linktree_reduce(blocks, store)
        assert isinstance(result, LinksBlock)
        assert result.element_count() == 50

    def test_tail_optimization(self, store):
        """More than max_links_per_block blocks triggers tail split + recursive call."""
        max_links = (SIZE_MAX - 4) // 36  # 227
        # Create max_links + 1 entries to trigger the tail path
        blocks = [make_leaf_block(b"x") for _ in range(max_links + 1)]
        result = linktree_reduce(blocks, store)
        assert isinstance(result, LinksBlock)
        assert result.element_count() == max_links + 1
