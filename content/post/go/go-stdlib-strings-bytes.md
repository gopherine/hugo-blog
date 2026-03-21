---
title: 'Lesson 8: strings and bytes Builders — Stop concatenating in loops'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 8
keywords:
  - Go
  - golang
  - strings
  - bytes
  - strings.Builder
  - bytes.Buffer
  - string concatenation
  - performance
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2025-07-05T00:00:00.000Z'
---

String concatenation with `+` is one of the most common performance bugs I see in Go code reviews. Not because developers are careless, but because the bug is invisible — the code looks clean and correct. `result += piece` is obvious. The quadratic memory behavior that follows is not.

The `strings` and `bytes` packages are where Go provides the right tools for this job. `strings.Builder` and `bytes.Buffer` are both efficient for incremental string construction, but they're not the same type and the choice between them matters in specific cases. Beyond building strings, these packages contain functions that have non-obvious but significant performance implications: `strings.Contains` vs `strings.Index`, `strings.Split` vs `strings.SplitN`, and the ones people reach for that they shouldn't.

## The Problem

String concatenation in a loop is quadratic:

```go
// WRONG — O(n²) time and memory
func buildCSV(records []Record) string {
    result := ""
    for _, r := range records {
        result += r.Field1 + "," + r.Field2 + "," + r.Field3 + "\n"
        // Each += allocates a new string of length (previous + new).
        // For N records of average length L:
        // Total allocations: 1L + 2L + 3L + ... + NL = O(N²·L)
    }
    return result
}
```

With 10,000 records, this allocates roughly 50 million bytes total (for 100-byte records) in N quadratic steps, triggering GC repeatedly. With a builder, it allocates approximately the final size once.

The second mistake: using `bytes.Buffer` when `strings.Builder` suffices:

```go
// Not wrong, just unnecessary overhead for pure string building
var buf bytes.Buffer
buf.WriteString("hello")
buf.WriteString(", ")
buf.WriteString("world")
return buf.String() // copies the buffer contents to a new string
```

`bytes.Buffer` is for mixed read/write operations — it has `Read`, `Write`, `WriteByte`, and can be used as both an `io.Reader` and `io.Writer`. If you only need to build a string and never read intermediate bytes, `strings.Builder` is the right type: it avoids the copy in `String()` that `bytes.Buffer` requires.

## The Idiomatic Way

`strings.Builder` for string construction:

```go
// Correct — O(n) time and memory
func buildCSV(records []Record) string {
    var b strings.Builder

    // Pre-allocate capacity if you have a size estimate.
    // This avoids repeated internal reallocations.
    b.Grow(len(records) * 80) // estimate 80 bytes per record

    for _, r := range records {
        b.WriteString(r.Field1)
        b.WriteByte(',')
        b.WriteString(r.Field2)
        b.WriteByte(',')
        b.WriteString(r.Field3)
        b.WriteByte('\n')
    }

    return b.String() // no copy — returns the underlying buffer directly
}
```

`strings.Builder.Grow(n)` hints at the required capacity. If you know the output size, calling `Grow` once before the loop prevents multiple internal reallocations as the builder expands.

`bytes.Buffer` for code that needs both reading and writing, or interoperability with `io.Reader`/`io.Writer`:

