# Block Inspector

A CLI tool for inspecting hashbuffers wire format blocks. It decodes the internal
structure of encoded blocks and displays their contents in human-readable or JSON form.

Accepts invalid/truncated blocks and decodes as much as possible, reporting errors
inline.

## Running

```bash
# As a module
python3 -m hashbuffers [--json] <block> [<block> ...]

# As an entry point (if installed)
hashbuffers [--json] <block> [<block> ...]

# From stdin (auto-detects hex, base64, or raw binary)
echo '<hex>' | python3 -m hashbuffers [--json]
```

## Input formats

Blocks on the command line can be **hex-encoded** or **base64-encoded** (auto-detected).

On stdin, the tool tries text decoding (hex, then base64) first, and falls back to
treating the input as raw binary.

## Options

- `--json` &mdash; output as JSON instead of human-readable text.

## Output

### Human-readable (default)

```
TABLE block (14 bytes)
  vtable (2 entries):
    [0] BLOCK offset=8
    [1] INLINE value=7
  sub-block at vtable[0]:
    TABLE block (6 bytes)
      vtable (1 entries):
        [0] INLINE value=99
      heap (0 bytes):
  heap (12 bytes): 060001006380
```

Sub-blocks embedded in TABLE BLOCK entries are recursively inspected and shown inline.

For invalid blocks, errors appear at the end of the block's output:

```
TABLE block (44 bytes)
  vtable (1 entries):
    [0] LINK offset=6 digest=0000000000000000... limit=1
  heap (76 bytes): 000000...
  ERROR: Validation: Offset 6 is not 4-aligned
```

### JSON (`--json`)

```json
{
  "block_type": "TABLE",
  "size": 14,
  "vtable": [
    "BLOCK offset=8",
    "INLINE value=7"
  ],
  "sub_blocks": [[0, {"block_type": "TABLE", "size": 6, "...": "..."}]],
  "heap": "060001006380"
}
```

Errors appear as an `"error"` key in the JSON object.

## Vtable entry types

The inspector displays vtable entries according to their type:

| Type | Display |
|------|---------|
| `NULL` | `NULL` |
| `INLINE` | `INLINE value=N` (shows signed interpretation if different) |
| `DIRECT4` | `DIRECT4 offset=N data=HHHHHHHH` |
| `DIRECT8` | `DIRECT8 offset=N data=HHHHHHHHHHHHHHHH` |
| `DIRECTDATA` | `DIRECTDATA offset=N align_power=P length=L data=...` |
| `BLOCK` | `BLOCK offset=N` (sub-block shown recursively below) |
| `LINK` | `LINK offset=N digest=...  limit=N` |

Unknown entry types (e.g., reserved `0b111`) display as `UNKNOWN(type=N) offset=M`.

## DATA blocks

DATA blocks show the `elem_info` fields parsed from the block header:

```
DATA block (16 bytes)
  elem_size: 4
  elem_align: 4
  elem_count: 3
  data: 0a000000140000001e000000
```

## Error tolerance

The inspector tries to show as much structure as possible even for invalid blocks:

- **Reserved header bit set**: patched out, block parsed normally, `[RESERVED BIT SET]`
  shown in the header line.
- **Unknown vtable entry types**: displayed as `UNKNOWN(type=N)`, rest of vtable parsed
  normally.
- **Truncated blocks**: header info shown, then `ERROR: Truncated: declared size N, got M bytes`.
- **Validation failures**: full structure shown, then `ERROR: Validation: ...` at the end.

## Extracting blocks from test vectors with jq

The `vectors_json/` directory contains test vectors. Use `jq` to extract hex-encoded
blocks:

```bash
# All blocks from all positive vectors
jq -r '.[].store | to_entries[].value' vectors_json/positive.json

# Root block of a specific test case
jq -r '.[] | select(.name == "nested_struct") | .store[.root_digest]' vectors_json/positive.json

# All blocks from a specific test case
jq -r '.[] | select(.name == "large_data_outlink") | .store | to_entries[].value' vectors_json/positive.json

# Pipe directly into the inspector
jq -r '.[] | select(.name == "nested_struct") | .store[.root_digest]' vectors_json/positive.json \
  | xargs python3 -m hashbuffers
```

## Implementation

- `src/hashbuffers/inspector.py` &mdash; inspection logic, reuses codec's
  `_decode_without_validation()` for parsing and runs `validate()` separately to
  report errors without aborting.
- `src/hashbuffers/__main__.py` &mdash; CLI argument parsing and input decoding.
- Entry point registered in `pyproject.toml` as `hashbuffers`.
