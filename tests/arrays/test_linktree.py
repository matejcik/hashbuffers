"""Unit tests for LinkTree — __len__, find_leaf, collect_leaves.

LinkTree is tested in isolation using manually constructed block trees.
"""

import pytest

from hashbuffers.arrays import LinkTree, limits_to_cumulative
from hashbuffers.codec import Block, DataBlock, Link, LinksBlock
from hashbuffers.store import BlockStore


@pytest.fixture
def store() -> BlockStore:
    return BlockStore(b"test-key")


def data_leaf(data: bytes) -> DataBlock:
    return DataBlock.build(data)


def data_leaf_length(block: Block) -> int:
    if not isinstance(block, DataBlock):
        raise ValueError(f"Expected DataBlock, got {type(block).__name__}")
    return len(block.get_data())


def assert_leaf_data(block: Block, expected: bytes) -> None:
    assert isinstance(block, DataBlock)
    assert bytes(block.get_data()) == expected


def build_links_block(store: BlockStore, leaves: list[DataBlock]) -> LinksBlock:
    """Build a LinksBlock from leaf DataBlocks, using byte lengths as limits."""
    links = []
    for leaf in leaves:
        digest = store.store(leaf)
        links.append(Link(digest, len(leaf.get_data())))
    return LinksBlock.build(limits_to_cumulative(links))


def build_two_level_tree(
    store: BlockStore, leaf_groups: list[list[DataBlock]]
) -> LinksBlock:
    """Build a 2-level tree: root LinksBlock -> inner LinksBlocks -> leaf DataBlocks."""
    inner_links: list[Link] = []
    for group in leaf_groups:
        inner = build_links_block(store, group)
        digest = store.store(inner)
        total = sum(len(leaf.get_data()) for leaf in group)
        inner_links.append(Link(digest, total))
    return LinksBlock.build(limits_to_cumulative(inner_links))


class TestLinkTreeLen:
    def test_leaf_root(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store, data_leaf_length)
        assert len(tree) == 5

    def test_links_root(self, store: BlockStore) -> None:
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        root = build_links_block(store, [leaf1, leaf2])
        tree = LinkTree(root, store, data_leaf_length)
        assert len(tree) == 5


class TestLinkTreeFindLeaf:
    def test_short_circuit_non_links(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store, data_leaf_length)
        idx, block = tree.find_leaf(2)
        assert idx == 2
        assert block is leaf

    def test_single_level_descent(self, store: BlockStore) -> None:
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        root = build_links_block(store, [leaf1, leaf2])
        tree = LinkTree(root, store, data_leaf_length)

        # Index 0 should land in leaf1
        idx, block = tree.find_leaf(0)
        assert idx == 0
        assert_leaf_data(block, b"abc")

        # Index 2 should also land in leaf1 (last element)
        idx, block = tree.find_leaf(2)
        assert idx == 2
        assert_leaf_data(block, b"abc")

    def test_single_level_boundary(self, store: BlockStore) -> None:
        """Index exactly at a cumulative limit boundary should go to the next child."""
        leaf1 = data_leaf(b"abc")  # elements [0, 3)
        leaf2 = data_leaf(b"de")  # elements [3, 5)
        root = build_links_block(store, [leaf1, leaf2])
        tree = LinkTree(root, store, data_leaf_length)

        # Index 3 should land in leaf2 (relative index 0 within leaf2)
        idx, block = tree.find_leaf(3)
        assert idx == 0
        assert_leaf_data(block, b"de")

    def test_two_level_descent(self, store: BlockStore) -> None:
        leaves1 = [data_leaf(b"ab"), data_leaf(b"cd")]
        leaves2 = [data_leaf(b"efg")]
        root = build_two_level_tree(store, [leaves1, leaves2])
        tree = LinkTree(root, store, data_leaf_length)

        # Total: 2+2+3 = 7 bytes
        assert len(tree) == 7

        # Index 0 → first leaf of first group
        idx, block = tree.find_leaf(0)
        assert idx == 0
        assert_leaf_data(block, b"ab")

        # Index 3 → second leaf of first group (relative index 1)
        idx, block = tree.find_leaf(3)
        assert idx == 1
        assert_leaf_data(block, b"cd")

    def test_two_level_boundary(self, store: BlockStore) -> None:
        """Index at the boundary between two inner groups."""
        leaves1 = [data_leaf(b"ab"), data_leaf(b"cd")]  # 4 elements total
        leaves2 = [data_leaf(b"efg")]  # 3 elements
        root = build_two_level_tree(store, [leaves1, leaves2])
        tree = LinkTree(root, store, data_leaf_length)

        # Index 4 → first element of second group
        idx, block = tree.find_leaf(4)
        assert idx == 0
        assert_leaf_data(block, b"efg")

    def test_inner_size_mismatch_raises(self, store: BlockStore) -> None:
        """When a child LinksBlock's actual cumulative limit doesn't match the parent's expectation."""
        leaf = data_leaf(b"abc")
        digest = store.store(leaf)
        inner = LinksBlock.build([Link(digest, 3)])
        inner_digest = store.store(inner)
        root = LinksBlock.build([Link(inner_digest, 999)])
        tree = LinkTree(root, store, data_leaf_length)
        with pytest.raises(ValueError, match="Expected 999 elements, got 3"):
            tree.find_leaf(0)

    def test_leaf_size_mismatch_raises(self, store: BlockStore) -> None:
        """When the leaf's actual length doesn't match what the parent link promised."""
        leaf = data_leaf(b"abc")  # actual length = 3
        digest = store.store(leaf)
        root = LinksBlock.build([Link(digest, 10)])
        tree = LinkTree(root, store, data_leaf_length)
        with pytest.raises(ValueError, match="Expected 10 elements, got 3"):
            tree.find_leaf(0)


