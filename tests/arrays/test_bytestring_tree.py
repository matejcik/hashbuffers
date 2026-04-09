"""Unit tests for BytestringTree — leaf_length type checks.

Build+decode roundtrip tests are in test_build.py::TestBuildBytestringTree.
"""

import pytest

from hashbuffers.arrays import BytestringTree
from hashbuffers.codec import DataBlock, SlotsBlock


class TestBytestringTreeLeafLength:
    def test_data_block(self):
        block = DataBlock.build(b"hello")
        assert BytestringTree.leaf_length(block) == 5

    def test_rejects_non_data_block(self):
        block = SlotsBlock.build_slots([b"a"])
        with pytest.raises(ValueError, match="DataBlock"):
            BytestringTree.leaf_length(block)
