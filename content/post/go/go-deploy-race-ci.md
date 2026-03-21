---
title: 'Lesson 6: Race Detector in CI — Run -race on every PR or ship bugs'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 6
keywords:
  - Go
  - golang
  - race detector
  - data race
  - -race
  - CI
  - concurrency
  - testing
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2025-02-12T00:00:00.000Z'
---

A data race is a memory safety bug. Two goroutines access the same variable without synchronization, at least one is writing, and the Go memory model makes no guarantees about what happens. In practice: values get corrupted, counters drift, maps panic at runtime. These bugs are intermittent — they appear under load, on fast machines, during deploys — and they're nearly impossible to reproduce deterministically.

The Go race detector is one of the most powerful debugging tools in any language ecosystem. It instruments every memory access at the compiler level and reports races precisely: the exact goroutine stacks at the moment of the conflicting accesses. But it only helps if you run it. Most teams run it once after a bug report. The right approach is running it on every PR, in CI, before the code is ever merged.

## The Problem

The bugs the race detector catches look innocent in code review:

```go
// WRONG — data race on 'result', caught by race detector
func fetchAll(urls []string) []string {
    var results []string // shared variable

    var wg sync.WaitGroup
    for _, url := range urls {
        wg.Add(1)
        go func(u string) {
            defer wg.Done()
            resp, err := http.Get(u)
            if err != nil {
                return
            }
            defer resp.Body.Close()
            body, _ := io.ReadAll(resp.Body)
            results = append(results, string(body)) // RACE: concurrent writes
        }(url)
    }
    wg.Wait()
    return results
}
```

This looks reasonable. The goroutines wait for each other via `WaitGroup`. But `results` is written by multiple goroutines concurrently — `append` on a slice is not atomic. The race detector reports this immediately:

```
==================
WARNING: DATA RACE
Write at 0x00c0001a4060 by goroutine 7:
  main.fetchAll.func1()
      /app/main.go:18 +0x148

Previous write at 0x00c0001a4060 by goroutine 6:
  main.fetchAll.func1()
      /app/main.go:18 +0x148

Goroutine 7 was created at:
  main.fetchAll()
      /app/main.go:11 +0xb4
==================
```

The report gives you the exact line, the exact goroutines, and their creation sites. Without the race detector, this bug might silently corrupt data or panic randomly.

## The Idiomatic Way

Running the race detector in CI requires no special infrastructure — just the `-race` flag:

```bash
go test -race -count=1 ./...
```

That's the core. The binary instruments every memory access. Any race observed during test execution is reported and the test fails.

For the CI configuration specifically, I separate the race run from the regular test run so they can run in parallel and I get separate status checks:

```yaml
# In .github/workflows/ci.yml

  test:
    name: Tests (no race)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.22', cache: true }
      - run: go test -count=1 -timeout=5m ./...

  race:
    name: Tests (race detector)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.22', cache: true }
      - run: go test -race -count=1 -timeout=15m ./...
        env:
          # GORACE options tune the detector's behavior
          GORACE: "halt_on_error=1 history_size=7"
```

The `GORACE` environment variable controls the race detector. `halt_on_error=1` stops the program immediately on the first race rather than continuing. `history_size=7` increases the goroutine history depth, which produces more useful stack traces for complex races. The available memory for history is `32KB × 2^history_size`, so don't set it too high.

Writing tests that reliably trigger races requires running concurrent operations in a loop:

```go
// Test that deliberately exercises concurrent access to catch races
func TestCacheReadWrite(t *testing.T) {
    c := NewCache()
    const workers = 20
    const iterations = 1000

    var wg sync.WaitGroup
    for i := 0; i < workers; i++ {
        wg.Add(1)
        go func(id int) {
            defer wg.Done()
            for j := 0; j < iterations; j++ {
                key := fmt.Sprintf("key-%d", j%10)
                if j%2 == 0 {
                    c.Set(key, id)
                } else {
                    c.Get(key)
                }
            }
        }(i)
    }
    wg.Wait()
}
```

Without concurrent access patterns in your tests, the race detector has nothing to observe. Write tests that create the concurrency that your production code experiences.

## In The Wild

Some patterns produce races that only appear under the race detector because the detector's memory model tracking is more conservative than the CPU's actual behavior. When the detector reports a false positive — which is rare — you can annotate specific variables:

```go
// Suppress the race detector for a specific access.
// This is an escape hatch — use it only when you're certain the detector
// is wrong, and document why.
import "sync/atomic"

// Use atomic operations to express the "this is intentionally concurrent"
// intent to both the race detector and future readers.
var counter atomic.Int64

func increment() {
    counter.Add(1) // atomic — no race
}
```

In practice, the race detector is almost never wrong. If it reports a race, there's a race. The "fix" is correct synchronization, not suppression.

One pattern I've found useful for catching races in long-running services: run the race detector build in a staging environment for a period before each production deploy. The race detector binary is ~2x slower and uses more memory, so you can't run it in production permanently, but a 30-minute burn-in on staging under realistic traffic catches races that tests miss.

```bash
# Build a race-detector-enabled binary for staging
CGO_ENABLED=1 GOOS=linux go build -race \
    -o dist/myapp-race \
    ./cmd/myapp
```

Note: `-race` requires CGO. This is the one case where your staging binary differs from production. Worth it.

## The Gotchas

**Race detector requires CGO.** You cannot build a static `CGO_ENABLED=0` binary with `-race`. The race detector implementation uses the ThreadSanitizer runtime, which is C code. This is why race detection runs in CI on Linux runners with CGO available, not in the static binary build step.

**Races in test setup code are also reported.** If your test helpers or `TestMain` have races, the detector catches those too. I've seen teams confused by race reports in lines of code they thought were "outside the test." The setup and teardown code runs in goroutines too.

**The detector increases memory usage by 5-10x.** Tests that just barely fit in a 4GB CI runner might OOM under `-race`. If this happens, use `go test -race -parallel=4` to limit concurrency, or allocate more memory to the runner.

**Not all races are caught.** The race detector catches races that occur during the test run. If your test doesn't exercise the concurrent code path, the race isn't observed. High concurrency tests with many goroutines and iterations increase the probability of triggering a race. Think of it as a fuzzer: the more paths you exercise, the more races you find.

## Key Takeaway

Data races are silent memory corruption bugs that manifest under load. The race detector finds them with surgical precision — exact goroutines, exact lines, exact moment of conflict. Run it on every PR in CI. Accept the slower test times as the cost of not shipping memory safety bugs to production. Write high-concurrency tests that actually exercise the code paths the detector needs to observe.

---

**Previous:** [Lesson 5: CI/CD for Go](/post/go/go-deploy-cicd/)
**Next:** [Lesson 7: Profiling in Containers — pprof works in Kubernetes too](/post/go/go-deploy-profiling-containers/)
