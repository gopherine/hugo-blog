---
title: "Lesson 4: String and Byte Conversions — The copy nobody sees"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 4
keywords:
  - Go
  - golang
  - performance
  - string conversion
  - byte slice
  - strings.Builder
  - zero copy
  - string interning
tags:
  - Go tutorial
  - golang
  - performance
date: '2024-09-25T00:00:00.000Z'
---

Go strings are immutable. Byte slices are mutable. Converting between them requires copying the data — every time, without exception, unless you use unsafe tricks you almost certainly shouldn't. That sounds like a minor footnote, but it becomes a significant issue the moment you start handling large volumes of text: parsing HTTP requests, processing log lines, building JSON responses. I've watched a single `string(b)` call inside a tight loop add measurable latency to a production API, and the fix was two lines of code once I knew what to look for.

## The Problem

The Go spec guarantees that `[]byte(s)` and `string(b)` produce independent copies of the underlying data. This is necessary for correctness — strings are immutable, so if `string(b)` returned a view into `b`, mutating `b` afterward would silently corrupt the string. The copy is a feature. The problem is when you pay for that copy repeatedly, unnecessarily, in code that could avoid it.

The classic trap is building strings in a loop:

```go
// COSTLY — O(n²) copies; each concatenation allocates a new string
func joinLines(lines []string) string {
    result := ""
    for _, line := range lines {
        result += line + "\n"
    }
    return result
}
```

String concatenation with `+` allocates a new string for every iteration. For 100 lines, you copy the accumulating string 100 times. For 10,000 lines, you're doing 50 million character copies. This is the classic O(n²) string-building antipattern, and it shows up in code review constantly.

The second trap is round-tripping through `[]byte` for operations that don't need it:

```go
// Unnecessary conversion — strings.ToUpper works directly on strings
func normalizeHeaders(headers map[string]string) {
    for k, v := range headers {
        // Converting to []byte and back just to use bytes.ToUpper — wasteful
        headers[k] = string(bytes.ToUpper([]byte(v)))
    }
}
```

Two copies per value (one to `[]byte`, one back to `string`), neither of which was necessary. The `strings` package has a `strings.ToUpper` that operates on strings directly.

The third trap is using `fmt.Sprintf` for simple string construction:

```go
// fmt.Sprintf uses reflect and heap-allocates even for simple formats
func makeKey(prefix string, id int) string {
    return fmt.Sprintf("%s:%d", prefix, id)  // heap allocation
}
```

## The Idiomatic Way

For building strings from parts, `strings.Builder` is the standard tool since Go 1.10. It maintains a `[]byte` buffer internally and converts to string once at the end, without an extra copy:

```go
// strings.Builder — one allocation, O(n) copies total
func joinLines(lines []string) string {
    var sb strings.Builder
    sb.Grow(estimateSize(lines)) // optional: pre-size the internal buffer
    for _, line := range lines {
        sb.WriteString(line)
        sb.WriteByte('\n')
    }
    return sb.String()
}

func estimateSize(lines []string) int {
    n := 0
    for _, l := range lines {
        n += len(l) + 1
    }
    return n
}
```

The `Grow` call is optional but valuable if you can estimate the final size — it pre-allocates the internal buffer and avoids growth reallocations inside the builder. `sb.String()` at the end performs one allocation for the final immutable string.

For simple key construction, `strconv` functions are faster than `fmt.Sprintf` because they don't use reflection:

```go
// strconv approach — typically zero or one allocation
func makeKey(prefix string, id int) string {
    var b [64]byte // stack-allocated scratch buffer
    buf := append(b[:0], prefix...)
    buf = append(buf, ':')
    buf = strconv.AppendInt(buf, int64(id), 10)
    return string(buf)
}
```

The `[64]byte` array is stack-allocated. We build the key into it using `append` (which starts writing at index 0 via `b[:0]`). If the key fits in 64 bytes, there's no heap allocation until the final `string(buf)` conversion. For keys that exceed 64 bytes, `append` falls back to a heap-allocated slice automatically.

