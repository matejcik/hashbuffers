"""Block inspector for hashbuffers wire format.

Decodes and displays the internal structure of encoded blocks.
Reuses codec classes where possible, with fault-tolerant fallbacks
for invalid or truncated blocks.
"""

from __future__ import annotations

import json
import typing as t
from dataclasses import dataclass

from .codec import (
    BlockType,
    DataBlock,
    Link,
    LinksBlock,
    Reader,
    SlotsBlock,
    TableBlock,
    Tagged16,
)
from .codec.table import TableEntryRaw, TableEntryType


@dataclass
class InspectionResult:
    """Result of inspecting a block, including any errors encountered."""

    block_type: str | None = None
    size: int | None = None
    reserved_bit: bool = False
    error: str | None = None
    details: dict[str, t.Any] | None = None

    def to_dict(self) -> dict[str, t.Any]:
        d: dict[str, t.Any] = {}
        if self.block_type is not None:
            d["block_type"] = self.block_type
        if self.size is not None:
            d["size"] = self.size
        if self.reserved_bit:
            d["reserved_bit"] = True
        if self.details:
            d.update(self.details)
        if self.error:
            d["error"] = self.error
        return d

    def to_text(self, indent: int = 0) -> str:
        prefix = "  " * indent
        lines: list[str] = []

        header = f"{self.block_type or '???'} block"
        if self.size is not None:
            header += f" ({self.size} bytes)"
        if self.reserved_bit:
            header += " [RESERVED BIT SET]"
        lines.append(prefix + header)

        if self.details:
            lines.extend(_format_details(self.details, indent + 1))

        if self.error:
            lines.append(prefix + f"  ERROR: {self.error}")

        return "\n".join(lines)


def _format_details(details: dict[str, t.Any], indent: int) -> list[str]:
    prefix = "  " * indent
    lines: list[str] = []
    for key, value in details.items():
        if key == "vtable":
            lines.append(prefix + f"vtable ({len(value)} entries):")
            for i, entry in enumerate(value):
                lines.append(prefix + f"  [{i}] {entry}")
        elif key == "sub_blocks":
            for i, sub in value:
                lines.append(prefix + f"sub-block at vtable[{i}]:")
                lines.append(sub.to_text(indent + 1))
        elif key == "links":
            lines.append(prefix + f"links ({len(value)} entries):")
            for i, link in enumerate(value):
                lines.append(prefix + f"  [{i}] {link}")
        elif key == "offsets":
            lines.append(prefix + f"offsets: {value}")
        elif key == "slots":
            lines.append(prefix + f"slots ({len(value)} entries):")
            for i, slot in enumerate(value):
                lines.append(prefix + f"  [{i}] {slot}")
        elif key == "data":
            lines.append(prefix + f"data: {value}")
        elif key == "heap":
            lines.append(prefix + f"heap ({len(value)} bytes): {value}")
        else:
            lines.append(prefix + f"{key}: {value}")
    return lines


def _hex(data: bytes, max_bytes: int = 64) -> str:
    if len(data) <= max_bytes:
        return data.hex()
    return data[:max_bytes].hex() + f"... ({len(data)} bytes total)"


