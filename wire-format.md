Hashbuffers: an efficient, content-addressed wire format
========================================================

## Overview and motivation

The purpose of Hashbuffers is to enable resource-constrained devices, such as
hardware cryptographic signers, to reliably and efficiently access data
structures much larger than their memory.

The format splits the data structure into blocks with a maximum size of 8 KiB,
replacing large sub-structures with hash-based links to other blocks. A host
device (usually in a writer role) serves as a block repository, which the
constrained device (typically in a reader role) can query by hash.

The maximum total size of the data structure is effectively unlimited. The
maximum size of any single array is 2^32 elements; in particular, the maximum
size of a single byte array is 4 GiB.

Understanding the encoded data structure requires schema separate from the data.
Schema definition is outside the scope of this document. A schema-less reader
can only understand the structure enough to reliably follow links and fetch all
the data.

The format is designed for zero-copy reading on little-endian platforms.

### Goals

A malicious writer or block repository should not be able to cause security
issues for the reader.

The format is designed so that after an up-front validation of received data, it
is possible to blindly access fields and indices in a zero-copy manner without
risk of buffer overflows or other memory corruptions.

By providing the root of a data structure, the writer commits to its entire
content. This specifically prevents a certain class of TOCTOU attacks, where a
malicious block repository would present one version of data in an initial phase
of reader's processing, and a different version in a later phase.

### Non-goals

The format itself does not provide confidentiality. In particular, a block
repository **can record access patterns**, which in some scenarios could leak
cryptographically sensitive information.

