---
title: 'Lesson 10: Zero Values Are Useful — Go types that work before you touch them'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - zero values
  - sync.Mutex
  - bytes.Buffer
  - nil
  - struct design
  - initialization
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 10
date: '2026-03-09T00:00:00.000Z'
---

In most languages, a freshly declared variable is either uninitialized garbage you can't touch safely, or it needs a constructor call before it does anything useful. Go takes a different approach: every variable always has a value. When you don't provide one, Go assigns the zero value for the type. What makes this interesting is that Go's standard library is full of types designed so that their zero value is immediately useful — no constructor required.

## The Problem

The constructor-everywhere pattern is what you'll write if you're not thinking about zero values:

```go
// WRONG design: requires a constructor to be usable
type RateLimiter struct {
    maxPerSec int
}

func NewRateLimiter(max int) *RateLimiter {
    return &RateLimiter{maxPerSec: max}
}

func (r *RateLimiter) Allow() bool {
    return r.maxPerSec > 0 // zero value = reject everything — not useful
}
```

The zero value here actively works against you. A `RateLimiter{}` rejects all requests, which is the worst possible default. Anyone who embeds this type in a struct and forgets the constructor gets silent rejections.

The nil map problem is the most common sharp edge when zero values aren't thought through. Reading from a nil map is safe and returns the zero value for the value type. But writing to a nil map panics:

```go
// WRONG: writing to a nil map panics
var counts map[string]int
counts["hello"]++ // panic: assignment to entry in nil map
```

This is an unintuitive asymmetry that causes real bugs.

## The Idiomatic Way

Design your types so the zero value is a valid, sensible state. The standard library does this throughout, and it's worth copying.

`sync.Mutex` is the canonical example. You don't need to call any constructor. You don't need to `New` it. Just declare it and use it:

```go
// RIGHT: zero value is a valid, unlocked mutex
var mu sync.Mutex
mu.Lock()
defer mu.Unlock()
// ...
```

This pays off more when `sync.Mutex` is embedded in a struct — the containing struct is ready to use without any initialization code:

```go
type SafeCounter struct {
    mu    sync.Mutex
    count int
}

func (c *SafeCounter) Increment() {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.count++
}

func (c *SafeCounter) Value() int {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.count
}

// No constructor needed
counter := SafeCounter{}
counter.Increment()
counter.Increment()
fmt.Println(counter.Value()) // 2
```

`bytes.Buffer` works the same way — write to it without calling `new` or any constructor:

```go
// RIGHT: zero value works directly
var buf bytes.Buffer
buf.WriteString("hello, ")
buf.WriteString("world")
fmt.Println(buf.String()) // "hello, world"
```

For your own types, the approach is to make the zero state of each field represent a sensible default. The rate limiter example becomes clean with one design decision:

```go
// RIGHT design: zero value means "allow everything" (sensible default)
type RateLimiter struct {
    maxPerSec int
    // zero value: maxPerSec=0 means unlimited
}

func (r *RateLimiter) Allow() bool {
    if r.maxPerSec == 0 {
        return true // zero means unlimited — useful for dev/test
    }
    // ... actual rate limiting logic
    return true
}
```

Now `RateLimiter{}` is immediately useful in development, testing, and anywhere rate limiting isn't needed yet.

For the nil map problem, lazy initialization gives you the best of both worlds:

```go
type WordCounter struct {
    counts map[string]int
}

func (w *WordCounter) Add(word string) {
    if w.counts == nil {
        w.counts = make(map[string]int)
    }
    w.counts[word]++
}

func (w *WordCounter) Count(word string) int {
    return w.counts[word] // reading from nil map is safe, returns 0
}
```

`var wc WordCounter` works immediately — you can call `Count` right away (returns 0), and `Add` initializes the map on first use. No constructor required.

## In The Wild

`sync.Once` is another zero-value-ready type from the standard library. It ensures a function runs exactly once regardless of how many goroutines call it concurrently:

```go
type Connection struct {
    once sync.Once
    conn *sql.DB
}

func (c *Connection) DB() *sql.DB {
    c.once.Do(func() {
        db, err := sql.Open("postgres", os.Getenv("DATABASE_URL"))
        if err != nil {
            panic(err)
        }
        c.conn = db
    })
    return c.conn
}
```

`Connection` works at zero value. First call to `DB()` initializes the connection; every subsequent call returns the cached one. No constructor, no separate mutex to initialize, no `initialized bool` field to track — the zero value handles all of it.

Nil channels have a related useful behavior worth knowing about. A nil channel in a `select` case is silently ignored — never selected. This is intentional and idiomatic for disabling select cases:

```go
func merge(ch1, ch2 <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for ch1 != nil || ch2 != nil {
            select {
            case v, ok := <-ch1:
                if !ok {
                    ch1 = nil // disable this case once channel closes
                    continue
                }
                out <- v
            case v, ok := <-ch2:
                if !ok {
                    ch2 = nil
                    continue
                }
                out <- v
            }
        }
    }()
    return out
}
```

Setting a channel to `nil` after it closes disables that arm of the select. This is idiomatic Go for channel merging — the nil zero value is being used deliberately, not defensively.

## The Gotchas

**Not all zero values are safe in all ways.** The nil map write panic is the most common example. A nil slice is safe to read and append to, but a nil map cannot be written to. These asymmetries exist; the solution isn't to avoid nil values but to design types that handle them gracefully through nil checks or lazy initialization.

**Don't force a useful zero value when the semantics don't support it.** Some types have mandatory configuration with no sensible default — a database connection pool needs a connection string, a rate limiter might need an explicit "no limit" constant rather than relying on zero. Forcing a zero-value-usable design when the domain doesn't support it leads to confusing APIs. The goal is useful zero values where they make sense, not zero values everywhere at any cost.

**The zero value of an interface is nil, which is a different flavor of "nothing."** A nil interface and a non-nil interface holding a nil pointer are not the same thing — the latter satisfies type assertions and can have methods called on it (potentially panicking). This is a separate topic but worth keeping in mind when your zero-value-ready type involves interface fields.

## Key Takeaway

When you design a type in Go, the zero value question is worth asking explicitly: is the zero value a valid, useful state? If you can make it one, you should. The payoff is real: users of your type don't need to call `NewFoo()` just to get a no-op default. Embedding your type doesn't require a separate init step. Tests can declare `var thing MyType` and start calling methods. The standard library demonstrates this consistently with `sync.Mutex`, `bytes.Buffer`, `sync.Once`, and others — zero-value-ready types are a gift to everyone who uses them. Design your types the same way.

---

← [Lesson 9: range Gotchas](/post/go/go-idioms-range-gotchas/) | [Course Index](#) | [Lesson 11: nil vs Empty Slice →](/post/go/go-idioms-nil-vs-empty-slice/)