class TestLinkTreeCollectLeaves:
    def test_non_links_root(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store, data_leaf_length)
        start, leaves = tree.collect_leaves()
        assert len(leaves) == 1
        assert leaves[0] is leaf

    def test_empty_range(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store, data_leaf_length)
        start, leaves = tree.collect_leaves(slice(3, 3))
        assert leaves == []

    def test_step_not_supported(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store, data_leaf_length)
        with pytest.raises(NotImplementedError, match="Step"):
            tree.collect_leaves(slice(0, 5, 2))

    def test_all_leaves_from_links(self, store: BlockStore) -> None:
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        root = build_links_block(store, [leaf1, leaf2])
        tree = LinkTree(root, store, data_leaf_length)
        start, leaves = tree.collect_leaves()
        assert start == 0
        assert len(leaves) == 2

    def test_partial_overlap(self, store: BlockStore) -> None:
        """Only collect leaves overlapping the requested range."""
        leaf1 = data_leaf(b"abc")  # elements 0-2
        leaf2 = data_leaf(b"de")  # elements 3-4
        leaf3 = data_leaf(b"fgh")  # elements 5-7
        root = build_links_block(store, [leaf1, leaf2, leaf3])
        tree = LinkTree(root, store, data_leaf_length)
        # Request only elements 3-4 (should only return leaf2)
        start, leaves = tree.collect_leaves(slice(3, 5))
        assert len(leaves) == 1
        assert isinstance(leaves[0], DataBlock)
        assert bytes(leaves[0].get_data()) == b"de"
        assert start == 3

    def test_multi_level_dfs(self, store: BlockStore) -> None:
        leaves1 = [data_leaf(b"ab"), data_leaf(b"cd")]
        leaves2 = [data_leaf(b"efg")]
        root = build_two_level_tree(store, [leaves1, leaves2])
        tree = LinkTree(root, store, data_leaf_length)
        start, leaves = tree.collect_leaves()
        assert start == 0
        assert len(leaves) == 3

    def test_inner_size_mismatch_raises(self, store: BlockStore) -> None:
        leaf = data_leaf(b"abc")
        digest = store.store(leaf)
        inner = LinksBlock.build([Link(digest, 3)])
        inner_digest = store.store(inner)
        root = LinksBlock.build([Link(inner_digest, 999)])
        tree = LinkTree(root, store, data_leaf_length)
        with pytest.raises(ValueError, match="Expected 999 elements, got 3"):
            tree.collect_leaves()

    def test_leaf_size_mismatch_in_dfs_raises(self, store: BlockStore) -> None:
        """Leaf size mismatch caught during DFS collection."""
        leaf = data_leaf(b"abc")  # actual = 3
        digest = store.store(leaf)
        root = LinksBlock.build([Link(digest, 10)])  # claims 10
        tree = LinkTree(root, store, data_leaf_length)
        with pytest.raises(ValueError, match="Expected 10 elements, got 3"):
            tree.collect_leaves()
