---
title: 'Go Idioms: defer for Cleanup'
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
date: '2025-06-30T00:00:00.000Z'
---
re done reading it. In most languages, you manage these with `finally` blocks or RAII patterns. Go has `defer`, and it's one of the most elegant ideas in the language.

## The Core Idea

`defer` schedules a function call to run when the surrounding function returns — no matter how it returns. Normal return, early return, panic — deferred calls always execute. This lets you place cleanup code right next to the resource acquisition, where it's easy to see and impossible to forget.

```go
// WRONG — cleanup separated from acquisition, easy to miss on early returns
func processFile(path string) error {
    f, err := os.Open(path)
    if err != nil {
        return fmt.Errorf("processFile: open: %w", err)
    }

    data, err := io.ReadAll(f)
    if err != nil {
        f.Close()  // easy to forget this
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

Every early return needs its own `f.Close()`. Add another error check and you add another chance to forget. This is how leaks happen.

```go
// RIGHT — defer placed immediately after successful open
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

One defer. No duplication. The file closes on every code path.

## Mutex Unlock

The same pattern applies to mutexes. Without defer, every early return in a function holding a lock needs a manual `Unlock`. Miss one and you've introduced a deadlock.

```go
var mu sync.Mutex
var cache = make(map[string]string)

// WRONG — manual unlock on every return path
func getFromCache(key string) (string, bool) {
    mu.Lock()
    val, ok := cache[key]
    if !ok {
        mu.Unlock()  // easy to miss
        return "", false
    }
    mu.Unlock()
    return val, true
}
```

```go
// RIGHT — defer unlock immediately after lock
func getFromCache(key string) (string, bool) {
    mu.Lock()
    defer mu.Unlock()

    val, ok := cache[key]
    return val, ok
}
```

This is idiomatic Go. You'll see `mu.Lock(); defer mu.Unlock()` on back-to-back lines in virtually every Go codebase that uses mutexes directly.

## Closing HTTP Response Bodies

This is a classic gotcha for Go beginners working with HTTP clients. The response body must be closed to return the connection to the pool. Failing to do so causes connection leaks that show up as strange timeouts under load.

```go
// WRONG — forgetting to close, or closing only on the happy path
func fetchUser(id string) (User, error) {
    resp, err := http.Get("https://api.example.com/users/" + id)
    if err != nil {
        return User{}, fmt.Errorf("fetchUser: GET: %w", err)
    }

    if resp.StatusCode != http.StatusOK {
        return User{}, fmt.Errorf("fetchUser: unexpected status %d", resp.StatusCode)
        // body never closed!
    }

    var user User
    if err := json.NewDecoder(resp.Body).Decode(&user); err != nil {
        resp.Body.Close()
        return User{}, fmt.Errorf("fetchUser: decode: %w", err)
    }

    resp.Body.Close()
    return user, nil
}
```

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

## LIFO Order

Multiple defers in a single function execute in last-in, first-out order — like a stack. This matters when cleanup operations depend on each other.

```go
func multiResourceExample() {
    fmt.Println("acquiring resources")

    defer fmt.Println("cleanup 1 — runs last")
    defer fmt.Println("cleanup 2 — runs second")
    defer fmt.Println("cleanup 3 — runs first")

    fmt.Println("doing work")
}
// Output:
// acquiring resources
// doing work
// cleanup 3 — runs first
// cleanup 2 — runs second
// cleanup 1 — runs last
```

This mirrors the natural order for nested resources. If you open a file, then wrap it in a buffered reader, then wrap that in a gzip reader — you want to close the gzip reader first, then the buffer, then the file. LIFO gives you that automatically:

```go
f, _ := os.Open("data.gz")
defer f.Close()  // runs third

gz, _ := gzip.NewReader(f)
defer gz.Close()  // runs second

buf := bufio.NewReader(gz)
defer someCleanup(buf)  // runs first
```

## The Loop Gotcha

Here's where defer trips people up. Deferred calls run when the *function* returns, not when the block ends. In a loop, you might expect defers to fire at the end of each iteration. They don't.

```go
// WRONG — all files stay open until processAll returns
func processAll(paths []string) error {
    for _, path := range paths {
        f, err := os.Open(path)
        if err != nil {
            return err
        }
        defer f.Close()  // deferred to end of processAll, not end of loop body

        if err := process(f); err != nil {
            return err
        }
    }
    return nil
}
```

If `paths` has 10,000 entries, you'll have 10,000 open file descriptors by the time any of them close. The fix is to extract the per-iteration work into a separate function, where `defer` scopes correctly:

```go
// RIGHT — extracted function scopes the defer properly
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
    defer f.Close()  // correctly runs at end of processOne

    return process(f)
}
```

Alternatively, for small loops, you can use an immediately invoked function literal:

```go
for _, path := range paths {
    if err := func() error {
        f, err := os.Open(path)
        if err != nil {
            return err
        }
        defer f.Close()
        return process(f)
    }(); err != nil {
        return err
    }
}
```

The extracted function approach is cleaner and more testable.

## defer with Named Returns

This is an advanced pattern that can be genuinely useful, but it also surprises people. When a function has named return values, a deferred function can read and modify those values.

```go
// Named return enables the defer to see and modify the return value
func openDB(dsn string) (db *sql.DB, err error) {
    db, err = sql.Open("postgres", dsn)
    if err != nil {
        return  // named return: returns db=nil, err=<the error>
    }

    defer func() {
        if err != nil {
            db.Close()  // if something after Open fails, close the DB we opened
            db = nil
        }
    }()

    if err = db.Ping(); err != nil {
        err = fmt.Errorf("openDB: ping failed: %w", err)
        return  // defer fires here, closes db, sets db=nil
    }

    return  // defer fires here, err is nil, so db is returned as-is
}
```

The deferred function modifies the named return value `db` based on whether `err` is set. This pattern is used to ensure that if any post-acquisition setup fails, the resource is cleaned up before returning to the caller.

Be careful with this pattern. It only works with named returns, and it can produce surprising behavior if you mix named and unnamed returns or assign to the named variable in unintuitive ways. Use it when it genuinely clarifies intent, not just because it's clever.

## Real-World Scenario: Database Transactions

Transactions are one of the best use cases for defer-based cleanup. You want to roll back on any error and commit on success — and you want this to be foolproof.

```go
func transferFunds(db *sql.DB, fromID, toID string, amount int) error {
    tx, err := db.Begin()
    if err != nil {
        return fmt.Errorf("transferFunds: begin: %w", err)
    }

    // This deferred function runs on every return path.
    // If committed is still false when the function returns,
    // the transaction gets rolled back.
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

The defer guarantees rollback on any early return. There's no way to add a new error check and accidentally leave a dangling transaction.

`defer` shifts the question from "did I remember to clean up here?" to "did I set up the cleanup right after acquiring the resource?" That's a much smaller, more localized thing to verify — which is why code using defer tends to be more reliable than code that manually closes things at every exit point.
