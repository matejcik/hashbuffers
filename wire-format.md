Wire format specification draft
===============================

# Overview and motivation

The purpose of this wire format is to enable resource-constrained devices, such
as hardware cryptocurrency signers, to securely and efficiently access data
structures much larger than their memory.

The format splits the data structure into blocks with a maximum size of 8192
bytes, replacing large sub-structures with content-addressed links to other
blocks.

The maximum total size of the data structure is effectively unlimited. The
maximum size of any single array is 2^32 elements; in particular, the maximum
size of any given text string or byte array is therefore 4 GiB.

Because the sub-blocks are content-addressed, the reader can verify that
repeated access to the same block returns the exact same content. The reader
therefore doesn't need to store all the data to be able to re-read earlier
parts.

The wire format is designed in a way that allows zero-copy reading on a
little-endian platform (which is the overwhelming majority of contemporary
platforms, including ARM based microcontrollers), allowing readers to skip
parsing step and avoid additional memory allocations.

Understanding the encoded data structure depends on knowledge of the schema. A
schema-less reader can only understand the structure enough to reliably follow
all links and fetch all the data.

We assume that the reader and the writer agree on the schema schema for the data
structure, and their communication protocol allows (a) transfer of an initial
block (or just a commitment) and (b) requesting any block by its digest.

## Non-goals

The format is designed to **provide access** to any data structure chosen by the
writer. It specifically does not concern itself with *validity* nor *provenance*
of the data beyond its encoding. An untrusted writer can use the format and
protocol to transfer any data to the reader, it is up to the reader to
critically examine it.

The format does not provide any chain of trust, beyond the fact that the initial
block (which can be reduced to a digest) commits to the whole content of the
structure. If a chain of trust is required, it is up to the users to establish
it on top of the format.

# Data types

## Primitives

The format supports primitive values of 1, 2, 4, or 8 bytes.

Depending on context, these may be interpreted as:

- unsigned integers `u8` `u16` `u32` `u64`
- twos-complement signed integers `i8` `i16` `i32` `i64`
- IEEE754 floats `f32` `f64`

All values are stored in **little-endian** byte order, and their alignment
requirement is their size.

## Tagged u16

A **tagged u16,** denoted `t16`, is an u16 value with the following structure:

- `parameters` 3 bits
- `number` 13 bits

`parameters` are context-dependent and each usage specifies their meaning separately.

`number` denotes a size or an offset into a block (or, in struct context, can be
overloaded to specify an inline numeric value). This means that the maximum size
/ offset within a block is 8192 bytes.

Negative offsets are not supported.

## Link

A link is a 36-byte structure:

- `u8 digest[32]` — 32-byte HMAC-SHA256 digest of the pointed-to child block
- `u32 limit` — the exclusive upper bound index for the pointed-to content

The alignment of a link structure is 4, because the alignment of `limit` is 4
(and the 32-byte digest keeps that alignment).

The `limit` denotes an upper bound (exclusive) of an index into the linked
content. For a stand-alone link, this is identical to the length of the content.
In a `LINKS` block, this is the length of content from the start of the `LINKS`
block up to and including this link.

`limit == 0` indicates that this link points not to an array, but to a single
element, or leaf block.

# Blocks

Data in the format is organized in individual **blocks**. Each block may
contain a struct or an array.

## Alignment

The wire format is designed to work in zero-copy mode (on major CPU platforms
with little-endian byte order), that is, readers can directly interpret memory
content as the appropriate value.

To make this possible, writers MUST ensure that all data in the block is
properly aligned, that is:

**Every primitive value MUST be stored at an offset that is a multiple of the
value size**, relative to start of the block.

**Every sub-block MUST be aligned to the maximum alignment of any values
contained inside it,** so that the alignment relative to block start is also
correct for any values and sub-sub-blocks inside it.

Writer is free to insert padding bytes to ensure the alignment.

A zero-copy reader may then place the block in memory so that the alignment
holds for the target platform — that is, block start must be aligned to platform
`max_align_t` (typically 8 or 16 bytes).

Reader SHOULD perform platform-appropriate alignment checks to ensure that a
primitive value will be read out correctly; more specifically, it MUST NOT
attempt a load values from unaligned addresses if there is risk of reading out
invalid value.

## Block header

Every block is prefixed by a tagged u16 header specifying the block type and
size:

- `type` 2 bits
- `reserved` 1 bit
- `size` 13 bits

