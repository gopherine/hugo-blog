---
title: "Lesson 7: Race Conditions and the Go Memory Model — The bug you can't reproduce"
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 7
keywords:
  - Go
  - golang
  - concurrency
  - race condition
  - data race
  - go memory model
  - happens-before
  - race detector
  - -race flag
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-06-12T00:00:00.000Z'
---

Race conditions are the category of bug that makes senior engineers paranoid and junior engineers dismissive. They don't reproduce consistently. Your tests pass. Your staging environment looks fine. Then, in production, under a specific load pattern at a specific time, two goroutines read and write the same memory address with no synchronization, and you get corrupted data — or a panic — or silently wrong results that sit in your database for three weeks before someone notices. Understanding why they happen at the language level is what separates engineers who prevent them from engineers who just get lucky.

## The Problem

Here's a data race so clean it almost looks intentional:

```go
// WRONG — data race: two goroutines read and write count with no synchronization
func main() {
    count := 0

    var wg sync.WaitGroup
    wg.Add(2)

    go func() {
        defer wg.Done()
        for i := 0; i < 1000; i++ {
            count++ // read-modify-write, not atomic
        }
    }()

    go func() {
        defer wg.Done()
        for i := 0; i < 1000; i++ {
            count++ // same address, no lock
        }
    }()

    wg.Wait()
    fmt.Println(count) // might be 2000, might be 1347, might be anything
}
```

`count++` is three operations: load the value, increment it, store it back. If both goroutines load the same value before either stores, one increment is lost. Under the right scheduling, you can lose most of them. The result is nondeterministic, and on x86 it often happens to be correct — which is why race conditions from local testing don't show up until ARM, or until higher concurrency, or until you change something unrelated that shifts the scheduling.

Here's the subtler version — the one that looks like it's protected but isn't:

```go
// WRONG — checking and acting are separate, race between them
type Cache struct {
    data map[string]string
}

var globalCache = &Cache{data: make(map[string]string)}

func getOrSet(key, value string) string {
    // Two goroutines can both reach here simultaneously
    if _, ok := globalCache.data[key]; !ok {
        // Both see "not present", both try to write
        globalCache.data[key] = value // concurrent map write — runtime panic
    }
    return globalCache.data[key]
}
```

Concurrent writes to a Go map cause a runtime panic — not a race condition in the data-corruption sense, but an explicit crash the runtime detects: `concurrent map read and map write`. This is actually Go being helpful — map operations aren't safe for concurrent use, and the runtime tells you loudly. The race is still a bug, but at least it's a visible one.

The third kind is the one the race detector sometimes misses — a logical race condition:

```go
// WRONG — logical race: we check, then act, but state can change between check and act
func (b *BankAccount) Withdraw(amount int) error {
    if b.balance < amount { // check
        return ErrInsufficientFunds
    }
    // Another goroutine can withdraw between check and action
    b.balance -= amount // act
    return nil
}
```

If two goroutines both pass the check simultaneously with a balance of 100 and both try to withdraw 100, you end up with -100. This is a classic TOCTOU (time of check to time of use) race — the race detector won't catch it unless it observes a concurrent access to `b.balance` at the same instant. The logic is racy even if the memory access doesn't technically overlap.

## The Idiomatic Way

The Go memory model defines when a read of a variable is *guaranteed* to observe the value written by a specific write. The key concept is **happens-before**: if operation A happens-before operation B, B is guaranteed to see A's effect. Synchronization primitives — channel sends/receives, mutex lock/unlock, `sync/atomic` operations — establish happens-before relationships.

Without a happens-before relationship between a write and a read, the read can observe any value — the old one, the new one, or a torn intermediate value on architectures that don't guarantee word-level atomicity.

The fix for the counter is synchronization:

```go
// RIGHT — atomic increment for a simple counter
import "sync/atomic"

func main() {
    var count int64

    var wg sync.WaitGroup
    wg.Add(2)

    go func() {
        defer wg.Done()
        for i := 0; i < 1000; i++ {
            atomic.AddInt64(&count, 1) // single atomic operation
        }
    }()

    go func() {
        defer wg.Done()
        for i := 0; i < 1000; i++ {
            atomic.AddInt64(&count, 1)
        }
    }()

    wg.Wait()
    fmt.Println(atomic.LoadInt64(&count)) // always 2000
}
```

`atomic.AddInt64` is a hardware-level atomic operation — read, increment, write, indivisible. The happens-before relationship is established by the atomic itself.

For the map case, a mutex or `sync.Map`:

