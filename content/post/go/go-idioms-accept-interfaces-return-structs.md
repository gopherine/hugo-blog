---
title: 'Lesson 6: Accept Interfaces, Return Structs — Flexibility in, certainty out'
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
series: "Idiomatic Go"
lesson: 6
date: '2025-04-07T00:00:00.000Z'
---

Here's a mistake I see in almost every Go codebase written by people coming from Java or C#: they accept concrete types everywhere and return interfaces from constructors. It feels "enterprise-y". It's actually backwards. The idiomatic Go version is the opposite — accept the smallest interface that does the job, return the richest concrete type you have.

## The Problem

Most of the pain comes from accepting concrete types. Once you lock a function to a specific concrete type, every caller that doesn't have exactly that type is stuck. Tests become integration tests. Swapping implementations requires rewriting functions. And the function itself becomes harder to compose.

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

This looks completely innocent, but it's a trap. To test `ProcessLogs` you must create a real `*os.File` — write a temp file to disk, open it, and clean it up after. Your unit test just became an integration test that touches the filesystem. Tomorrow when you need to process logs streamed from an HTTP response body, you have to rewrite the function. And if you want to test the unhappy path with a reader that returns an error halfway through? Good luck constructing that with a real file.

The second half of this mistake is returning interfaces from constructors:

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

The `redisStore` underneath almost certainly has `Close()`, `Ping()`, maybe `FlushAll()` for tests. By returning `Store`, you've thrown those away. Now callers who need `Close` have to do a type assertion to get back what you hid from them — coupling themselves to the concrete type anyway, with extra friction added.

## The Idiomatic Way

For inputs: find the narrowest interface that covers what your function actually needs. The standard library already did this work for the most common cases.

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

`io.Reader` is just one method: `Read(p []byte) (n int, err error)`. Files satisfy it, network connections satisfy it, `bytes.Buffer` satisfies it, `strings.Reader` satisfies it, gzip readers satisfy it. Now your test looks like this:

```go
func TestProcessLogs(t *testing.T) {
    input := strings.NewReader("line one\nline two\nline three\n")
    if err := ProcessLogs(input); err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
}
```

No files. No disk I/O. No cleanup. And when the requirements change to reading from S3, you pass the HTTP response body and nothing in `ProcessLogs` changes at all.

For outputs: return the concrete struct and let the caller decide what interface to hold it through.

```go
// RIGHT: return the concrete type with all its capabilities exposed
func NewRedisStore(addr string) *RedisStore {
    return &RedisStore{client: redis.NewClient(&redis.Options{Addr: addr})}
}
```

Now the caller who only needs `Store` behavior can assign it:

```go
var s Store = NewRedisStore("localhost:6379")
```

And the caller who needs `Close` can just call it:

```go
rs := NewRedisStore("localhost:6379")
defer rs.Close()
```

No type assertions. No hidden capabilities. The interface gets defined at the point of use, not at the point of construction — which is exactly where it belongs.

## In The Wild

HTTP handlers are where this pattern shows up most visibly. Here's a handler that fetches users from a database:

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

Testing this requires a real database. Switching from Postgres to a read-through cache requires rewriting the handler. Contrast that with the interface version:

```go
// RIGHT: depends on behavior, not an implementation
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

Your test creates a mock `UserStore` in five lines. Your production code wires in the real SQL implementation. The handler genuinely doesn't know the difference. This is the whole point.

Look at the standard library and you'll see it consistently: `json.NewEncoder` accepts `io.Writer`. `bufio.NewReader` accepts `io.Reader`. `http.NewRequest` accepts `io.Reader` for the body. Maximum flexibility on the input side. And what do they return? `*json.Encoder`, `*bufio.Reader` — concrete types with all their methods exposed. This wasn't an accident.

## The Gotchas

**Defining interfaces in the producer package.** If you define `RedisStoreInterface` in the same package as `RedisStore`, you've already tied the interface to the implementation. Define interfaces where they're consumed — in the package that needs the behavior — not where the types live.

**Interfaces that are too wide.** There's a temptation to define one big `Repository` interface with twelve methods and use it everywhere. Resist this. If a function only needs to read one user, it should accept an interface with one method. Wide interfaces make mocking painful and force implementors to carry methods they'd never otherwise provide.

**The `error` interface is the exception, not the template.** `error` is an interface return type, but it's the textbook example of a case where the concrete type genuinely doesn't matter to most callers. When you find yourself reaching for interface return types "because error does it," ask whether your callers actually want to be shielded from the concrete type or whether they're going to need it.

## Key Takeaway

The rule is symmetrical: on the input side, interfaces make your functions flexible — the weaker the constraint, the more callers can pass something useful. On the output side, concrete types make your functions honest — you're handing over the full thing, not a restricted view. Together, these two decisions produce Go code that's easy to test because you can always swap inputs, easy to extend because callers get everything your type offers, and easy to read because there are no surprise type assertions. The standard library has followed this principle from day one. When you adopt it, your code starts to feel like it belongs alongside the packages you're already importing.

---

← [Lesson 5: Implicit Interfaces](/post/go/go-idioms-implicit-interfaces/) | [Course Index](#) | [Lesson 7: Slices Are Views →](/post/go/go-idioms-slices-are-views/)
