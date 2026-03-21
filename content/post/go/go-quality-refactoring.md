---
title: 'Lesson 1: Refactoring Go Code Safely — Change structure without changing behavior'
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 1
keywords:
  - Go
  - golang
  - refactoring
  - code quality
  - maintainability
  - tests
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-06-16T00:00:00.000Z'
---

Refactoring is the one activity that makes codebases better without adding features — and it's also the activity most likely to introduce bugs if done carelessly. I learned this the hard way on a payments service where I renamed a function, ran the tests, saw green, deployed, and watched a webhook handler silently stop processing because it had been calling the old function name through a string-based registry I hadn't touched. The tests were green because the old function still existed — I just hadn't deleted it yet. The refactor was correct; the process was not.

## The Problem

The fundamental trap in Go refactoring is the same as in every language: you change something, the tests pass, you ship, something breaks. But Go has a few specific shapes this failure takes.

```go
// BEFORE — monolithic function, hard to test
func processOrder(db *sql.DB, order Order) error {
    // validate
    if order.UserID == 0 {
        return errors.New("missing user ID")
    }
    if order.Total <= 0 {
        return errors.New("invalid total")
    }

    // persist
    _, err := db.Exec(
        "INSERT INTO orders (user_id, total) VALUES (?, ?)",
        order.UserID, order.Total,
    )
    if err != nil {
        return fmt.Errorf("insert order: %w", err)
    }

    // notify
    if err := sendEmail(order.UserID, "order confirmed"); err != nil {
        return fmt.Errorf("send confirmation: %w", err)
    }
    return nil
}
```

This function is doing three things: validation, persistence, and notification. Refactoring it is correct — it's too dense to test in isolation. But if you break it apart without a safety net, you're flying blind.

## The Idiomatic Way

The process I follow now is mechanical and boring, and boring is exactly what you want when refactoring.

**Step 1: Write characterization tests before you touch anything.** These aren't unit tests — they're tests that describe current behavior, including bugs.

```go
func TestProcessOrder_Integration(t *testing.T) {
    db := setupTestDB(t)

    tests := []struct {
        name    string
        order   Order
        wantErr bool
    }{
        {"valid order", Order{UserID: 1, Total: 99.99}, false},
        {"missing user", Order{UserID: 0, Total: 10}, true},
        {"zero total", Order{UserID: 1, Total: 0}, true},
        {"negative total", Order{UserID: 1, Total: -1}, true},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            err := processOrder(db, tt.order)
            if (err != nil) != tt.wantErr {
                t.Errorf("processOrder() error = %v, wantErr %v", err, tt.wantErr)
            }
        })
    }
}
```

**Step 2: Extract in the smallest possible increments.** Don't rewrite — move code and immediately verify.

```go
// AFTER — same behavior, better structure
func validateOrder(order Order) error {
    if order.UserID == 0 {
        return errors.New("missing user ID")
    }
    if order.Total <= 0 {
        return errors.New("invalid total")
    }
    return nil
}

func persistOrder(db *sql.DB, order Order) error {
    _, err := db.Exec(
        "INSERT INTO orders (user_id, total) VALUES (?, ?)",
        order.UserID, order.Total,
    )
    if err != nil {
        return fmt.Errorf("insert order: %w", err)
    }
    return nil
}

func processOrder(db *sql.DB, order Order) error {
    if err := validateOrder(order); err != nil {
        return err
    }
    if err := persistOrder(db, order); err != nil {
        return err
    }
    if err := sendEmail(order.UserID, "order confirmed"); err != nil {
        return fmt.Errorf("send confirmation: %w", err)
    }
    return nil
}
```

The public interface hasn't changed. All existing tests still pass. Now you can write isolated tests for `validateOrder` and `persistOrder` independently.

**Step 3: Use `go test -count=1 ./...` after every extract.** The `-count=1` flag disables caching. Cached test results are useless for refactoring verification.

## In The Wild

On a billing service I worked on, we had a 400-line `InvoiceService.Generate()` function. Rather than rewriting it, we applied the strangler fig pattern inside the function: we identified a coherent sub-operation (tax calculation), wrote a characterization test for it, extracted `calculateTax()`, verified tests still passed, then pushed. Repeat for the next chunk. The full refactor took three pull requests and three weeks. The function went from 400 lines to 40 with six focused helpers — none of them broke anything in production because each step was independently verifiable.

The discipline that made it work: we made a rule that no PR in the sequence could change behavior — only structure. Feature changes happened in separate PRs.

## The Gotchas

**Renaming exported identifiers breaks callers outside your module.** Before renaming an exported type, function, or method, search for all usages across the codebase with `grep -r "OldName" .` or your IDE's find-usages. If you're working in a library, a rename is a breaking change — use a type alias or a deprecation comment to provide a migration path.

**String-based registries don't update with renames.** If your code uses function names as strings (plugin systems, `reflect`-based routing, config-driven dispatch), renames won't be caught by the compiler. These usages must be audited manually.

**`go vet` and `staticcheck` catch things your tests won't.** Run both as part of your refactoring workflow. A function that compiles and whose tests pass can still have unreachable code, shadowed variables, or misused error values that `go vet` will catch.

**Unexported functions are fair game — exported ones aren't.** You can rename and restructure unexported functions freely. Exported functions require more care and, in public APIs, a deprecation period.

## Key Takeaway

Safe refactoring in Go is a process, not a skill. Write characterization tests before touching anything, extract in the smallest possible increments, run tests with `-count=1` after every step, and keep structural changes strictly separate from behavioral changes. The compiler is your friend but it won't catch every broken assumption — especially in dynamic dispatch, config-driven code, or string registries. Slow and mechanical beats fast and clever every single time.

---

[Course Index](/series/go-code-quality-maintainability) | [Next → Lesson 2: Spotting Over-Abstraction](/post/go/go-quality-over-abstraction)