The `size` includes the 2-byte block header itself. I.e., the maximum size of a
block with header is 8192 bytes.

(Implicitly, the minimum value of `size` must be 2, for the block header itself.)

## Offsets

All relative offsets (in structs, slotted arrays, etc.), are always relative to
**the start of the block**.

## Types of blocks

### `0b00 STRUCT`

A struct prefixed by a vtable size + vtable entries

`[t16 block_header] [t16 vtable_count] [u16 vtable_entry 0, 1, 2,...] [heap]`

See Structs below.

### `0b01 DATA`

Non-delimited array spanning the `size` of the block

`[t16 block_header] [data...]`

`data` may start with padding to achieve proper alignment of the first
element of the array:

- array data starts at offset `start_offset = max(align, 2)`
- element count is `count = (size - start_offset) / elem_size`

Information about alignment and element size is provided by schema.

### `0b10 SLOTS`

Slotted array.

`[t16 block_header] [t16 slot_header] [u16 offset 0, 1, 2, ...] [heap]`

Tagged u16 `slot_header` has the following structure:

- `raw_entries` 1 bit
- `reserved` 2 bits
- `count` 13 bits

When `raw_entries` bit is set, individual entries are raw data, e.g., strings,
with an alignment of 1. The following properties apply:

- Offsets in the array MUST be non-decreasing.
- A sentinel offset MUST be placed at the end of the offset array / start of
  the heap. This sentinel is not counted in `count`. Its value MUST be equal
  to block `size`.
    - Readers MUST check the presence of this sentinel and reject the block
      if it is not there.
    - Given the sentinel, you can safely read out any item as `offset[n] ..
      offset[n+1]` (`n` inclusive, `n+1` exclusive).
- The first offset MUST be equal to `sizeof(block_header) +
  sizeof(slot_header) + count * sizeof(offset) + sizeof(sentinel)`

When `raw_entries` bit is unset, individual entries are sub-blocks. Slot offsets
MAY be listed in arbitrary order -- in particular, it is allowed to list the
same offset multiple times to place the same sub-block multiple times in the
array.

### `0b11 LINKS`

An array of links / an inner node of a link tree.

`[t16 block_header] [u16 parameters] [link 0, 1, 2 ...]`

The block header is followed by a two-byte parameters field which aligns the
rest of the block to 4. After it follows a non-delimited array of 36-byte links
(see Links above).

`parameters` have the following structure:

- `leaf_parent` 1 bit
- `reserved` 15 bits

If `leaf_parent` bit is set, the block is a parent-of-leaves node in a `LINKS`
tree. All the links MUST have `limit == 0`, and link directly to leaf blocks.

If `leaf_parent` bit is unset, the block is an inner node, and all links MUST
have non-zero `limit`.

In an inner node, the meaning of the link’s `limit` field is a cumulative count
of content up to and including the current link. This is to facilitate efficient
binary search: an item with index `N` will be found under the link whose `limit`
is the smallest such that `limit < N` (`limit` is exclusive so for `limit == N`,
the item `N` will be under the next link).

Readers MUST reject the block if

* `leaf_parent` bit is set and some link has `limit != 0`
* `leaf_parent` bit is unset and the `limit` values are not strictly increasing,
  or if any `limit` is zero.

Example: For an inner block of 321 items divided into max 100-item sub-blocks,
this would be the structure:

- `[t16 block_header]`
- `[u16 parameters]` — 0b0... indicating inner node
- `[hash 0] [100]` — items 0 to 99
- `[hash 1] [200]` — items 100 to 199
- `[hash 2] [300]` — items 200 to 299
- `[hash 3] [321]` — items 300 to 321

# Structs

A struct block consists of the block header, a vtable, and a data heap.

The vtable is an array of tagged offsets pointing into the data heap.

```
[t16 block_header]
[t16 vtable_header]
[vtable_entry 0]
[vtable_entry 1]
...
[vtable_entry [count-1]]
[heap]
```

`vtable_header` is a tagged u16 with the following structure:

- `flags` 3 bits. Reserved for future use.
- `count` 13 bits: count of vtable entries.

`vtable_entry` is a tagged u16 with the following structure:

- `type` 3 bits
- `offset` 13 bits

The `offset` is the same size as `size` of a block, which is enough to point to
any location within the block.

