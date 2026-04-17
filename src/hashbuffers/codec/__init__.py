from .base import BlockType
from .io import Reader

# isort: split
# re-exports
from .base import SIZE_MAX as SIZE_MAX
from .base import Block as Block
from .base import Tagged16 as Tagged16
from .data import DataBlock as DataBlock
from .links import DEPTH_MAX as DEPTH_MAX
from .links import Link as Link
from .links import LinksBlock as LinksBlock
from .slots import SlotsBlock as SlotsBlock
from .table import TableBlock as TableBlock
from .table import TableEntry as TableEntry


def decode_block(data: bytes, exact: bool = True) -> Block:
    header = Reader(data).read_exact(2)
    block_type, size = BlockType.decode(header)
    if not exact:
        data = data[:size]
    if block_type == BlockType.TABLE:
        return TableBlock.decode(data)
    if block_type == BlockType.DATA:
        return DataBlock.decode(data)
    if block_type == BlockType.SLOTS:
        return SlotsBlock.decode(data)
    if block_type == BlockType.LINKS:
        return LinksBlock.decode(data)
    raise ValueError(f"Unknown block type: {block_type}")
