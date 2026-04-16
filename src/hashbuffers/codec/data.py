import typing as t
from dataclasses import dataclass

from ..util import align_up, pack_flat_array, unpack_flat_array
from .base import Block, BlockType, Tagged16


@dataclass
class DataBlock(Block[bytes]):
    data: bytes
    elem_size: int
    elem_align: int

    BLOCK_TYPE = BlockType.DATA

    @property
    def start_offset(self) -> int:
        return max(self.elem_align, 4)

    @classmethod
    def build(cls, data: bytes, *, elem_size: int, elem_align: int = 1) -> t.Self:
        if elem_size <= 0:
            raise ValueError("Element size must be positive")
        new = cls(cls.BLOCK_TYPE, 0, data, elem_size, elem_align)
        new.size = new.compute_size()
        return new

    def compute_size(self) -> int:
        return self.start_offset + len(self.data)

    @classmethod
    def build_array(cls, data: t.Sequence[bytes], *, align: int = 1) -> t.Self:
        if not data:
            raise ValueError("Data sequence must not be empty")
        elem_size = len(data[0])
        if not all(len(elem) == elem_size for elem in data):
            raise ValueError("All elements must have the same length")
        if elem_size < 1:
            raise ValueError("Element size must be positive")
        array_data = pack_flat_array(data, align)
        return cls.build(array_data, elem_size=elem_size, elem_align=align)

    def get_data(self) -> memoryview:
        return memoryview(self.data)

    def get_element(self, index: int) -> bytes:
        return self.data[index * self.elem_size : (index + 1) * self.elem_size]

    def element_count(self) -> int:
        return len(self.data) // align_up(self.elem_size, self.elem_align)

    def alignment(self) -> int:
        return max(self.elem_align, 2)

    def validate(self) -> None:
        super().validate()
        if self.size < 4:
            raise ValueError("DATA block must be at least 4 bytes")
        if self.elem_align < 1 or self.elem_align.bit_count() != 1:
            raise ValueError(
                f"Invalid element alignment: {self.elem_align} (not a power of two)"
            )
        if self.elem_size <= 0:
            raise ValueError("Element size must be positive")
        padded_elem_size = align_up(self.elem_size, self.elem_align)
        if len(self.data) % padded_elem_size != 0:
            raise ValueError(
                f"Data length {len(self.data)} is not a multiple of "
                f"padded element size {padded_elem_size}"
            )

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        # encode elem_info t16: align_power (3 bits) + elem_size (13 bits)
        align_power = self.elem_align.bit_length() - 1
        w.write(Tagged16(align_power, self.elem_size).encode())
        # padding between headers and data
        pad_size = self.start_offset - 4
        w.write(b"\x00" * pad_size)
        w.write(self.data)
        return w.getvalue()

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        # decode elem_info t16
        elem_info = Tagged16.decode(r.read_exact(2))
        align_power = elem_info.parameters
        elem_size = elem_info.number
        elem_align = 1 << align_power
        # skip padding
        start_offset = max(elem_align, 4)
        pad_size = start_offset - 4
        r.read_exact(pad_size)  # skip padding
        # read array data
        array_data = r.read_until(size)
        r.done()
        return cls(cls.BLOCK_TYPE, size, array_data, elem_size, elem_align)

    def __len__(self) -> int:
        return self.element_count()

    def __iter__(self) -> t.Iterator[bytes]:
        return unpack_flat_array(self.data, self.elem_size, self.elem_align)

    @t.overload
    def __getitem__(self, index: int) -> bytes: ...
    @t.overload
    def __getitem__(self, index: slice) -> t.Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | t.Sequence[bytes]:
        if isinstance(index, int):
            return self.get_element(index)
        else:
            start, stop, step = index.indices(self.element_count())
            return [self.get_element(i) for i in range(start, stop, step)]
