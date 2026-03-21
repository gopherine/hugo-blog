---
title: "Lesson 18: The sync Package — Coordination tools for concurrent code"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 18
keywords: [Go, golang, beginner, sync, mutex, rwmutex, waitgroup, once, sync.Map, sync.Pool, concurrency]
tags: [Go tutorial, golang, beginner]
date: '2024-04-06T00:00:00.000Z'
---

Goroutines make concurrency easy to start, but easy to start isn't the same as easy to get right. The first time I ran multiple goroutines that touched shared data, I got a data race — a situation where two goroutines read and write the same variable at the same time, producing unpredictable results. The Go race detector caught it immediately, but I still had to understand *how to fix it*.

That's what the `sync` package is for. It gives you the low-level coordination tools that goroutines need when they share state: locks, barriers, one-time initialization, and a few specialized data structures. You won't need all of these on day one, but you'll reach for them regularly once you start writing real concurrent programs.

---

## The Basics

### `sync.Mutex` — the basic lock

A *mutex* (mutual exclusion lock) ensures that only one goroutine can execute a section of code at a time. Any goroutine that tries to lock an already-locked mutex will wait until the holder unlocks it.

```go
package main

import (
    "fmt"
    "sync"
)

type SafeCounter struct {
    mu    sync.Mutex
    value int
}

func (c *SafeCounter) Increment() {
    c.mu.Lock()
    c.value++
    c.mu.Unlock()
}

func (c *SafeCounter) Value() int {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.value
}

func main() {
    counter := &SafeCounter{}
    var wg sync.WaitGroup

    for i := 0; i < 1000; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            counter.Increment()
        }()
    }

    wg.Wait()
    fmt.Println(counter.Value()) // Always 1000
}
```

Notice two things: the mutex is embedded inside the struct it protects, and I use `defer mu.Unlock()` in `Value()` so the lock is released even if the function panics. Pair every `Lock()` with an `Unlock()`, and use `defer` to make that pairing obvious.

**Never copy a mutex.** Passing a mutex by value breaks it. Always use a pointer (`*sync.Mutex`) or embed it in a struct that you pass by pointer.

### `sync.RWMutex` — efficient read-heavy workloads

A regular `Mutex` is all-or-nothing: only one goroutine at a time, whether reading or writing. If your data is read much more often than it's written, this is unnecessarily slow. `sync.RWMutex` lets multiple goroutines hold a *read lock* simultaneously, while a *write lock* still requires exclusive access.

```go
type SafeMap struct {
    mu   sync.RWMutex
    data map[string]string
}

func (m *SafeMap) Set(key, value string) {
    m.mu.Lock()         // exclusive write lock
    defer m.mu.Unlock()
    m.data[key] = value
}

func (m *SafeMap) Get(key string) string {
    m.mu.RLock()         // shared read lock
    defer m.mu.RUnlock()
    return m.data[key]
}
```

Use `RWMutex` when reads dominate. If writes are just as common as reads, the overhead of tracking readers makes `RWMutex` slower than a plain `Mutex`.

### `sync.WaitGroup` — waiting for goroutines to finish

A `WaitGroup` is a counter that lets you wait for a collection of goroutines to complete. You call `Add(n)` before launching goroutines, each goroutine calls `Done()` when it finishes, and the main goroutine calls `Wait()` to block until the counter reaches zero.

```go
package main

import (
    "fmt"
    "sync"
)

func processItem(id int, wg *sync.WaitGroup) {
    defer wg.Done()
    fmt.Printf("Processing item %d\n", id)
}

func main() {
    var wg sync.WaitGroup

    for i := 1; i <= 5; i++ {
        wg.Add(1)
        go processItem(i, &wg)
    }

    wg.Wait()
    fmt.Println("All items processed.")
}
```

Call `Add` before launching the goroutine, not inside it. If you call `Add` inside the goroutine, there's a race between the goroutine starting and the `Wait` call — you might `Wait` before any `Add` has happened, and it returns immediately.

### `sync.Once` — run something exactly once

`sync.Once` guarantees that a function runs exactly once, no matter how many goroutines call it. This is perfect for lazy initialization — setting up something expensive the first time it's needed.

