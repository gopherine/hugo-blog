---
title: 'Go Idioms: Accept Interfaces, Return Structs'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - interfaces
  - dependency injection
  - testability
  - io.Reader
  - Go design patterns
tags:
  - Go tutorial
  - golang
date: '2025-04-07T00:00:00.000Z'
---
"I don't care what concrete type you hand me, as long as it satisfies this contract." That opens the door to substitution — you can pass a real database connection, a mock, a test double, or something that did not exist when you wrote the function.

**Returning a concrete struct** says: "I am giving you the real thing, with all of its methods and fields exposed." The caller gets the full picture. Nothing is hidden behind an abstraction.

Flipping those two decisions — accepting concrete types and returning interfaces — is a surprisingly common mistake, and it causes real pain.

## The Wrong Way: Accepting Concrete Types

Suppose you are writing a function that processes log lines from some source.

```go
// WRONG: accepting a concrete type locks callers in
func ProcessLogs(f *os.File) error {
    scanner := bufio.NewScanner(f)
    for scanner.Scan() {
        line := scanner.Text()
        if err := handleLine(line); err != nil {
            return err
        }
    }
    return scanner.Err()
}
```

This looks innocent. But now your tests have a problem: to call `ProcessLogs` in a test, you must create a real `*os.File`. That means writing a temporary file to disk, opening it, and cleaning it up. Your unit test just became an integration test. Worse, if you later want to process logs from an HTTP response body or an in-memory buffer, you have to rewrite the function.

## The Right Way: Accept io.Reader

The standard library already defined the abstraction you need. An `io.Reader` is anything with a `Read(p []byte) (n int, err error)` method — files, network connections, `bytes.Buffer`, `strings.Reader`, gzip readers, you name it.

```go
// RIGHT: accepting an interface unlocks substitution
func ProcessLogs(r io.Reader) error {
    scanner := bufio.NewScanner(r)
    for scanner.Scan() {
        line := scanner.Text()
        if err := handleLine(line); err != nil {
            return err
        }
    }
    return scanner.Err()
}
```

Now your test looks like this:

```go
func TestProcessLogs(t *testing.T) {
    input := strings.NewReader("line one\nline two\nline three\n")
    if err := ProcessLogs(input); err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
}
```

No files. No disk I/O. No cleanup. And if tomorrow you need to process logs from an S3 object stream, you pass the HTTP response body directly — the function does not change at all.

## The Wrong Way: Returning an Interface

Now consider the opposite mistake. Suppose you have a constructor that returns an interface.

```go
// WRONG: returning an interface hides methods the caller might need
type Store interface {
    Get(key string) (string, error)
    Set(key string, value string) error
}

func NewRedisStore(addr string) Store {
    return &redisStore{client: redis.NewClient(&redis.Options{Addr: addr})}
}
```

The `redisStore` struct probably has a `Close() error` method, a `Ping() error` method for health checks, and maybe a `FlushAll() error` that is useful in tests. By returning `Store`, you have thrown those away. The caller has to do an ugly type assertion to get them back:

```go
s := NewRedisStore("localhost:6379")
// Can't call s.Close() — it's not on the Store interface
// Have to do this:
if rs, ok := s.(*redisStore); ok {
    rs.Close()
}
```

That type assertion couples the caller to the concrete type anyway — you got none of the benefit of returning an interface, and you added friction.

## The Right Way: Return the Concrete Struct

Return the struct. Let the caller decide which interface to hold it in.

```go
// RIGHT: return the concrete type with all its capabilities exposed
func NewRedisStore(addr string) *RedisStore {
    return &RedisStore{client: redis.NewClient(&redis.Options{Addr: addr})}
}
```

Now the caller that only needs `Store` behavior can hold it as `Store`:

```go
var s Store = NewRedisStore("localhost:6379")
```

And the caller that needs `Close` can call it directly:

```go
rs := NewRedisStore("localhost:6379")
defer rs.Close()
```

No type assertions. No hidden methods. The interface is defined at the point of use, not at the point of construction.

## A Real-World Scenario: HTTP Handlers

This pattern shows up constantly in HTTP handler design. A handler that fetches users from a database is a perfect example.

```go
// WRONG: tightly coupled to a concrete database type
type UserHandler struct {
    db *sql.DB
}

func (h *UserHandler) GetUser(w http.ResponseWriter, r *http.Request) {
    id := r.URL.Query().Get("id")
    row := h.db.QueryRow("SELECT name FROM users WHERE id = ?", id)
    // ...
}
```

Testing this requires a real database. Switching to a different storage layer requires rewriting the handler. Compare with the interface-accepting version:

```go
// RIGHT: depends on a behavior, not an implementation
type UserStore interface {
    GetUser(id string) (*User, error)
}

type UserHandler struct {
    store UserStore
}

func (h *UserHandler) GetUser(w http.ResponseWriter, r *http.Request) {
    id := r.URL.Query().Get("id")
    user, err := h.store.GetUser(id)
    if err != nil {
        http.Error(w, "not found", http.StatusNotFound)
        return
    }
    json.NewEncoder(w).Encode(user)
}
```

Your test creates a mock `UserStore` in five lines. Your production code wires in the real SQL implementation. The handler never knows the difference.

## The Standard Library Agrees

Look at how the standard library itself is designed. `json.NewEncoder` accepts `io.Writer`. `bufio.NewReader` accepts `io.Reader`. `http.NewRequest` accepts `io.Reader` for the body. These functions are maximally flexible because they take the smallest interface that satisfies their needs.

But what do they *return*? Concrete types. `json.NewEncoder` returns `*json.Encoder`, not some `Encoder` interface. `bufio.NewReader` returns `*bufio.Reader`. You get the full struct with all its methods — `Peek`, `ReadLine`, `ReadBytes` — not just the subset that was anticipated at design time.

This is not an accident. The Go authors made this choice deliberately, and the standard library is vastly more useful because of it.

## When Returning an Interface Is Acceptable

There are genuine exceptions. The most common is when you explicitly need to express that the return value might be one of several unrelated concrete types and the caller should only use the common interface. `error` is the canonical example — you return the `error` interface because the concrete error type is often irrelevant or implementation-specific.

Factory functions that produce different backends based on a configuration string are another case: `database/sql.Open` returns `*sql.DB` (a struct), but if you were writing a driver registry that could return fundamentally different client types, an interface return might be justified.

The rule of thumb holds for the vast majority of application code: keep the interface on the input side where it gives flexibility, and keep the concrete type on the output side where it exposes capability.

## Putting It Together

The principle is simple once you internalize the reasoning. Parameters are constraints you impose on callers — the weaker the constraint, the more callers you can accept. Return values are promises you make to callers — the richer the promise, the more useful you are.

Accepting interfaces keeps your code open to substitution. Returning structs keeps your code honest about what it produces. Those two choices together produce Go code that is genuinely easy to test, easy to extend, and easy to read a year later when you have forgotten what you were thinking.

Next time you write a constructor or a utility function, ask yourself: am I accepting the narrowest interface that satisfies my needs? Am I returning the richest concrete type I have? If the answer to both is yes, you are writing idiomatic Go.
