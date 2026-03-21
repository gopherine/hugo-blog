---
title: 'Go Idioms: Multiple Return Values'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - multiple return values
  - named returns
  - blank identifier
  - error handling pattern
  - tuple return
tags:
  - Go tutorial
  - golang
date: '2025-10-06T00:00:00.000Z'
---
ve used it for a while, returning a single value with a side channel for errors (like exceptions or result objects) starts to feel like a hack.

## The (result, error) Pattern

The most common use of multiple returns is the `(value, error)` pair. A function returns what the caller asked for, plus an indication of whether it succeeded.

```go
// WRONG — using a sentinel value (-1) to signal failure
func divide(a, b float64) float64 {
    if b == 0 {
        return -1  // caller has to know that -1 means error
    }
    return a / b
}

result := divide(10, 0)
if result == -1 {
    fmt.Println("division failed")
}
// What if -1 is a legitimate result? What if we call divide(10, -10)?
```

This approach breaks down fast. Sentinel values pollute the valid result space. What's the sentinel for a function that returns a string? An empty string? What if empty is valid? You end up with conventions that are implicit, fragile, and impossible to enforce at compile time.

```go
// RIGHT — multiple returns make the success and failure paths explicit
func divide(a, b float64) (float64, error) {
    if b == 0 {
        return 0, errors.New("division by zero")
    }
    return a / b, nil
}

result, err := divide(10, 0)
if err != nil {
    fmt.Printf("divide failed: %v\n", err)
    return
}
fmt.Printf("result: %f\n", result)
```

The function signature itself tells you: this operation can fail. The compiler forces you to acknowledge both return values. You can't silently ignore the error without explicitly using `_`.

## Returning Zero Values on Error

A Go convention worth internalizing: when returning an error, return the zero value for the non-error returns, not some partial result. This prevents callers from accidentally using a partially constructed value.

```go
// WRONG — returning partially initialized struct on error
func parseConfig(data []byte) (Config, error) {
    var cfg Config
    cfg.Timeout = 30  // partially set before we hit an error

    if err := json.Unmarshal(data, &cfg); err != nil {
        return cfg, fmt.Errorf("parseConfig: %w", err)  // don't return this
    }
    return cfg, nil
}
```

```go
// RIGHT — return zero value when returning an error
func parseConfig(data []byte) (Config, error) {
    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil {
        return Config{}, fmt.Errorf("parseConfig: %w", err)
    }
    return cfg, nil
}
```

The caller already checks the error. If they see a non-nil error and still use the returned `Config`, that's their mistake. But by returning `Config{}`, you avoid inadvertently giving them something that looks valid but isn't.

For pointer returns, return `nil` on error:

```go
func newServer(cfg Config) (*Server, error) {
    if cfg.Port == 0 {
        return nil, errors.New("newServer: port must be non-zero")
    }
    return &Server{cfg: cfg}, nil
}
```

## Named Return Values

Go allows you to name the return values in the function signature. This serves two purposes: documentation and deferred modification.

```go
// Named returns as documentation — the signature explains what's returned
func minMax(nums []int) (min, max int, err error) {
    if len(nums) == 0 {
        err = errors.New("minMax: empty slice")
        return  // naked return: returns zero values for min and max
    }

    min, max = nums[0], nums[0]
    for _, n := range nums[1:] {
        if n < min {
            min = n
        }
        if n > max {
            max = n
        }
    }
    return  // naked return: returns current values of min, max, err
}
```

Named returns make long functions with multiple return points easier to read. But naked returns (just `return` without explicit values) can hurt readability in short functions by hiding what's actually being returned.

```go
// WRONG — naked returns in a short function obscure intent
func getUserAge(id string) (age int, err error) {
    u, err := db.Find(id)
    if err != nil {
        return  // what's age here? 0, fine, but not obvious at a glance
    }
    age = u.Age
    return
}

// RIGHT — explicit returns in short functions are clearer
func getUserAge(id string) (int, error) {
    u, err := db.Find(id)
    if err != nil {
        return 0, fmt.Errorf("getUserAge: %w", err)
    }
    return u.Age, nil
}
```

