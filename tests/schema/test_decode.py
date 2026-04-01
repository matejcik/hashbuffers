"""Tests for decode error paths and edge cases."""

import pytest

from hashbuffers.codec import (
    DataBlock,
    Link,
    SlotsBlock,
    TableBlock,
    VTableEntry,
    VTableEntryType,
)
from hashbuffers.store import StoredBlock

from .conftest import (
    ArrayStruct,
    Inner,
    Outer,
    RequiredStruct,
    SimpleStruct,
)


class TestDecodeErrors:
    def test_wrong_block_type(self, store):
        """Feed a DATA block where a TABLE is expected."""
        data_block = DataBlock.build(b"not a table").encode()
        with pytest.raises(ValueError, match="Expected .* block, got"):
            SimpleStruct.decode(data_block, store)

    def test_truncated_block(self, store):
        """Truncated block data should fail."""
        obj = SimpleStruct(x=42, y=1)
        sb = obj.encode(store)
        with pytest.raises((ValueError, IOError)):
            SimpleStruct.decode(sb.data[:4], store)

    def test_reserved_entry_type(self, store):
        """TABLE with a reserved entry type (0b010) should be rejected."""
        valid = TableBlock.build([VTableEntry(VTableEntryType.NULL, 0)], b"")
        encoded = bytearray(valid.encode())
        # Mutate entry at bytes 4-5 to reserved type 0b010
        entry_val = (0b010 << 13) | 0
        encoded[4:6] = entry_val.to_bytes(2, "little")
        with pytest.raises(ValueError):
            SimpleStruct.decode(bytes(encoded), store)

    def test_link_with_missing_store_block(self, store):
        """LINK pointing to a digest not in the store should raise KeyError."""
        fake_link = Link(b"\xaa" * 32, 1)
        link_bytes = fake_link.encode()
        vtable = [
            VTableEntry(VTableEntryType.LINK, 8),
            VTableEntry(VTableEntryType.NULL, 0),
        ]
        table = TableBlock.build(vtable, link_bytes)
        encoded = table.encode()
        with pytest.raises(KeyError):
            Outer.decode(encoded, store)

    def test_corrupted_nested_block(self, store):
        """Nested BLOCK with garbage data should fail validation."""
        garbage = b"\xff" * 20
        vtable = [VTableEntry(VTableEntryType.BLOCK, 6)]
        table = TableBlock.build(vtable, garbage)
        encoded = table._encode_without_validation()
        with pytest.raises((ValueError, IOError)):
            Outer.decode(encoded, store)

    def test_slots_where_data_expected(self, store):
        """A SLOTS block where a DATA array is expected should fail."""
        slots = SlotsBlock.build_slots([b"wrong"]).encode()
        vtable = [VTableEntry(VTableEntryType.BLOCK, 6)]
        table = TableBlock.build(vtable, slots)
        encoded = table.encode()
        with pytest.raises(ValueError, match="Expected DATA leaf"):
            ArrayStruct.decode(encoded, store)

    def test_required_field_null_in_table(self, store):
        """Required field explicitly NULL should raise."""
        vtable = [
            VTableEntry(VTableEntryType.NULL, 0),  # name (required)
            VTableEntry(VTableEntryType.INLINE, 42),  # value
        ]
        table = TableBlock.build(vtable, b"")
        encoded = table.encode()
        with pytest.raises(ValueError, match="Required field"):
            RequiredStruct.decode(encoded, store)

    def test_required_field_missing_from_short_table(self, store):
        """Required field missing from a short TABLE should raise."""
        vtable = []
        table = TableBlock.build(vtable, b"")
        encoded = table.encode()
        with pytest.raises(ValueError, match="Required field"):
            RequiredStruct.decode(encoded, store)


class TestDecodeForwardCompat:
    def test_extra_fields_ignored(self, store):
        """TABLE with more entries than schema expects should still decode."""
        vtable = [
            VTableEntry(VTableEntryType.INLINE, 42),  # x
            VTableEntry(VTableEntryType.INLINE, 7),  # y
            VTableEntry(VTableEntryType.NULL, 0),
            VTableEntry(VTableEntryType.NULL, 0),
            VTableEntry(VTableEntryType.NULL, 0),
        ]
        table = TableBlock.build(vtable, b"")
        decoded = SimpleStruct.decode(table.encode(), store)
        assert decoded.x == 42
        assert decoded.y == 7

    def test_shorter_table_than_schema(self, store):
        """TABLE shorter than schema: missing fields become None."""
        vtable = [VTableEntry(VTableEntryType.INLINE, 42)]
        table = TableBlock.build(vtable, b"")
        decoded = SimpleStruct.decode(table.encode(), store)
        assert decoded.x == 42
        assert decoded.y is None


class TestStoreIntegrity:
    def test_tampered_block_in_store(self, store):
        """If a stored block is tampered with, retrieval should fail."""
        inner = Inner(value=5)
        sb = inner.encode(store)
        tampered = StoredBlock(b"\x00" * len(sb.data), sb.link, sb.alignment)
        store._blocks[sb.link.digest] = tampered
        with pytest.raises(ValueError, match="HMAC verification failed"):
            store[sb.link.digest]
