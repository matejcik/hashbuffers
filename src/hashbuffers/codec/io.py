import typing as t
from io import SEEK_CUR, BytesIO


class Reader(BytesIO):
    def __init__(self, data: bytes | bytearray | memoryview):
        super().__init__(data)

    def read_exact(self, n: int) -> bytes:
        data = self.read(n)
        if len(data) != n:
            raise IOError(f"Expected {n} bytes, got {len(data)}")
        return data

    def read_until(self, offset: int) -> bytes:
        if offset < self.tell():
            raise IOError(f"Offset {offset} is before current position {self.tell()}")
        data = self.read(offset - self.tell())
        if self.tell() != offset:
            raise IOError(
                f"Expected to read to offset {offset}, stopped at {self.tell()}"
            )
        return data

    def read_uint(self, n: int) -> int:
        return int.from_bytes(self.read_exact(n), "little")

    def done(self) -> None:
        if self.tell() != len(self.getbuffer()):
            raise IOError("Unparsed trailing data")

    def child(self, length: int | None = None) -> t.Self:
        mv = self.getbuffer()[self.tell() :]
        # skip
        if length is None:
            length = len(mv)
        if length < 0:
            raise ValueError(f"Invalid length: {length}")
        if length > len(mv):
            raise ValueError(
                f"Not enough data left: want {length} bytes, remaining {len(mv)}"
            )

        mv = mv[:length]
        # skip over the child data
        self.seek(length, SEEK_CUR)
        return self.__class__(mv)


class Writer(BytesIO):
    def write_uint(self, *, size: int, value: int) -> t.Self:
        self.write(value.to_bytes(size, "little"))
        return self
