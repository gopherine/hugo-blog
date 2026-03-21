---
title: "Lesson 5: Implicit Interfaces — The best decoupling you'll never declare"
author: Atharva Pandey
keywords:
  - Go
  - golang
  - interfaces
  - implicit interfaces
  - io.Reader
  - io.Writer
  - interface composition
  - duck typing
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 5
date: '2025-08-25T00:00:00.000Z'
---

In Java or C#, you declare that a class implements an interface. You write `implements Runnable`, and the compiler ties that class to that interface forever. Go doesn't work that way. A type satisfies an interface the moment it has the right methods — no declaration, no explicit relationship. This sounds like a minor syntactic difference, but it changes how you design systems in ways that compound over time.

## The Problem

When interfaces are declared by the implementor (the Java way), you end up with a few recurring problems.

First, every type that wants to implement your interface must import your package. That creates a hard dependency, even if the type only needs one of the eight methods you defined. Second, you tend to define big interfaces upfront, because that's when you're thinking about what a type can do:

```go
// WRONG — fat interface defined by the implementor
type UserRepository interface {
    FindByID(id string) (User, error)
    FindByEmail(email string) (User, error)
    FindAll() ([]User, error)
    Create(u User) error
    Update(u User) error
    Delete(id string) error
    FindAllWithPagination(page, limit int) ([]User, int, error)
    SearchByName(name string) ([]User, error)
}
```

Any type that wants to satisfy `UserRepository` must implement all eight methods. Your test fakes become enormous. Your specialized implementations (a read-only replica, a cache layer) must either implement methods they'll never use or fail to compile. The interface is tightly coupled to one specific use case, and it's hard to change without breaking everything that depends on it.

The same problem appears when you accept concrete types instead of interfaces:

```go
// WRONG — writing a function that only works with files
func copyToFile(src *os.File, dst *os.File) error {
    _, err := io.Copy(dst, src)
    return err
}
```

This function can only copy files. You can't use it for network connections, buffers, HTTP responses, or anything else. Every new source or destination type requires a new function.

## The Idiomatic Way

In Go, you define interfaces at the point of use — in the package that needs the behavior, not in the package that provides it. And you keep them small.

```go
// RIGHT — define only what you need, where you need it
type UserFinder interface {
    FindByID(id string) (User, error)
}

func sendWelcomeEmail(finder UserFinder, mailer Mailer, userID string) error {
    user, err := finder.FindByID(userID)
    if err != nil {
        return fmt.Errorf("sendWelcomeEmail: %w", err)
    }
    return mailer.Send(user.Email, welcomeTemplate)
}
```

`sendWelcomeEmail` only needs to find a user. It accepts `UserFinder`, not `UserRepository`. Your real `PostgresUserStore` satisfies `UserFinder` automatically — it has the method, so it implements the interface, no declaration needed. And your test fake is four lines:

```go
type fakeUserFinder struct{ user User }
func (f fakeUserFinder) FindByID(id string) (User, error) {
    return f.user, nil
}
```

No database. No full `UserRepository` implementation. Just enough to satisfy the exact interface the function requires.

The standard library's `io.Reader` and `io.Writer` are the canonical example of this done right:

```go
type Reader interface {
    Read(p []byte) (n int, err error)
}

type Writer interface {
    Write(p []byte) (n int, err error)
}
```

Two methods. That's it. But because they're so small and so focused, dozens of types satisfy them without knowing about each other: `os.File`, `bytes.Buffer`, `net.Conn`, `http.ResponseWriter`, `gzip.Reader`. Any function that accepts `io.Reader` works with all of them:

```go
// RIGHT — accepting interfaces, not concrete types
func copyData(src io.Reader, dst io.Writer) error {
    _, err := io.Copy(dst, src)
    return err
}

// Same function, three completely different use cases:
f, _ := os.Open("input.txt")
copyData(f, os.Stdout)                              // file to stdout

var buf bytes.Buffer
copyData(strings.NewReader("hello"), &buf)          // string to buffer

conn, _ := net.Dial("tcp", "example.com:80")
copyData(conn, os.Stdout)                           // network to stdout
```

