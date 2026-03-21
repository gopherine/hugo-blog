---
title: "Lesson 1: Subtests and t.Run — Name your test cases or debug blind"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 1
keywords:
  - Go
  - golang
  - testing
  - subtests
  - t.Run
  - t.Parallel
  - test isolation
  - test naming
tags:
  - Go tutorial
  - golang
  - testing
date: '2024-06-15T00:00:00.000Z'
---

I used to write tests the way I wrote the first functions I ever wrote in Go — flat, repetitive, and with names so generic that when they failed, I had no idea which case broke. "TestParseDate failed" is not information. It's a dare. Go figure out which of the twelve implicit cases in the body is responsible. I wasted hours doing exactly that before I committed to subtests.

`t.Run` is one of those language features that looks like a small convenience and turns out to be load-bearing for any serious test suite. Once you start using it, you wonder how you ever shipped anything without it.

## The Problem

Here's how tests look when you don't reach for `t.Run`:

```go
// WRONG — flat tests, no isolation, cryptic failure messages
func TestParseDate(t *testing.T) {
    d, err := ParseDate("2024-01-15")
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
    if d.Year() != 2024 {
        t.Errorf("expected year 2024, got %d", d.Year())
    }

    // This runs even if the first case panics in some code paths
    d2, err := ParseDate("not-a-date")
    if err == nil {
        t.Error("expected error for invalid date")
    }
    _ = d2

    d3, err := ParseDate("")
    if err == nil {
        t.Error("expected error for empty string")
    }
    _ = d3
}
```

The problem is threefold. First, when this test fails, you get `FAIL: TestParseDate` — you have to read the output body to find which assertion failed. Second, if the first `t.Fatalf` fires, the rest of the test is skipped entirely, so you don't know whether the other cases would have passed. Third, there's no parallel execution — these cases run sequentially even when they have no shared state.

A slightly more experienced version of this pattern is the table loop without `t.Run`:

```go
// WRONG — table loop but no subtest names
func TestParseDate(t *testing.T) {
    cases := []struct {
        input   string
        wantErr bool
    }{
        {"2024-01-15", false},
        {"not-a-date", true},
        {"", true},
    }
    for _, tc := range cases {
        _, err := ParseDate(tc.input)
        if tc.wantErr && err == nil {
            t.Errorf("input %q: expected error, got nil", tc.input)
        }
        if !tc.wantErr && err != nil {
            t.Errorf("input %q: unexpected error %v", tc.input, err)
        }
    }
}
```

Better — at least there's an input in the error message — but failures for all cases are still reported under `TestParseDate`. You can't run just the `"not-a-date"` case with `-run`. You can't add `t.Parallel()` to each case. And if you use `t.Fatal` inside the loop, you exit the entire test, not just that case.

## The Idiomatic Way

`t.Run` wraps each case in its own sub-test with its own failure scope, its own name, and its own lifecycle.

```go
// RIGHT — subtests with t.Run
func TestParseDate(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        wantErr bool
        wantYear int
    }{
        {
            name:     "valid ISO date",
            input:    "2024-01-15",
            wantErr:  false,
            wantYear: 2024,
        },
        {
            name:    "invalid format",
            input:   "not-a-date",
            wantErr: true,
        },
        {
            name:    "empty string",
            input:   "",
            wantErr: true,
        },
        {
            name:    "leap day",
            input:   "2024-02-29",
            wantErr: false,
            wantYear: 2024,
        },
    }

    for _, tt := range tests {
        tt := tt // capture range variable (Go < 1.22)
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel() // each subtest runs concurrently

            d, err := ParseDate(tt.input)
            if tt.wantErr {
                if err == nil {
                    t.Fatalf("expected error, got nil for input %q", tt.input)
                }
                return
            }
            if err != nil {
                t.Fatalf("unexpected error: %v", err)
            }
            if d.Year() != tt.wantYear {
                t.Errorf("year: got %d, want %d", d.Year(), tt.wantYear)
            }
        })
    }
}
```

