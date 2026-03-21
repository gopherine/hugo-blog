---
title: "Lesson 8: Avoiding Premature Optimization — Measure first, optimize never (usually)"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 8
keywords:
  - Go
  - golang
  - performance
  - premature optimization
  - profiling
  - benchmarking
  - performance engineering
  - optimization strategy
tags:
  - Go tutorial
  - golang
  - performance
date: '2025-04-10T00:00:00.000Z'
---

I've spent time in this series showing you how to make Go programs faster — escape analysis, stack allocation, pre-sizing data structures, zero-copy string handling, benchmarking discipline, pprof profiling, CPU vs memory tradeoffs. Every technique is real and useful. And every single one of them has been misapplied, including by me, when applied before understanding whether they were needed. The last lesson isn't another technique. It's the discipline that makes all the other techniques worth using: measure first, understand where the actual cost is, and optimize only there.

## The Problem

Premature optimization is code that is harder to read, harder to change, and harder to debug — for a performance gain that either doesn't exist or doesn't matter in context. It's the `unsafe` pointer cast where a regular conversion would have been fast enough. It's the hand-rolled parser where `encoding/json` was the bottleneck in zero hot paths. It's the pool for objects that are allocated once per process.

The damage isn't just wasted time. Prematurely optimized code is harder to evolve. I've seen perfectly readable business logic rewritten as unsafe byte manipulation because someone read that `string` conversions were slow — and then the logic changed, the byte manipulation had a subtle off-by-one, and it took three engineers two days to find it. The original code would have been correct and fast enough.

Here's the thing Knuth's quote actually says, in full: "We should forget about small efficiencies, say about 97% of the time: premature optimization is the root of all evil. Yet we should not pass up our opportunities in that critical 3%." It's not "never optimize." It's "the 3% of code that matters is not the 97% you're currently staring at."

The challenge is identifying which 3% you're in. Without data, you can't. Without data, everyone optimizes the code they understand best or the code they wrote most recently — neither of which has any correlation with where the actual bottleneck lives.

```go
// Classic premature optimization: custom integer stringification
// to avoid the "overhead" of strconv.Itoa
func itoa(n int) string {
    if n == 0 {
        return "0"
    }
    var buf [20]byte
    pos := len(buf)
    for n > 0 {
        pos--
        buf[pos] = byte(n%10) + '0'
        n /= 10
    }
    return string(buf[pos:])
}
```

This code is harder to read than `strconv.Itoa`, has edge cases (`n < 0` is broken), and is probably not faster than `strconv.Itoa` which is already highly optimized in the standard library. It exists because someone assumed `strconv.Itoa` was slow. They didn't measure. They wrote maintenance debt and got nothing for it.

## The Idiomatic Way

The process I follow before any optimization work:

**1. Establish a working, correct baseline first.** Optimization on incorrect code is optimization of a lie. Get the tests green, get the behavior right, then and only then ask "is this fast enough?"

**2. Define "fast enough" before starting.** What's your SLO? What does P99 latency need to be? What's your memory budget? Without a target, you optimize indefinitely. With a target, you stop when you hit it.

**3. Measure under realistic conditions.** A microbenchmark tells you about a function in isolation. Production performance depends on working set size, concurrency, memory pressure, competing workloads, and GC behavior. Both kinds of measurement are useful; neither is complete alone.

```go
// Set up a benchmark that resembles real usage, not just the hottest path
func BenchmarkAPIHandler(b *testing.B) {
    // Use a realistic request body, not an empty one
    body := loadTestFixture("realistic_request.json")
    req := httptest.NewRequest("POST", "/api/v1/process", bytes.NewReader(body))
    req.Header.Set("Content-Type", "application/json")

    handler := NewHandler(testDB, testCache)
    rec := httptest.NewRecorder()

    b.ResetTimer()
    for i := 0; i < b.N; i++ {
        req.Body = io.NopCloser(bytes.NewReader(body)) // reset body per iteration
        handler.ServeHTTP(rec, req)
    }
}
```

A benchmark with a realistic request body and a real handler pipeline tells you how the actual system performs, not how the JSON deserializer performs in isolation. The profile from this benchmark points at the real bottleneck, not an artificial one.

**4. Profile before optimizing.** Run `go test -bench -cpuprofile` and look at the output with `go tool pprof -http`. Find the function with the highest flat time. That's where you start — not where you guess, not where the code looks complicated, not where a blog post said Go is slow.

```bash
# The workflow that avoids premature optimization
$ go test -bench=BenchmarkAPIHandler -benchmem -cpuprofile=cpu.prof -count=5
$ go tool pprof -http=:8080 cpu.prof
# Now look at the flame graph. Where is the widest bar?
# THAT is where you optimize.
```