Interfaces also compose cleanly. You build a larger interface from smaller ones:

```go
type ReadWriteCloser interface {
    io.Reader
    io.Writer
    io.Closer
}

type WriterWithFlush interface {
    io.Writer
    Flush() error
}
```

A `*bufio.Writer` satisfies `WriterWithFlush` — no declaration needed. You define the interface at the point where you need the capability, and any type with those methods works.

## In The Wild

Implicit interfaces make HTTP handlers trivially testable. Instead of accepting a concrete database type, the handler accepts a narrow interface:

```go
// Define only what the handler needs
type ProfileStore interface {
    GetProfile(userID string) (Profile, error)
}

type ProfileHandler struct {
    store ProfileStore
}

func (h *ProfileHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    userID := r.URL.Query().Get("id")
    profile, err := h.store.GetProfile(userID)
    if err != nil {
        if errors.Is(err, ErrNotFound) {
            http.Error(w, "not found", http.StatusNotFound)
            return
        }
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(profile)
}
```

In production, you wire up the real implementation:

```go
handler := &ProfileHandler{store: &PostgresProfileStore{db: db}}
```

In tests, you pass a stub:

```go
type stubProfileStore struct {
    profile Profile
    err     error
}
func (s *stubProfileStore) GetProfile(_ string) (Profile, error) {
    return s.profile, s.err
}

func TestProfileHandler_NotFound(t *testing.T) {
    h := &ProfileHandler{store: &stubProfileStore{err: ErrNotFound}}
    req := httptest.NewRequest("GET", "/?id=123", nil)
    w := httptest.NewRecorder()
    h.ServeHTTP(w, req)
    if w.Code != http.StatusNotFound {
        t.Errorf("expected 404, got %d", w.Code)
    }
}
```

The stub doesn't know about `ProfileHandler`. The handler doesn't know about `PostgresProfileStore`. They're connected only through a one-method interface, and that's exactly enough coupling to get the job done.

## The Gotchas

**`any` (aka `interface{}`) is an escape hatch, not a design pattern.** Every type satisfies `any`, which means you've thrown away compile-time type checking. Errors move from compile time to runtime. Use `any` when you genuinely cannot know the type at compile time — JSON decoding into unknown structures, generic logging, test helpers. Not as a way to avoid thinking about types:

```go
// WRONG — using any everywhere loses all type safety
func process(input any) any {
    switch v := input.(type) {
    case string:
        return strings.ToUpper(v)
    case int:
        return v * 2
    }
    return nil
}

// RIGHT — define the actual capability you need
type Processor interface {
    Process() (Result, error)
}
```

**Don't define interfaces in the package that implements them.** Define them where they're consumed. If your `storage` package defines `UserRepository` and your `service` package imports it, you've created a hard dependency in the wrong direction. Let `service` define the narrow interface it needs, and have `storage` satisfy it without knowing about it.

**The "accept interfaces, return concrete types" rule.** Function parameters should be interfaces when you want flexibility. Return types should be concrete when you want callers to be able to access the full API. Returning `io.Reader` from a function that actually created an `*os.File` means callers can't call `f.Stat()` — you've hidden capabilities without any benefit.

## Key Takeaway

Small interfaces defined at the point of use — rather than large interfaces defined by the implementor — flip the dependency relationship in your favor. With a big `UserRepository` interface in the data layer, all your business logic depends on the data layer's decisions. With small interfaces defined where you need them, the data layer depends on the business logic, not the other way around. This is what people mean when they say Go enables dependency inversion almost accidentally. The language doesn't require explicit interface declarations, so you end up defining interfaces where you actually need them, which naturally produces better architecture. Accept interfaces, return concrete types. Keep interfaces small. Define them where they're used.

---

← Previous: [Lesson 4: The comma ok Idiom](/post/go/go-idioms-comma-ok/) | [Course Index](/post/go/) | Next: [Lesson 6: Accept Interfaces, Return Structs](/post/go/go-idioms-accept-interfaces-return-structs/) →
