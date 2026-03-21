---
title: 'Go Idioms: Slices Are Views, Not Arrays'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - slices
  - arrays
  - append
  - copy
  - slice header
  - memory
tags:
  - Go tutorial
  - golang
date: '2026-01-12T00:00:00.000Z'
---
"array" and "list" are essentially the same thing, slices will feel familiar right up until the moment they bite you. The bite usually happens in one of two ways: you mutate a slice inside a function and are surprised that the change is visible outside it, or you mutate a slice and are surprised that the change is *not* visible. Both surprises come from the same root cause — a slice is not a copy of its data, it is a *view* into an underlying array.

Understanding the mechanics of that view will make you a significantly better Go programmer.

## The Slice Header

At runtime, a Go slice is represented by three fields. You can think of it as a struct:

```
+---------+---------+----------+
| pointer | length  | capacity |
| to array|  (len)  |   (cap)  |
+---------+---------+----------+
```

- **Pointer** — the address of the first element visible to this slice in the underlying array
- **Length** — how many elements are currently accessible through this slice
- **Capacity** — how many elements are available in the underlying array starting from the pointer

When you pass a slice to a function, you pass a copy of this three-field header. The pointer inside that header still points to the same backing array. That is the source of both the surprises mentioned above.

You can inspect these values directly:

```go
s := make([]int, 3, 6) // length 3, capacity 6
fmt.Println(len(s))    // 3
fmt.Println(cap(s))    // 6
```

## Mutation Through Slices

Because slices share the underlying array, mutations through one slice are visible through another slice that shares the same backing memory.

```go
// WRONG assumption: modifying a slice copy leaves the original untouched
func doubleFirst(s []int) {
    s[0] *= 2
}

func main() {
    nums := []int{1, 2, 3}
    doubleFirst(nums)
    fmt.Println(nums[0]) // prints 2, not 1!
}
```

The function received a copy of the slice header, but both headers point to the same array. Writing to `s[0]` inside the function modifies the same memory location that `nums[0]` refers to.

This is sometimes what you want — modifying a slice in-place is efficient. But if you intend to work on independent data, you need an explicit copy.

```go
// RIGHT: copy the data before mutating
func doubleFirstSafe(s []int) []int {
    result := make([]int, len(s))
    copy(result, s)
    result[0] *= 2
    return result
}
```

## Re-slicing: Sharing the Same Backing Array

Re-slicing is where things get subtle. When you take a sub-slice, you do not allocate new memory. You get a new header pointing into the same array.

```
original: [A][B][C][D][E]
           ^              ^
           ptr            ptr+cap

sub := original[1:3]
       [B][C]
        ^        ^
        ptr   ptr+cap (still points into original's array)
```

In code:

```go
original := []int{10, 20, 30, 40, 50}
sub := original[1:3] // [20, 30]

sub[0] = 99
fmt.Println(original) // [10 99 30 40 50] — original is modified!
```

This is not a bug in Go. It is the intended behavior. But if you pass `sub` into a library function and that function modifies its contents, your `original` slice changes underneath you. The fix is `copy`.

```go
// RIGHT: make a genuine independent copy
sub := make([]int, 2)
copy(sub, original[1:3])
sub[0] = 99
fmt.Println(original) // [10 20 30 40 50] — untouched
```

## The append Gotcha: When Capacity Runs Out

The most dangerous slice behavior involves `append`. Here is the scenario that confuses nearly every Go programmer at least once.

When you append to a slice that has remaining capacity, `append` writes into the existing backing array and returns a new header with an incremented length. The backing array is shared.

```go
// WRONG assumption: append always creates new memory
a := make([]int, 3, 6) // len=3, cap=6
a[0], a[1], a[2] = 1, 2, 3

b := append(a, 4) // len=4, cap=6 — still same backing array!
b[0] = 99

fmt.Println(a[0]) // 99 — a was mutated through b
fmt.Println(b[0]) // 99
```

