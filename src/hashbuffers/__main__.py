"""Block inspector CLI for hashbuffers.

Usage:
    python3 -m hashbuffers [--json] <block> [<block> ...]
    echo <block> | python3 -m hashbuffers [--json]

Blocks can be hex-encoded or base64-encoded. Raw binary is accepted on stdin.
"""

from __future__ import annotations

import argparse
import base64
import sys

from .inspector import inspect_and_format


def _try_decode(text: str) -> bytes:
    """Try to decode a string as hex or base64."""
    text = text.strip()

    # Try hex first
    try:
        return bytes.fromhex(text)
    except ValueError:
        pass

    # Try base64
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        pass

    raise ValueError(f"Cannot decode input as hex or base64: {text[:40]}...")


def _read_stdin() -> bytes:
    """Read a block from stdin, auto-detecting raw binary vs text encoding."""
    if sys.stdin.isatty():
        print(
            "Reading from stdin (paste hex/base64/raw, then Ctrl-D):", file=sys.stderr
        )

    # Check if stdin has binary data
    raw = sys.stdin.buffer.read()
    if not raw:
        print("Error: empty input", file=sys.stderr)
        sys.exit(1)

    # Try to interpret as text first (hex or base64)
    try:
        text = raw.decode("ascii").strip()
        if text:
            return _try_decode(text)
    except (UnicodeDecodeError, ValueError):
        pass

    # Treat as raw binary
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hashbuffers",
        description="Inspect hashbuffers wire format blocks",
    )
    parser.add_argument(
        "blocks",
        nargs="*",
        help="Hex or base64 encoded blocks",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )
    args = parser.parse_args()

    if args.blocks:
        blocks = []
        for block_str in args.blocks:
            try:
                blocks.append(_try_decode(block_str))
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        blocks = [_read_stdin()]

    for i, block_data in enumerate(blocks):
        if i > 0 and not args.as_json:
            print()
        print(inspect_and_format(block_data, as_json=args.as_json))


if __name__ == "__main__":
    main()
