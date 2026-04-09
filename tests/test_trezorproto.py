"""Tests for trezorproto bridge: protobuf MessageType <-> Hashbuffers."""

import pytest
from trezorlib import messages

from hashbuffers.store import BlockStore
from hashbuffers.trezorproto import deserialize, serialize


@pytest.fixture
def store():
    return BlockStore(b"test-key-trezorproto")


class TestSimpleMessages:
    def test_success_roundtrip(self, store):
        msg = messages.Success(message="Operation successful")
        data = serialize(msg, store)
        decoded = deserialize(messages.Success, data, store)
        assert decoded.message == "Operation successful"

    def test_success_default(self, store):
        msg = messages.Success()
        data = serialize(msg, store)
        decoded = deserialize(messages.Success, data, store)
        assert decoded.message == ""

    def test_failure_roundtrip(self, store):
        msg = messages.Failure(
            code=messages.FailureType.DataError, message="Data error"
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.Failure, data, store)
        assert decoded.code == messages.FailureType.DataError
        assert decoded.message == "Data error"

    def test_failure_null_fields(self, store):
        msg = messages.Failure()
        data = serialize(msg, store)
        decoded = deserialize(messages.Failure, data, store)
        assert decoded.code is None
        assert decoded.message is None


class TestBytesFields:
    def test_bytes_roundtrip(self, store):
        msg = messages.Entropy(entropy=b"\xde\xad\xbe\xef" * 8)
        data = serialize(msg, store)
        decoded = deserialize(messages.Entropy, data, store)
        assert decoded.entropy == b"\xde\xad\xbe\xef" * 8

    def test_empty_bytes(self, store):
        msg = messages.Entropy(entropy=b"")
        data = serialize(msg, store)
        decoded = deserialize(messages.Entropy, data, store)
        assert decoded.entropy == b""


class TestEnumFields:
    def test_enum_roundtrip(self, store):
        msg = messages.Failure(code=messages.FailureType.ActionCancelled)
        data = serialize(msg, store)
        decoded = deserialize(messages.Failure, data, store)
        assert decoded.code == messages.FailureType.ActionCancelled

    def test_enum_zero_value(self, store):
        """Enum with value 0 should round-trip (not be confused with NULL)."""
        msg = messages.SignTx(
            outputs_count=1,
            inputs_count=1,
            amount_unit=messages.AmountUnit.BITCOIN,  # value 0
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.SignTx, data, store)
        assert decoded.amount_unit == messages.AmountUnit.BITCOIN


class TestBoolFields:
    def test_bool_roundtrip(self, store):
        msg = messages.SignTx(
            outputs_count=1,
            inputs_count=1,
            serialize=False,
            decred_staking_ticket=True,
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.SignTx, data, store)
        assert decoded.serialize is False
        assert decoded.decred_staking_ticket is True

    def test_bool_default(self, store):
        msg = messages.SignTx(outputs_count=1, inputs_count=1)
        data = serialize(msg, store)
        decoded = deserialize(messages.SignTx, data, store)
        assert decoded.serialize is True
        assert decoded.decred_staking_ticket is False


class TestLargeIntegers:
    def test_large_u32(self, store):
        msg = messages.SignTx(
            outputs_count=0xDEADBEEF,
            inputs_count=1,
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.SignTx, data, store)
        assert decoded.outputs_count == 0xDEADBEEF


class TestRepeatedFields:
    def test_repeated_strings(self, store):
        msg = messages.BenchmarkNames(names=["alpha", "beta", "gamma"])
        data = serialize(msg, store)
        decoded = deserialize(messages.BenchmarkNames, data, store)
        assert decoded.names == ["alpha", "beta", "gamma"]

    def test_empty_repeated(self, store):
        msg = messages.BenchmarkNames()
        data = serialize(msg, store)
        decoded = deserialize(messages.BenchmarkNames, data, store)
        assert decoded.names == []

    def test_repeated_ints(self, store):
        msg = messages.TxInput(
            address_n=[44, 0, 0, 0, 0],
            prev_hash=b"\xab" * 32,
            prev_index=0,
            amount=100000,
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.TxInput, data, store)
        assert decoded.address_n == [44, 0, 0, 0, 0]


class TestNestedMessages:
    def test_nested_roundtrip(self, store):
        msg = messages.TxAck(
            tx=messages.TransactionType(
                version=2,
                lock_time=500000,
            )
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.TxAck, data, store)
        assert decoded.tx is not None
        assert decoded.tx.version == 2
        assert decoded.tx.lock_time == 500000

    def test_nested_null(self, store):
        msg = messages.TxAck()
        data = serialize(msg, store)
        decoded = deserialize(messages.TxAck, data, store)
        assert decoded.tx is None


class TestSignTx:
    def test_complex_message(self, store):
        msg = messages.SignTx(
            outputs_count=5,
            inputs_count=3,
            coin_name="Litecoin",
            version=2,
            lock_time=500000,
            amount_unit=messages.AmountUnit.MILLIBITCOIN,
            decred_staking_ticket=False,
            serialize=True,
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.SignTx, data, store)
        assert decoded.outputs_count == 5
        assert decoded.inputs_count == 3
        assert decoded.coin_name == "Litecoin"
        assert decoded.version == 2
        assert decoded.lock_time == 500000
        assert decoded.amount_unit == messages.AmountUnit.MILLIBITCOIN
        assert decoded.decred_staking_ticket is False
        assert decoded.serialize is True


class TestTxInput:
    def test_full_roundtrip(self, store):
        msg = messages.TxInput(
            address_n=[44, 0, 0, 0, 0],
            prev_hash=b"\xab" * 32,
            prev_index=0,
            script_sig=b"\x00\x14" + b"\xcd" * 20,
            sequence=0xFFFFFFFE,
            script_type=messages.InputScriptType.SPENDWITNESS,
            amount=100000,
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.TxInput, data, store)
        assert decoded.address_n == [44, 0, 0, 0, 0]
        assert decoded.prev_hash == b"\xab" * 32
        assert decoded.prev_index == 0
        assert decoded.script_sig == b"\x00\x14" + b"\xcd" * 20
        assert decoded.sequence == 0xFFFFFFFE
        assert decoded.script_type == messages.InputScriptType.SPENDWITNESS
        assert decoded.amount == 100000

    def test_defaults_preserved(self, store):
        msg = messages.TxInput(
            prev_hash=b"\x00" * 32,
            prev_index=0,
            amount=0,
        )
        data = serialize(msg, store)
        decoded = deserialize(messages.TxInput, data, store)
        assert decoded.sequence == 4294967295
        assert decoded.script_type == messages.InputScriptType.SPENDADDRESS
        assert decoded.coinjoin_flags == 0


class TestEmptyMessage:
    def test_empty_roundtrip(self, store):
        msg = messages.BenchmarkListNames()
        data = serialize(msg, store)
        decoded = deserialize(messages.BenchmarkListNames, data, store)
        assert isinstance(decoded, messages.BenchmarkListNames)