def _format_vtable_entry(
    entry: TableEntryRaw, heap: bytes, heap_start: int, block_size: int
) -> str:
    """Format a single vtable entry for human-readable display."""
    if not isinstance(entry.type, TableEntryType):
        return f"UNKNOWN(type={entry.type}) offset={entry.offset}"
    match entry.type:
        case TableEntryType.NULL:
            return "NULL"
        case TableEntryType.INLINE:
            unsigned = entry.offset
            BIT13 = 1 << 12
            signed = (
                entry.offset - (BIT13 << 1) if entry.offset & BIT13 else entry.offset
            )
            if signed == unsigned:
                return f"INLINE value={unsigned}"
            return f"INLINE value={unsigned} (signed: {signed})"
        case TableEntryType.DIRECT4:
            data = _heap_slice(heap, entry.offset, heap_start, 4)
            return f"DIRECT4 offset={entry.offset} data={data.hex()}"
        case TableEntryType.DIRECT8:
            data = _heap_slice(heap, entry.offset, heap_start, 8)
            return f"DIRECT8 offset={entry.offset} data={data.hex()}"
        case TableEntryType.DIRECTDATA:
            try:
                hdr = _heap_slice(heap, entry.offset, heap_start, 2)
                t16 = Tagged16.decode(hdr)
                length = t16.number
                align_power = t16.parameters
                payload = _heap_slice(heap, entry.offset + 2, heap_start, length)
                return (
                    f"DIRECTDATA offset={entry.offset} "
                    f"align_power={align_power} length={length} "
                    f"data={_hex(payload)}"
                )
            except Exception:
                return f"DIRECTDATA offset={entry.offset}"
        case TableEntryType.BLOCK:
            return f"BLOCK offset={entry.offset}"
        case TableEntryType.LINK:
            try:
                link_data = _heap_slice(heap, entry.offset, heap_start, Link.SIZE)
                link = Link.decode(link_data)
                return (
                    f"LINK offset={entry.offset} "
                    f"digest={link.digest.hex()[:16]}... limit={link.limit}"
                )
            except Exception:
                return f"LINK offset={entry.offset}"


def _heap_slice(heap: bytes, offset: int, heap_start: int, length: int) -> bytes:
    """Extract bytes from the heap, clamping to available data."""
    rel = offset - heap_start
    return heap[rel : rel + length]


def inspect_block(data: bytes) -> InspectionResult:
    """Inspect a block, decoding as much as possible even if invalid."""
    result = InspectionResult()

    if len(data) < 2:
        result.error = f"Too short ({len(data)} bytes), need at least 2 for header"
        return result

    # Parse header
    try:
        tagged = Tagged16.decode(data[:2])
        params = tagged.parameters
        result.reserved_bit = bool(params & 0b001)
        type_bits = params >> 1
        result.block_type = BlockType(type_bits).name
        result.size = tagged.number
    except Exception as e:
        result.error = f"Failed to parse header: {e}"
        return result

    if result.size is not None and len(data) < result.size:
        result.error = f"Truncated: declared size {result.size}, got {len(data)} bytes"

    block_data = (
        data[: result.size] if result.size and len(data) >= result.size else data
    )

    # If the reserved bit is set, patch it out so the codec decoders can parse
    if result.reserved_bit:
        patched_header = Tagged16(params & 0b110, tagged.number).encode()
        block_data = patched_header + block_data[2:]

    try:
        block_type = BlockType(type_bits)
    except ValueError:
        result.error = f"Unknown block type bits: {type_bits}"
        result.details = {"raw": _hex(data)}
        return result

    # Dispatch to type-specific inspection
    try:
        if block_type == BlockType.TABLE:
            result.details = _inspect_table(block_data, result)
        elif block_type == BlockType.DATA:
            result.details = _inspect_data(block_data, result)
        elif block_type == BlockType.SLOTS:
            result.details = _inspect_slots(block_data, result)
        elif block_type == BlockType.LINKS:
            result.details = _inspect_links(block_data, result)
    except Exception as e:
        if result.error is None:
            result.error = str(e)
        if result.details is None:
            result.details = {"raw": _hex(block_data)}

    return result


def _decode_table_lenient(data: bytes) -> TableBlock:
    """Parse a TABLE block, tolerating unknown vtable entry types."""
    r = Reader(data)
    header = Tagged16.decode(r.read_exact(2))
    size = header.number
    vtable_header = Tagged16.decode(r.read_exact(2))
    reserved_bits = vtable_header.parameters
    count = vtable_header.number
    vtable: list[TableEntryRaw] = []
    for _ in range(count):
        tagged = Tagged16.decode(r.read_exact(2))
        try:
            entry_type: TableEntryType | int = TableEntryType(tagged.parameters)
        except ValueError:
            entry_type = tagged.parameters
        vtable.append(TableEntryRaw(entry_type, tagged.number))  # type: ignore[arg-type]
    heap_start = 4 + 2 * count
    heap = data[heap_start:size] if size <= len(data) else data[heap_start:]
    return TableBlock(BlockType.TABLE, size, vtable, heap, reserved_bits=reserved_bits)


