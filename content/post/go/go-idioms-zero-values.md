---
title: 'Go Idioms: Zero Values Are Useful'
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
date: '2026-03-09T00:00:00.000Z'
---
""` (empty string)
- Pointers, slices, maps, channels, functions, interfaces: `nil`
- Structs: each field initialized to its own zero value

This happens everywhere — local variables, struct fields, slice elements, map values. There is no concept of an "uninitialized" variable in Go. The question is not whether a value is initialized, but whether its zero value is meaningful.

## sync.Mutex: A Zero Value That Works Out of the Box

The most cited example of a useful zero value in the standard library is `sync.Mutex`. You do not need to call any constructor. You do not need to `New` it. You just declare it and use it.

```go
// WRONG: unnecessary initialization
mu := sync.Mutex{}  // valid but redundant
mu.Lock()
// ...
mu.Unlock()
```

```go
// RIGHT: zero value is a valid, unlocked mutex
var mu sync.Mutex
mu.Lock()
defer mu.Unlock()
// ...
```

More usefully, when `sync.Mutex` is embedded in a struct, the struct itself is ready to use without any initialization code:

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

`SafeCounter` works correctly without a `NewSafeCounter` function. The zero value of `sync.Mutex` is an unlocked mutex, and the zero value of `int` is zero. The struct's zero value is immediately useful.

## bytes.Buffer: Write Without Initialization

`bytes.Buffer` is another standard library type where the zero value is immediately functional. You can write to a `bytes.Buffer` without calling `new(bytes.Buffer)` or any constructor.

```go
// WRONG: unnecessary allocation
buf := bytes.NewBuffer(nil) // valid, but unnecessary
buf.WriteString("hello")

// RIGHT: zero value works directly
var buf bytes.Buffer
buf.WriteString("hello, ")
buf.WriteString("world")
fmt.Println(buf.String()) // "hello, world"
```

This is especially clean when `bytes.Buffer` is embedded in a struct — the struct is ready to write to immediately upon allocation, without any separate initialization step.

## Designing Your Own Types with Useful Zero Values

The standard library's approach is a pattern worth copying in your own code. The guiding question when designing a type is: *is the zero value of this type a valid and sensible default?*

Consider a rate limiter that defaults to allowing all requests when not configured:

```go
// WRONG design: requires a constructor to be usable
type RateLimiter struct {
    maxPerSec int
    // zero value: maxPerSec=0, which means "reject everything" — not useful
}

func NewRateLimiter(max int) *RateLimiter {
    return &RateLimiter{maxPerSec: max}
}

func (r *RateLimiter) Allow() bool {
    return r.maxPerSec > 0 // zero value rejects all requests — bad default
}
```

```go
// RIGHT design: zero value means "allow everything" (sensible default)
type RateLimiter struct {
    maxPerSec int
    // zero value: maxPerSec=0 means unlimited — useful default
}

func (r *RateLimiter) Allow() bool {
    if r.maxPerSec == 0 {
        return true // zero means unlimited
    }
    // ... actual rate limiting logic
    return true
}
```

With the second design, code that embeds `RateLimiter` without configuring it gets a "pass everything through" default, which is sensible for development, testing, and any context where rate limiting is not yet needed.

A more complete example — a logger with configurable level:

```go
type LogLevel int

const (
    LogLevelInfo  LogLevel = 0 // zero value = info — sensible default
    LogLevelWarn  LogLevel = 1
    LogLevelError LogLevel = 2
)

type Logger struct {
    Level  LogLevel
    output io.Writer
}

func (l *Logger) writer() io.Writer {
    if l.output == nil {
        return os.Stdout // nil output defaults to stdout
    }
    return l.output
}

func (l *Logger) Info(msg string) {
    if l.Level <= LogLevelInfo {
        fmt.Fprintln(l.writer(), "[INFO]", msg)
    }
}
```

A zero-value `Logger` logs everything to stdout at info level — a perfectly reasonable default. No constructor required.

## When Zero Values Bite: nil Maps

Not all zero values are immediately safe to use in all ways. The canonical example is a nil map. You can *read* from a nil map (it returns the zero value for the value type), but you cannot *write* to a nil map — it panics.

```go
// WRONG: writing to a nil map panics
var counts map[string]int
counts["hello"]++ // panic: assignment to entry in nil map
```

```go
// RIGHT: initialize the map before writing
counts := make(map[string]int)
counts["hello"]++
```

This is a genuine zero value trap. The zero value of a map is `nil`, and `nil` maps are not writable. The idiomatic fix is to either initialize in a constructor or use a pattern that lazily initializes:

```go
// A struct that lazily initializes its map on first use
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

`WordCounter` has a useful zero value: you can call `Count` immediately (returns 0), and `Add` initializes the map on first use. Callers never need to construct it — `var wc WordCounter` and you are ready to go.

## When Zero Values Bite: nil Channels

A nil channel is another case where the zero value has specific, sometimes surprising behavior:

- Sending to a nil channel blocks forever
- Receiving from a nil channel blocks forever
- A nil channel in a `select` case is ignored (never selected)

The third behavior is actually useful:

```go
// Using nil channel to disable a select case
func merge(ch1, ch2 <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for ch1 != nil || ch2 != nil {
            select {
            case v, ok := <-ch1:
                if !ok {
                    ch1 = nil // disable this case once channel is closed
                    continue
                }
                out <- v
            case v, ok := <-ch2:
                if !ok {
                    ch2 = nil // disable this case once channel is closed
                    continue
                }
                out <- v
            }
        }
    }()
    return out
}
```

Setting a channel variable to `nil` after it closes disables that `select` case. This is idiomatic Go for merging channels — the nil channel zero value is genuinely useful here.

## The sync.Once Pattern

`sync.Once` is another zero-value-ready type. It ensures a function is called exactly once, no matter how many goroutines call it concurrently. No initialization needed:

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

`Connection` works at zero value. The first call to `DB()` initializes the database connection; all subsequent calls return the cached connection. No constructor, no `sync.Mutex` to initialize separately, no `initialized bool` field to track.

## The Principle

When you design a type in Go, think about what the zero value means and whether you can make it useful. The payoff is that users of your type never have to write `NewFoo()` just to get something that does nothing. Embedding your type in a struct does not require an init step. Tests can create `&MyType{}` without a bunch of required arguments.

The canonical checklist:
1. What does the zero value of each field represent?
2. Is there a sensible behavior for the type when all fields are at their zero values?
3. If a field being nil or zero is dangerous, can you add a nil-check or lazy initialization to handle it gracefully?

Not every type can have a useful zero value — sometimes there is mandatory configuration that has no sensible default. But when you can arrange for the zero value to be useful, you should. It makes your API easier to use, your code easier to test, and your struct initialization cleaner. That is idiomatic Go.
