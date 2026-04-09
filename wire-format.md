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

### Offsets and bounds

All relative offsets (in structs, slotted arrays, etc.), are always relative to
**the start of the block**.

Zero offsets are invalid and MUST be rejected. (Such offset would point to the
block itself, creating a cycle.)

Within a given block, the maximum allowed offset is equal to the block size.

When performing a read in a context of a particular block, the read MUST NOT
exceed the bounds of the block.

### Types of blocks

#### `0b00 TABLE`

A table of heterogeneous elements.

`[t16 block_header] [t16 entry_count] [t16 entry_offset 0, 1, 2,...] [heap]`

A minimum valid size of a `TABLE` block is 4, to fit the `entry_count` field.

`entry_count` is a tagged u16 with the following structure:

- `flags` 3 bits. All zero, other values are reserved for future use.
- `count` 13 bits: count of table entries.

`entry` is a tagged u16 with the following structure:

- `type` 3 bits
- `offset` 13 bits

##### Entry types

The following values are recognized for type tags of a table entry:

- `0b000 NULL` - skipped. SHOULD be all zero but impls MAY ignore nonzero values.
- `0b100 INLINE` - `offset` is interpreted as an integer value of the field.
- `0b101 DIRECT` - `offset` points to a raw value of schema-defined size on the
  heap (int or float of more than 13 bits, or a fixed-size array of fixed-size
  elements). The value has no header or wrapper. `offset` MUST be properly
  aligned to the value’s requirements.
- `0b110 BLOCK` - `offset` points to a sub-block on the heap. The sub-block has
  its own block header and is interpreted as a complete block. `offset` MUST be
  aligned to the sub-block’s alignment requirement.
- `0b111 LINK` - `offset` points to a raw link on the heap. The link has no
  header or wrapper. `offset` MUST be aligned to link alignment.

Values `0b001` `0b010` and `0b011` are reserved for future use.

Offsets for `DIRECT`, `BLOCK`, and `LINK`, MUST point into the heap, that is,
_after_ the end of the entries list, and _before_ the end of the block.

