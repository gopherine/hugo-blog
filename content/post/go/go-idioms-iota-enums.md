---
title: 'Lesson 13: iota for Enums — Constants that count themselves'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - iota
  - enums
  - constants
  - bit flags
  - stringer
tags:
  - Go tutorial
  - golang
date: '2025-09-22T00:00:00.000Z'
series: "Idiomatic Go"
lesson: 13
---

Go doesn't have a built-in enum keyword. What it has is `iota`, a constant counter that resets to zero at the start of each `const` block and increments with every constant declaration. It sounds underwhelming. In practice it gives you typed enums, bitmask permissions, and self-maintaining constant sequences — all without any runtime overhead.

## The Problem

The naive approach to enums in Go is plain integer constants or string constants. Both work, but neither gives you type safety:

```go
// WRONG — untyped constants, no compile-time safety
const (
    StatusPending  = 0
    StatusActive   = 1
    StatusInactive = 2
    StatusBanned   = 3
)

func updateStatus(userID string, status int) error {
    // nothing stops a caller from passing 42 here
}

// Called later in some other file...
updateStatus(id, 99) // compiles fine, invalid state at runtime
```

The function signature says `int`. The caller passes any integer. You only find out at runtime — probably via a database constraint violation or a confused switch statement — that 99 is not a valid status. You also have no human-readable representation: logging shows `2`, not `StatusInactive`.

## The Idiomatic Way

Define a named type based on `int`, then use `iota` in a `const` block:

```go
type UserStatus int

const (
    StatusPending  UserStatus = iota // 0
    StatusActive                     // 1
    StatusInactive                   // 2
    StatusBanned                     // 3
)

func updateStatus(userID string, status UserStatus) error {
    // now the compiler enforces the type
}

// This no longer compiles:
updateStatus(id, 99) // cannot use 99 (untyped int constant) as type UserStatus
```

The type system now prevents passing arbitrary integers. You still need a bounds check for values that come from external sources (a database int column, a JSON number), but code that stays within Go gets compile-time enforcement.

For bitmask permissions, `1 << iota` gives you power-of-two values:

```go
type Permission uint

const (
    PermRead    Permission = 1 << iota // 1  (0001)
    PermWrite                          // 2  (0010)
    PermExecute                        // 4  (0100)
    PermAdmin                          // 8  (1000)
)

func hasPermission(userPerms, required Permission) bool {
    return userPerms&required == required
}

// Grant read and write, check later
userPerms := PermRead | PermWrite
fmt.Println(hasPermission(userPerms, PermRead))    // true
fmt.Println(hasPermission(userPerms, PermExecute)) // false
```

This pattern is how `os.O_RDONLY`, `os.O_WRONLY`, and `os.O_CREATE` work in the standard library.

For the zero-value question: sometimes you want zero to be an explicitly invalid state, so you catch uninitialized variables. Sometimes you want it to be a sensible default. Both are valid:

```go
// Zero is invalid — use a blank identifier to skip it
type Direction int
const (
    _     Direction = iota // 0 — skip it, zero means "not set"
    North                  // 1
    South                  // 2
    East                   // 3
    West                   // 4
)

// Zero is valid — give it an explicit name
type ApprovalState int
const (
    ApprovalDraft     ApprovalState = iota // 0 — sensible default
    ApprovalSubmitted                       // 1
    ApprovalApproved                        // 2
    ApprovalRejected                        // 3
)
```

## In The Wild

Here's how I use this for a document approval workflow. The typed state prevents invalid transitions and the `stringer` tool generates human-readable output automatically:

```go
//go:generate stringer -type=ApprovalState

type ApprovalState int

const (
    ApprovalDraft     ApprovalState = iota
    ApprovalSubmitted
    ApprovalReview
    ApprovalApproved
    ApprovalRejected
)

type Document struct {
    ID    string
    Title string
    State ApprovalState
}

func (d *Document) Submit() error {
    if d.State != ApprovalDraft {
        return fmt.Errorf("cannot submit document in state %s", d.State)
    }
    d.State = ApprovalSubmitted
    return nil
}
```

Running `go generate` creates a `approvalstate_string.go` file with a `String()` method. The error message prints `"cannot submit document in state ApprovalApproved"` instead of `"cannot submit document in state 3"`. That single line of `//go:generate` saves you from writing and maintaining a switch statement for the rest of the project's life.

## The Gotchas

**iota values shift when you reorder constants.** If you insert a new state in the middle of a `const` block, every constant after it gets a new numeric value. If you've stored these numbers in a database, you've just corrupted your data:

```go
// Before: StatusActive = 1, StatusInactive = 2
// After inserting StatusSuspended between them:
const (
    StatusPending   UserStatus = iota // 0 — same
    StatusActive                      // 1 — same
    StatusSuspended                   // 2 — NEW, shifted everything below it
    StatusInactive                    // 3 — was 2, now 3 — DATABASE MISMATCH
    StatusBanned                      // 4 — was 3, now 4
)
```

For anything stored externally, assign explicit stable values instead of relying on `iota`'s ordering:

```go
const (
    StatusPending   UserStatus = 1
    StatusActive    UserStatus = 2
    StatusSuspended UserStatus = 5 // appended, not inserted
    StatusInactive  UserStatus = 3
    StatusBanned    UserStatus = 4
)
```

**No exhaustive switch checking by default.** The compiler won't warn you if you add a new constant and forget to handle it in a switch. The `exhaustruct` and `exhaustive` static analysis tools catch this. Wire them into your CI and you get the safety you'd expect from a proper enum.

**iota only works inside `const` blocks.** You can't use it in a `var` block or as a function argument. It's a compile-time construct, which is also why it has zero runtime cost.

## Key Takeaway

`iota` is Go's enum mechanism, and it's more capable than it first appears. The typed constant pattern gives you compile-time type safety, the `1 << iota` pattern gives you bitmasks, and pairing any `iota` type with the `stringer` tool gives you human-readable logging at zero maintenance cost. The one discipline required: never rely on iota ordering for values that live outside your process. For in-memory state machines, workflow states, and feature flags, `iota` is exactly the right tool.

---

← [Lesson 12: Value vs Pointer Receivers](/post/go/go-idioms-value-vs-pointer-receivers) | [Course Index](#) | [Lesson 14: context.Context](/post/go/go-idioms-context) →
