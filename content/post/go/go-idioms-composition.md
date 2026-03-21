---
title: 'Go Idioms: Composition Over Inheritance'
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
date: '2025-06-02T00:00:00.000Z'
---
ve come from Java or C++, you're probably waiting for Go to show you its inheritance model. Where's the `extends` keyword? Where are the base classes? The answer is: there aren't any. Go made a deliberate choice to leave them out, and it's one of the better decisions in the language's design.

Go uses composition instead. You build complex behavior by combining small, focused pieces — not by constructing deep hierarchies that your colleagues will need a whiteboard to untangle six months later.

## "Has-A" vs "Is-A"

Object-oriented languages lean heavily on the "is-a" relationship. A `Dog` is-a `Animal`. A `Manager` is-a `Employee`. This sounds elegant until real-world complexity enters the picture: is a `FlyingFish` a `Fish` or a `Bird`? Do you give it two parents? Now you're in multiple inheritance territory and things get messy fast.

Go flips the model. Rather than asking what something *is*, you ask what something *has* or *can do*. A `Dog` has locomotion behavior. It has a sound. You compose those capabilities rather than inheriting them from a common ancestor.

This shift is subtle but it changes how you design systems from the ground up.

## Struct Embedding

The mechanical way Go enables composition is struct embedding. You place one type inside another without giving it a field name.

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

When you embed `Logger` without a field name, all of `Logger`'s exported methods get promoted onto `Server`. Callers can call `s.Log(...)` directly. The embedded type's name becomes an implicit field name when you need to refer to it explicitly — `s.Logger` — but you rarely need to.

## Embedding Interfaces

You can embed interfaces too, not just concrete types. This is a powerful pattern that shows up throughout the standard library.

```go
// WRONG — implementing a large interface fully when you only need one method
type ReadWriteCloser interface {
    Read(p []byte) (n int, err error)
    Write(p []byte) (n int, err error)
    Close() error
}

type MyWriter struct{}

func (w MyWriter) Read(p []byte) (int, error)  { return 0, nil }  // fake, unused
func (w MyWriter) Write(p []byte) (int, error) { return len(p), nil }
func (w MyWriter) Close() error                { return nil }  // fake, unused
```

```go
// RIGHT — embed the interface to satisfy it partially or wrap it
type responseRecorder struct {
    http.ResponseWriter        // embed the interface
    statusCode int
    body       bytes.Buffer
}

func (r *responseRecorder) WriteHeader(code int) {
    r.statusCode = code
    r.ResponseWriter.WriteHeader(code)  // delegate to original
}

func (r *responseRecorder) Write(b []byte) (int, error) {
    r.body.Write(b)
    return r.ResponseWriter.Write(b)  // delegate to original
}
```

This `responseRecorder` satisfies `http.ResponseWriter` completely — not because it implements every method, but because it embeds the interface and only overrides the two methods it cares about. The rest delegate automatically.

## Shadowing Embedded Methods

When an outer struct defines a method with the same name as an embedded type's method, the outer method wins. This is called shadowing, and it's how you customize behavior without modifying the original type.

```go
type Base struct{}

func (b Base) Describe() string {
    return "I am Base"
}

type Extended struct {
    Base
}

func (e Extended) Describe() string {
    return "I am Extended (Base says: " + e.Base.Describe() + ")"
}

func main() {
    e := Extended{}
    fmt.Println(e.Describe())
    // Output: I am Extended (Base says: I am Base)
}
```

`e.Describe()` calls `Extended`'s version. If you want the embedded version, you reach through the field: `e.Base.Describe()`. This explicit call is intentional — Go doesn't do magic dispatch.

## Real Example: HTTP Middleware Through Composition

HTTP middleware is where composition really shines. The standard library's `http.Handler` interface is just one method:

```go
type Handler interface {
    ServeHTTP(ResponseWriter, *Request)
}
```

You can compose an entire middleware stack by wrapping handlers:

```go
// Logging middleware
func withLogging(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()
        log.Printf("started %s %s", r.Method, r.URL.Path)
        next.ServeHTTP(w, r)
        log.Printf("completed in %v", time.Since(start))
    })
}

// Auth middleware
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

// Recovery middleware
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

// Your actual handler
func helloHandler(w http.ResponseWriter, r *http.Request) {
    fmt.Fprintln(w, "hello, world")
}

func main() {
    handler := http.HandlerFunc(helloHandler)

    // Compose the stack — inner to outer
    stack := withLogging(withAuth(withRecovery(handler)))

    http.ListenAndServe(":8080", stack)
}
```

Each middleware function takes an `http.Handler` and returns an `http.Handler`. They know nothing about each other. You compose them by nesting the calls, and the request flows through each layer in order. Adding rate limiting later means writing one more function and adding it to the chain — the existing code doesn't change.

Compare this to an inheritance-based approach where you'd have `BaseHandler`, `LoggingHandler extends BaseHandler`, `AuthHandler extends LoggingHandler`, and suddenly changing the order of two concerns requires restructuring a class hierarchy.

## Why This Matters in Practice

Composition keeps your code flexible in ways that inheritance doesn't. When a requirement changes — say, you want logging but not auth on a particular route — you just assemble a different stack. With inheritance, you'd either add a parameter to the base class or create another subclass.

The other advantage is testability. Each piece is small and focused. You can test `withAuth` in isolation by passing a mock handler. You can test your business logic handler without any middleware in the picture at all.

Go doesn't give you inheritance because the designers concluded it creates more problems than it solves. After writing a few thousand lines of Go, most developers agree with them.

The principle is simple: instead of asking "what does this type inherit?", ask "what does this type know how to do, and what does it delegate?" Build from pieces. Keep each piece small. Compose them when you need something larger.