The `limit` field of a `LINK` entry is the count of elements in the linked
array. However, "count of elements" can be schema-dependent. See [Links in
TABLEs](#links-in-tables) for details.

##### Alignment requirements

The alignment of a `TABLE` is equal to the largest alignment of any of its entries.

As a sub-algorithm of [Validation](#validation), the following algorithm can be used to
determine the alignment of a block:

1. Assume that the block is 2-aligned in its parent.
2. Run the [Validation](#validation) algorithm for the block header only.
3. Set `max_align = 2`.
4. Walk all entries:
   * if a `LINK` is found, bump `max_align` to `LINK_ALIGN` (4 bytes)
   * if a `DIRECT` is found, bump `max_align` to schema-defined alignment of that field
   * if a `BLOCK` is found, check the block's alignment and run this algorithm
     recursively. Then bump `max_align` to the block's alignment requirement

The resulting maximum value `max_align` is the alignment of the block.

##### Validation

Upon receiving a `TABLE` block, the reader MUST perform these validation steps:

1. Check that the block size is at least 4.
2. Check that `flags` of `entry_count` are all zero.
3. Calculate `heap_start = 4 + 2 * entry_count`, and check that `heap_start` is
   no greater than block size.

For each entry, the following checks MUST be performed:

1. The entry is not a reserved type (`0b001` `0b010` `0b011`).
2. For `DIRECT`, `BLOCK`, and `LINK`, check that `heap_start <= offset < size`.
3. For `DIRECT`, if schema is available:
  - `offset <= size - entry_size`
  - `offset % entry_align == 0`
4. For `LINK`:
  - `offset <= size - LINK_SIZE` (36 bytes)
  - `offset % LINK_ALIGN == 0` (4 bytes)
  - Then examine the link and check that its `limit != 0`.
5. For `BLOCK`:
  1. `offset <= size - 2`
  2. `offset % BLOCK_ALIGN == 0` (2 bytes)
  3. Read `block_size` from the header at `offset`. Check that `offset + block_size <= size`.
  4. Determine the block's alignment requirement and check that it is properly aligned.
  5. Validate the block at `offset` as a complete block.

A block that fails any of these checks MUST be rejected.

#### `0b01 DATA`

Non-delimited array spanning the `size` of the block

`[t16 block_header] [data...]`

`DATA` blocks represent arrays of fixed-size elements. The array has an _element
size_ and _alignment_ defined by the schema. Writers MUST add padding:

* after the block header, to achieve the required alignment for the first
  element, and
* after each element, if its size is not a multiple of its alignment.

This specifically means that padding after the last element (if any) is always
included, in order to simplify reader implementation at the cost of wasted
trailing padding bytes.

To achieve that, the following values are defined:

- array data starts at offset `start_offset = max(align, 2)`, to account for the
  block header
- `padded_element_size` is the element size rounded up to the nearest multiple of
  its alignment
- element count is `count = (size - start_offset) / padded_element_size`

An empty array is represented by a block where `count` is 0, that is, block size
is equal to `start_offset`. For alignment of 2 or less, this is just the `DATA`
header; for larger alignments, padding up to `start_offset` MUST be included.

Upon receiving a `DATA` block, a schema-aware reader MUST perform these
validation steps:

1. Check that the block size is at least `start_offset`.
2. Check that the data size (`size - start_offset`) is a multiple of
   `padded_element_size`.

A schema-less reader doesn't know size and alignment of elements, so it can only
check that `size >= 2`.

A block that fails any of these checks MUST be rejected.

The alignment of a `DATA` block is equal to element alignment, but at least 2.
I.e., it is equal to `start_offset`.

#### `0b10 SLOTS`

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

#### `0b11 LINKS`

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

### Link trees

The **root** of a link tree is a `LINKS` block, that is, at most block-sized
array of links. Each link points to:

1. inner node: a `LINKS` block that is the root of a sub-tree
2. leaf node: a block that carries individual elements of the array.

Writers SHOULD balance the tree, but the format permits unbalanced trees. In
particular, inner nodes and leaf nodes are allowed to be intermixed at the same
level.

The leaves of a link tree can either be `DATA`, `SLOTS`, or `TABLE` blocks.
Their interpretation is schema defined.

Link trees MUST NOT use `LINKS` blocks as leaf nodes. Implementations are free
to assume that any `LINKS` block is an inner node.

#### Traversal

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

Content length of:
 - `DATA` leaf: schema-defined length of its array
 - `SLOTS` leaf: count of its elements, that is, `offset_count - 1`
 - `TABLE` leaf: count of its entries
 - `LINKS` inner node: maximum `limit` of its links, that is, the `limit` of the
   last link

### Linking to arbitrary size arrays

Every table field contains either a _primitive value_, or an _array_. (On the
encoding level, structs are represented as tables, which are arrays.)

Primitive values are never linked, because the size of the link is larger than
the value itself.

When linking to an array, the link's `limit` field is the count of elements in
the array. Because `limit` of 0 is reserved, zero-length arrays cannot be
linked, and instead MUST be stored as a `BLOCK` containing an empty `DATA`,
`SLOTS`, or `TABLE` block.

(Schema MAY allow for representing an empty array with a `NULL` entry.)

In all cases, the link may either point to a block that is a leaf node (that is,
a `DATA`, `SLOTS`, or `TABLE` block), or to a root of a link tree.

A link's `limit` MUST always match the element count of the linked array.

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

### Fixed-size types

#### Integer-like types

The following types of integers are allowed:

`u8` `u16` `u32` `u64` `i8` `i16` `i32` `i64`

Enums are represented as an integer of a size appropriate to the number of
values. (most commonly `u8`).

Booleans are represented as an `u8` enum with two allowed values `TRUE = 1` and
`FALSE = 0`.

An integer SHOULD be stored as `INLINE` if its actual value fits in 13 bits or
less (counting sign bit for signed integers), regardless of its declared type
size.

When reading a signed integer from an `INLINE` field, the reader MUST
sign-extend the value to the full width of the type.

Integer values larger than 13 bits MUST be stored as `DIRECT`.

#### Floats

The following types of floats are allowed: `f32` `f64`

Floats are stored as `DIRECT`.

#### Custom fixed-size types

Implementations MAY define fixed-size types that are not primitives (such as
tuples or packed structs). Such types MUST specify their alignment requirements.

If the size is no larger than a link (36 bytes), it MUST be stored as `DIRECT`.

Larger values can be stored as `DIRECT`, if they fit inside the struct, or as a
`LINK` to an arbitrary size `DATA` array.

#### Fixed-size arrays

This section describes arrays whose element count is explicitly specified in the
schema. For variable-size arrays, see [Arrays](#arrays).

Such an array of fixed-size elements can be stored as `DIRECT`, if it fits
inside the block (see [Fitting](#fitting)).

The alignment of an array is equal to its element's alignment.

Arrays that can fit inside a block SHOULD NOT be stored as `BLOCK`, to avoid
unnecessary block overhead.

A fixed-size array of fixed-size elements is itself considered a fixed-size
type, and may be an element of another array. When other constraints allow,
such arrays-of-arrays can be stored as flat data.

Arrays whose element size is smaller than a block can be stored as a `LINK` to
arbitrary size flat `DATA`. If the element size is larger than a block, the
array MUST be represented as a variable-size `TABLE` array (see
[Arrays](#arrays)).

### String types

#### Text strings

Text strings are stored as UTF-8 byte strings, that is, arrays of `u8` (see
below). Strings are **not** null-terminated, any null bytes are considered part
of the string.

#### Byte strings

Variable-size byte strings are stored as arrays of `u8` (see below).

### Arrays

Fixed-size arrays of primitives or small fixed-size elements are considered a
fixed-size type, and described under [Fixed-size arrays](#fixed-size-arrays).

All other arrays are stored as variable-size. In particular, if a schema
prescribes a fixed size to an array of variable-size elements, the wire format
is still a variable-size array, but the reader MUST check that the actual size
matches the schema.

Arrays are homogenous: their elements are all of the same type. In particular,
because element representation is determined by element size, writers need to
choose a common representation that works for every value in the array.

If the whole array fits into a single sub-block of a containing block, it can be
stored as a `BLOCK`.

If the whole array fits into a single block, it should be stored as a `LINK` to
that block. Otherwise, the array must be stored as a link tree.

There are three possible representations of an array:

#### 1. Flat data

If the elements are all of the same fixed size and smaller than a block -- that
is, a single array element can fit in a `DATA` block -- then the array will be
represented as an arbitrary size `DATA` array, which the reader can map into its
elements in memory directly.

#### 2. Slotted array

Arrays of variable-size byte strings, where every element can by itself fit in a
`SLOTS` block, can be represented as an arbitrary size `SLOTS` array.

This representation is especially appropriate when:

- *most* elements are roughly link-sized (36 bytes) or shorter, or
- the entire array fits in a single `SLOTS` block.

#### 3. Table

For complex element types (structs, arrays, arbitrary size byte strings), the
array can be represented as an arbitrary size `TABLE` of its elements.

Each entry of the table MUST be either an inlined `BLOCK` with the value, or a
`LINK` to such block. The rules for `limit` in `TABLE`s apply, see [Links in
TABLEs](#links-in-tables).

### Link trees

The root of a link tree is a `LINKS` block. This block can either be stored as a
`BLOCK`, if it fits in the containing table. Otherwise, it is stored as a `LINK`.

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
block: for a primitive, its byte width; for a sub-block (sub-struct or inline
array), the block size including its 2-byte block header; for a link, 36 bytes.

### Struct Member Fitting

When building a single-block `TABLE` for a struct, the writer must determine
which members are stored inline, which are placed on the block's heap (`DIRECT`
or `BLOCK`), and which are externalized as `LINK`s to other blocks.

The following rules apply:

1. **Inline values:** Primitive integers that fit within 13 bits SHOULD be
   stored as `INLINE`.

2. **Small values:** Any field (primitive, array, string, or sub-struct) whose
   encoded size is no larger than the size of a Link (36 bytes) MUST NOT be
   externalized as a `LINK`. It MUST be stored on the heap as `DIRECT` or
   `BLOCK` (depending on the type), because the link would be at least as large
   as the data it points to, while adding child block overhead.

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

4. **Alignment packing:** When placing `DIRECT` or `BLOCK` fields on the heap,
   writers SHOULD order them to minimize padding bytes, using the following
   algorithm:
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

Because alignment padding (step 4) affects total heap consumption, writers
SHOULD account for padding when evaluating whether fields fit in step 3. No
simple greedy rule is optimal in all cases, however. Space-conscious writers
can, e.g., try all permutations of heap-bound fields (feasible for typical field
counts) to find the best fit.

A practical approach is to interleave steps 3 and 4: run the alignment packing
algorithm on the candidate set of fields, and if the result exceeds the block
size, drop the largest non-mandatory field and retry.

### Array Representation

As described in [Arrays](#arrays), array elements can be represented as
fixed-size `DATA`, variable-size `SLOTS`, or individually determined via
`TABLE`. The following algorithms recommend a specific choice and describe how
to build each representation.

#### 1. Fixed-Size Primitives (`DATA`)

If the array elements are fixed-size primitive values (e.g., `u32`, `f64`), the
array MUST be represented as `DATA`.
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
      all (i.e., `element_size + sizeof(block_header) + sizeof(entry_count) +
      sizeof(entry_offset) > max_block_size`), set `elem` to a `LINK` to the
      block instead.
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

#### 3. Byte strings (`SLOTS` vs `TABLE`)

If the array elements are variable-size byte strings (e.g., strings or raw byte
arrays), the writer must choose between `SLOTS` and `TABLE` representation.

**Choosing `SLOTS`:** The `SLOTS` representation is available when every element
can fit in a single `SLOTS` block (at most ~8 KiB minus overhead). `SLOTS` is
generally preferred because it stores elements as raw bytes with no per-element
block header, saving 2 bytes per element compared to `TABLE` (where each element
requires a `DATA` sub-block header).

To build a `SLOTS` array, pack elements sequentially into `SLOTS` blocks. When
adding the next element would exceed the block size, seal the current block
and start a new one.

If multiple `SLOTS` blocks are produced, build a `LINKS` tree over them.

**Choosing `TABLE`:** If any element is too large to fit in a single `SLOTS`
block, the array MUST be represented as `TABLE`. In this representation, each
byte string is stored as a `BLOCK` (containing a `DATA` sub-block of `u8`
elements) or as a `LINK` to an external `DATA` block or `DATA` tree. The fitting
rules from [section 2 (Complex elements)](#2-complex-elements-table) apply.

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
