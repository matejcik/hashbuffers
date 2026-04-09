import typing as t

Elem = t.TypeVar("Elem")
InnerElem = t.TypeVar("InnerElem")


class CollectionAdapter(t.Sequence[Elem], t.Generic[Elem, InnerElem]):
    def __init__(
        self,
        sequence: t.Sequence[InnerElem],
        from_elem: t.Callable[[Elem], InnerElem],
        to_elem: t.Callable[[InnerElem], Elem],
    ) -> None:
        self.sequence = sequence
        self.from_elem = from_elem
        self.to_elem = to_elem

    def __len__(self) -> int:
        return len(self.sequence)

    @t.overload
    def __getitem__(self, index: int) -> Elem: ...
    @t.overload
    def __getitem__(self, index: slice) -> t.Sequence[Elem]: ...

    def __getitem__(self, index: int | slice) -> Elem | t.Sequence[Elem]:
        if isinstance(index, int):
            return self.to_elem(self.sequence[index])
        else:
            return type(self)(self.sequence[index], self.from_elem, self.to_elem)

    def __iter__(self) -> t.Iterator[Elem]:
        return (self.to_elem(elem) for elem in self.sequence)
