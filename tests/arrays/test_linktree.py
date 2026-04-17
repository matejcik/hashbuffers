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
    return DataBlock.build(data, elem_size=1, elem_align=1)


def assert_leaf_data(block: Block, expected: bytes) -> None:
    assert isinstance(block, DataBlock)
    assert bytes(block.data) == expected


def build_links_block(
    store: BlockStore, leaves: list[DataBlock], depth: int = 0
) -> LinksBlock:
    """Build a LinksBlock from leaf DataBlocks, using byte lengths as limits."""
    links = []
    for leaf in leaves:
        digest = store.store(leaf)
        links.append(Link(digest, len(leaf.data)))
    return LinksBlock.build(limits_to_cumulative(links), depth=depth)


def build_two_level_tree(
    store: BlockStore, leaf_groups: list[list[DataBlock]]
) -> LinksBlock:
    """Build a 2-level tree: root LinksBlock -> inner LinksBlocks -> leaf DataBlocks."""
    inner_links: list[Link] = []
    for group in leaf_groups:
        inner = build_links_block(store, group, depth=0)
        digest = store.store(inner)
        total = sum(len(leaf.data) for leaf in group)
        inner_links.append(Link(digest, total))
    return LinksBlock.build(limits_to_cumulative(inner_links), depth=1)


class TestLinkTreeLen:
    def test_leaf_root(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store)
        assert len(tree) == 5

    def test_links_root(self, store: BlockStore) -> None:
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        root = build_links_block(store, [leaf1, leaf2])
        tree = LinkTree(root, store)
        assert len(tree) == 5


class TestLinkTreeFindLeaf:
    def test_short_circuit_non_links(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store)
        idx, block = tree.find_leaf(2)
        assert idx == 2
        assert block is leaf

    def test_single_level_descent(self, store: BlockStore) -> None:
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        root = build_links_block(store, [leaf1, leaf2])
        tree = LinkTree(root, store)

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
        tree = LinkTree(root, store)

        # Index 3 should land in leaf2 (relative index 0 within leaf2)
        idx, block = tree.find_leaf(3)
        assert idx == 0
        assert_leaf_data(block, b"de")

    def test_two_level_descent(self, store: BlockStore) -> None:
        leaves1 = [data_leaf(b"ab"), data_leaf(b"cd")]
        leaves2 = [data_leaf(b"efg"), data_leaf(b"hi")]
        root = build_two_level_tree(store, [leaves1, leaves2])
        tree = LinkTree(root, store)

        # Total: 2+2+3+2 = 9 bytes
        assert len(tree) == 9

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
        leaves2 = [data_leaf(b"efg"), data_leaf(b"hi")]  # 5 elements
        root = build_two_level_tree(store, [leaves1, leaves2])
        tree = LinkTree(root, store)

        # Index 4 → first element of second group
        idx, block = tree.find_leaf(4)
        assert idx == 0
        assert_leaf_data(block, b"efg")

    def test_inner_size_mismatch_raises(self, store: BlockStore) -> None:
        """When a child LinksBlock's actual cumulative limit doesn't match the parent's expectation."""
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        inner = build_links_block(store, [leaf1, leaf2], depth=0)
        inner_digest = store.store(inner)
        dummy = data_leaf(b"x")
        dummy_digest = store.store(dummy)
        root = LinksBlock.build(
            [Link(inner_digest, 999), Link(dummy_digest, 1000)], depth=1
        )
        tree = LinkTree(root, store)
        with pytest.raises(ValueError, match="Expected 999 elements, got 5"):
            tree.find_leaf(0)

    def test_leaf_size_mismatch_raises(self, store: BlockStore) -> None:
        """When the leaf's actual length doesn't match what the parent link promised."""
        leaf1 = data_leaf(b"abc")  # actual length = 3
        leaf2 = data_leaf(b"de")
        d1 = store.store(leaf1)
        d2 = store.store(leaf2)
        root = LinksBlock.build([Link(d1, 10), Link(d2, 12)])
        tree = LinkTree(root, store)
        with pytest.raises(ValueError, match="Expected 10 elements, got 3"):
            tree.find_leaf(0)


