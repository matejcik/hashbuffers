from __future__ import annotations

import typing as t
from dataclasses import dataclass

Inner = t.TypeVar("Inner")
Outer = t.TypeVar("Outer")


@dataclass(frozen=True)
class AdapterCodec(t.Generic[Outer, Inner]):
    encode: t.Callable[[Outer], Inner]
    decode: t.Callable[[Inner], Outer]

    @staticmethod
    def identity() -> AdapterCodec[Outer, Outer]:
        return AdapterCodec(lambda x: x, lambda x: x)
