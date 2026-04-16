"""Tests for lazy LINK resolution on descriptor-backed fields."""

from hashbuffers.codec import Link, TableBlock
from hashbuffers.codec.table import TableEntryRaw, TableEntryType

from .conftest import Inner, Outer


def _make_outer_with_inner_link(store) -> bytes:
    inner = Inner(value=7)
    inner_table = TableBlock.decode(inner.encode(store))
    digest = store.store(inner_table)
    link = Link(digest, 1)
    vtable = [
        TableEntryRaw(TableEntryType.NULL, 0),
        TableEntryRaw(TableEntryType.LINK, 8),
    ]
    table = TableBlock.build(vtable, link.encode())
    return table.encode()


def test_link_field_not_fetched_until_access(store):
    encoded = _make_outer_with_inner_link(store)
    fetch_calls = 0
    original_fetch = store.fetch

    def counted_fetch(digest: bytes):
        nonlocal fetch_calls
        fetch_calls += 1
        return original_fetch(digest)

    store.fetch = counted_fetch  # type: ignore[method-assign]
    decoded = Outer.decode(encoded, store)
    assert fetch_calls == 0
    assert decoded.inner is not None
    assert decoded.inner.value == 7
    assert fetch_calls == 1


def test_link_field_cached_after_first_access(store):
    encoded = _make_outer_with_inner_link(store)
    fetch_calls = 0
    original_fetch = store.fetch

    def counted_fetch(digest: bytes):
        nonlocal fetch_calls
        fetch_calls += 1
        return original_fetch(digest)

    store.fetch = counted_fetch  # type: ignore[method-assign]
    decoded = Outer.decode(encoded, store)
    first = decoded.inner
    second = decoded.inner
    assert first is not None
    assert second is not None
    assert first.value == 7
    assert second.value == 7
    assert fetch_calls == 1
