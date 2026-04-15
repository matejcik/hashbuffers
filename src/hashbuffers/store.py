"""Content-addressed block storage with HMAC-SHA256."""

import hashlib
import hmac

from .codec import Block, decode_block


class BlockStore:
    """Content-addressed block store using HMAC-SHA256.

    Stores blocks and retrieves them by digest, verifying integrity on read.
    """

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.blocks: dict[bytes, bytes] = {}

    def store_bytes(self, block_data: bytes) -> bytes:
        """Store raw block bytes, return digest."""
        digest = hmac.new(self.key, block_data, hashlib.sha256).digest()
        self.blocks[digest] = block_data
        return digest

    def store(self, block: Block) -> bytes:
        """Store a block, return digest."""
        return self.store_bytes(block.encode())

    def fetch(self, digest: bytes) -> Block:
        block_data = self.blocks.get(digest)
        if block_data is None:
            raise KeyError(f"Block {digest} not found in store")
        expected = hmac.new(self.key, block_data, hashlib.sha256).digest()
        if not hmac.compare_digest(digest, expected):
            raise ValueError("HMAC verification failed")
        return decode_block(block_data)

    def __contains__(self, digest: bytes) -> bool:
        return digest in self.blocks

    def __len__(self) -> int:
        return len(self.blocks)
