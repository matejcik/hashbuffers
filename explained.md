# Hashbuffers Explained

## What problem does it solve?

Imagine a tiny device — a hardware crypto signer, say — that needs to work
with a data structure far bigger than its memory. It can't just load the whole
thing. Hashbuffers lets it request pieces on demand, verify each piece is
authentic, and never hold more than a small chunk at a time.

A **host** (typically a PC or server) holds the full encoded structure. The
**constrained device** asks for blocks by their hash, checks them, and
navigates deeper as needed — like paging through a book one paragraph at a
time, where every paragraph has a tamper-proof seal.

## The big picture

A Hashbuffers-encoded value is a tree of **blocks**. Each block is at most
~8 KiB. When something doesn't fit in one block, it gets split across
multiple blocks connected by cryptographic **links** (HMAC-SHA256 hashes).

```
              ┌──────────┐
              │  root     │  (a TABLE block — your top-level struct)
              │  block    │
              └──┬───┬────┘
                 │   │
        ┌────────┘   └────────┐
        ▼                     ▼
  ┌───────────┐         ┌──────────┐
  │ DATA block│         │ LINKS    │  (inner node of a link tree)
  │ (an array │         │ block    │
  │ of u32s)  │         └──┬───┬───┘
  └───────────┘            │   │
                   ┌───────┘   └───────┐
                   ▼                   ▼
             ┌──────────┐        ┌──────────┐
             │ SLOTS    │        │ SLOTS    │
             │ block    │        │ block    │
             └──────────┘        └──────────┘
```

The device starts with the root block's hash. It requests that block, verifies
the hash, then follows links to children as needed.


## Building blocks

Before diving into the block types, here are the low-level pieces that
everything is built from.

### Primitives

The format supports integers and floats of 1, 2, 4, or 8 bytes. Everything is
**little-endian**. A value's alignment requirement equals its size (a `u32`
must sit at a 4-byte-aligned offset). <<Primitives>>

| Type | Size | Notes |
|------|------|-------|
| `u8` / `i8` | 1 | Also used for booleans and small enums |
| `u16` / `i16` | 2 | |
| `u32` / `i32` | 4 | |
| `u64` / `i64` | 8 | |
| `f32` | 4 | IEEE 754 |
| `f64` | 8 | IEEE 754 |

### Tagged u16 (`t16`)

A `t16` packs 3 parameter bits and a 13-bit number into 2 bytes. Block
headers and table entries are `t16`s. The 13-bit number caps offsets and
sizes at 8191 bytes — this is where the ~8 KiB block limit comes from.
<<Tagged u16>>

### Link

A link is 36 bytes: a 32-byte HMAC-SHA256 digest of a child block, plus a
4-byte `limit` (element count). The digest lets the reader verify the child's
integrity; the limit tells it how many elements the child contains.
`limit == 0` is always invalid. <<Link>>


## Block types

Every block starts with a 2-byte `t16` header encoding its type and size.
There are four types. <<Block types>>

### TABLE — heterogeneous container

A TABLE holds a mix of different things — think of it as a struct or a row.
After the header comes an entry count, then an array of entries, then a heap
of variable-size data.

Each entry is a `t16` with a type tag and an offset:

- **NULL** — empty / missing field
- **DIRECTDATA** — offset points to a small bytestring on the heap (a `t16`
  header + raw bytes); saves 2 bytes vs. a full DATA sub-block
- **DIRECT4 / DIRECT8** — offset points to a raw 4- or 8-byte value on the heap
- **INLINE** — a small integer (≤13 bits) stored right in the entry itself
- **BLOCK** — offset points to a nested sub-block (with its own header)
- **LINK** — offset points to a 36-byte link referencing an external block

This is the workhorse type. Your top-level struct is a TABLE. Arrays of
complex elements are TABLEs. <<TABLE>>

### DATA — flat array of fixed-size elements

A DATA block is a contiguous array of same-sized values (integers, floats,
etc.). After the block header comes a `t16` **elem_info** field encoding the
element alignment and size, followed by the element data with alignment padding
as needed. The element count is derived from the block size and elem_info.

Use case: an array of `u32`, a byte string, a list of `f64`. <<DATA>>

### SLOTS — array of variable-length byte strings

A SLOTS block stores multiple variable-length entries. After the header comes
an array of `u16` offsets — one per entry plus a sentinel — followed by the
raw data. Entry `n` spans from `offset[n]` to `offset[n+1]`.

