---
title: "Lesson 3: Multiple Return Values — Go functions don't hide their failures"
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
series: "Idiomatic Go"
lesson: 3
date: '2025-10-06T00:00:00.000Z'
---

In most languages, a function returns one thing and communicates failure through a side channel — an exception, a null, a magic sentinel value. Go's approach is different: functions can return multiple values, and the convention is to use that to make failure explicit in the signature itself. Once you've used it for a while, hiding failures in side channels starts to feel dishonest.

## The Problem

Sentinel values are the old-school way to signal failure from a function. You pick some value that "shouldn't" appear in normal results and treat it as an error signal:

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

This breaks down fast. What's the sentinel for a function returning a string — empty string? What if empty is valid? You end up with implicit conventions that aren't enforced anywhere. I've debugged bugs where `divide(10, -10)` returned `-1.0`, which the caller treated as an error. Took two hours to find.

The same problem appears in typed languages with exceptions. The function signature `User getUserById(String id)` doesn't tell you whether it throws `NotFoundException`, `DatabaseException`, or something else entirely. You find out at runtime, or by reading docs, or by getting paged.

## The Idiomatic Way

Multiple returns make the success and failure paths explicit in the type signature itself:

```go
// RIGHT — multiple returns make both paths visible
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

The function signature itself tells you: this operation can fail. The compiler forces you to acknowledge both return values. You can't silently ignore the error without explicitly using `_` — which is at least visible.

**Return zero values on error.** Go convention: when returning an error, return the zero value for the non-error returns, not some partial result. This prevents callers from accidentally using a partially constructed value:

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

// RIGHT — return zero value when returning an error
func parseConfig(data []byte) (Config, error) {
    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil {
        return Config{}, fmt.Errorf("parseConfig: %w", err)
    }
    return cfg, nil
}
```

The caller already checks the error. By returning `Config{}`, you avoid giving them something that looks valid but isn't.

**Named return values** serve two purposes: documentation and deferred modification (covered in Lesson 2). For documentation, they're especially useful when a function returns multiple values of the same type:

```go
// Named returns as documentation — the signature explains what's returned
func minMax(nums []int) (min, max int, err error) {
    if len(nums) == 0 {
        err = errors.New("minMax: empty slice")
        return
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
    return
}
```

Without named returns, `(int, int, error)` tells the caller nothing about which int is which.

## In The Wild

A database query for a user profile. In Java you'd throw a `NotFoundException`. In Go, the return signature is the contract:

```go
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

The caller doesn't need to read documentation to know this can fail — the signature says so. And they can handle different failure modes separately:

```go
user, err := store.FindByEmail(input.Email)
if err != nil {
    if errors.Is(err, ErrNotFound) {
        return nil, status.Error(codes.NotFound, "no account with that email")
    }
    return nil, status.Errorf(codes.Internal, "database error: %v", err)
}
```

This is the kind of thing that matters at scale. Your gRPC client gets the right status code, your monitoring tracks 404s separately from 500s, and nobody is guessing which exception might have been thrown somewhere in the stack.

Multiple returns aren't only for `(value, error)`. Sometimes a function genuinely computes several related things, and bundling them into a struct would be overkill:

```go
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
```

When results are small, closely related, and only used together at the call site — multiple returns. When results are complex, reused across multiple call sites, or need to be stored and passed around — define a struct.

## The Gotchas

**Naked returns in short functions obscure intent.** Named returns with bare `return` statements are fine in longer functions with multiple return points. In a five-line function, they hide what's actually being returned:

```go
// WRONG — naked returns in a short function obscure intent
func getUserAge(id string) (age int, err error) {
    u, err := db.Find(id)
    if err != nil {
        return  // what's age here? 0, fine, but not obvious
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

**Discarding returns with `_` is explicit, but it's still a choice you have to justify.** Using `_` on an error return isn't always wrong — sometimes you genuinely know a call can't fail in context. But it's always a decision, not a default. If you're reaching for `_` on an error, stop and ask yourself why.

**Returning a non-nil interface holding a nil pointer is a notorious trap.** This mostly bites you when you return a typed nil through an interface return:

```go
// This looks like it returns nil, but it doesn't
func getWriter() io.Writer {
    var buf *bytes.Buffer = nil
    return buf  // returns a non-nil interface wrapping a nil pointer
}
```

The interface value is non-nil (it has type information), even though the underlying pointer is nil. Callers checking `if writer != nil` will think they have a valid writer. Stick to returning concrete types or untyped `nil`.

## Key Takeaway

Multiple return values make failure a first-class part of a function's contract. Every function that can fail says so in its signature. Every caller has to acknowledge both paths. This isn't just a stylistic preference — it changes how you design systems, because you think about failure modes at the API design stage rather than discovering them in production. The information is right there, inline, where you need it.

---

← Previous: [Lesson 2: defer for Cleanup](/post/go/go-idioms-defer-cleanup/) | [Course Index](/post/go/) | Next: [Lesson 4: The comma ok Idiom](/post/go/go-idioms-comma-ok/) →