A struct cannot span multiple blocks, which puts a limit on its size: the vtable plus
all data on the heap must fit inside a single block.

## Struct types

The following values are recognized for type tags in the vtable:

- `0b000 NULL` - skipped. SHOULD be all zero but impls MAY ignore nonzero values.
- `0b100 INLINE` - `offset` is interpreted as an integer value of the field.
- `0b101 DIRECT` - `offset` points to a single raw value of schema-defined size
  (int or float of more than 13 bits, or a fixed-size array of fixed-size
  elements). `offset` MUST be properly aligned to the value’s requirements.
- `0b110 BLOCK` - a block header is located at the offset
- `0b111 LINK` - a link is located at the offset

Values `0b001` `0b010` and `0b011` are reserved for future use.

# Arbitrary size arrays

Unlike structs, which are limited to a single 8 kB block, the format can
represent arrays of up to 2^32 elements.

## Link trees

The **root** of a link tree is a `LINKS` block, that is, at most block-sized
array of links. Each link points to:

1. inner node: a `LINKS` block that is the root of a sub-tree
2. leaf node: a `DATA`, `SLOTS`, or a leaf-parent `LINKS` block that carries
   individual elements of the array.

Writers SHOULD balance the tree, but the format makes no such requirement;
in particular, inner nodes and leaf nodes can be intermixed at the same level.

(Note however that a "leaf node" in a `LINKS` tree is a leaf-parent `LINKS`
block; it is not allowed to place a `limit == 0` leaf link into a node whose
other children are inner nodes.)

There are three kinds of link trees, matching the three kinds of array blocks:

* `DATA` tree represents a single contiguous non-delimited array of fixed-size
  elements. Its leaf nodes are `DATA` blocks.
* `SLOTS` tree represents a slotted array of variable-size elements. Its leaf
  nodes are `SLOTS` blocks.
* `LINKS` tree represents an array of links to blocks. Its leaf nodes are
  leaf-parent `LINKS` blocks, whose links point to individual leaf blocks.

A link tree MUST be homogenous, that is, all leaves MUST be of the same type.

### Traversal

To access an element at global index `N` in a link tree, one must typically
descend down into inner nodes in order to locate the leaf containing that element.

Each `LINKS` block defines its own *local* index space starting at 0. A link’s
`limit` is a cumulative count in that enclosing `LINKS` block (not a global
index). In order to correctly determine the global index, implementations have to
track a global offset when descending into inner nodes.

When descending into a link, implementations MUST:

1. Calculate _stated content length_ of the link, by subtracting its `limit`
   from the previous link's `limit` (or 0 if this is the first link).
2. Verify that the pointed-to block's content length matches the stated
   content length, and reject the data structure if it doesn't.

Content length of:
 - `DATA` leaf: schema-defined length of its array
 - `SLOTS` leaf: count of its slots
 - `LINKS` inner node: maximum `limit` of its links, that is, the `limit` of the
   last link

## Linking to arbitrary size arrays

Every struct field contains one of the following:

- a primitive value,
- an array,
- or another struct.

Primitive values are never linked, because the size of the link is larger than
the value itself.

When linking to an array, the link's `limit` field is the count of elements in
the array. Links to arrays with `limit == 0` are not allowed and readers MUST
reject such data. (Zero-length arrays can be represented as a `DIRECT` pointing
to an empty `LINKS` block.)

In all cases, the link may either point to a block that is a leaf node (that is,
a `DATA`, `SLOTS`, or a leaf-parent `LINKS` block), or to a root of a link tree.

When linking to a struct, the link has `limit` set to zero, and links directly
to the `STRUCT` block.

A link's `limit` MUST always match the element count of the linked array.

# Data model

Every data structure stored in this format must be rooted in a single *block*.
Most typically, this will be a `STRUCT` block.

The following data types can be natively stored in the wire format.

## Fixed-size types

### Integer-like types

The following types of integers are allowed:

`u8` `u16` `u32` `u64` `i8` `i16` `i32` `i64`

Enums are represented as an integer of a size appropriate to the number of
values. (most commonly `u8`).

Booleans are represented as an `u8` enum with two allowed values `TRUE = 1` and
`FALSE = 0`.

Integers SHOULD be stored as `INLINE` if the value is 13 bits or shorter
(counting sign bit for signed integers). When reading a signed integer from an
`INLINE` field, the reader MUST sign-extend the value to the full width of the
type.

Larger values MUST be stored as `DIRECT`.