```go
// RIGHT — mutex protects the check-and-set as a single atomic operation
type SafeCache struct {
    mu   sync.RWMutex
    data map[string]string
}

func (c *SafeCache) GetOrSet(key, value string) string {
    c.mu.RLock()
    if v, ok := c.data[key]; ok {
        c.mu.RUnlock()
        return v
    }
    c.mu.RUnlock()

    c.mu.Lock()
    defer c.mu.Unlock()
    // Double-check — another goroutine might have set it between our RUnlock and Lock
    if v, ok := c.data[key]; ok {
        return v
    }
    c.data[key] = value
    return value
}
```

The check and the write are now in the same critical section. No other goroutine can see the "not present" state between our check and our write. The double-check after acquiring the write lock handles the case where another goroutine set the key in that brief window.

For the bank account — `check` and `act` must be atomic:

```go
// RIGHT — check and act under the same lock
type BankAccount struct {
    mu      sync.Mutex
    balance int
}

func (b *BankAccount) Withdraw(amount int) error {
    b.mu.Lock()
    defer b.mu.Unlock()

    if b.balance < amount {
        return ErrInsufficientFunds
    }
    b.balance -= amount
    return nil
}
```

Now there's no window between check and act. The mutex establishes a happens-before relationship: every goroutine that sees `b.balance` after acquiring the lock is guaranteed to see all writes that released the lock before them.

## In The Wild

The most instructive race I've debugged was in a session management service. Sessions had an `expiresAt` field that a background goroutine updated on each access. The HTTP handler read `expiresAt` to check validity and the background goroutine wrote it on a timer — no lock, because "it's just a time.Time, what's the worst that could happen?"

On a 32-bit ARM instance (long story), `time.Time` writes weren't atomic — it's a 12-byte struct on that platform. We got partially-written timestamps, causing sessions to appear expired when they weren't.

```go
// RIGHT — production session with proper synchronization
type Session struct {
    mu        sync.RWMutex
    id        string
    userID    string
    expiresAt time.Time
}

func (s *Session) IsValid() bool {
    s.mu.RLock()
    defer s.mu.RUnlock()
    return time.Now().Before(s.expiresAt)
}

func (s *Session) Extend(d time.Duration) {
    s.mu.Lock()
    defer s.mu.Unlock()
    s.expiresAt = time.Now().Add(d)
}
```

The race detector would have caught this immediately. We weren't running it in CI. We were after that.

To use the race detector:

```bash
# Run tests with race detection
go test -race ./...

# Run a binary with race detection
go run -race main.go

# Build a race-enabled binary for staging
go build -race -o app-race .
```

The race detector adds roughly 5-10x overhead. You won't run it in production on a hot path — but running it in CI, in staging, and periodically on load test environments catches the vast majority of real races before they bite.

## The Gotchas

**The race detector only catches races it observes.** It instruments memory accesses and records which goroutine made them — but it can only detect a race if two goroutines actually access the same location concurrently during a run. A race that only triggers at 10x load won't be caught by tests that run at 1x. This is why you also need load testing with `-race` and careful reasoning about synchronization — the detector is a net, not a guarantee.

**"It works in testing" means nothing for races.** The x86 memory model is relatively strong — it provides store-load ordering guarantees that ARM doesn't. Code that data-races on ARM can behave correctly on x86 indefinitely. If you're developing on macOS with Apple Silicon (ARM), the race detector is even more important — you might actually *see* races that would've been invisible on a developer's x86 laptop.

**`sync/atomic` doesn't make logic races disappear.** Atomic operations guarantee that individual reads and writes are indivisible — but if your correctness requires a check-then-act sequence, atomic alone isn't enough. You need a mutex to make the whole sequence atomic from other goroutines' perspectives. `atomic.CompareAndSwap` (CAS) is the exception — it's a check-and-act as a single atomic operation, useful for lock-free data structures, but complex enough to warrant extreme care.

## Key Takeaway

Race conditions are correctness bugs, not performance bugs — they corrupt state, produce wrong answers, and crash programs, and they do it nondeterministically in ways that resist both testing and reasoning. The Go memory model tells you exactly what you need: establish a happens-before relationship (via mutex, channel, or atomic) between every write and every read that depends on that write. Run `-race` in CI, in load tests, and whenever something is behaving mysteriously. The race detector isn't optional tooling — it's the first thing you reach for when a concurrent system starts misbehaving.

---

[← Lesson 6: Mutexes Done Right](/post/go/go-concurrency-mutexes) | [Course Index](/series/go-concurrency-masterclass) | [Next → Lesson 8: Fan-Out Fan-In Pipelines](/post/go/go-concurrency-fan-out-fan-in)