Use named returns for documentation value and deferred modification patterns. Use naked returns only in longer functions where they genuinely reduce noise. In short functions, explicit returns are clearer.

## The Blank Identifier

When you call a function with multiple returns and you don't need one of the values, use `_` to explicitly discard it.

```go
// Getting only the error from a function that also returns a value
if _, err := fmt.Fprintf(w, "hello %s", name); err != nil {
    return fmt.Errorf("write failed: %w", err)
}

// Getting only the value when you know there's no error
// (use with caution — only do this when you're certain)
user, _ := cache.Get("admin")  // only safe if you've already verified the key exists
```

The `_` is intentional and visible. Anyone reading the code sees that you chose to discard a value. This is very different from languages where unused return values are silently ignored.

## Real-World Scenario: Database Queries

Consider a function that queries a database for a user profile. In Java, you might throw a `NotFoundException`. In Go, the return signature is the contract.

```go
// Returns the user and an explicit error — caller sees the contract immediately
func (s *UserStore) FindByEmail(email string) (User, error) {
    var u User
    row := s.db.QueryRow("SELECT id, name, email FROM users WHERE email = $1", email)
    err := row.Scan(&u.ID, &u.Name, &u.Email)
    if err == sql.ErrNoRows {
        return User{}, fmt.Errorf("FindByEmail %q: %w", email, ErrNotFound)
    }
    if err != nil {
        return User{}, fmt.Errorf("FindByEmail %q: scan: %w", email, err)
    }
    return u, nil
}
```

The caller doesn't need to read documentation to know this can fail — the signature says so. And they can handle different error cases:

```go
user, err := store.FindByEmail(input.Email)
if err != nil {
    if errors.Is(err, ErrNotFound) {
        return nil, status.Error(codes.NotFound, "no account with that email")
    }
    return nil, status.Errorf(codes.Internal, "database error: %v", err)
}
```

Compare this to exception-based code where you might need to dig through documentation or source code to know which exceptions a function throws.

## Returning Multiple Values for Richer Results

Multiple returns aren't only for `(value, error)`. Sometimes a function genuinely computes several related things, and bundling them into a struct would be overkill.

```go
// Parsing a duration string returns the value and its unit separately
func parseDuration(s string) (value int, unit string, err error) {
    parts := strings.Fields(s)
    if len(parts) != 2 {
        return 0, "", fmt.Errorf("parseDuration: expected 'N unit', got %q", s)
    }

    value, err = strconv.Atoi(parts[0])
    if err != nil {
        return 0, "", fmt.Errorf("parseDuration: invalid number %q: %w", parts[0], err)
    }

    unit = parts[1]
    return value, unit, nil
}

// Usage
val, unit, err := parseDuration("30 seconds")
if err != nil {
    log.Fatal(err)
}
fmt.Printf("sleeping for %d %s\n", val, unit)
```

When results are small, closely related, and only used together at the call site, multiple returns are cleaner than defining a struct. When results are complex, reused across multiple call sites, or need to be stored and passed around, define a struct.

## Comparison with Exceptions

In Java or Python, a function's failure modes are documented separately from its return type — or not documented at all. The signature `User getUserById(String id)` doesn't tell you whether it throws `NotFoundException`, `DatabaseException`, or something else. You find out at runtime, or by reading docs and source code.

```java
// Java — failure modes are invisible in the signature
User getUserById(String id) throws NotFoundException, DatabaseException {
    // ...
}
// Caller can ignore checked exceptions, or catch Exception broadly
```

In Go, every function that can fail says so in its signature. You can't call `GetUserByID` and forget that it might fail — you'll have an unhandled return value that the compiler will flag. The information is right there, inline, where you need it.

This isn't just stylistic. It changes how you design systems. You think about failure paths at the API design stage, not as an afterthought. Every function boundary is an explicit decision: does this fail? how? what does the caller need to know? Go's multiple returns make those decisions visible and enforced.
