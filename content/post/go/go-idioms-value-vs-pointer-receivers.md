---
title: 'Go Idioms: Value vs Pointer Receivers'
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
---
"(%g, %g)", p.X, p.Y)
}
```

Here, working with a copy is correct behavior. You do not want `Distance` or `String` to be able to modify the point — the value receiver enforces that at the type system level.

## Method Sets and Interface Satisfaction — Where It Gets Serious

This is where the pointer/value distinction stops being a style choice and starts being a correctness issue.

In Go, a pointer type `*T` has all the methods of both `T` and `*T`. A value type `T` only has the methods defined with value receivers. This matters enormously when satisfying interfaces:

```go
type Stringer interface {
    String() string
}

type Person struct {
    Name string
}

// Pointer receiver on String
func (p *Person) String() string {
    return p.Name
}

func main() {
    p := Person{Name: "Alice"}

    // WRONG — this does not compile
    // var s Stringer = p // cannot use p (type Person) as type Stringer
    // Person does not implement Stringer (String method has pointer receiver)

    // RIGHT
    var s Stringer = &p // *Person implements Stringer
    fmt.Println(s.String())
}
```

The compiler error message is clear but can feel confusing the first time you see it. The fix is always the same: pass a pointer.

Here is a more realistic example with `http.Handler`:

```go
type MyHandler struct {
    db     *sql.DB
    logger *log.Logger
}

// WRONG — value receiver
func (h MyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // This works, but h is a copy
    // If ServeHTTP stores anything on h, it will be lost
    // Also, copying a sql.DB or logger is wrong
}

// RIGHT — pointer receiver
func (h *MyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // Works on the original, no copying of db or logger
}

func main() {
    h := &MyHandler{
        db:     openDB(),
        logger: log.New(os.Stdout, "", log.LstdFlags),
    }

    // http.Handle expects an http.Handler interface
    // *MyHandler satisfies it because ServeHTTP is on *MyHandler
    http.Handle("/", h)
}
```

## Do Not Mix Receiver Types on the Same Type

Go allows you to define some methods with value receivers and some with pointer receivers on the same type. The compiler will not stop you. But this is almost always a mistake:

```go
type Session struct {
    ID    string
    token string
}

// WRONG — mixing receiver types is confusing and creates asymmetric method sets
func (s Session) GetID() string {
    return s.ID
}

func (s *Session) Refresh() {
    s.token = generateToken()
}
```

The problem is that `Session` (value type) has `GetID` in its method set but not `Refresh`. Only `*Session` has both. This asymmetry creates surprises when you try to use `Session` as an interface — you have to remember which methods are on the pointer type vs the value type.

The rule: once a type has any pointer receiver methods, all methods should use pointer receivers. Make the type consistently "pointer-based".

```go
// RIGHT — consistent pointer receivers
func (s *Session) GetID() string {
    return s.ID
}

func (s *Session) Refresh() {
    s.token = generateToken()
}
```

## The Three Rules for Choosing

When deciding on receiver type, apply these rules in order:

**Rule 1: Does the method need to mutate the receiver?**
Use a pointer receiver. No exceptions.

**Rule 2: Does the struct contain a field that should not be copied?**
Use a pointer receiver. This includes `sync.Mutex`, `sync.WaitGroup`, database connections, file handles, and any type whose documentation says "do not copy." Copying a mutex defeats its purpose entirely.

```go
// WRONG — copying a mutex
type Cache struct {
    mu   sync.Mutex
    data map[string]string
}

func (c Cache) Get(key string) string { // copies Cache, copies the mutex — wrong
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.data[key]
}

// RIGHT
func (c *Cache) Get(key string) string {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.data[key]
}
```

**Rule 3: Is the struct large?**
Use a pointer receiver. Copying a 500-byte struct on every method call adds up. There is no hard cutoff, but anything beyond a few fields with basic types should be a pointer receiver.

If none of these rules apply — the method is read-only, the struct is small, there are no non-copyable fields — a value receiver is fine. Small value types like `time.Time`, `net.IP`, and `image.Point` use value receivers throughout the standard library.

## Auto-Addressable Values Don't Bail You Out at Interface Boundaries

One last thing worth being explicit about. Go is helpful at direct method call sites:

```go
c := Counter{count: 0}
c.Increment() // Go auto-takes the address: (&c).Increment()
```

This works and the language spec guarantees it for addressable values. But it does not work when you are assigning to an interface:

```go
var c Counter
c.Increment()         // fine — Go handles this at the call site
_ = c.count           // 1, correct

var s fmt.Stringer = c // if String() is on *Counter, this fails to compile
```

The auto-address trick is only available at direct call sites. The moment you assign to an interface, the method set rules apply strictly. This catches people out regularly — they test methods directly, everything works, then they try to pass the value to a function that expects an interface and the compiler refuses.

The fix is always to pass a pointer: `var s fmt.Stringer = &c`.

## Putting It Together

Pointer receivers are the default for almost every struct type you define. Value receivers are appropriate for small, purely immutable types. When in doubt, use a pointer receiver — it is safer, it avoids surprises with interface satisfaction, and it prevents accidental copying of types that should not be copied.

The one situation where you want to be thoughtful about it is small value types designed to be passed around by value — think `net.IP` or a custom `Color` type. For those, value receivers are intentional. For everything else, pointer receivers keep your code predictable.
