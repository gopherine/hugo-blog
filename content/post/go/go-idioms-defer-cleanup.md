---
title: "Lesson 2: defer for Cleanup — Put the cleanup next to the mess"
author: Atharva Pandey
keywords:
  - Go
  - golang
  - defer
  - cleanup
  - resource management
  - named returns
  - LIFO
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 2
date: '2025-06-30T00:00:00.000Z'
---

Every time you open a file, acquire a lock, or start a database transaction, you've created a resource that needs to be released when you're done with it. In most languages you manage this with `finally` blocks or RAII patterns. Miss one cleanup and you've got a leak. Go has `defer`, and once it clicks, you'll wonder how you ever coded without it.

## The Problem

Here's what resource cleanup looks like without `defer`. Real code, the kind that ships:

```go
// WRONG — cleanup duplicated on every return path
func processFile(path string) error {
    f, err := os.Open(path)
    if err != nil {
        return fmt.Errorf("processFile: open: %w", err)
    }

    data, err := io.ReadAll(f)
    if err != nil {
        f.Close()  // easy to forget
        return fmt.Errorf("processFile: read: %w", err)
    }

    if err := validate(data); err != nil {
        f.Close()  // and this
        return fmt.Errorf("processFile: validate: %w", err)
    }

    f.Close()  // and this
    return process(data)
}
```

Every early return needs its own `f.Close()`. Add another error check later and you add another chance to forget. This is how file descriptor leaks happen — they're invisible until your process hits the OS limit and starts failing to open anything.

The same problem shows up with mutexes. Every early return in a function holding a lock needs a manual `Unlock`. Miss one and you've introduced a deadlock that will manifest at 3am under load.

## The Idiomatic Way

`defer` schedules a function call to run when the surrounding function returns — no matter how it returns. Normal return, early return, panic — deferred calls always execute.

```go
// RIGHT — one defer, handles every code path
func processFile(path string) error {
    f, err := os.Open(path)
    if err != nil {
        return fmt.Errorf("processFile: open: %w", err)
    }
    defer f.Close()  // runs when processFile returns, no matter what

    data, err := io.ReadAll(f)
    if err != nil {
        return fmt.Errorf("processFile: read: %w", err)
    }

    if err := validate(data); err != nil {
        return fmt.Errorf("processFile: validate: %w", err)
    }

    return process(data)
}
```

One defer. No duplication. The file closes on every code path. The cleanup is right next to the acquisition — you can see them together and reason about them together.

Mutexes follow the same pattern, and you'll see this on back-to-back lines in virtually every Go codebase:

```go
// RIGHT — defer unlock immediately after lock
func getFromCache(key string) (string, bool) {
    mu.Lock()
    defer mu.Unlock()

    val, ok := cache[key]
    return val, ok
}
```

HTTP response bodies are another classic case. Failing to close the response body causes connection leaks — they show up as strange timeouts under load, usually when you're getting hammered:

```go
// RIGHT — defer close immediately after checking err from http.Get
func fetchUser(id string) (User, error) {
    resp, err := http.Get("https://api.example.com/users/" + id)
    if err != nil {
        return User{}, fmt.Errorf("fetchUser: GET: %w", err)
    }
    defer resp.Body.Close()  // safe to defer once we know err is nil

    if resp.StatusCode != http.StatusOK {
        return User{}, fmt.Errorf("fetchUser: unexpected status %d", resp.StatusCode)
    }

    var user User
    if err := json.NewDecoder(resp.Body).Decode(&user); err != nil {
        return User{}, fmt.Errorf("fetchUser: decode: %w", err)
    }

    return user, nil
}
```

Note that `defer resp.Body.Close()` goes after the nil check on `err`. If `http.Get` fails, `resp` might be nil, and deferring on a nil body would panic.

## In The Wild

Database transactions are one of the best use cases for `defer`. You want to roll back on any error and commit on success — and you want this to be impossible to accidentally break:

```go
func transferFunds(db *sql.DB, fromID, toID string, amount int) error {
    tx, err := db.Begin()
    if err != nil {
        return fmt.Errorf("transferFunds: begin: %w", err)
    }

    committed := false
    defer func() {
        if !committed {
            tx.Rollback()  // safe to call even after Commit
        }
    }()

    if _, err := tx.Exec("UPDATE accounts SET balance = balance - $1 WHERE id = $2", amount, fromID); err != nil {
        return fmt.Errorf("transferFunds: debit: %w", err)
    }

    if _, err := tx.Exec("UPDATE accounts SET balance = balance + $1 WHERE id = $2", amount, toID); err != nil {
        return fmt.Errorf("transferFunds: credit: %w", err)
    }

    if err := tx.Commit(); err != nil {
        return fmt.Errorf("transferFunds: commit: %w", err)
    }

    committed = true
    return nil
}
```

The defer guarantees rollback on any early return. You can add ten more error checks and none of them can accidentally leave a dangling transaction. That's the real value of `defer` — it makes omission impossible.

## The Gotchas

**Defer in loops doesn't do what you think.** Deferred calls run when the function returns, not when the block ends. In a loop with 10,000 iterations, you'll have 10,000 open file descriptors before any of them close:

```go
// WRONG — all files stay open until processAll returns
func processAll(paths []string) error {
    for _, path := range paths {
        f, err := os.Open(path)
        if err != nil {
            return err
        }
        defer f.Close()  // deferred to end of processAll, NOT end of loop body
        // ...
    }
    return nil
}
```

The fix is to extract the per-iteration work into its own function:

```go
func processAll(paths []string) error {
    for _, path := range paths {
        if err := processOne(path); err != nil {
            return err
        }
    }
    return nil
}

func processOne(path string) error {
    f, err := os.Open(path)
    if err != nil {
        return fmt.Errorf("processOne: open %q: %w", path, err)
    }
    defer f.Close()  // correctly scoped to processOne
    return process(f)
}
```

**Multiple defers run in LIFO order.** Last registered, first to run. This is usually what you want — if you open a file, then wrap it in a gzip reader, you want to close the gzip reader before the underlying file. But you have to be aware of it:

```go
defer fmt.Println("cleanup 1 — runs last")
defer fmt.Println("cleanup 2 — runs second")
defer fmt.Println("cleanup 3 — runs first")
```

**Named returns and defer can interact in surprising ways.** When a function has named return values, a deferred closure can read and modify those values before the function actually returns. This pattern is real and useful, but it surprises almost everyone who encounters it for the first time:

```go
func openDB(dsn string) (db *sql.DB, err error) {
    db, err = sql.Open("postgres", dsn)
    if err != nil {
        return
    }

    defer func() {
        if err != nil {
            db.Close()
            db = nil
        }
    }()

    if err = db.Ping(); err != nil {
        err = fmt.Errorf("openDB: ping failed: %w", err)
        return  // defer fires here, closes db, sets db=nil
    }

    return
}
```

Use it when it genuinely clarifies intent. Don't use it to be clever.

## Key Takeaway

`defer` shifts the question from "did I remember to clean up here?" to "did I set up the cleanup right after acquiring the resource?" That's a much smaller, more localized thing to verify — and it means every new code path you add is automatically covered. The pattern is always: acquire, check the error, defer the cleanup. In that order. Every time.

---

← Previous: [Lesson 1: Error Handling](/post/go/go-idioms-error-handling/) | [Course Index](/post/go/) | Next: [Lesson 3: Multiple Return Values](/post/go/go-idioms-multiple-return-values/) →