Use case: a list of short strings or small byte blobs. <<SLOTS>>

### LINKS — inner node of a link tree

A LINKS block is an array of 36-byte links, preceded by a `depth_field`.
The depth field is a countdown that bounds traversal depth: depth 0 means
all children are leaves; depth N means children may be leaves or LINKS
blocks with depth < N. The maximum allowed depth is **4**, giving at most
5 levels of LINKS nesting — enough for 2^32 elements. A LINKS block must
contain at least 2 links.

Each link's `limit` is cumulative: it tells you how many elements exist up
to and including that child. This enables binary search to find which child
holds a given index.

Use case: when an array is too large for one block, a LINKS block stitches
multiple leaf blocks into a tree. <<LINKS>>


## Data model and schema

The wire format is schema-agnostic — a reader without a schema can parse
block structure and follow links. But to know what the data *means*, you need
a schema. The spec doesn't prescribe a schema language; this section describes
the data model the format can express, then sketches what a schema language
might look like. <<Overview and motivation>>

### Structs

A struct maps to a single TABLE block. Each field gets a schema-defined
**index** — its position in the table's entry list. Indices must be unique but
don't have to be consecutive; gaps become NULL entries. <<Structs>>

Fields can be stored as:
- **INLINE** — small integers (≤13 bits)
- **DIRECTDATA** — short byte strings and alignment-1 composites
- **DIRECT4 / DIRECT8** — 4- or 8-byte values (primitives or composites)
- **BLOCK** — nested sub-blocks (sub-structs, inline arrays, or custom
  composites that don't fit a DIRECT slot)
- **LINK** — anything too big to fit in this block

Tables are allowed to be longer or shorter than the schema expects — extra
entries are ignored, missing ones read as NULL. This gives you forward and
backward compatibility for free.

On the wire, every field is technically optional (it can be NULL). The schema
can layer a "required" constraint on top and reject blocks where required
fields are missing.

### Arrays

Arrays come in three flavors, chosen by what's inside them:

| Element type | Representation | How it looks on the wire |
|---|---|---|
| Fixed-size primitives / composites | **DATA** | Packed contiguously, no per-element overhead |
| Variable-size byte strings | **SLOTS + TABLE** | SLOTS for small entries, TABLE for oversized ones |
| Structs, nested arrays | **TABLE** | Each element as a BLOCK or LINK entry |

If an array doesn't fit in one block, it becomes a **link tree**: a LINKS
root with leaf blocks. Leaves of a link tree don't have to be the same type —
for example, a bytestring array's tree can mix SLOTS and TABLE leaves. Trees
can be up to 5 levels deep (depth limit of 4), which is sufficient for the
maximum array size of 2^32 elements. <<Arrays>>

#### Link tree traversal

To find element `N` in a link tree:

1. Binary-search the LINKS block for the first link whose `limit > N`.
2. Subtract the previous link's limit to get a local index within that child.
3. Descend. If the child is another LINKS block, verify its depth is strictly
   less than the parent's, then repeat. If it's a leaf, read the element at
   the local index.

At each step, the reader verifies that the child's actual element count
matches what the parent's limits claim. <<Traversal>>

### Boolean, enum, string

- **Boolean**: a `u8` with two valid values — `0` (false) and `1` (true).
- **Enum**: an integer (usually `u8`) with a schema-defined set of valid
  values. On the wire, it's indistinguishable from an integer.
- **Text string**: a variable-size array of `u8`, interpreted as UTF-8.
  Not null-terminated.
- **Byte string**: same as text, without the UTF-8 interpretation. When a
  direct member of a struct, short byte strings can be stored as DIRECTDATA.

<<Integer-like types>>, <<String types>>

### Fixed-size arrays

An array whose length is known at schema time (e.g., `[u8; 32]` for a hash
digest). On the wire, fixed-size arrays are encoded identically to
variable-size arrays — the schema-defined count is not stored; readers verify
it matches. A fixed-size array of fixed-size elements is itself a fixed-size
type.

**Multi-dimensional arrays of primitives** are flattened: `[[u32; 3]; 4]` is
stored as a flat DATA array of 12 `u32`s, and the reader reshapes.
<<Fixed-size arrays>>

### Custom composite types

Implementations can define fixed-size composite types (tuples, packed structs,
fixed-size arrays) with explicit size and alignment. The storage depends on
size and alignment:
- **DIRECT4** — exactly 4 bytes, alignment ≤ 4
- **DIRECT8** — exactly 8 bytes, alignment ≤ 8
- **DIRECTDATA** — alignment 1 (any size that fits)
- **BLOCK** embedding a DATA sub-block — everything else

There is no general-purpose "direct" slot for arbitrary-size, higher-alignment
composites; those always go through a BLOCK. <<Custom composite types>>

### Sketch of a schema language

**NOTE:** There is currently no plan to implement this schema language. This is
just pseudo-code for explanation purposes.

```
enum Color : u8 {
    Red   = 0,
    Green = 1,
    Blue  = 2,
}

struct Point {
    x @0 : f64,          # field at table index 0
    y @1 : f64,          # field at table index 1
}

struct Polygon {
    name     @0 : string,          # variable-size UTF-8
    vertices @1 : [Point],         # variable-size array of structs → TABLE
    color    @2 : Color,           # enum, stored as u8
    closed   @3 : bool,            # u8, 0 or 1
}

struct Image {
    width  @0 : u32,
    height @1 : u32,
    pixels @2 : [u8],              # variable-size byte array → DATA
    hash   @3 : [u8; 32],          # fixed-size 32-byte array → DIRECTDATA or BLOCK
}

struct Document {
    title    @0 : string,
    pages    @1 : [Image],         # array of structs → TABLE tree
    metadata @2 : string,
    tags     @3 : [string],        # array of strings → SLOTS tree
}
```

Key observations:
- **`@N`** indices determine table positions, not declaration order. Gaps are
  fine — `@0`, `@3` with nothing in between means two NULL entries at 1 and 2.
- **`[T]`** is a variable-size array; **`[T; N]`** is fixed-size.
- **`string`** is sugar for `[u8]` with UTF-8 semantics.
- **`bool`** is sugar for a two-valued `u8` enum.
- Fields are optional by default. A `required` keyword could enforce presence.


## Writing: fitting and alignment

### Fitting

When encoding, the writer must decide what fits inline in a block and what
gets externalized as a link. This is called **fitting**, and it proceeds
bottom-up: serialize children first, then figure out what fits in the parent.
<<Fitting>>

Key rules:

1. **Small values stay inline**: anything whose encoded size is ≤36 bytes (the
   size of a link) MUST NOT be externalized — linking it would waste space.
2. **Smallest-first heuristic**: among remaining fields, pack the smallest
   ones first to maximize the number of inline fields.
3. **Whatever doesn't fit** gets externalized as a LINK to its own block(s).

<<Struct Member Fitting>>

### Alignment

Zero-copy reading requires proper alignment. Every value must sit at an offset
(from block start) that's a multiple of its alignment. Writers insert padding
bytes as needed. <<Alignment>>

A block's own alignment requirement is the maximum alignment of anything
inside it (minimum 2, for the `t16` header). When a block is nested inside
another, the parent must place it at a suitably aligned offset.

When placing fields on the heap, writers should **order them to minimize
padding**: prefer placing higher-alignment fields where the current offset
already satisfies their requirement, and use alignment-preserving fields
(whose size is a multiple of their alignment) to avoid degrading the offset
for subsequent placements. <<Struct Member Fitting>>


## Reading: security and validation

### Security model

**Integrity**: every link is an HMAC-SHA256 hash. The reader and writer share
a session key (ideally short-lived and random). Before using any block, the
reader verifies its hash. This means the writer commits to the entire tree
when providing the root hash — a malicious block repository can't swap blocks
out later. <<Hashing>>

**What it doesn't protect**: access patterns are visible to the host. A
malicious repository can observe which blocks the device requests and in what
order, which might leak information in some threat models. <<Non-goals>>

**Why HMAC, not plain SHA-256?** A pre-computed SHA-256 collision could be
replayed forever. With a fresh session key, an attacker would need a new
collision every session — computationally infeasible. <<Rationale>>

### Validation

Every block type has validation rules that MUST be checked before trusting
the data. The common themes: <<Validation>>

- Block size must be at least the type's minimum (4 for TABLE, DATA, and
  SLOTS; 76 for LINKS, which requires at least 2 links).
- Offsets must be in-bounds and properly aligned.
- No zero offsets (would create a self-referencing cycle).
- Type-specific invariants: SLOTS offsets are non-decreasing, LINKS limits
  are strictly increasing, and so on.

Failing any check = reject the block.
