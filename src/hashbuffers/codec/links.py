import typing as t
from dataclasses import dataclass

from .base import SIZE_MAX, Block, BlockType, check_bounds
from .io import Reader, Writer


@dataclass
class Link:
    digest: bytes
    limit: int

    SIZE: t.ClassVar[int] = 36
    ALIGNMENT: t.ClassVar[int] = 4

    @classmethod
    def decode(cls, data: bytes) -> t.Self:
        r = Reader(data)
        digest = r.read_exact(32)
        limit = r.read_uint(4)
        r.done()
        return cls(digest, limit)

    def encode(self) -> bytes:
        if len(self.digest) != 32:
            raise ValueError("Invalid digest length")
        check_bounds(self.limit, 1, 0xFFFF_FFFF)
        w = Writer()
        w.write(self.digest)
        w.write_uint(size=4, value=self.limit)
        return w.getvalue()


@dataclass
class LinksBlock(Block[Link]):
    links: list[Link]

    reserved_bits: int = 0

    BLOCK_TYPE = BlockType.LINKS

    LINKS_MAX: t.ClassVar[int] = (SIZE_MAX - 4) // Link.SIZE

    def alignment(self) -> int:
        return 4

    def element_count(self) -> int:
        return self.links[-1].limit

    def compute_size(self) -> int:
        return 4 + Link.SIZE * len(self.links)

    @classmethod
    def build(cls, links: list[Link]) -> t.Self:
        if len(links) > cls.LINKS_MAX:
            raise ValueError(
                f"Links block exceeds {cls.LINKS_MAX} links (len: {len(links)})"
            )
        new = cls(cls.BLOCK_TYPE, 0, links)
        new.size = new.compute_size()
        return new

    def _encode_without_validation(self) -> bytes:
        w = self._start_encode()
        w.write_uint(size=2, value=self.reserved_bits)
        for link in self.links:
            w.write(link.encode())
        return w.getvalue()

    def validate(self) -> None:
        super().validate()
        if self.reserved_bits != 0:
            raise ValueError(f"Reserved bits {self.reserved_bits} are not zero")
        if not self.links:
            raise ValueError("LINKS block must have at least one link")
        if any(
            self.links[i].limit >= self.links[i + 1].limit
            for i in range(len(self.links) - 1)
        ):
            raise ValueError("Links must be strictly increasing")
        if any(link.limit == 0 for link in self.links):
            raise ValueError("Links must not have limit 0")

    @classmethod
    def _decode_without_validation(cls, data: bytes) -> t.Self:
        r, size = cls._start_decode(data)
        reserved_bits = r.read_uint(2)
        data_size = size - 4
        if data_size % Link.SIZE != 0:
            raise ValueError(
                f"LINKS block data length is not a multiple of link size ({Link.SIZE})"
            )

        links = []
        for _ in range(data_size // Link.SIZE):
            links.append(Link.decode(r.read_exact(Link.SIZE)))
        r.done()

        return cls(cls.BLOCK_TYPE, size, links, reserved_bits=reserved_bits)

    def __len__(self) -> int:
        return self.links.__len__()

    @t.overload
    def __getitem__(self, index: int) -> Link: ...
    @t.overload
    def __getitem__(self, index: slice) -> t.Sequence[Link]: ...

    def __getitem__(self, index: int | slice) -> Link | t.Sequence[Link]:
        return self.links.__getitem__(index)

    def __iter__(self) -> t.Iterator[Link]:
        return self.links.__iter__()