Both `a` and `b` share the same underlying array because `b` was created by appending within the existing capacity. Modifying `b[0]` changes `a[0]`.

When capacity *is* exceeded, `append` allocates a new array, copies the data, and returns a header pointing to the new allocation. Now `a` and `b` are truly independent.

```go
a := []int{1, 2, 3} // len=3, cap=3 — no spare capacity
b := append(a, 4)   // cap exceeded — new array allocated

b[0] = 99
fmt.Println(a[0]) // 1 — a is unaffected
fmt.Println(b[0]) // 99
```

The behavior is inconsistent depending on the capacity at the time of the append. This inconsistency is the real gotcha. Code that works correctly when the slice has no spare capacity can silently break when the slice has spare capacity.

The idiomatic fix is to use a full three-index slice expression to limit the capacity of the original slice before appending, or to copy explicitly:

```go
// RIGHT: cap the capacity so append always allocates on first write
a := make([]int, 3, 6)
a[0], a[1], a[2] = 1, 2, 3

// Limit capacity to length: any append to b will allocate new memory
b := append(a[:3:3], 4)
b[0] = 99

fmt.Println(a[0]) // 1 — a is now safe
```

The three-index slice `a[:3:3]` sets the capacity equal to the length, so any `append` to `b` is forced to allocate.

## copy() Is Your Safety Net

The built-in `copy` function is the correct tool when you need genuinely independent slice data. It copies the minimum of `len(dst)` and `len(src)` elements and has no aliasing behavior.

```go
src := []int{1, 2, 3, 4, 5}
dst := make([]int, len(src))
copy(dst, src)

dst[0] = 999
fmt.Println(src[0]) // 1 — completely independent
```

A common pattern when you need to return a "snapshot" of a slice from a cache or buffer is to always return a copy:

```go
// RIGHT: cache returns a copy so callers cannot corrupt internal state
type Cache struct {
    items []string
}

func (c *Cache) Items() []string {
    result := make([]string, len(c.items))
    copy(result, c.items)
    return result
}
```

If `Items()` returned `c.items` directly, any caller could modify the cache's internal slice through the returned value. Returning a copy prevents that class of bug entirely.

## A Real-World Scenario: Buffered Reading

This pattern matters enormously in I/O code. Consider reading chunks from a reader into a buffer and accumulating results:

```go
// WRONG: all appended slices share the same backing buffer
func readChunks(r io.Reader) [][]byte {
    buf := make([]byte, 512)
    var chunks [][]byte

    for {
        n, err := r.Read(buf)
        if n > 0 {
            chunks = append(chunks, buf[:n]) // BUG: appending a view!
        }
        if err == io.EOF {
            break
        }
    }
    return chunks
}
```

Every element of `chunks` is a sub-slice of the same `buf` array. After the loop, every element contains the data from the *last* read, because each iteration overwrote the same buffer. This is a real production bug that is subtle enough to pass code review.

```go
// RIGHT: copy each chunk into its own allocation
func readChunks(r io.Reader) [][]byte {
    buf := make([]byte, 512)
    var chunks [][]byte

    for {
        n, err := r.Read(buf)
        if n > 0 {
            chunk := make([]byte, n)
            copy(chunk, buf[:n])
            chunks = append(chunks, chunk)
        }
        if err == io.EOF {
            break
        }
    }
    return chunks
}
```

## Putting It Together

The mental model to carry with you: a slice value is a lightweight descriptor — three words of data — that describes a window into an array that may be shared with other slice descriptors.

- Assigning a slice copies the descriptor, not the array
- Re-slicing creates a new descriptor into the same array
- Mutating through any descriptor affects all descriptors that share the array
- `append` within capacity stays in the same array; `append` beyond capacity creates a new one
- `copy` is the explicit escape hatch when you need independent data

Once this model is clear, the surprises stop. You start writing `copy` where you mean independence and letting sharing happen intentionally where you want efficiency. That is idiomatic Go.