class TestLinkTreeCollectLeaves:
    def test_non_links_root(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store)
        start, leaves = tree.collect_leaves()
        assert len(leaves) == 1
        assert leaves[0] is leaf

    def test_empty_range(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store)
        start, leaves = tree.collect_leaves(slice(3, 3))
        assert leaves == []

    def test_step_not_supported(self, store: BlockStore) -> None:
        leaf = data_leaf(b"hello")
        tree = LinkTree(leaf, store)
        with pytest.raises(NotImplementedError, match="Step"):
            tree.collect_leaves(slice(0, 5, 2))

    def test_all_leaves_from_links(self, store: BlockStore) -> None:
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        root = build_links_block(store, [leaf1, leaf2])
        tree = LinkTree(root, store)
        start, leaves = tree.collect_leaves()
        assert start == 0
        assert len(leaves) == 2

    def test_partial_overlap(self, store: BlockStore) -> None:
        """Only collect leaves overlapping the requested range."""
        leaf1 = data_leaf(b"abc")  # elements 0-2
        leaf2 = data_leaf(b"de")  # elements 3-4
        leaf3 = data_leaf(b"fgh")  # elements 5-7
        root = build_links_block(store, [leaf1, leaf2, leaf3])
        tree = LinkTree(root, store)
        # Request only elements 3-4 (should only return leaf2)
        start, leaves = tree.collect_leaves(slice(3, 5))
        assert len(leaves) == 1
        assert isinstance(leaves[0], DataBlock)
        assert bytes(leaves[0].data) == b"de"
        assert start == 3

    def test_multi_level_dfs(self, store: BlockStore) -> None:
        leaves1 = [data_leaf(b"ab"), data_leaf(b"cd")]
        leaves2 = [data_leaf(b"efg"), data_leaf(b"hi")]
        root = build_two_level_tree(store, [leaves1, leaves2])
        tree = LinkTree(root, store)
        start, leaves = tree.collect_leaves()
        assert start == 0
        assert len(leaves) == 4

    def test_inner_size_mismatch_raises(self, store: BlockStore) -> None:
        leaf1 = data_leaf(b"abc")
        leaf2 = data_leaf(b"de")
        inner = build_links_block(store, [leaf1, leaf2], depth=0)
        inner_digest = store.store(inner)
        dummy = data_leaf(b"x")
        dummy_digest = store.store(dummy)
        root = LinksBlock.build(
            [Link(inner_digest, 999), Link(dummy_digest, 1000)], depth=1
        )
        tree = LinkTree(root, store)
        with pytest.raises(ValueError, match="Expected 999 elements, got 5"):
            tree.collect_leaves()

    def test_leaf_size_mismatch_in_dfs_raises(self, store: BlockStore) -> None:
        """Leaf size mismatch caught during DFS collection."""
        leaf1 = data_leaf(b"abc")  # actual = 3
        leaf2 = data_leaf(b"de")
        d1 = store.store(leaf1)
        d2 = store.store(leaf2)
        root = LinksBlock.build([Link(d1, 10), Link(d2, 12)])  # claims 10+2
        tree = LinkTree(root, store)
        with pytest.raises(ValueError, match="Expected 10 elements, got 3"):
            tree.collect_leaves()


class TestDepthValidation:
    """Depth countdown is verified during traversal."""

    def test_find_leaf_rejects_depth_not_decreasing(self, store: BlockStore) -> None:
        """Child LINKS depth must be strictly less than parent's."""
        leaf1 = data_leaf(b"ab")
        leaf2 = data_leaf(b"cd")
        # inner block claims depth=1 (should be 0 for leaf children)
        inner = build_links_block(store, [leaf1, leaf2], depth=1)
        inner_digest = store.store(inner)
        dummy = data_leaf(b"x")
        dummy_digest = store.store(dummy)
        # root has depth=1 — same as child, violating strict decrease
        root = LinksBlock.build([Link(inner_digest, 4), Link(dummy_digest, 5)], depth=1)
        tree = LinkTree(root, store)
        with pytest.raises(ValueError, match="not less than"):
            tree.find_leaf(0)

    def test_collect_leaves_rejects_depth_not_decreasing(
        self, store: BlockStore
    ) -> None:
        leaf1 = data_leaf(b"ab")
        leaf2 = data_leaf(b"cd")
        inner = build_links_block(store, [leaf1, leaf2], depth=1)
        inner_digest = store.store(inner)
        dummy = data_leaf(b"x")
        dummy_digest = store.store(dummy)
        root = LinksBlock.build([Link(inner_digest, 4), Link(dummy_digest, 5)], depth=1)
        tree = LinkTree(root, store)
        with pytest.raises(ValueError, match="not less than"):
            tree.collect_leaves()

    def test_valid_depth_countdown(self, store: BlockStore) -> None:
        """Properly decreasing depth traverses without error."""
        leaves1 = [data_leaf(b"ab"), data_leaf(b"cd")]
        leaves2 = [data_leaf(b"efg"), data_leaf(b"hi")]
        root = build_two_level_tree(store, [leaves1, leaves2])
        assert root.depth == 1
        tree = LinkTree(root, store)
        # Should work fine
        idx, block = tree.find_leaf(0)
        assert idx == 0
        assert_leaf_data(block, b"ab")
