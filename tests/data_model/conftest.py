"""Shared fixtures for data_model tests."""

import pytest

from hashbuffers.store import BlockStore


@pytest.fixture
def store():
    return BlockStore(b"test-key")
