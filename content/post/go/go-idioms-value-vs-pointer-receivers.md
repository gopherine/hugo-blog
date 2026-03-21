---
title: 'Lesson 12: Value vs Pointer Receivers — The method that silently does nothing'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - value receiver
  - pointer receiver
  - method sets
  - interfaces
tags:
  - Go tutorial
  - golang
date: '2026-02-23T00:00:00.000Z'
series: "Idiomatic Go"
lesson: 12
---

Value vs pointer receivers is one of those topics that seems like a style preference until it silently breaks your program. The tell is a method that looks like it mutates a struct, compiles without complaint, but the mutations simply don't persist. You add a log line, the value is right inside the method — and wrong the moment the method returns.

## The Problem

A value receiver operates on a copy. Every time you call the method, Go copies the entire struct and passes the copy to the method. Mutations happen on the copy, which gets discarded when the method returns. The original is untouched.

```go
type Counter struct {
    count int
}

// WRONG — value receiver, operates on a copy
func (c Counter) Increment() {
    c.count++ // increments the copy, not the original
}

func main() {
    c := Counter{}
    c.Increment()
    c.Increment()
    fmt.Println(c.count) // 0 — both increments were lost
}
```

There is no compiler warning. The code runs. The counter just never moves. This is the kind of bug that takes twenty minutes to find the first time you encounter it, and two seconds every time after.

The interface satisfaction version is worse because it doesn't compile but the error message is cryptic:

```go
type Stringer interface {
    String() string
}

type Person struct{ Name string }

func (p *Person) String() string { return p.Name }

func main() {
    p := Person{Name: "Alice"}
    var s Stringer = p  // compile error: Person does not implement Stringer
                        // (String method has pointer receiver)
}
```

The fix is `&p`. But if you don't understand why, you'll be confused every time this surfaces.

## The Idiomatic Way

Use a pointer receiver when the method needs to mutate the receiver, when the struct contains non-copyable types (mutexes, connections), or when the struct is large enough that copying it repeatedly is wasteful. That covers the vast majority of struct methods you'll write.

```go
// RIGHT — pointer receiver, mutates the original
func (c *Counter) Increment() {
    c.count++
}

func (c *Counter) Value() int {
    return c.count
}

func main() {
    c := Counter{}
    c.Increment()
    c.Increment()
    fmt.Println(c.Value()) // 2 — correct
}
```

For types that contain non-copyable fields, a pointer receiver isn't just idiomatic — it's mandatory:

```go
type Cache struct {
    mu   sync.Mutex
    data map[string]string
}

// WRONG — copying Cache copies the mutex, which the sync package explicitly forbids
func (c Cache) Get(key string) string {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.data[key]
}

// RIGHT — pointer receiver avoids the copy
func (c *Cache) Get(key string) string {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.data[key]
}
```

Once any method on a type uses a pointer receiver, make all methods pointer receivers. Mixing them creates an asymmetric method set — `*Session` has all methods, but `Session` only has the value-receiver ones. That asymmetry causes interface satisfaction surprises:

```go
// WRONG — mixed receivers
type Session struct{ ID, token string }

func (s Session) GetID() string { return s.ID }   // value receiver
func (s *Session) Refresh() { s.token = "new" }  // pointer receiver

// Session only has GetID in its method set
// *Session has both GetID and Refresh

// RIGHT — consistent pointer receivers
func (s *Session) GetID() string { return s.ID }
func (s *Session) Refresh()      { s.token = "new" }
```

Value receivers make sense for small, purely read-only types — think `time.Time`, `net.IP`, a custom `Color` type. If the type is designed to be passed by value and never mutated through a method, value receivers are intentional. For everything else, default to pointer receivers.

## In The Wild

The most common place I've seen this go wrong in production is HTTP handlers. Someone writes a handler struct with value receivers, registers it with `http.Handle("/", handler)` (passing a value), and then wonders why the request count field stays at zero or why the database query returns stale cached data.

```go
type MyHandler struct {
    db     *sql.DB
    logger *log.Logger
}

// WRONG — copies db and logger on every request
func (h MyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // h.db and h.logger are copies — also, sql.DB explicitly says don't copy
}

// RIGHT
func (h *MyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // operates on the original handler, db and logger are not copied
}

func main() {
    h := &MyHandler{db: openDB(), logger: log.New(os.Stdout, "", log.LstdFlags)}
    http.Handle("/", h) // *MyHandler satisfies http.Handler
}
```

## The Gotchas

**Auto-addressability at call sites doesn't help you at interface boundaries.** Go will automatically take the address of an addressable value when you call a pointer-receiver method directly:

```go
c := Counter{count: 0}
c.Increment() // Go does (&c).Increment() — works fine
```

But that trick evaporates the moment you assign to an interface:

```go
var s fmt.Stringer = c // if String() is on *Counter, this does NOT compile
```

Test your methods directly and everything looks fine. Try to pass the value to a function expecting an interface and the compiler refuses. The fix is always to pass a pointer.

**Embedding a type with pointer receivers.** If you embed a type that has pointer receivers, the embedding struct must be used as a pointer if you want the embedded methods in its method set. Forgetting this causes the same interface satisfaction failures with a more confusing error trail.

**Copying a mutex panics under the race detector.** Go's race detector will flag a copied mutex at runtime. If you're passing a struct with a `sync.Mutex` field by value to a function, `go test -race` will catch it. Don't wait for the race detector — just use pointer receivers when your struct has a mutex.

## Key Takeaway

Pointer receivers are the right default for almost every struct type. They prevent the "mutates a copy" bug, they satisfy interfaces correctly, and they avoid accidentally copying non-copyable types like mutexes and database connections. Reserve value receivers for small, immutable value types — the `net.IP` and `time.Time` tier of types that are explicitly designed to be passed by value. When in doubt, use a pointer receiver. The compiler will tell you if you're doing something wrong, but it won't tell you that your value receiver silently threw away your mutation.

---

← [Lesson 11: Nil Slice vs Empty Slice](/post/go/go-idioms-nil-vs-empty-slice) | [Course Index](#) | [Lesson 13: iota for Enums](/post/go/go-idioms-iota-enums) →