**5. Optimize one thing at a time.** Change one variable, run the benchmark, compare with `benchstat`. If the change helps, keep it. If it doesn't, revert it. If you change three things at once and performance improves, you don't know which change was responsible — and you probably kept two changes that added complexity for zero benefit.

## In The Wild

The most expensive premature optimization I've witnessed in production wasn't a performance bug — it was a custom memory allocator that someone wrote for a Go service because "the GC is too slow." It was a custom slab allocator, several hundred lines of unsafe code, maintaining its own free list. It was, of course, written before anyone profiled the service.

When we finally did profile the service (because allocations weren't actually the problem — network I/O was), we found that the custom allocator was producing more GC pressure than the standard allocator would have, because it held large slabs that kept many objects live even when most of them were logically freed. The GC had to scan them all.

The fix was deleting the custom allocator entirely and using standard Go allocation. Startup time improved. Steady-state memory dropped. The service became easier to reason about. Three hundred lines of unsafe code removed, performance improved, correctness improved.

The original author had optimized for the wrong thing, without measurement, and created a maintenance burden that the team carried for two years.

Compare this with a correct optimization workflow I ran on a different service:

```go
// BEFORE optimization (baseline)
// BenchmarkTokenize-8    12384    96812 ns/op    48320 B/op    412 allocs/op

func Tokenize(input string) []Token {
    var tokens []Token
    // ... tokenization logic using regexp.FindAllStringIndex ...
    return tokens
}

// AFTER: profiler showed 70% of time in regexp.FindAllStringIndex
// Fix: precompile the regexp, use a manual scanner for simple cases
var tokenRe = regexp.MustCompile(`\w+|[^\w\s]`)

func Tokenize(input string) []Token {
    tokens := make([]Token, 0, len(input)/5) // rough capacity hint
    matches := tokenRe.FindAllStringIndex(input, -1)
    for _, m := range matches {
        tokens = append(tokens, Token{Value: input[m[0]:m[1]], Start: m[0]})
    }
    return tokens
}

// AFTER benchmark:
// BenchmarkTokenize-8    31247    38291 ns/op    12160 B/op    103 allocs/op
```

2.5x faster, 4x fewer allocations, 15 lines of change. The optimization was obvious once the profiler pointed at `regexp.FindAllStringIndex` — and completely non-obvious before. Without measurement, I might have rewritten the token struct, changed the return type, or introduced a pool for tokens — none of which would have moved the needle.

## The Gotchas

**Readability is a performance concern.** Code that's hard to read is hard to optimize when optimization actually becomes necessary. The best performance strategy for code that doesn't matter yet is to write it clearly, so that when it does matter, someone can understand it quickly, instrument it correctly, and optimize it effectively.

**"Fast enough" changes.** A service handling 100 requests per second can afford to be 5x less efficient than one handling 50,000. Don't optimize to the scale you don't have. Write correct, clear code; optimize to the scale you do have. Optimize again when scale changes.

**Compiler and runtime improvements make old optimizations obsolete.** Code optimized for Go 1.12 may be slower in Go 1.22 than the idiomatic version, because the compiler has gotten smarter. Hand-optimized code that bypasses normal idioms may not benefit from new compiler improvements. Measure after every significant Go version upgrade.

**Optimization has a cost beyond the code change.** Every optimization that deviates from idiomatic Go requires future maintainers to understand why it's written that way. Document the before/after benchmark results in a comment. If you don't, the next person to read the code will "simplify" it back to the readable version and lose the optimization.

```go
// Always document why non-idiomatic code exists
// BenchmarkSerialize before: 24µs, 8 allocs/op
// BenchmarkSerialize after:   9µs, 1 allocs/op
// Reason: pre-allocated buffer and direct struct field encoding
// avoids the reflect-based path in encoding/json
func serializeFast(e *Event, buf *bytes.Buffer) error {
    // ... non-standard serialization path ...
}
```

## Key Takeaway

The seven lessons before this one are a toolkit. This lesson is the judgment about when to use it. The toolkit is only valuable in the 3% of code that's actually a bottleneck — and the only way to find that 3% is to profile under realistic load, not to guess based on reading the code. Write correct, idiomatic Go first. Measure. Find the actual bottleneck. Optimize only there, one change at a time, with benchmarks before and after. Stop when "fast enough" is satisfied. This is the discipline that makes the toolkit matter.

---

[← Lesson 7: CPU vs Memory Tradeoffs](/post/go/go-perf-cpu-memory) | [Course Index](/series/go-performance-engineering)

---

🎓 Course Complete! You've finished **Go Performance Engineering**. From escape analysis to premature optimization, you now have the full toolkit for reasoning about, measuring, and improving Go program performance. Go build something fast — but measure it first.