### Floats

The following types of floats are allowed: `f32` `f64`

Floats are stored as `DIRECT`.

### Fixed-size arrays of fixed-size elements

An array of fixed-size elements can be stored as `DIRECT`, if it fits inside the
block (see Fitting).

Arrays that can fit inside a block SHOULD NOT be stored as `BLOCK`.

Arrays that do not fit inside a block are stored as `LINK` to an arbitrary size
`DATA` array.

A fixed-size array of fixed-size elements is itself considered a fixed-size
type, and as such, an arbitrarily deeply nested fixed-size array of fixed-size
arrays can be stored as flat data, as specified above.

The alignment of any such array is the alignment of its primitive element.

## String types

### Text strings

Text strings are stored as UTF-8 byte strings, that is, arrays of `u8` (see
below). Strings are **not** null-terminated, any null bytes are considered part
of the string.

### Byte strings

Variable-size byte strings are stored as arrays of `u8` (see below).

## Arrays

Fixed-size arrays of fixed-size elements are considered a fixed-size type, and
described under [Fixed-size arrays section](#fixed-size-arrays-of-fixed-size-elements).

All other arrays are stored as variable-size. In particular, if a schema
prescribes a fixed size to an array of variable-size elements, the wire format
is still a variable-size array, but the reader MUST check that the actual size
matches the schema.

Arrays are homogenous: their elements are all of the same type and represented
the same way. In particular, because element representation is determined by
element size, writers need to choose a common representation that works for
every value in the array.

There are three possible element representations:

### 1. Fixed-size elements

If the elements are all of the same fixed size, the array will be represented as
an arbitrary size `DATA` array.

### 2. Slots of a slotted array

If every element can fit into a `SLOTS` block, the whole array can be
represented an arbitrary size `SLOTS` array. This representation is especially
appropriate when:

- *most* elements are roughly link-sized (36 bytes) or shorter, or
- the entire array fits in a single `SLOTS` block, or
- the reader is expected to read every element, as opposed to direct indexing to
  points of interest.

### 3. Links

The array becomes an arbitrary size array of links to its elements. Most
appropriate when:

- any single element does not fit into a `SLOTS` block, e.g., when it is a large
  byte string,
- *most* elements are significantly larger than a link.

If the whole array fits into a single sub-block of a containing block, it can be
stored as a `BLOCK` of the appropriate type (`DATA`, `SLOTS`, or `LINKS`),
depending on fitting constraints.

If the whole array fits into a single block, it should be stored as a `LINK` to
that block.

Otherwise, the array must be stored as a link-tree.

## Structs

Struct members of a struct are encoded as a `STRUCT` block -- either, if the
block fits, as a `BLOCK` on the heap, or as a `LINK` with `limit == 0` pointing
to the struct block.

## Future extensions

### Dictionaries

One option for storing dictionaries is to use two struct members: a `keys` array
and a `values` array. They need to be the same size, but they may be of
different types and stored differently.

### Tagged unions

A tagged union type is just a struct whose all members are optional. The writer
is responsible for encoding exactly one member, and the reader needs to check
that this is the case.

# Note on deep nesting

The format intentionally does not specify a maximum nesting depth.
Implementations need to take care not to blindly recurse into the structure,
which may cause stack overflows.

We note that it is possible to traverse an arbitrarily deep structure safely, by
following the links and evicting blocks from memory as they are read. The reader
can always retain just the root block of the entire structure, plus an index
into a particluar deep link tree, which gives it an ability to always reach the
same place by re-starting from the root.

It is up to the reader to not implement schemas that are inherently too deep for
said reader to safely traverse.

# Hashing

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

# Format variants

The following variations seem to make sense:

* Very Large Arrays: by modifying the `link` type to use a 64-bit `limit`, and
  tweaking the alignment requirements accordingly, it is possible for the
  modified protocol to support arrays of up to 2^64 elements.

* 29-bit sizes and offsets: by converting `t16` type to `t32` with 3 bits for
  `parameters` and 29 bits for `number`, it is possible for the modified protocol
  to support individual blocks of up to 512 MiB in size. Such modification would
  likely imply Very Large Arrays as well.

* Big-endian byte order, if required by the platforms in question.

Note that the format intentionally does not explicitly carry any of those
parameters. The proposed variants are different instances of the format and
cannot be used interchangeably; implementers must agree on the variant to use.
