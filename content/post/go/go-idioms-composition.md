---
title: 'Lesson 21: Composition Over Inheritance — Small pieces, loosely joined'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - composition
  - struct embedding
  - interfaces
  - middleware
  - inheritance
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 21
date: '2025-06-02T00:00:00.000Z'
---

If you've come from Java or C++, you're probably waiting for Go to show you its inheritance model. Where's the `extends` keyword? Where are the base classes? There aren't any — Go made a deliberate choice to leave them out. After writing a few thousand lines of Go, most people agree it was the right call.

## The Problem

Object-oriented languages lean heavily on the "is-a" relationship. A `Dog` is-a `Animal`. A `Manager` is-a `Employee`. This sounds elegant until real-world complexity enters the picture: is a `FlyingFish` a `Fish` or a `Bird`? Now you're in multiple inheritance territory and things get messy fast. And in Go, the instinct to fake it with named fields creates its own noise:

```go
// WRONG — using a named field when you want to promote behavior
type Logger struct {
    prefix string
}

func (l Logger) Log(msg string) {
    fmt.Printf("[%s] %s\n", l.prefix, msg)
}

type Server struct {
    logger Logger  // named field — you must call s.logger.Log(...)
    addr   string
}

func main() {
    s := Server{logger: Logger{prefix: "SERVER"}, addr: ":8080"}
    s.logger.Log("starting up")  // verbose, always need the field name
}
```

Every call site has to know about `logger`. Refactoring what that field is called breaks every call site. You've created tight coupling through a back door.

## The Idiomatic Way

Embed the type without naming it. Go promotes the embedded type's methods onto the outer struct automatically.

```go
// RIGHT — embed the type to promote its methods
type Logger struct {
    prefix string
}

func (l Logger) Log(msg string) {
    fmt.Printf("[%s] %s\n", l.prefix, msg)
}

type Server struct {
    Logger        // embedded — methods promoted to Server
    addr   string
}

func main() {
    s := Server{Logger: Logger{prefix: "SERVER"}, addr: ":8080"}
    s.Log("starting up")  // clean, direct access
}
```

When you embed `Logger` without a field name, all of `Logger`'s exported methods get promoted onto `Server`. The embedded type's name becomes an implicit field when you need to reach into it explicitly — `s.Logger` — but you rarely need to.

You can embed interfaces too, not just concrete types. This is the pattern behind Go's entire `http` middleware ecosystem:

```go
// RIGHT — embed an interface to override only the methods you care about
type responseRecorder struct {
    http.ResponseWriter        // satisfies the full interface automatically
    statusCode int
    body       bytes.Buffer
}

func (r *responseRecorder) WriteHeader(code int) {
    r.statusCode = code
    r.ResponseWriter.WriteHeader(code)  // delegate to original
}

func (r *responseRecorder) Write(b []byte) (int, error) {
    r.body.Write(b)
    return r.ResponseWriter.Write(b)
}
```

`responseRecorder` satisfies `http.ResponseWriter` completely — not because it implements every method, but because it embeds the interface and only overrides the two it cares about.

When an outer struct defines a method with the same name as an embedded type's method, the outer method wins. That's shadowing — it's how you customize behavior without touching the original:

```go
type Base struct{}

func (b Base) Describe() string { return "I am Base" }

type Extended struct{ Base }

func (e Extended) Describe() string {
    return "I am Extended (Base says: " + e.Base.Describe() + ")"
}
```

`e.Describe()` calls `Extended`'s version. Reach through explicitly if you want the embedded one: `e.Base.Describe()`. Go never does magic dispatch.

## In The Wild

HTTP middleware is where composition really shines. The standard library's `http.Handler` is one method:

```go
type Handler interface {
    ServeHTTP(ResponseWriter, *Request)
}
```

You compose an entire middleware stack by wrapping handlers:

```go
func withLogging(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()
        log.Printf("started %s %s", r.Method, r.URL.Path)
        next.ServeHTTP(w, r)
        log.Printf("completed in %v", time.Since(start))
    })
}

func withAuth(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("Authorization")
        if token != "Bearer secret" {
            http.Error(w, "unauthorized", http.StatusUnauthorized)
            return
        }
        next.ServeHTTP(w, r)
    })
}

func withRecovery(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        defer func() {
            if err := recover(); err != nil {
                log.Printf("panic: %v", err)
                http.Error(w, "internal server error", http.StatusInternalServerError)
            }
        }()
        next.ServeHTTP(w, r)
    })
}

func main() {
    handler := http.HandlerFunc(helloHandler)
    stack := withLogging(withAuth(withRecovery(handler)))
    http.ListenAndServe(":8080", stack)
}
```

Each middleware takes an `http.Handler` and returns an `http.Handler`. They know nothing about each other. Adding rate limiting later means writing one more function and adding it to the chain — the existing code doesn't change. Compare that to an inheritance-based approach where changing the order of two concerns requires restructuring a class hierarchy.

## The Gotchas

**Embedding leaks the embedded type's entire surface.** If you embed a `sync.Mutex`, callers can call `s.Lock()` directly on your outer struct. That's usually not what you want. Consider embedding as a private field instead, or exposing only the methods you actually intend to make public.

**Embedding isn't inheritance.** When you embed `Logger` in `Server`, passing `Server` to a function that expects `Logger` still doesn't work. Go doesn't do implicit upcasting. Embedding promotes methods, it doesn't establish an "is-a" type relationship.

**Shadowed methods can hide bugs.** If an embedded type gains a new method in a later version that has the same name as a method on your outer type, your outer method silently shadows it. The compiler won't warn you. Keep embedded types in dependencies narrow, or embed interfaces rather than large concrete types.

## Key Takeaway

Go flips the inheritance model: instead of asking what a type *is*, you ask what it *has* and what it *can do*. Struct embedding promotes methods onto the outer type, letting you build complex behavior by combining small, focused pieces. The middleware pattern — each function wrapping one `http.Handler` and returning another — is the purest expression of this in the standard library. Once you internalize it, you'll stop missing class hierarchies entirely.

---

← [Lesson 20: internal Package Is Underrated](/post/go/go-idioms-internal-package) | [Course Index](/post/go/) | [Lesson 22: Small Packages Win](/post/go/go-idioms-small-packages) →
