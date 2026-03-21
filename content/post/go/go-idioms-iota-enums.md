---
title: 'Go Idioms: iota for Enums'
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
---
"unset", not a valid direction
    North              // 1
    South              // 2
    East               // 3
    West               // 4
)

func navigate(d Direction) {
    if d == 0 {
        // zero value means "not set"
        panic("direction not initialized")
    }
}
```

This makes a zero-value `Direction` represent an explicitly invalid state, which can help catch bugs where a direction variable was never assigned.

Alternatively, name the zero case explicitly:

```go
type Direction int

const (
    DirectionUnknown Direction = iota // 0 — explicit "not set" sentinel
    North
    South
    East
    West
)
```

Which approach you use depends on whether you want the zero value to be a valid state in your domain.

## Bit Flags with iota

This is where `iota` gets genuinely powerful. Using `1 << iota` you can build bitmask constants for permissions, feature flags, and other set-valued states:

```go
type Permission uint

const (
    PermRead    Permission = 1 << iota // 1 (0001)
    PermWrite                          // 2 (0010)
    PermExecute                        // 4 (0100)
    PermAdmin                          // 8 (1000)
)

func hasPermission(userPerms, required Permission) bool {
    return userPerms&required == required
}

func main() {
    // Grant read and write
    userPerms := PermRead | PermWrite

    fmt.Println(hasPermission(userPerms, PermRead))    // true
    fmt.Println(hasPermission(userPerms, PermExecute)) // false
    fmt.Println(hasPermission(userPerms, PermAdmin))   // false

    // Admin gets everything
    adminPerms := PermRead | PermWrite | PermExecute | PermAdmin
    fmt.Println(hasPermission(adminPerms, PermExecute)) // true
}
```

This pattern is used throughout the Go standard library and operating system APIs. `os.O_RDONLY`, `os.O_WRONLY`, `os.O_CREATE` are bit flags. HTTP method constants in some routing libraries use this pattern.

## Adding a String() Method with go generate and stringer

The biggest practical limitation of `iota` enums is that they print as integers by default. If you log a `UserStatus` value, you get `2` instead of `StatusActive`. Debugging this is painful.

The manual approach is to add a `String()` method:

```go
func (s UserStatus) String() string {
    switch s {
    case StatusPending:
        return "Pending"
    case StatusActive:
        return "Active"
    case StatusInactive:
        return "Inactive"
    case StatusBanned:
        return "Banned"
    default:
        return fmt.Sprintf("UserStatus(%d)", int(s))
    }
}
```

This works but is tedious to maintain. The idiomatic solution is the `stringer` tool from `golang.org/x/tools`:

```go
//go:generate stringer -type=UserStatus

type UserStatus int

const (
    StatusPending  UserStatus = iota
    StatusActive
    StatusInactive
    StatusBanned
)
```

Running `go generate` produces a `userstatus_string.go` file with the `String()` method automatically. It also handles the default case for unknown values. Add the `//go:generate` comment near the type definition, commit the generated file to version control, and re-run `go generate` whenever you add new constants.

## iota Expressions for Real-World Patterns

`iota` can appear in any constant expression, not just assignments. This enables patterns like HTTP status code groupings:

```go
type HTTPStatusGroup int

const (
    StatusGroupInformational HTTPStatusGroup = (iota + 1) * 100 // 100
    StatusGroupSuccess                                           // 200
    StatusGroupRedirection                                       // 300
    StatusGroupClientError                                       // 400
    StatusGroupServerError                                       // 500
)

func classifyStatus(code int) HTTPStatusGroup {
    return HTTPStatusGroup(code / 100 * 100)
}
```

Or memory size constants:

```go
const (
    _           = iota // ignore first value
    KB float64  = 1 << (10 * iota) // 1 << 10 = 1024
    MB                              // 1 << 20
    GB                              // 1 << 30
    TB                              // 1 << 40
)
```

This is taken almost directly from the Go tour and is a classic example of `iota` in an expression context. The `_` discards the first value (which would be `1 << 0 = 1`, not a useful size unit).

## A Real-World Enum Pattern: Workflow States

Putting it all together, here is how you might model a document approval workflow:

```go
//go:generate stringer -type=ApprovalState

type ApprovalState int

const (
    ApprovalDraft     ApprovalState = iota // 0 — valid zero value here
    ApprovalSubmitted                       // 1
    ApprovalReview                         // 2
    ApprovalApproved                       // 3
    ApprovalRejected                       // 4
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

func (d *Document) Approve() error {
    if d.State != ApprovalReview {
        return fmt.Errorf("cannot approve document in state %s, must be in Review", d.State)
    }
    d.State = ApprovalApproved
    return nil
}
```

Because `String()` is defined (via `stringer`), the error messages print the state name, not a number. The typed constant prevents passing arbitrary integers into the state machine. The zero value (`ApprovalDraft`) is a sensible default for a new document.

## What iota Cannot Do

`iota` is not a full-blown enum. There is no built-in exhaustive switch checking — the compiler will not warn you if you add a new constant and forget to handle it in a switch statement. For that you need either `go vet` with the `exhaustive` analyzer (a third-party tool) or a careful code review discipline.

Also, `iota` values are not stable across reordering. If you insert a new constant in the middle of the block, every constant after it shifts. Never store `iota`-based constants in a database or external file unless you explicitly assign stable values. For persistent storage, assign explicit values:

```go
// RIGHT for persistent storage
const (
    StatusPending  UserStatus = 1 // explicit, stable
    StatusActive   UserStatus = 2
    StatusInactive UserStatus = 3
)
```

Use `iota` for in-memory state, bit flags, and internal constants. Use explicit values when the numbers need to survive a refactor.
