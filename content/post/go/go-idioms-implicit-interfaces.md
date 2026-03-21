---
title: 'Go Idioms: Implicit Interfaces'
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
date: '2025-08-25T00:00:00.000Z'
---
"alice"}
fmt.Println(s.String())  // "alice"
```

`User` satisfies `Stringer` because it has a `String() string` method. That's it. The `User` package doesn't need to know `Stringer` exists.

## The io.Reader and io.Writer Pattern

The most famous example of Go's interfaces is `io.Reader` and `io.Writer`. They're defined in the standard library:

```go
type Reader interface {
    Read(p []byte) (n int, err error)
}

type Writer interface {
    Write(p []byte) (n int, err error)
}
```

Two methods. That's the entire interface. But because they're so small and so focused, dozens of types in the standard library satisfy them without knowing about each other:

- `os.File` satisfies both — reading and writing files
- `bytes.Buffer` satisfies both — in-memory buffers
- `net.Conn` satisfies both — network connections
- `http.ResponseWriter` satisfies `Writer` — HTTP response bodies
- `gzip.Reader` satisfies `Reader` — decompressed streams

```go
// WRONG — writing a function that only works with files
func copyToFile(src *os.File, dst *os.File) error {
    _, err := io.Copy(dst, src)
    return err
}
// This function can only copy files. You can't use it for network connections,
// buffers, or any other data source/destination.
```

```go
// RIGHT — accepting interfaces, not concrete types
func copyData(src io.Reader, dst io.Writer) error {
    _, err := io.Copy(dst, src)
    return err
}

// Now the same function works everywhere:
f, _ := os.Open("input.txt")
defer f.Close()
copyData(f, os.Stdout)        // file to stdout

var buf bytes.Buffer
copyData(strings.NewReader("hello"), &buf)  // string to buffer

conn, _ := net.Dial("tcp", "example.com:80")
copyData(conn, os.Stdout)    // network to stdout
```

The `copyData` function doesn't know or care what `src` and `dst` are. It just needs them to implement those two tiny interfaces. This is why Go programs tend to be highly composable — every function that accepts an `io.Reader` works with files, buffers, network connections, test fakes, and anything else you can imagine, without any changes to the function itself.

## Interface Composition

Go interfaces compose cleanly. You can build a larger interface from smaller ones:

```go
// Standard library combines Reader and Writer into ReadWriter
type ReadWriter interface {
    Reader
    Writer
}

// You can compose your own
type ReadWriteCloser interface {
    io.Reader
    io.Writer
    io.Closer
}

// Or add methods to an existing interface
type WriterWithFlush interface {
    io.Writer
    Flush() error
}
```

This is more powerful than inheritance. A `*bufio.Writer` satisfies `WriterWithFlush` — no declaration needed. You define the interface at the point where you need the capability, and any type with those methods works.

```go
func writeAndFlush(w WriterWithFlush, data []byte) error {
    if _, err := w.Write(data); err != nil {
        return fmt.Errorf("writeAndFlush: write: %w", err)
    }
    if err := w.Flush(); err != nil {
        return fmt.Errorf("writeAndFlush: flush: %w", err)
    }
    return nil
}
```

## The Empty Interface

`interface{}` (or `any` in Go 1.18+) is the interface with no methods. Every type satisfies it.

```go
// any holds any value
var x any = 42
x = "hello"
x = []int{1, 2, 3}
```

This is Go's escape hatch for generic containers before generics were introduced. You'll still see it in older code and in places where genuinely heterogeneous values are needed:

```go
// WRONG — using any everywhere as a shortcut
func process(input any) any {
    // Now what? You've lost all type information.
    // You'll need type assertions everywhere.
    switch v := input.(type) {
    case string:
        return strings.ToUpper(v)
    case int:
        return v * 2
    }
    return nil
}
```

```go
// RIGHT — define the actual capability you need
type Processor interface {
    Process() (Result, error)
}

func processItem(p Processor) (Result, error) {
    return p.Process()
}
```

Using `any` throws away compile-time type checking. You're pushing errors from compile time to runtime. Use `any` only when you genuinely cannot know the type at compile time — JSON decoding into unknown structures, generic logging, or test helpers. For everything else, define an interface that captures the specific behavior you need.

## Designing Good Interfaces

The Go community has a strong preference for small interfaces. The principle is sometimes summarized as: accept interfaces, return concrete types.

```go
// WRONG — fat interface that demands too much from implementors
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

Any type that wants to satisfy `UserRepository` must implement all eight methods. This makes the interface hard to fake in tests, hard to satisfy with specialized implementations, and tightly coupled to one specific use case.

```go
// RIGHT — small interfaces focused on specific capabilities
type UserFinder interface {
    FindByID(id string) (User, error)
}

type UserCreator interface {
    Create(u User) error
}

type UserUpdater interface {
    Update(u User) error
}

// Functions accept only what they need
func sendWelcomeEmail(finder UserFinder, mailer Mailer, userID string) error {
    user, err := finder.FindByID(userID)
    if err != nil {
        return fmt.Errorf("sendWelcomeEmail: %w", err)
    }
    return mailer.Send(user.Email, welcomeTemplate)
}
```

`sendWelcomeEmail` only needs to find a user. It accepts `UserFinder`, not `UserRepository`. This means your test can pass a dead-simple fake:

```go
type fakeUserFinder struct {
    user User
}
func (f fakeUserFinder) FindByID(id string) (User, error) {
    return f.user, nil
}

// In the test
finder := fakeUserFinder{user: User{Email: "test@example.com"}}
err := sendWelcomeEmail(finder, mockMailer, "any-id")
```

No database. No full `UserRepository` implementation. Just the four lines needed to satisfy the exact interface the function requires.

## Real-World Scenario: Testable HTTP Handlers

Implicit interfaces make testing handlers much easier. Instead of accepting a concrete database type, the handler accepts an interface.

```go
// Define only what the handler needs
type ProfileStore interface {
    GetProfile(userID string) (Profile, error)
}

// Handler depends on the interface, not the implementation
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

In production, you wire up the real database implementation:

```go
handler := &ProfileHandler{store: &PostgresProfileStore{db: db}}
```

In tests, you pass a fake:

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

The `stubProfileStore` doesn't know about `ProfileHandler`. `ProfileHandler` doesn't know about `PostgresProfileStore`. They're connected only through the two-method interface, and that's exactly enough coupling to get the job done.

## Why Small Interfaces Win

The deeper insight is that small interfaces defined at the point of use — rather than large interfaces defined by the implementor — flip the dependency relationship. With a big `UserRepository` interface defined in the data layer, all your business logic depends on the data layer's decisions. With small interfaces defined where you need them, the data layer depends on the business logic, not the other way around.

This is what people mean when they talk about Go enabling dependency inversion almost accidentally. The language doesn't have the machinery for explicit interface declarations, so you end up defining interfaces where you need them, which naturally produces better architecture. The idiom enforces the design principle.

Accept interfaces, return concrete types. Keep interfaces small. Define them where they're used, not where the types are defined. These three rules will take you far in Go.
