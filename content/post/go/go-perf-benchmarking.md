---
title: "Lesson 5: Benchmarking Done Right — testing.B is not what you think"
author: Atharva Pandey
series: "Go Performance Engineering"
lesson: 5
keywords:
  - Go
  - golang
  - performance
  - benchmarking
  - testing.B
  - go test bench
  - benchmark optimization
  - pprof benchmarks
tags:
  - Go tutorial
  - golang
  - performance
date: '2024-11-10T00:00:00.000Z'
---

Writing a Go benchmark feels simple. You drop `Benchmark` in front of a function name, loop from 0 to `b.N`, run `go test -bench=.`, and get a number. The number feels authoritative. I spent about a year trusting benchmark numbers that were wrong — not wrong because of bugs, but wrong because of how the benchmark was written. The Go benchmark framework is excellent, but it has sharp edges that will mislead you until you learn to see them.

## The Problem

The most common benchmark mistake is writing one that measures almost nothing useful:

```go
// WRONG — the compiler may eliminate this entirely
func BenchmarkAdd(b *testing.B) {
    for i := 0; i < b.N; i++ {
        _ = 1 + 1
    }
}
```

The compiler's dead code eliminator sees that `1 + 1` has no side effects and the result is discarded. In optimized builds, it may compile this benchmark down to a loop over nothing. You get results in the sub-nanosecond range that tell you nothing about actual addition performance.

The second mistake is not resetting the timer:

```go
// WRONG — setup time is included in measurement
func BenchmarkProcessLargeSlice(b *testing.B) {
    data := make([]int, 1_000_000)
    populateData(data) // this might take 50ms

    for i := 0; i < b.N; i++ {
        _ = processSlice(data)
    }
}
```

If `populateData` takes 50ms and `b.N` starts at 1, the first iteration measures 50ms of setup plus the actual work. The framework calibrates `b.N` based on those initial iterations, and if setup dominates, the calibration goes wrong. Your reported ns/op will be inflated.

The third mistake is not using `-benchmem`:

```go
$ go test -bench=BenchmarkBuildJSON
BenchmarkBuildJSON-8    45231    26142 ns/op
```

This tells you how fast the function is. It tells you nothing about how many allocations it makes, which is often the more actionable number. Every allocation is potential GC pressure. A benchmark that runs fast in isolation but allocates heavily will degrade under real load in ways the timing alone won't predict.

## The Idiomatic Way

Three practices that make every benchmark trustworthy:

**1. Prevent dead code elimination with `sink` variables.**

```go
var result int // package-level sink

func BenchmarkProcess(b *testing.B) {
    b.ResetTimer()
    for i := 0; i < b.N; i++ {
        result = processItem(i) // assign to package-level var
    }
}
```

Assigning to a package-level variable prevents the compiler from eliminating the call — the result must be computed because it might be observed from outside the function. Alternatively, use `b.ReportAllocs()` and check that allocs/op is non-zero to confirm the function is actually running.

**2. Always reset the timer after setup.**

```go
func BenchmarkProcessLargeSlice(b *testing.B) {
    data := make([]int, 1_000_000)
    populateData(data)

    b.ResetTimer() // start measuring from here
    for i := 0; i < b.N; i++ {
        result = processSlice(data)
    }
}
```

`b.ResetTimer()` zeroes the elapsed time and allocation counts accumulated during setup. Now your ns/op reflects only the actual operation you care about.

**3. Always run with `-benchmem`.**

```bash
$ go test -bench=BenchmarkBuildJSON -benchmem -count=5
BenchmarkBuildJSON-8    45231    26142 ns/op    4096 B/op    12 allocs/op
BenchmarkBuildJSON-8    44987    26389 ns/op    4096 B/op    12 allocs/op
BenchmarkBuildJSON-8    45102    26201 ns/op    4096 B/op    12 allocs/op
BenchmarkBuildJSON-8    44876    26310 ns/op    4096 B/op    12 allocs/op
BenchmarkBuildJSON-8    45010    26250 ns/op    4096 B/op    12 allocs/op
```

The `-count=5` flag runs the benchmark five times. Comparing the variance across runs tells you whether your measurements are stable. If you see 20% variance in ns/op across runs of the same benchmark, something environmental is interfering — thermal throttling, background processes, garbage collection mid-measurement.

For comparing two implementations, use the `benchstat` tool from `golang.org/x/perf`:

