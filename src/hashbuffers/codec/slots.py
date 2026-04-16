import typing as t
from dataclasses import dataclass

from .base import SIZE_MAX, Block, BlockType, check_bounds


@dataclass
class SlotsBlock(Block):
    offsets: list[int]
    heap: bytes

    BLOCK_TYPE = BlockType.SLOTS

    # block header + first offset + sentinel = 6 bytes overhead
    MAX_ELEMENT_SIZE: t.ClassVar[int] = SIZE_MAX - 6

    def compute_size(self) -> int:
        return 2 + 2 * len(self.offsets) + len(self.heap)

    @staticmethod
    def heap_start(offsets_len: int) -> int:
        return 2 + 2 * offsets_len

    @classmethod
    def build(cls, offsets: list[int], heap: bytes) -> t.Self:
        new = cls(cls.BLOCK_TYPE, 0, offsets, heap)
        new.size = new.compute_size()
        return new

    @classmethod
    def build_slots(cls, items: list[bytes]) -> t.Self:
        offsets = []
        heap = bytearray()
        for item in items:
            offsets.append(len(heap))
            heap.extend(item)
        offsets.append(len(heap))
        heap_start = cls.heap_start(len(offsets))
        return cls.build([off + heap_start for off in offsets], bytes(heap))

    def element_count(self) -> int:
        return len(self.offsets) - 1

    def get_entry(self, index: int) -> bytes:
        check_bounds(index, 0, self.element_count() - 1)
        heap_start = self.heap_start(len(self.offsets))
        start = self.offsets[index]
        end = self.offsets[index + 1]
        return self.heap[start - heap_start : end - heap_start]

    def get_entries(self) -> list[bytes]:
        return [self.get_entry(i) for i in range(self.element_count())]

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        for off in self.offsets:
            w.write_uint(size=2, value=off)
        w.write(self.heap)
        return w.getvalue()

    def alignment(self) -> int:
        return 2

    def validate(self) -> None:
        super().validate()
        if self.size < 4:
            raise ValueError("SLOTS block must be at least 4 bytes")
        if not self.offsets:
            raise ValueError("SLOTS block must have at least one offset (sentinel)")
        first = self.offsets[0]
        if first < 4:
            raise ValueError(f"First offset {first} must be at least 4")
        if first % 2 != 0:
            raise ValueError(f"First offset {first} must be divisible by 2")
        if first > self.size:
            raise ValueError(f"First offset {first} exceeds block size {self.size}")
        expected_count = (first - 2) // 2
        if len(self.offsets) != expected_count:
            raise ValueError(
                f"Offset count {len(self.offsets)} does not match expected {expected_count}"
            )
        if any(
            self.offsets[i] > self.offsets[i + 1] for i in range(len(self.offsets) - 1)
        ):
            raise ValueError("Offsets are not non-decreasing")
        if self.offsets[-1] != self.size:
            raise ValueError(
                f"Sentinel offset {self.offsets[-1]} is not equal to block size {self.size}"
            )

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        if size < 4:
            raise ValueError("SLOTS block too small")
        first_offset = r.read_uint(2)
        if first_offset < 4 or first_offset % 2 != 0:
            raise ValueError(f"Invalid first offset {first_offset}")
        offset_count = (first_offset - 2) // 2
        offsets = [first_offset]
        for _ in range(offset_count - 1):
            offsets.append(r.read_uint(2))
        heap = r.read_until(size)
        r.done()
        return cls(cls.BLOCK_TYPE, size, offsets, heap)

    def __len__(self) -> int:
        return self.element_count()

    @t.overload
    def __getitem__(self, index: int) -> bytes: ...
    @t.overload
    def __getitem__(self, index: slice) -> t.Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | t.Sequence[bytes]:
        if isinstance(index, int):
            return self.get_entry(index)
        else:
            start, stop, step = index.indices(self.element_count())
            return [self.get_entry(i) for i in range(start, stop, step)]