Canonicalization is a non-goal, there are multiple equally valid representations
of the same data. The format **commits to a particular representation** of a
given data structure, valid for a single session (see [Hashing](#hashing)). Due
to session-bound keys, re-encoding the same data at a later time will
necessarily produce different bytes; as such, canonicalization would be of
limited value.

The spec does not generally prohibit format abuses such as overlapping offsets,
as long as a compliant reader can parse the content unambiguously. The thinking
goes, if a legitimate writer could encode that same data in some other way, it's
a non-issue.

## Data types

### Primitives

The format supports primitive values of 1, 2, 4, or 8 bytes.

Depending on context, these may be interpreted as:

- unsigned integers `u8` `u16` `u32` `u64`
- twos-complement signed integers `i8` `i16` `i32` `i64`
- IEEE754 floats `f32` `f64`

All values are stored in **little-endian** byte order, and their alignment
requirement is their size.

### Tagged u16

A **tagged u16,** denoted `t16`, is a u16 value with the following structure:

- `parameters` 3 bits
- `number` 13 bits

`parameters` are context-dependent and each usage specifies their meaning separately.

`number` denotes a size or an offset into a block (or, in struct context, can be
overloaded to specify an inline numeric value). This means that the maximum size
/ offset within a block is 8191 bytes.

Negative offsets are not supported.

### Link

A link is a 36-byte structure:

- `u8 digest[32]` — 32-byte HMAC-SHA256 digest of the pointed-to child block
- `u32 limit` — the exclusive upper bound index for the pointed-to content

The alignment of a link structure is 4, because the alignment of `limit` is 4
(and the 32-byte digest keeps that alignment).

The `limit` denotes an upper bound (exclusive) of an index into the linked
content. For a stand-alone link, this is identical to the length of the content.
In a `LINKS` block, this is the length of content from the start of the `LINKS`
block up to and including this link.

`limit == 0` is a reserved value and MUST be rejected.

## Blocks

Data in the format is organized in individual **blocks**, each of which contains
a certain kind of array.

### Alignment

The wire format is designed to work in zero-copy mode (on little-endian
platforms), that is, readers can directly interpret memory content as the
appropriate value.

To make this possible, writers MUST ensure that all data in the block is
properly aligned, that is:

**Every value in a block MUST be stored at an offset that is a multiple of its
alignment requirement**, relative to start of the block.

Notably, sub-blocks within a block MUST be aligned to their alignment
requirement, which is **the maximum alignment of any values contained** inside
it. This includes the `t16` header, so the minimum alignment of a block is 2.

The writer is free to insert padding bytes to ensure the alignment.

Only power-of-two alignments are supported.

A zero-copy reader can then place the block in memory so that the alignment
holds for the target platform — that is, block start must be aligned to platform
`max_align_t` (typically 8 or 16 bytes).

Reader SHOULD perform platform-appropriate alignment checks to ensure that a
primitive value will be read out correctly; more specifically, it MUST NOT
attempt to load values from unaligned addresses if there is a risk of reading
out invalid value.

### Block header

Every block is prefixed by a tagged u16 header specifying the block type and
size:

- `type` 2 bits
- `reserved` 1 bit
- `size` 13 bits

The `size` includes the 2-byte block header itself. I.e., the maximum size of a
block with header is 8191 bytes.

The minimum valid size of a block is 2, smaller sizes MUST be rejected.

(Note that all four currently-specified block types require at least one
additional field, so in practice the minimum size is 4.)

### Offsets and bounds

All relative offsets (in structs, slotted arrays, etc.), are always relative to
**the start of the block**.

Zero offsets are invalid and MUST be rejected. (Such offset would point to the
block itself, creating a cycle.)

Within a given block, the maximum allowed offset is equal to the block size.

When performing a read in a context of a particular block, the read MUST NOT
exceed the bounds of the block.

## Block types

### `0b00 TABLE`

A table of heterogeneous elements.

`[t16 block_header] [t16 entry_count] [t16 entry_offset 0, 1, 2,...] [heap]`

A minimum valid size of a `TABLE` block is 4, to fit the `entry_count` field.

`entry_count` is a tagged u16 with the following structure:

- `flags` 3 bits. All zero, other values are reserved for future use.
- `count` 13 bits: count of table entries.

`entry` is a tagged u16 with the following structure:

- `type` 3 bits
- `offset` 13 bits

The alignment of a `TABLE` block is equal to the largest alignment of any of its
entries, but at least 2.

#### Entry types

The following values are recognized for type tags of a table entry:

- `0b000 NULL` - empty/absent entry
- `0b001 DIRECTDATA` - bytestring on the heap
- `0b010 DIRECT4` - raw 4-byte primitive value (int or float) on the heap
- `0b011 DIRECT8` - raw 8-byte primitive value (int or float) on the heap
- `0b100 INLINE` - integer value stored inline in the entry itself
- `0b101 BLOCK` - sub-block on the heap
- `0b110 LINK` - raw link on the heap

Value `0b111` is reserved for future use.

All pointer-type entries (`DIRECTDATA`, `DIRECT4`, `DIRECT8`, `BLOCK`, `LINK`)
share the following requirements on `offset`:

1. It MUST point _into the heap_, that is, _after_ the end of the entries list
   and _before_ the end of the block.
2. It MUST satisfy the alignment requirement of the target.
3. Pointed-to data MUST fit on the heap, that is, `offset + size <= heap_end`.

The size alignment requirements for entry types are shown in the following table:

| Entry type   | Alignment                          | Size on the heap |
| ------------ | ---------------------------------- | ---------------- |
| `NULL`       | 0                                  | 0                |
| `INLINE`     | 0                                  | 0                |
| `DIRECTDATA` | 2                                  | 2 + `length`     |
| `DIRECT4`    | 4                                  | 4                |
| `DIRECT8`    | 8                                  | 8                |
| `BLOCK`      | alignment of sub-block (minimum 2) | block size       |
| `LINK`       | `LINK_ALIGN` (4)                   | `LINK_SIZE` (36) |

##### `0b000 NULL`

Empty entry. In struct context, represents a field whose value was not set.

The `offset` of a `NULL` entry SHOULD be 0, but implementations are free to
ignore non-zero values.

##### `0b001 DIRECTDATA`

Minimal version of a `DATA` sub-block. `offset` points to a `t16` header on the
heap, with the following structure:

* `align_power` 3 bits: power-of-two element alignment. The actual alignment is
  `2 ^ align_power`. In version 1 of the format, this MUST be 0, i.e., alignment 1.
* `length` 13 bits: length of the data.

The header is followed by `length` bytes of raw data.

##### `0b010 DIRECT4` and `0b011 DIRECT8`

`offset` points to bare primitive values (no header or delimiter) of size 4 or 8
bytes on the heap.

##### `0b100 INLINE`

`offset` is directly interpreted as a 13-bit integer. No heap space is used.

Schema specifies signedness; readers are responsible for sign-extending the value
to the target type.

##### `0b101 BLOCK`

`offset` points to a sub-block that has its own block header and is interpreted
as a complete block.

##### `0b110 LINK`

`offset` points to a bare link (no header or delimiter).

The `limit` field is the count of elements in the linked array; however, "count
of elements" can be schema-dependent. See [Links in TABLEs](#links-in-tables)
for details.

#### Validation

Upon receiving a `TABLE` block, the reader MUST perform these validation steps:

1. Check that the block size is at least 4.
2. Check that `flags` of `entry_count` are all zero.
3. Calculate `heap_start = 4 + 2 * entry_count`, and check that `heap_start` is
   no greater than block size.

For each entry, the following checks MUST be performed:

1. The entry is not a reserved type (`0b111`).
2. For `DIRECTDATA`, `DIRECT4`, `DIRECT8`, `BLOCK`, and `LINK`, check that
   `heap_start <= offset < size`.
3. For `DIRECTDATA`:
  - `offset <= size - 2` (room for the t16 header)
  - `offset % 2 == 0`
  - Read the `t16` header at `offset`. Check that `align_power == 0`.
  - Read `length` from the header's `number` field.
  - Check that `offset + 2 + length <= size`.
4. For `DIRECT4`:
  - `offset <= size - 4`
  - `offset % 4 == 0`
5. For `DIRECT8`:
  - `offset <= size - 8`
  - `offset % 8 == 0`
6. For `LINK`:
  - `offset <= size - LINK_SIZE` (36 bytes)
  - `offset % LINK_ALIGN == 0` (4 bytes)
  - Then examine the link and check that its `limit != 0`.
7. For `BLOCK`:
  1. `offset <= size - 2`
  2. `offset % BLOCK_ALIGN == 0` (2 bytes)
  3. Read `block_size` from the header at `offset`. Check that `offset + block_size <= size`.
  4. Determine the sub-block's alignment requirement and check that it is
     properly aligned.
  5. Validate the block at `offset` as a complete block.

A block that fails any of these checks MUST be rejected.

### `0b01 DATA`

Non-delimited array spanning the `size` of the block.

`[t16 block_header] [t16 elem_info] [data...]`

The block header is followed by a `t16` element info field with the following
structure:

- `align_power` 3 bits: power-of-two element alignment. The actual alignment is
  `2 ^ align_power`. For example, `align_power = 3` means alignment 8.
- `elem_size` 13 bits: unpadded element size in bytes.

`DATA` blocks represent arrays of fixed-size elements. Writers MUST add padding:

* after the `elem_info` field, to achieve the required alignment for the first
  element, and
* after each element, if its size is not a multiple of its alignment.

This specifically means that padding after the last element (if any) is always
included, in order to simplify reader implementation at the cost of wasted
trailing padding bytes.

To achieve that, the following values are defined:

- array data starts at offset `start_offset = max(align, 4)`, to account for the
  block header and the `elem_info` field
- `padded_element_size` is the element size rounded up to the nearest multiple of
  its alignment
- element count is `count = (size - start_offset) / padded_element_size`

An empty array is represented by a block where `count` is 0, that is, block size
is equal to `start_offset`. For alignment of 4 or less, this is just the two
`t16` headers; for larger alignments, padding up to `start_offset` MUST be
included.

A minimum valid size of a `DATA` block is 4, to fit both the block header and
the `elem_info` field.

Upon receiving a `DATA` block, a reader MUST perform these validation steps:

1. Check that the block size is at least 4.
2. Check that the block size is at least `start_offset`.
3. Check that the data size (`size - start_offset`) is a multiple of
   `padded_element_size`.

A schema-aware reader SHOULD additionally verify that the `elem_info` field
matches the expected element size and alignment from the schema.

A block that fails any of these checks MUST be rejected.

The alignment of a `DATA` block is equal to element alignment, but at least 2.

### `0b10 SLOTS`

Slotted array.

`[t16 block_header] [u16 offset 0, 1, 2, ...] [heap]`

`SLOTS` blocks represent arrays of variable-size byte strings (whose alignment
is 1). Each `offset[n]` points to the start of an entry `n`, which ends at
`offset[n+1]`.

Offsets MUST be non-decreasing. The first offset (`offset[0]`) points to the
start of the heap, which is also the start of the first element. The last offset
is the **sentinel**, which MUST be equal to the block size. (The sentinel is the
last entry in the offset array, immediately preceding the heap in memory.) Given
the sentinel, readers can safely access any item `n` as
`offset[n] .. offset[n+1]` (inclusive start, exclusive end).

A `SLOTS` block MUST have at least the sentinel offset; the minimum allowed size
of a `SLOTS` block is 4. Such minimum block MUST have `4` as its only
offset, and represents an empty array.

Upon receiving a `SLOTS` block, the reader MUST perform these validation steps:

1. check that the block size is at least 4
2. check that the value of offset 0 is:
   a. at least 4
   b. divisible by 2 (it must point to the start of the data after a 2-aligned offset array)
   c. no larger than the block size
3. calculate `offset_count = (offset[0] - 2) / 2` (subtracting the size of the
   block header). This is the number of offsets in the array.
4. check that all offsets are non-decreasing
5. check that `offset[offset_count - 1]` (sentinel offset) is equal to the block size

After the validation passes, it is safe to:

* directly access `offset[0]` in order to calculate `offset_count`
* calculate `element_count = offset_count - 1`
* access any element `n` as `offset[n] .. offset[n+1]`

The alignment of a `SLOTS` block is minimum block alignment, which is 2.

### `0b11 LINKS`

An array of links / an inner node of a link tree.

`[t16 block_header] [u16 reserved] [link 0, 1, 2 ...]`

The block header is followed by a two-byte reserved field which aligns the rest
of the block to 4. After it follows a non-delimited array of 36-byte links (see
[Link](#link).)

The `reserved` field is all zero, other values are reserved for future use.

In a `LINKS` block, the meaning of the link’s `limit` field is a cumulative
count of content up to and including the current link. This is to facilitate
efficient binary search: an item with index `N` will be found under the link
whose `limit` is the smallest such that `limit > N` (`limit` is exclusive so for
`limit == N`, the item `N` will be under the next link).

Example: For an inner block of 321 items divided into max 100-item sub-blocks,
this would be the structure:

- `[t16 block_header]`
- `[u16 reserved]`
- `[hash 0] [100]` — items 0 to 99
- `[hash 1] [200]` — items 100 to 199
- `[hash 2] [300]` — items 200 to 299
- `[hash 3] [321]` — items 300 to 320

Upon receiving a `LINKS` block, the reader MUST check the following invariants:

1. block size is at least 4
2. value of `reserved` is zero
3. `size - 4` is a multiple of link size (36 bytes)
4. `limit` values are strictly increasing
5. no `limit` is zero

Blocks that fail any of these invariants MUST be rejected.

The alignment of a `LINKS` block is 4.

## Arbitrary size arrays

The format can represent arrays of up to 2^32 elements.

An arbitrary-size array is defined as either:

- a single `DATA`, `SLOTS`, or `TABLE` block, or
- a `LINKS` block that is the root of a link tree.

Each `LINKS` block is at most block-sized array of links. Each link points to:

1. inner node: a `LINKS` block that is the root of a sub-tree
2. leaf node: a block that carries individual elements of the array.

Writers SHOULD balance the tree, but the format permits unbalanced trees. In
particular, inner nodes and leaf nodes are allowed to be intermixed at the same
level.

Each leaf of a link tree is a block of type `DATA`, `SLOTS`, or `TABLE`. Mixed
trees are permitted. (Constrained readers only see one leaf of a tree at a time,
so rejecting inconsistent trees would be needlessly complicated.)

Link trees MUST NOT use `LINKS` blocks as leaf nodes. Implementations are free
to assume that any `LINKS` block is an inner node.

### Traversal

To access an element at global index `N` in a link tree, one must typically
descend down into inner nodes in order to locate the leaf containing that element.

Each `LINKS` block defines its own *local* index space starting at 0. A link’s
`limit` is a cumulative count in that enclosing `LINKS` block (not a global
index). Readers need to adjust for this when descending down the tree.

When descending into a link, implementations MUST check that the child node's
content length matches what the parent states.

The following algorithm can be used for tree traversal:

1. Set `index` to queried element index.
2. Find the link `i` whose limit is the smallest such that `limit[i] > index`.
3. Set `position` to `limit[i-1]`, or `0` if `i` is 0. This is the starting
   global index of link `i`'s content.
4. Set `stated_content_length` to `limit[i] - position`.
5. Update `index -= position` to convert to a local index within the child.
6. Descend into child `i`.
7. Calculate `actual_content_length` of the current node, verify that
   `stated_content_length == actual_content_length`.
8. If the node is a leaf (i.e., not a `LINKS` block), return the element at `index`.
9. Else, repeat from step 2.

Content length of a `LINKS` inner node is the maximum `limit` of its links.
Content length of a leaf block is its element count, per the block type
specification.

## Data model

### Structs

Every data structure stored in this format must be rooted in a single block.
Most typically, this will be a `TABLE` block representing a struct.

`TABLE` allows for representing heterogeneous elements. The schema MUST specify
an _index_ for each member of the struct, which will be its position in the table.

Indices have to be unique but do not have to be consecutive. Any gaps in the
indices will be filled with `NULL` entries.

Implementations MUST allow `TABLE`s that are longer than the expected number of
struct members, to allow future extensions. Implementations MUST also allow
`TABLE`s that are shorter; in that case, all missing members are assumed to be
`NULL`.

Encoding-wise, every field is optional and can be `NULL`. Schema MAY impose a
"required" constraint on some members; if such members are missing or `NULL`,
the block MUST be rejected.

In order to simplify reader implementation, a struct MUST fit into a single
`TABLE` block, and cannot be split into a link tree.

The following sections describe which other data types are recognized, and how
to store them in a `TABLE`.

### Links in TABLEs

On the encoding level, every type of block is an array. On the data model level,
this is no longer true:

* a `TABLE` block may represent a struct
* a `DATA` block may represent a single custom fixed-size type (see below).

When using a `LINK` entry in a schema-aware `TABLE`, the `limit` field is as follows:

* If the pointed-to item is an array, the `limit` MUST be the count of elements
  in the array.
* If the pointed-to item is a struct, the `limit` SHOULD be 1.
* Implementations MAY define custom rules for custom fixed size types. In the
  absence of such rule, we recommend using `limit` of 1.

Because `limit` of 0 is reserved, zero-length arrays MUST NOT be linked.
Instead, they MUST be stored as a `BLOCK` containing an empty `DATA`, `SLOTS`,
or `TABLE` block. (Schema MAY allow representing an empty array with a `NULL`
entry.)

### Fixed-size types

#### Integer-like types

The following types of integers are allowed:

`u8` `u16` `u32` `u64` `i8` `i16` `i32` `i64`

Enums are represented as an integer of a size appropriate to the number of
values. (most commonly `u8`).

Booleans are represented as an `u8` enum with two allowed values `TRUE = 1` and
`FALSE = 0`.

When an integer is a direct member of a `TABLE` (i.e., a struct field), it is
stored not by its declared type size, but by its actual value size.

The maximum allowed representation is determined by the declared type:

- `u8`, `i8`: `INLINE` only (all values fit in 13 bits).
- `u16`, `i16`, `u32`, `i32`: up to `DIRECT4`.
- `u64`, `i64`: up to `DIRECT8`.

Writers MUST NOT exceed the maximum representation for the declared type.
Writers SHOULD use the smallest representation that fits the actual value:
`INLINE` if the value fits in 13 bits (counting sign bit for signed integers),
`DIRECT4` if it fits in 32 bits, otherwise `DIRECT8`.

Readers MUST accept any representation up to the maximum for the declared type,
and sign-extend or zero-extend the value to the full width of the type. Readers
MUST reject representations that exceed the maximum (e.g., `DIRECT8` for a
`u32` field, or `DIRECT4` for a `u8` field).

Integer values that are elements of an array (stored in a `DATA` block) are
stored at their declared type size, as per the `DATA` block format.

#### Floats

The following types of floats are allowed: `f32` `f64`

`f32` is stored as `DIRECT4`. `f64` is stored as `DIRECT8`.

#### Custom composite types

Implementations MAY define custom fixed-size composite types, such as
heterogeneous n-tuples or fixed-size arrays. Custom types MUST have a fixed size
and be built out of other fixed-size types.

The size of a composite type MUST NOT be larger than the largest available size
in a `TABLE` (excluding the block header, entry count, and one entry offset),
that is, 8191 - 6 = 8185 bytes.

The alignment requirement of a composite type is equal to the largest alignment
of any of its components, that is, the largest inner primitive type. In a
heterogeneous type, implementations MUST ensure that all individual sub-fields
are correctly aligned.

When a custom fixed-size type is a direct member of a `TABLE`, it can be stored as:

* `DIRECT4` if its size is exactly 4 and alignment is at most 4
* `DIRECT8` if its size is exactly 8 and alignment is at most 8
* `DIRECTDATA` if its alignment is 1
* `BLOCK` embedding a `DATA` sub-block otherwise.

When storing a fixed-size array as `DATA`, its `elem_size` is the element size,
and element count (for link `limit`s) is the number of elements in the array.

For a heterogeneous type, `elem_size` SHOULD be set to the total size of the
composite type, and element count to 1.

#### Fixed-size arrays

This section describes arrays whose element count is explicitly specified in the
schema. For variable-size arrays, see [Arrays](#arrays).

On the wire, fixed-size arrays are encoded identically to variable-size arrays
(see [Arrays](#arrays)). The schema-defined element count is not stored in the
format; a schema-aware reader MUST verify that the actual element count matches
the expected count.

**Multi-dimensional arrays of primitives:** If the schema defines an array of
fixed-size arrays of primitives (i.e., a multi-dimensional array), the writer
SHOULD store it as a flat array of the innermost primitive type, with the total
element count being the product of all dimensions. The application layer is
responsible for reshaping the flat data into the appropriate dimensions.

For example, a `[3][4]u32` is stored as a flat `DATA` array of 12 `u32`
elements. A reader expecting this shape MUST verify that the element count is 12.

**Fixed-size arrays as elements:** A fixed-size array of fixed-size elements is
itself considered a fixed-size type with size equal to `count * padded_element_size`
and alignment equal to the element's alignment. As such, it may appear as an
element of another (variable-size) array.

When the innermost element is a primitive, the multi-dimensional flattening rule
above applies: the outer array stores flat primitive data, and the application
layer reshapes.

### String types

#### Text strings

Text strings are stored as UTF-8 byte strings, that is, arrays of `u8` (see
below). Strings are **not** null-terminated, any null bytes are considered part
of the string.

#### Byte strings

Variable-size byte strings are stored as arrays of `u8` (see below).

When a byte string is a direct member of a struct, it can be stored as
`DIRECTDATA` on the TABLE heap (if it fits), or as an arbitrary-size `DATA`
array.

### Arrays

Arrays are stored as variable-size on the wire. If a schema prescribes a fixed
element count, the reader MUST verify that the actual count matches the schema.

If the whole array fits into a single sub-block of a containing block, it can be
stored as a `BLOCK`. Arrays of alignment-1 elements (byte strings) can
alternatively be stored as `DIRECTDATA`, saving 2 bytes of overhead per entry.

There are three possible representations of an array:

#### 1. Flat data (`DATA` array)

If the elements are all primitives or custom composite types, then the array
will be represented as an arbitrary-size `DATA` array, which the reader can map
into its elements in memory directly.

#### 2. Bytestring array

Arrays of alignment-1 byte strings are represented as a link tree whose leaves
can be a mix of `SLOTS` and `TABLE` blocks:

- **`SLOTS` blocks** hold byte strings that fit directly in a slot.
- **`TABLE` blocks** hold elements that are too large for a single `SLOTS`
  block. Each entry in such a `TABLE` is an arbitrary-size flat data array
  representing the oversized bytestring.

Writers can pack elements into `SLOTS` blocks sequentially, and when an element
does not fit in a `SLOTS` block, they emit a single-entry `TABLE` block carrying
the root of the oversized element's link tree.

#### 3. Table array

For complex element types (structs, nested arrays), the array is represented as
an arbitrary-size link tree of `TABLE` blocks.

Each entry of the table MUST be either an inlined `BLOCK` with the value, or a
`LINK` to such block. The rules for `limit` in `TABLE`s apply, see [Links in
TABLEs](#links-in-tables).

#### Multi-dimensional arrays

**Arrays of fixed-size elements**, including fixed-size arrays, SHOULD be
flattened into a single flat `DATA` array, with the reader responsible for
reshaping the data. The element count of such array is the element count of the
outermost dimension.

**Arrays of variable-size arrays** are represented as an arbitrary-size `TABLE`
array whose each element is a sub-array.

### Future extensions

#### Dictionaries

One option for storing dictionaries is to use two struct members: a `keys` array
and a `values` array. They need to be the same size, but they may be of
different types and stored differently.

#### Tagged unions

A tagged union type is just a struct whose all members are optional. The writer
is responsible for encoding exactly one member, and the reader needs to check
that this is the case.

## Fitting

When serializing a data structure, the writer must map the logical structure to
physical blocks. Because the maximum block size is 8 KiB, large structures must
be split across multiple blocks joined by links. The process of deciding what
fits in the current block and what gets externalized is called _fitting_.

Fitting proceeds bottom-up: a field's encoded size is only known after it has
been serialized, so writers MUST serialize children (sub-structs, arrays) before
the parent struct that contains them.

The _encoded size_ of a field is the total number of bytes it occupies in a
block: for a `DIRECT4`, 4 bytes; for a `DIRECT8`, 8 bytes; for a `DIRECTDATA`,
2 bytes (t16 header) plus the data length; for a sub-block (sub-struct or inline
array), the block size including its 2-byte block header; for a link, 36 bytes.

### Struct Member Fitting

When building a single-block `TABLE` for a struct, the writer must determine
which members are stored inline, which are placed on the block's heap (`DIRECT4`,
`DIRECT8`, `DIRECTDATA`, or `BLOCK`), and which are externalized as `LINK`s to
other blocks.

The following rules apply:

1. **Inline values:** Primitive integers that fit within 13 bits SHOULD be
   stored as `INLINE`.

2. **Small values:** Any field (primitive, array, string, or sub-struct) whose
   encoded size is no larger than the size of a Link (36 bytes) MUST NOT be
   externalized as a `LINK`. It MUST be stored on the heap as `DIRECT4`,
   `DIRECT8`, `DIRECTDATA`, or `BLOCK` (depending on the type), because the link
   would be at least as large as the data it points to, while adding child block
   overhead.

3. **Filling the remaining space:** For fields too large to be considered
   "small values" but not required to be linked, the writer must decide how to
   utilize the remaining space in the block.

   Determining the globally optimal packing is a variation of the knapsack
   problem. A recommended heuristic is **"smallest first"**: evaluate the size
   of all remaining fields, and add them to the heap in increasing order of size
   until the block is full. Any fields that do not fit will be externalized as
   `LINK`s to newly allocated blocks.

   This algorithm maximizes the _number_ of inline fields, minimizing the total
   number of blocks.

   *Note:* This heuristic does not account for alignment padding — a set of
   small fields may require more padding than fewer larger fields. Writers
   that need tighter packing MAY use alignment-aware size estimates, or solve
   the knapsack with a dynamic programming approach, which is tractable given
   typical field counts and the small block capacity.

4. **Alignment packing:** When placing `DIRECTDATA`, `DIRECT4`, `DIRECT8`, or
   `BLOCK` fields on the heap, writers SHOULD order them to minimize padding
   bytes, using the following algorithm:
   - Separate all heap-bound fields into groups based on their required
     alignment.
   - Keep track of the `current_offset` on the heap (which starts at `4 + 2 *
     entry_count`).
   - Loop while there are unallocated fields:
     - Determine the available alignment at `current_offset`. For instance, if
       `current_offset` is a multiple of 8, the available alignment is 8. If
       `current_offset` is 12 (a multiple of 4 but not 8), the available
       alignment is 4.
     - Among the remaining fields, prefer the one with the highest alignment
       requirement that fits the available alignment. Within the same alignment
       class, prefer fields whose size is a multiple of their alignment
       ("alignment-preserving" fields), since they do not degrade the offset
       alignment for subsequent placements.
     - If a suitable field is found, place it at `current_offset` and advance
       by the field's size.
     - If no remaining field fits without violating its alignment, add 1 byte
       of padding and try again.

The minimum alignment requirement of a TABLE heap entry is 2, implementations
MAY use this fact to optimize alignment packing.

Because alignment padding (step 4) affects total heap consumption, writers
SHOULD account for padding when evaluating whether fields fit in step 3. No
simple greedy rule is optimal in all cases, however. Space-conscious writers
can, e.g., try all permutations of heap-bound fields (feasible for typical field
counts) to find the best fit.

A practical approach is to interleave steps 3 and 4: run the alignment packing
algorithm on the candidate set of fields, and if the result exceeds the block
size, drop the largest non-mandatory field and retry.

### Array Representation

As described in [Arrays](#arrays), there are three representations of an array:
flat `DATA`, bytestring (`SLOTS` with `TABLE` fallback), and `TABLE`. The
following algorithms describe how to build each representation. In all cases, if
more than one block is produced, the blocks are combined into a link tree.

#### 1. Fixed-Size Primitives (`DATA`)

If the array elements are of a fixed-size type, the array MUST be represented as
a flat `DATA` array:

- Serialize elements contiguously.
- If the total size exceeds the block size limit, chunk the array into
  block-sized `DATA` blocks and build a `LINKS` tree whose leaves are those
  `DATA` blocks.

#### 2. Complex elements (`TABLE`)

If the array elements are structs or arrays, the array MUST be represented as a
`TABLE` of blocks.

For the most typical case, where the reader is expected to read the array in order,
and the writer is trying to minimize the total number of blocks, the following
straightforward algorithm SHOULD be used:

1. Start a new `TABLE` block.
2. Follow these steps for each element:
   1. Serialize the element into a block, set `elem` to that block.
   2. Special case: if the encoded element is too big to embed into a table at
      all, set `elem` to a `LINK` to the block instead.
   3. Add `elem` into the current block.
   4. If it does not fit, optimize block storage by alignment-packing (see
      [Struct Member Fitting](#struct-member-fitting) step 4).
   5. If it still does not fit, seal the current block, start a new one and
      retry.
3. If more than one `TABLE` block is produced, build a `LINKS` tree over them.

This algorithm places all blocks inline that can be inlined, minimizing the
overall number of blocks.

In special cases, implementations MAY choose a different representation:

* If random access is required, or if the reader is not expected to read every
  element, a more practical approach is to `LINK` out every block larger than a
  link. For every single access, only the link index plus the requested block is
  transferred, without wasting reader's memory by blocks that are not currently
  needed.
* In certain usecases, it might be practical to choose an application-specific
  inline size bound, and only inline blocks smaller than that bound.

#### 3. Byte strings (`SLOTS` + `TABLE`)

If the array elements are variable-size byte strings (e.g., strings or raw byte
arrays), the writer packs them sequentially into `SLOTS` blocks, falling back to
`TABLE` for elements that do not fit in a single slot:

1. Start a new `SLOTS` block.
2. For each element:
   1. If the element fits in the current `SLOTS` block, append it.
   2. If it does not fit but *could* fit in an empty `SLOTS` block, seal the
      current block, start a new one, and append there.
   3. If the element is too large to ever fit in a `SLOTS` block, seal the
      current `SLOTS` block (if non-empty) and emit a `TABLE` block carrying the
      oversized element as its only entry.
3. Combine all produced blocks (both `SLOTS` and `TABLE`) into a link tree.

Implementations MAY optimize utilization of the `TABLE` block from step (2.3) by
packing subsequent elements into the same `TABLE` block.

## Note on deep nesting

The format intentionally does not specify a maximum nesting depth.
Implementations need to take care not to blindly recurse into the structure,
which may cause stack overflows.

We note that it is possible to traverse an arbitrarily deep structure safely, by
following the links and evicting blocks from memory as they are read. The reader
can always retain just the root block of the entire structure, plus an index
into a particular deep link tree, which gives it an ability to always reach the
same place by re-starting from the root.

It is up to the reader to not implement schemas that are inherently too deep for
said reader to safely traverse.

## Hashing

Links are defined as `HMAC-SHA256(key, message)`, where `message` is the raw
bytes of the pointed-to block.

The reader and writer need to agree on a shared value for `key`. The recommended
scenario is as follows:

1. At start of a session, reader randomly chooses a 32-byte `key` and sends it to
   the writer.
2. Writer encodes the data structure they wish to send into the wire format,
   using `key` for all links.

The meaning of "session" is left to the implementation. Typically, the session
should be short-lived, on the order of minutes to hours. The lifetime of the key
may even be limited to exchanging just the one data structure, re-keying for
each new exchange.

Every time the reader requests a block from the writer, they MUST verify that
the HMAC of the block matches the link value, and reject the block if it doesn't.

In a trusted environment, the reader and writer can pre-share a constant `key`
value.

### Rationale

One motivating reason for this wire format is to enable a constrained device to
access data structures much larger than its memory in a reliable manner. The
device may request the same data block multiple times, as it evicts it from
memory and accesses it again at a later time. Some security invariants rest on
the assumption that any such read returns the exact same content.

While SHA-256 is assumed to be collision resistant, it is nevertheless
theoretically possible that a birthday-style attack (esp. if facilitated by a
quantum computer) could find two different blocks that hash to the same value.
If such two blocks were ever found, their collision could be repeatedly replayed
for any user of this wire format.

Using a short-lived key for HMAC mitigates the risk, because it is no longer
possible to keep replaying the same collision. An attacker would need to
generate a new collision for every short-lived session, which we believe to be
computationally infeasible.

## Format variants

Hashbuffers format as described in this spec could be denoted as `hashbuffers-13/32`,
for 13-bit block size and 32-bit large array size.

The following variations seem to make sense:

* Very Large Arrays (`hashbuffers-13/64`): by modifying the `link` type to use a
  64-bit `limit`, and tweaking the alignment requirements accordingly, it is
  possible for the modified protocol to support arrays of up to 2^64 elements.

* 29-bit sizes and offsets (`hashbuffers-29/64`): by converting `t16` type to
  `t32` with 3 bits for `parameters` and 29 bits for `number`, it is possible
  for the modified protocol to support individual blocks of up to 512 MiB in
  size. With blocks this large, it makes little sense to keep the link limits
  small -- typically you would go with 64-bit large arrays too.

* Big-endian byte order (`hashbuffers-*-be`), if required by the platforms in
  question.

Note that the format intentionally does not explicitly carry any of those
parameters. The proposed variants are different instances of the format and
cannot be used interchangeably; implementers must agree on the variant to use.
