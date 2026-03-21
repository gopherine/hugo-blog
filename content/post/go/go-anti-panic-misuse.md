---
title: "Lesson 7: Panic as Error Handling — Panic is for bugs, not business logic"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 7
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-02-18T00:00:00.000Z'
---

I have reviewed Go code from developers who learned other languages where exceptions are the primary error handling mechanism. In Ruby, Python, or Java, you throw an exception and it propagates up the stack until something catches it. In Go, the analogous mechanism — panic — has a very different contract. A panic that is not recovered crashes the entire process. In a web server, that means every in-flight request dies. In a worker process, that means all queued work is dropped.

The pattern I see most often is using `panic` as a shorthand for error returns in code that is nested deeply or feels tedious to thread errors through. It works in small programs where a panic is expected to be caught at the top level. It fails catastrophically in production services when a panic in a background goroutine kills the entire server, or when a recovered panic suppresses an error that should have been returned and handled.

## The Problem

Using panic to avoid threading error returns through a call chain:

```go
// WRONG — panic used as error propagation mechanism
func mustParseConfig(path string) Config {
    data, err := os.ReadFile(path)
    if err != nil {
        panic(fmt.Sprintf("failed to read config: %v", err)) // process dies if file missing
    }

    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil {
        panic(fmt.Sprintf("failed to parse config: %v", err))
    }
    return cfg
}

func handleRequest(w http.ResponseWriter, r *http.Request) {
    userID := r.URL.Query().Get("user_id")
    if userID == "" {
        panic("user_id is required") // kills the server for a bad request
    }

    age, err := strconv.Atoi(r.URL.Query().Get("age"))
    if err != nil {
        panic("age must be an integer") // kills the server for malformed input
    }
    // ...
}
```

The second panic in `handleRequest` will kill the entire web server for a single client sending a bad query parameter. Even with a recover middleware, the handler's goroutine unwinds, the HTTP response is not written, and the client gets a broken connection.

Using `must`-style wrappers outside of initialization code:

```go
// WRONG — must wrapper used in request handling path
func mustGetUser(db *sql.DB, id string) *User {
    user, err := queryUser(db, id)
    if err != nil {
        panic(err) // not a bug — user might simply not exist
    }
    return user
}

func handleGetProfile(w http.ResponseWriter, r *http.Request) {
    // "user not found" will panic and crash the handler
    user := mustGetUser(db, r.URL.Query().Get("id"))
    json.NewEncoder(w).Encode(user)
}
```

A user not found is an expected, normal condition — it should return a 404, not crash the process.

## The Idiomatic Way

Return errors for any condition that is expected and recoverable. Use panic only for conditions that represent programmer errors — broken invariants that should never happen in correct code:

```go
// RIGHT — errors for recoverable conditions, panic for invariants
func parseConfig(path string) (Config, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return Config{}, fmt.Errorf("read config file %s: %w", path, err)
    }

    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil {
        return Config{}, fmt.Errorf("parse config: %w", err)
    }
    return cfg, nil
}

func handleGetProfile(w http.ResponseWriter, r *http.Request) {
    id := r.URL.Query().Get("id")
    if id == "" {
        http.Error(w, "id is required", http.StatusBadRequest)
        return
    }

    user, err := getUserByID(r.Context(), db, id)
    if errors.Is(err, ErrNotFound) {
        http.Error(w, "user not found", http.StatusNotFound)
        return
    }
    if err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }

    json.NewEncoder(w).Encode(user)
}
```

`must`-style helpers are legitimate for program initialization where a missing config or invalid state is genuinely unrecoverable — but only there:

```go
// RIGHT — must helpers for initialization only, clearly named
func mustLoadConfig() Config {
    cfg, err := parseConfig("config.json")
    if err != nil {
        // This IS a bug / unrecoverable startup failure — panic is appropriate
        panic(fmt.Sprintf("cannot start without valid config: %v", err))
    }
    return cfg
}

func main() {
    cfg := mustLoadConfig() // appropriate — startup time, unrecoverable
    // ...
}
```

For catching panics in web server middleware — to prevent a bug in one handler from killing the server — add a recovery middleware, but treat it as a last resort, not a primary error path:

```go
// RIGHT — recovery middleware as a safety net, not primary error handling
func RecoveryMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        defer func() {
            if r := recover(); r != nil {
                // Log with stack trace — this is a bug, not normal operation
                log.Printf("PANIC: %v\n%s", r, debug.Stack())
                http.Error(w, "internal server error", http.StatusInternalServerError)
            }
        }()
        next.ServeHTTP(w, r)
    })
}
```

## In The Wild

**Goroutine panics.** A panic in a goroutine that is not the main goroutine and has no recover will crash the entire process. Standard HTTP handlers run in goroutines managed by the `http.Server` — the server does recover panics from handlers (since Go 1.0). But goroutines you launch yourself do not. A background goroutine that panics takes the entire process down. Always add recovery to long-running goroutines:

```go
// RIGHT — recovery wrapper for background goroutines
go func() {
    defer func() {
        if r := recover(); r != nil {
            log.Printf("background goroutine panicked: %v\n%s", r, debug.Stack())
        }
    }()
    longRunningTask()
}()
```

**`log.Fatal` vs panic.** `log.Fatal` calls `os.Exit(1)` after logging — it terminates the process cleanly (deferred functions do NOT run). `panic` unwinds the stack and runs deferred functions. In `main()` at startup, `log.Fatal` is appropriate for unrecoverable conditions. In any other function, return an error.

**Runtime panics vs explicit panics.** A nil pointer dereference, a slice out of bounds, or a failed type assertion (without the two-value form) are runtime panics. They represent bugs in your code. Explicit `panic(...)` calls should be rare and represent programmer contracts: "if we reach this point with a nil receiver, the caller violated the API." Document these with a comment explaining what invariant is being asserted.

## The Gotchas

**`panic(string)` vs `panic(error)`.** When you must panic explicitly, panic with an `error` value so that recover handlers can use type assertions to inspect it. Panicking with a plain string makes recovery code harder to write portably.

**Panic in `init()`.** A panic in `init()` happens before `main()` starts and before your recovery middleware exists. It crashes the process with no recovery possible. Panicking in init for invalid configuration is acceptable (the program cannot run), but do not do complex work in `init()` that might fail.

**The "panic at top level" pattern.** Some libraries use panic internally and recover at the boundary to convert panics to errors. The Go standard library's `encoding/json` does this internally. This is acceptable in library code where you control both sides of the panic/recover boundary. It is not acceptable in application code where a panic might escape the intended recovery point.

**Panic is not for validation errors.** Every business logic condition — invalid input, resource not found, permission denied, quota exceeded — should be an error, not a panic. Panic is for the programmer's failures: nil dereferences, type mismatches, violated invariants. The distinction is: could a valid correct client trigger this? If yes, it is an error. If only a buggy program can trigger it, it might be a panic.

## Key Takeaway

Panic is for bugs and violated invariants. Errors are for expected, recoverable failure conditions. Every user action, network failure, database error, and configuration problem should be an `error` returned up the call stack. Panic should be reserved for cases where program correctness has been violated in a way that makes continued execution meaningless. Using panic as a shortcut for error propagation produces programs that crash unpredictably under normal operating conditions.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 6: Swallowing Errors — The silent failure that cost us 3 hours](/post/go/go-anti-swallowing-errors/)
Next: [Lesson 8: Premature Abstraction — Wrong abstraction costs more than duplication](/post/go/go-anti-premature-abstraction/)