Now when the "leap day" case fails, the output says `FAIL: TestParseDate/leap_day`. You can re-run just that case: `go test -run TestParseDate/leap_day`. Failures in one subtest don't mask other subtests. And `t.Parallel()` inside each `t.Run` body means the cases run concurrently — cutting wall-clock test time when your cases do anything I/O-adjacent.

Note: in Go 1.22+, the loop variable capture issue (`tt := tt`) was fixed. In older versions, you need it.

## In The Wild

The place where subtests shine brightest in production codebases is anywhere you have a function with distinct behavioral modes — parsers, validators, state machines, HTTP handlers with different input shapes.

Here's a pattern I use for functions that both parse and validate:

```go
func TestCreateOrder(t *testing.T) {
    t.Run("valid order", func(t *testing.T) {
        t.Parallel()
        order, err := CreateOrder(OrderRequest{
            UserID:   42,
            ItemID:   "sku-001",
            Quantity: 3,
        })
        if err != nil {
            t.Fatalf("unexpected error: %v", err)
        }
        if order.Status != StatusPending {
            t.Errorf("status: got %s, want %s", order.Status, StatusPending)
        }
    })

    t.Run("zero quantity rejected", func(t *testing.T) {
        t.Parallel()
        _, err := CreateOrder(OrderRequest{
            UserID:   42,
            ItemID:   "sku-001",
            Quantity: 0,
        })
        if err == nil {
            t.Fatal("expected validation error for zero quantity")
        }
        var ve *ValidationError
        if !errors.As(err, &ve) {
            t.Errorf("expected ValidationError, got %T: %v", err, err)
        }
    })

    t.Run("unknown item rejected", func(t *testing.T) {
        t.Parallel()
        _, err := CreateOrder(OrderRequest{
            UserID:   42,
            ItemID:   "does-not-exist",
            Quantity: 1,
        })
        if err == nil {
            t.Fatal("expected not-found error for unknown item")
        }
    })
}
```

The beauty here is that each subtest documents one specific behavior. Anyone reading the test suite understands the contract of `CreateOrder` without reading the implementation.

## The Gotchas

**The loop variable capture trap.** Before Go 1.22, every iteration of the range loop shares the same `tt` variable. By the time the goroutine launched by `t.Parallel()` runs, the loop may have advanced. `tt := tt` inside the loop body creates a local copy. Forget it and you'll get every subtest using the last value — one of the most confusing test bugs I've encountered.

**`t.Fatal` inside a subtest only fails that subtest.** This is the right behaviour, but if you're used to flat tests, it can surprise you. `t.Fatal` in a subtest calls `runtime.Goexit()` for that goroutine only, not the parent test. The parent test continues.

**Slash characters in subtest names.** If your test case name contains `/`, Go interprets it as a subtest hierarchy separator. A case named `"GET /users"` becomes `TestHandler/GET_/users` in output and `-run` patterns, which is odd. I use underscores or keep names short and descriptive.

**`t.Parallel()` placement.** It must be the first call inside `t.Run`'s function, before any setup code. If you put it after setup, you've already run the setup sequentially before releasing the test to run in parallel. More importantly, it signals to the test runner that this subtest should pause until all non-parallel siblings have started, then run concurrently with other parallel subtests.

## Key Takeaway

The rule I live by: if a test function has more than one logical case, it should have subtests. Not because it's stylistically nicer — because without names, every failure is a debugging session that starts from zero. `t.Run` makes failures self-describing. It makes `-run` filtering surgical. It makes `t.Parallel()` composable. The cost is two extra lines per case. The benefit is every failure message telling you exactly what broke and where. That's not optional in a codebase that ships.

---

[Course Index](/series/go-testing-masterclass) | [Next → Lesson 2: Fuzzing in Go](/post/go/go-testing-fuzzing)
