import typing as t


def align_up(value: int, align: int = 1) -> int:
    # round up to the nearest multiple of align
    return (value + align - 1) & ~(align - 1)


def pack_flat_array(elements: t.Sequence[bytes], elem_align: int) -> bytes:
    if not elements:
        return b""

    elem_size = len(elements[0])
    if not all(len(elem) == elem_size for elem in elements):
        raise ValueError("All elements must have the same length")
    padded_elem_size = align_up(elem_size, elem_align)

    padding_size = padded_elem_size - elem_size
    elem_padding = b"\x00" * padding_size
    return b"".join(elem + elem_padding for elem in elements)


ByteType = t.TypeVar("ByteType", bytes, bytearray, memoryview)


def unpack_flat_array(
    data: ByteType, elem_size: int, elem_align: int
) -> t.Iterator[ByteType]:
    padded_elem_size = align_up(elem_size, elem_align)
    if len(data) % padded_elem_size != 0:
        raise ValueError(
            f"Data length {len(data)} is not divisible by padded element size {padded_elem_size}"
        )
    for i in range(0, len(data), padded_elem_size):
        yield data[i : i + elem_size]


def bit_length(value: int, signed: bool) -> int:
    if not signed:
        return value.bit_length()
    else:
        return (value + (value < 0)).bit_length() + 1