```go
// bytes.Buffer for mixed IO — building content and sending it as an HTTP body
func buildRequestBody(data RequestData) io.Reader {
    var buf bytes.Buffer

    buf.WriteString(`{"version":1,`)
    buf.WriteString(`"data":`)
    json.NewEncoder(&buf).Encode(data) // json.Encoder writes to io.Writer
    buf.WriteByte('}')

    return &buf // *bytes.Buffer implements io.Reader
}

req, err := http.NewRequestWithContext(ctx, "POST", url, buildRequestBody(data))
```

Here `bytes.Buffer` is appropriate because `json.Encoder` needs an `io.Writer`, and the final `*bytes.Buffer` is used as an `io.Reader` by `http.NewRequestWithContext`.

`strings.Join` for simple slice joining — it's more readable and equally efficient:

```go
// These are equivalent in behavior; Join is more readable for simple cases
parts := []string{"a", "b", "c"}

// Using Builder
var b strings.Builder
for i, p := range parts {
    if i > 0 { b.WriteByte(',') }
    b.WriteString(p)
}
result1 := b.String()

// Using Join — cleaner for this case
result2 := strings.Join(parts, ",")
```

`strings.Join` uses a `strings.Builder` internally and pre-calculates the total capacity, so it's as efficient as the manual version.

For template-like string construction with `fmt.Sprintf`, the question is always: is the formatting simple enough that manual writes are faster? Profile first. `fmt.Sprintf` with `%s` is rarely a bottleneck in real code — the I/O around it usually dominates. Reach for `strings.Builder` when profiling shows string allocation is a hotspot, not preemptively.

## In The Wild

`strings.Fields` vs `strings.Split`: both split a string, but `Fields` splits on any whitespace and discards empty substrings. `Split` splits on an exact delimiter and preserves empty substrings:

```go
s := "  hello   world  "
strings.Split(s, " ")  // ["", "", "hello", "", "", "world", "", ""]
strings.Fields(s)       // ["hello", "world"] — trims and collapses whitespace
```

`strings.SplitN(s, sep, n)` stops splitting after n-1 separators, returning at most n substrings. For parsing "key=value=with=equals" where only the first "=" is the delimiter:

```go
parts := strings.SplitN("key=value=with=equals", "=", 2)
// parts = ["key", "value=with=equals"]
```

`strings.ContainsRune` vs `strings.Contains`: for single-character searches, `ContainsRune` avoids the allocation that `Contains` might cause for single-byte separators in some implementations. In practice, Go's `strings.Contains` is highly optimized. Profile before micro-optimizing.

The `unicode/utf8` package works in harmony with strings for correct rune handling:

```go
import "unicode/utf8"

// Count runes (Unicode code points), not bytes
s := "Hello, 世界"
fmt.Println(len(s))                    // 13 (bytes)
fmt.Println(utf8.RuneCountInString(s)) // 9  (runes)

// Iterate over runes correctly — for range decodes UTF-8 automatically
for i, r := range s {
    fmt.Printf("%d: %c (%d bytes)\n", i, r, utf8.RuneLen(r))
}
```

`for range` on a string iterates over Unicode code points (runes), not bytes. The index `i` is the byte offset of the start of the rune. This is correct for most text processing. If you need byte-level access, index directly with `s[i]`.

## The Gotchas

**`strings.Builder` must not be copied after first use.** Like `sync.Mutex`, copying a `strings.Builder` after writing to it produces two independent builders with the same underlying bytes — subsequent writes diverge. Use a pointer if you need to pass the builder to other functions.

**`bytes.Buffer.String()` copies.** When you call `buf.String()` on a `bytes.Buffer`, it copies the buffer contents into a new string allocation. If you call it in a loop, you're allocating a new string on each iteration. Capture the string once outside the loop.

**`strings.Replace` vs `strings.ReplaceAll`.** `strings.Replace(s, old, new, n)` replaces the first n occurrences. `strings.ReplaceAll(s, old, new)` replaces all. `strings.ReplaceAll` is `strings.Replace(s, old, new, -1)` — use the named function for clarity when you mean all.

**`fmt.Sprintf` inside a tight loop is slow.** Every `fmt.Sprintf` call uses reflection to parse the format string and type-check arguments. In a loop that runs millions of times, this is measurable. Use manual `strconv.AppendInt`, `strconv.AppendFloat`, and string writes for hot paths.

## Key Takeaway

Use `strings.Builder` for string construction in loops — pre-grow it if you know the size. Use `bytes.Buffer` when you need both read and write I/O on the same buffer. `strings.Join` is idiomatic for simple slice joining. Know the difference between `Split` and `Fields`. Never concatenate strings with `+` in a loop — the quadratic behavior is real and the profiler will find it.

🎓 **Course Complete!** You've finished Go Standard Library Mastery. You now know how to use `net/http`, `io`, `encoding/json`, `time`, `os/filepath`, `sync`, `context`, and `strings/bytes` at a depth that lets you write correct, efficient, and maintainable Go code from the standard library alone.

---

**Previous:** [Lesson 7: context Internals](/post/go/go-stdlib-context/)
**Series complete — explore all three Go courses:**
- [Go Networking & Distributed Systems](/post/go/go-net-http-client/)
- [Go Deployment & Operations](/post/go/go-deploy-static-binaries/)
- Go Standard Library Mastery (this series)
