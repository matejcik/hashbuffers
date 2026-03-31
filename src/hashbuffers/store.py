"""Content-addressed block storage with HMAC-SHA256."""

import hashlib
import hmac
from typing import NamedTuple

from .codec import Link


class StoredBlock(NamedTuple):
    """A stored block with its link and alignment.

    Universal return type for all block-building operations.
    - data: raw encoded block bytes (for transmission or embedding)
    - link: Link(digest, limit) for content addressing and parent references
    - alignment: block alignment requirement (for fitting into parent TABLE)
    """

    data: bytes
    link: Link
    alignment: int


class BlockStore:
    """Content-addressed block store using HMAC-SHA256.

    Stores blocks and retrieves them by digest, verifying integrity on read.
    """

    def __init__(self, key: bytes) -> None:
        self.key = key
        self._blocks: dict[bytes, StoredBlock] = {}

    def store(
        self, block_data: bytes, *, limit: int, alignment: int = 2
    ) -> StoredBlock:
        """Store a block, return StoredBlock with digest and metadata."""
        digest = hmac.new(self.key, block_data, hashlib.sha256).digest()
        sb = StoredBlock(block_data, Link(digest, limit), alignment)
        self._blocks[digest] = sb
        return sb

    def __getitem__(self, digest: bytes) -> StoredBlock:
        """Retrieve a block by digest. Verifies HMAC on retrieval."""
        sb = self._blocks[digest]
        expected = hmac.new(self.key, sb.data, hashlib.sha256).digest()
        if not hmac.compare_digest(digest, expected):
            raise ValueError("HMAC verification failed")
        return sb

    def __contains__(self, digest: bytes) -> bool:
        return digest in self._blocks

    def __len__(self) -> int:
        return len(self._blocks)