For cases where you need to pass a `[]byte` to a function that expects `string` (or vice versa) without copying, the `bytes` package often has a parallel API:

```go
// Instead of: strings.HasPrefix(string(b), "prefix:")
// Use:        bytes.HasPrefix(b, []byte("prefix:"))

// Instead of: strings.Split(string(b), "\n")
// Use:        bytes.Split(b, []byte("\n"))
```

The `bytes` package mirrors the `strings` package almost exactly. If you're working with a `[]byte` buffer, stay in `bytes` for the entire operation and only convert to `string` at the boundary where a `string` is required.

## In The Wild

I was profiling a log parsing service that ingested structured log lines at roughly 50,000 lines per second. Each line went through a parsing function that looked something like this:

```go
func parseLine(line []byte) (LogEntry, error) {
    parts := strings.Split(string(line), "|") // COPY #1
    if len(parts) < 4 {
        return LogEntry{}, ErrMalformed
    }
    entry := LogEntry{
        Level:   strings.TrimSpace(parts[0]),
        Service: strings.TrimSpace(parts[1]),
        Message: parts[3],
    }
    return entry, nil
}
```

At 50k lines/second, `string(line)` was producing 50k string allocations per second. The `strings.Split` was producing another slice allocation per line. The total allocation rate from this one function was dominating the GC profile.

The fix was to switch the parsing to operate directly on `[]byte`:

```go
func parseLine(line []byte) (LogEntry, error) {
    parts := bytes.Split(line, []byte("|"))
    if len(parts) < 4 {
        return LogEntry{}, ErrMalformed
    }
    entry := LogEntry{
        // string() here is unavoidable for the struct fields,
        // but we've eliminated the big allocation (the full line copy)
        Level:   string(bytes.TrimSpace(parts[0])),
        Service: string(bytes.TrimSpace(parts[1])),
        Message: string(parts[3]),
    }
    return entry, nil
}
```

We still copy the individual fields when assigning to strings, but we've eliminated the full-line copy and reduced per-line allocation from several large objects to three small ones. GC pressure dropped measurably, and the P99 parsing latency improved by about 18%.

## The Gotchas

**`unsafe.Slice` and `unsafe.String` can give you zero-copy conversions — but the data must remain valid.** The standard `unsafe` trick for converting `[]byte` to `string` without copying works, but if the original `[]byte` is modified or garbage collected, you have undefined behavior. Use this only in tightly controlled, performance-critical code where you can guarantee the lifetime relationship.

**`strings.Builder` is not goroutine-safe.** If you're building strings concurrently, use separate builders per goroutine and merge at the end. There's no lock inside.

**`[]byte("literal")` allocates on every call.** If you're calling `bytes.Contains(data, []byte("separator"))` in a hot loop, the `[]byte("separator")` allocation happens every iteration. Extract it to a package-level variable: `var sep = []byte("separator")`.

**`string` comparison does not copy.** `s1 == s2` compares strings without allocating. But `s1 + s2 == expected` allocates the concatenation before comparing. Use `s1 == expected[:len(s1)] && s2 == expected[len(s1):]` or restructure to avoid the intermediate allocation.

## Key Takeaway

The copy nobody sees is the one inside `string(b)` and `[]byte(s)` — silent, correct, and expensive at scale. The fix isn't arcane: use `strings.Builder` instead of concatenation, stay in `bytes` when you're working with byte slices, use `strconv` instead of `fmt.Sprintf` for simple formatting, and keep an eye on any hot path that converts between strings and bytes more than once. Converting is fine at boundaries. Converting in the middle of a hot loop is the problem. Most of the time you can restructure the code to push the conversion to the edge, and that's where the savings live.

---

[← Lesson 3: Slice and Map Performance](/post/go/go-perf-slice-map) | [Course Index](/series/go-performance-engineering) | [Next → Lesson 5: Benchmarking Done Right](/post/go/go-perf-benchmarking)