```bash
$ go test -bench=BenchmarkBuildJSON -benchmem -count=10 > before.txt
# Make your change
$ go test -bench=BenchmarkBuildJSON -benchmem -count=10 > after.txt
$ benchstat before.txt after.txt

name             old time/op    new time/op    delta
BuildJSON-8       26.2µs ± 1%   18.4µs ± 2%   -29.7%  (p=0.000 n=10+10)

name             old alloc/op   new alloc/op   delta
BuildJSON-8       4.10kB ± 0%   1.20kB ± 0%   -70.7%  (p=0.000 n=10+10)

name             old allocs/op  new allocs/op  delta
BuildJSON-8         12.0 ± 0%      4.0 ± 0%   -66.7%  (p=0.000 n=10+10)
```

`benchstat` applies statistical analysis and gives you a confidence level. A delta with `p=0.000` is statistically significant. A delta with `p=0.3` is noise.

## In The Wild

I was benchmarking a JSON serialization path and got results that seemed too good — sub-microsecond for what should have been a multi-microsecond operation. After adding `b.ReportAllocs()` and checking that allocs/op was non-zero, I realized the compiler had eliminated the JSON marshaling entirely because I wasn't using the output:

```go
// WRONG — marshal result thrown away, compiler may skip the call
func BenchmarkMarshal(b *testing.B) {
    event := Event{ID: "abc", Level: "info", Message: "test"}
    for i := 0; i < b.N; i++ {
        json.Marshal(event) // result discarded
    }
}
```

The correct version captures the result and prevents elimination:

```go
var benchBytes []byte
var benchErr error

func BenchmarkMarshal(b *testing.B) {
    event := Event{ID: "abc", Level: "info", Message: "test"}
    b.ResetTimer()
    for i := 0; i < b.N; i++ {
        benchBytes, benchErr = json.Marshal(event)
    }
    _ = benchErr // prevent "declared and not used"
}
```

Now the benchmark measures actual marshaling, including allocations. The real result was 3.2µs and 2 allocs/op — very different from the fabricated sub-microsecond. Armed with that real baseline, I could make a meaningful comparison after switching to `encoding/json/v2` and later to `github.com/bytedance/sonic`.

Beyond correctness, I also started using `-cpuprofile` and `-memprofile` directly from `go test`:

```bash
$ go test -bench=BenchmarkMarshal -cpuprofile cpu.prof -memprofile mem.prof
$ go tool pprof -http=:8080 cpu.prof
```

Profiling a benchmark rather than a running server removes a lot of noise. The benchmark controls exactly what code runs, so the profile is a much cleaner signal than one taken from production traffic. Every significant optimization I've made in Go started from a benchmark-driven profile, not a guess.

## The Gotchas

**`b.N` is not a constant you control.** The framework starts with `b.N=1`, measures, then increases `b.N` until it gets stable results or a minimum duration (default 1 second). Your benchmark loop must work correctly for any `b.N`, including `b.N=1`. Don't index into fixed-size arrays using `i % len(data)` without checking that `len(data) > 0`.

**`b.StopTimer()` and `b.StartTimer()` have overhead.** Using them inside the benchmark loop to exclude per-iteration setup costs can add enough overhead to distort results for fast operations. Prefer restructuring the benchmark to put setup before `b.ResetTimer()`.

**Benchmarks share the process with the test binary.** If your benchmark allocates a lot in early iterations, the GC may fire during later iterations, adding GC pause time to the ns/op measurement. `-gcflags='-N -l'` disables optimizations for debugging but also suppresses inlining that affects benchmarks. Use it carefully and never compare optimized vs. non-optimized benchmark results.

**Microbenchmarks lie about system-level behavior.** A benchmark that shows 2x improvement in isolation may show 5% improvement in production because the bottleneck was never the code you optimized — it was the database, the network, or the mutex contention you didn't model. Always validate benchmark improvements against production metrics.

## Key Takeaway

`testing.B` is one of Go's best-designed tools, and it will mislead you if you use it carelessly. The benchmarks that actually tell you something are the ones that prevent dead code elimination, reset the timer after setup, run with `-benchmem`, use `-count` for statistical validity, and get compared with `benchstat` rather than eyeballed. Writing a good benchmark is a skill that takes practice — and a well-written benchmark is worth more than ten minutes of speculation about whether your change made things faster.

---

[← Lesson 4: String and Byte Conversions](/post/go/go-perf-string-bytes) | [Course Index](/series/go-performance-engineering) | [Next → Lesson 6: pprof Deep Dive](/post/go/go-perf-pprof)