```go
package main

import (
    "fmt"
    "sync"
)

type Config struct {
    DSN string
}

var (
    config     *Config
    configOnce sync.Once
)

func GetConfig() *Config {
    configOnce.Do(func() {
        fmt.Println("Initializing config (runs once)")
        config = &Config{DSN: "postgres://localhost/mydb"}
    })
    return config
}

func main() {
    var wg sync.WaitGroup
    for i := 0; i < 5; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            cfg := GetConfig()
            fmt.Println("Got config:", cfg.DSN)
        }()
    }
    wg.Wait()
}
```

"Initializing config" will print exactly once even though five goroutines called `GetConfig()` simultaneously.

### `sync.Map` — when to use it

Go's built-in `map` is not safe for concurrent use. You need either a mutex around it or a `sync.Map`. `sync.Map` is a special map type designed for two specific patterns:

1. You write to the map once (or rarely) and read from it constantly
2. Many goroutines each write to disjoint sets of keys

For a general-purpose concurrent map, a `map` protected by an `RWMutex` is usually clearer and often faster. Reach for `sync.Map` only when profiling shows that mutex contention is a bottleneck.

```go
var m sync.Map

m.Store("language", "Go")
m.Store("version", "1.22")

if val, ok := m.Load("language"); ok {
    fmt.Println(val) // Go
}

m.Range(func(key, value any) bool {
    fmt.Printf("%s = %s\n", key, value)
    return true // returning false stops iteration
})
```

### `sync.Pool` — what it's for

`sync.Pool` is a cache of temporary objects. It lets you reuse allocated objects rather than creating new ones every time — which reduces pressure on the garbage collector.

```go
var bufPool = sync.Pool{
    New: func() any {
        return make([]byte, 1024)
    },
}

func processRequest(data []byte) {
    buf := bufPool.Get().([]byte)
    defer bufPool.Put(buf)

    copy(buf, data)
    // ... use buf ...
}
```

Important: `sync.Pool` does not guarantee that an object will still be in the pool the next time you ask. The GC can clear the pool at any time. Use it for objects that are expensive to allocate and can be safely reused, not for objects that carry state between calls.

---

## Try It Yourself

Write a program that launches 10 goroutines, each adding 100 to a shared integer using a mutex-protected struct. Use a `WaitGroup` to wait for all of them, then print the final value. It should always be 1000.

Then run it with the race detector enabled (`go run -race main.go`) to confirm there's no data race. If you remove the mutex, the race detector should flag it.

---

## Common Mistakes

**Copying a mutex or WaitGroup**

`sync.Mutex`, `sync.RWMutex`, `sync.WaitGroup`, and `sync.Once` must not be copied after first use. If you pass a struct containing one of these by value, the copy has a separate, broken lock state. Always use pointers.

**Calling `wg.Add` inside the goroutine**

```go
// WRONG
go func() {
    wg.Add(1)      // too late — wg.Wait() might have already returned
    defer wg.Done()
    // ...
}()
```

Always call `wg.Add(1)` in the goroutine's *parent*, right before the `go` statement.

**Using `sync.Map` everywhere**

`sync.Map` has a less ergonomic API than a plain map and is only faster in specific access patterns. Don't use it as a drop-in replacement for all maps. A `map` + `sync.RWMutex` is usually the right choice.

**Forgetting `defer` on `Unlock`**

If your locked function can panic or return early, and you haven't used `defer`, the mutex stays locked forever. Always `defer mu.Unlock()` immediately after `mu.Lock()`.

---

## Key Takeaway

The `sync` package gives you six essential coordination tools: `Mutex` for exclusive access, `RWMutex` for read-heavy workloads, `WaitGroup` for waiting on goroutines, `Once` for one-time initialization, `Map` for specific concurrent map patterns, and `Pool` for reusing temporary objects. Never copy these types. Always pair `Lock` with a deferred `Unlock`. Run `go test -race` regularly — it catches data races before they catch you.

---

*[Course Index: Go from Scratch](/series/go-from-scratch) | [← Lesson 17: Go Modules](/post/go/go-scratch-modules) | [Lesson 19: Context →](/post/go/go-scratch-context)*
