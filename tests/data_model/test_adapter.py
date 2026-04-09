"""Unit tests for hashbuffers.data_model.adapter — AdapterCodec."""

from hashbuffers.data_model.adapter import AdapterCodec


class TestAdapterCodec:
    def test_identity_passthrough(self):
        adapter = AdapterCodec.identity()
        assert adapter.encode(42) == 42
        assert adapter.decode(42) == 42

    def test_custom_adapter(self):
        adapter = AdapterCodec(encode=str.upper, decode=str.lower)
        assert adapter.encode("hello") == "HELLO"
        assert adapter.decode("HELLO") == "hello"

    def test_roundtrip(self):
        adapter = AdapterCodec(encode=lambda x: x * 2, decode=lambda x: x // 2)
        assert adapter.decode(adapter.encode(5)) == 5
