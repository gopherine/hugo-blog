---
title: 'Lesson 7: Slices Are Views, Not Arrays — Mutations you didn''t ask for'
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
series: "Idiomatic Go"
lesson: 7
date: '2026-01-12T00:00:00.000Z'
---

If you're coming from Python, JavaScript, or Java, slices look familiar enough that you'll assume you understand them. That assumption will hold right up until something mutates data you didn't expect to be mutable, or a change you made inside a function mysteriously doesn't show up outside it. Both surprises have the same root cause: a slice is not a copy of its data, it's a window into an underlying array that may be shared with other slices.

Once that model clicks, the surprises stop.

## The Problem

Here's the mutation surprise that trips up almost everyone eventually:

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

The function received a copy of the slice — but a "copy of the slice" means a copy of three fields: a pointer to the underlying array, a length, and a capacity. The pointer still points to the same array. So when `doubleFirst` writes to `s[0]`, it's writing to the same memory `nums[0]` refers to.

The sub-slice version catches people off guard too:

```go
original := []int{10, 20, 30, 40, 50}
sub := original[1:3] // looks like [20, 30]

sub[0] = 99
fmt.Println(original) // [10 99 30 40 50] — original is modified!
```

Taking a sub-slice doesn't allocate new memory. You get a new header pointing into the same backing array. Passing `sub` into a library function that modifies its contents will change your `original` underneath you. This is the intended behavior — not a bug — but it'll ruin your day if you don't expect it.

## The Idiomatic Way

When you need independent data, use `copy`. When you understand that you're sharing data and that's fine, use slices directly. The key is being deliberate about which one you're doing.

For the function case:

```go
// RIGHT: copy the data before mutating
func doubleFirstSafe(s []int) []int {
    result := make([]int, len(s))
    copy(result, s)
    result[0] *= 2
    return result
}
```

For the sub-slice case:

```go
// RIGHT: make a genuine independent copy
sub := make([]int, 2)
copy(sub, original[1:3])
sub[0] = 99
fmt.Println(original) // [10 20 30 40 50] — untouched
```

The `append` case is the subtlest. When you `append` to a slice that has remaining capacity, Go writes into the existing backing array — the new slice and the original still share memory:

```go
// WRONG assumption: append always creates new memory
a := make([]int, 3, 6) // len=3, cap=6
a[0], a[1], a[2] = 1, 2, 3

b := append(a, 4) // len=4, cap=6 — still the same backing array!
b[0] = 99

fmt.Println(a[0]) // 99 — a was mutated through b
```

Both `a` and `b` share the same underlying array because the append fit within the existing capacity. When capacity is exceeded, `append` allocates a new array and now they're independent — but that's capacity-dependent behavior, which is exactly the wrong kind of surprise. The idiomatic fix is the three-index slice:

```go
// RIGHT: cap the capacity so append always allocates on first write
a := make([]int, 3, 6)
a[0], a[1], a[2] = 1, 2, 3

b := append(a[:3:3], 4) // a[:3:3] sets cap=len, forcing allocation
b[0] = 99

fmt.Println(a[0]) // 1 — a is now safe
```

`a[:3:3]` is a three-index slice expression: `a[low:high:max]`. Setting `max` equal to `high` means cap equals len, so any append to `b` has to allocate new memory.

## In The Wild

This bites hardest in I/O code. Here's a real production bug pattern:

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

Every element in `chunks` is a sub-slice of the same `buf`. After the loop completes, every element contains the data from the *last* read, because each iteration overwrote the same 512 bytes. This bug is subtle enough to pass code review and not show up until you have inputs larger than 512 bytes.

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

The same issue shows up whenever you return internal slice data from a cache or shared buffer. The fix is the same: return a copy, not a view.

```go
// RIGHT: cache returns a copy so callers can't corrupt internal state
type Cache struct {
    items []string
}

func (c *Cache) Items() []string {
    result := make([]string, len(c.items))
    copy(result, c.items)
    return result
}
```

If `Items()` returned `c.items` directly, any caller could silently corrupt the cache's internal state.

## The Gotchas

**The append behavior is capacity-dependent.** Code that works correctly when a slice has no spare capacity can silently break when it has spare capacity. This is the worst kind of bug — intermittent and hard to reproduce. If you're appending to a slice returned from somewhere else, you often don't know its capacity. Use three-index slicing or explicit copying when sharing matters.

**Ranging over a slice doesn't protect you.** The `range` loop gives you a copy of each value, but if you take a pointer to a range variable and store it, you've got a pointer into the backing array. Same sharing rules apply.

**`copy` copies the minimum of `len(dst)` and `len(src)`.** If you make `dst` too small, you silently get a partial copy. Always make sure `dst` has the length you need, not just the capacity.

## Key Takeaway

The mental model to carry: a slice is three words of data — a pointer, a length, a capacity — that describe a window into an array that might be shared with other windows. Assigning a slice copies the window descriptor, not the array. Sub-slicing creates a new window into the same array. Mutation through any window affects all windows that share the same array. `append` within capacity stays in the same array; beyond capacity it goes to a new one. `copy` is the explicit escape hatch when you genuinely need independence. Once you've internalized this, you stop being surprised — you start writing `copy` where you mean independence and letting sharing happen intentionally where you want efficiency.

---

← [Lesson 6: Accept Interfaces, Return Structs](/post/go/go-idioms-accept-interfaces-return-structs/) | [Course Index](#) | [Lesson 8: Capacity Matters →](/post/go/go-idioms-capacity-matters/)