def _inspect_table(data: bytes, result: InspectionResult) -> dict[str, t.Any]:
    details: dict[str, t.Any] = {}

    # Try full decode first; fall back to lenient parsing
    try:
        table = TableBlock._decode_without_validation(data)
    except Exception:
        try:
            table = _decode_table_lenient(data)
        except Exception as e:
            details["raw"] = _hex(data)
            raise ValueError(f"Could not parse TABLE structure: {e}") from e

    heap_start = table.heap_start(len(table.vtable))

    # Format vtable entries
    vtable_strs = []
    sub_blocks: list[tuple[int, InspectionResult]] = []
    for i, entry in enumerate(table.vtable):
        vtable_strs.append(
            _format_vtable_entry(entry, table.heap, heap_start, table.size)
        )
        # Recursively inspect sub-blocks
        if entry.type == TableEntryType.BLOCK:
            try:
                sub_data = table.get_heap_data(entry.offset)
                sub_blocks.append((i, inspect_block(sub_data)))
            except Exception:
                pass

    details["vtable"] = vtable_strs
    if sub_blocks:
        details["sub_blocks"] = sub_blocks
    details["heap"] = _hex(table.heap)

    # Run validation and report issues
    try:
        table.validate()
    except Exception as e:
        result.error = f"Validation: {e}"

    return details


def _inspect_data(data: bytes, result: InspectionResult) -> dict[str, t.Any]:
    details: dict[str, t.Any] = {}

    try:
        block = DataBlock._decode_without_validation(data)
    except Exception as e:
        details["raw"] = _hex(data)
        raise ValueError(f"Could not parse DATA structure: {e}") from e

    details["elem_size"] = block.elem_size
    details["elem_align"] = block.elem_align
    details["elem_count"] = block.element_count()
    details["data"] = _hex(block.data)

    try:
        block.validate()
    except Exception as e:
        result.error = f"Validation: {e}"

    return details


def _inspect_slots(data: bytes, result: InspectionResult) -> dict[str, t.Any]:
    details: dict[str, t.Any] = {}

    try:
        block = SlotsBlock._decode_without_validation(data)
    except Exception as e:
        details["raw"] = _hex(data)
        raise ValueError(f"Could not parse SLOTS structure: {e}") from e

    details["offsets"] = block.offsets

    slots = []
    try:
        for entry in block.get_entries():
            slots.append(_hex(entry))
    except Exception as e:
        if not slots:
            details["heap"] = _hex(block.heap)
        result.error = f"Failed reading entries: {e}"

    if slots:
        details["slots"] = slots

    try:
        block.validate()
    except Exception as e:
        if result.error is None:
            result.error = f"Validation: {e}"

    return details


def _inspect_links(data: bytes, result: InspectionResult) -> dict[str, t.Any]:
    details: dict[str, t.Any] = {}

    try:
        block = LinksBlock._decode_without_validation(data)
    except Exception as e:
        details["raw"] = _hex(data)
        raise ValueError(f"Could not parse LINKS structure: {e}") from e

    details["depth"] = block.depth
    links = []
    for link in block.links:
        links.append(f"digest={link.digest.hex()} limit={link.limit}")
    details["links"] = links

    try:
        block.validate()
    except Exception as e:
        result.error = f"Validation: {e}"

    return details


def inspect_and_format(data: bytes, *, as_json: bool = False) -> str:
    """Inspect a block and return formatted output."""
    result = inspect_block(data)
    if as_json:
        return json.dumps(result.to_dict(), indent=2)
    return result.to_text()
