---
title: "Lesson 2: Avoiding Cyclic Dependencies — If packages import each other, your design is wrong"
author: Atharva Pandey
series: "Go Package & Module Architecture"
lesson: 2
keywords: [Go, golang, packages, modules, architecture]
tags: [Go tutorial, golang, architecture]
date: '2024-07-12T00:00:00.000Z'
---

The Go compiler refuses to build a program with a cyclic import. It is not a warning. It is not a lint violation. It is a hard failure. I used to find this annoying when I was newer to the language. Now I consider it one of Go's greatest gifts. The compiler is telling you something important: if package A needs package B and package B needs package A, you have not yet understood what the relationship between these two concepts really is.

This lesson is about why cycles happen, how to recognize the design mistake underneath them, and three concrete techniques to break them.

## The Problem

Cycles appear in codebases that grow faster than their design does. You have an `order` package and a `customer` package. A customer can place orders, so `customer` imports `order`. An order has a customer, so `order` imports `customer`. The compiler stops you cold.

```
import cycle not allowed:
    package myapp/order
        imports myapp/customer
        imports myapp/order
```

The naive response is to merge both packages into one fat package. That solves the compiler error while creating a worse problem: you now have no boundaries at all. The right response is to ask: why does this cycle exist and what does it reveal about my model?

Cycles almost always mean one of three things:

1. Two packages that should be one coherent concept are split for the wrong reasons.
2. Both packages depend on a shared concept that has not been extracted yet.
3. One package knows too much about another and needs to communicate through an abstraction instead.

Let me show all three patterns with real examples.

## The Idiomatic Way

**Wrong: mutual imports between order and notification**

```go
// package order — depends on notify to send confirmation emails
package order

import (
    "myapp/notify"
)

type Order struct {
    ID         int64
    CustomerID int64
    Total      float64
}

func Place(o Order) error {
    // ... persist the order
    notify.SendOrderConfirmation(o.ID, o.CustomerID) // cycle if notify imports order
    return nil
}
```

```go
// package notify — imports order to access order details
package notify

import (
    "myapp/order" // CYCLE: order imports notify, notify imports order
)

func SendOrderConfirmation(orderID, customerID int64) error {
    o, _ := order.GetByID(orderID) // notify reaches back into order
    // ... send email with o.Total
    return nil
}
```

This cycle exists because `notify` needs order data, so it pulls in the `order` package. The fix is to invert the dependency: pass the data `notify` needs directly, instead of having it reach back.

**Right: break the cycle by passing data, not importing the package**

```go
// package notify — knows nothing about the order package
package notify

type OrderConfirmation struct {
    OrderID    int64
    CustomerID int64
    Total      float64
}

func SendOrderConfirmation(c OrderConfirmation) error {
    // ... format and send email using c.Total, c.OrderID, etc.
    return nil
}
```

```go
// package order — imports notify but notify no longer imports order
package order

import (
    "myapp/notify"
)

type Order struct {
    ID         int64
    CustomerID int64
    Total      float64
}

func Place(o Order) error {
    // ... persist the order
    return notify.SendOrderConfirmation(notify.OrderConfirmation{
        OrderID:    o.ID,
        CustomerID: o.CustomerID,
        Total:      o.Total,
    })
}
```

The cycle is gone. `notify` defines its own input type. `order` populates it. Dependency flows one direction: `order` → `notify`.

**Wrong: two packages sharing a type that belongs to neither**

```go
// package billing — needs the User type from the user package
package billing

import "myapp/user"

func ChargeUser(u user.User, amount float64) error {
    // uses u.PaymentMethodID
    return nil
}
```

```go
// package user — needs Invoice type from billing package
package user

import "myapp/billing" // CYCLE

func GetInvoices(userID int64) ([]billing.Invoice, error) {
    return billing.ListByUser(userID)
}
```

Both packages need each other because both deal with user-billing interactions. The solution is to extract a shared domain type into a third package that neither of them owns.

**Right: extract shared types to a neutral domain package**

```go
// package domain — neutral types owned by neither billing nor user
package domain

type UserID int64

type Invoice struct {
    ID     int64
    UserID UserID
    Amount float64
}
```

```go
// package billing — imports domain, not user
package billing

import "myapp/domain"

func ChargeUser(userID domain.UserID, amount float64) error {
    // ... charge using userID
    return nil
}

func ListByUser(userID domain.UserID) ([]domain.Invoice, error) {
    // ... query
    return nil, nil
}
```

```go
// package user — imports domain, not billing
package user

import "myapp/domain"

type User struct {
    ID              domain.UserID
    PaymentMethodID string
}
```

Now `billing` and `user` both import `domain`, but neither imports the other. The cycle is structurally impossible.

**Right: use interfaces to break a cycle through inversion**

```go
// package order — defines the interface it needs from the outside world
package order

// Notifier is defined here, in the package that needs notification,
// not in the package that implements it.
type Notifier interface {
    NotifyPlaced(orderID int64) error
}

type Service struct {
    notifier Notifier
}

func NewService(n Notifier) *Service {
    return &Service{notifier: n}
}

func (s *Service) Place(o Order) error {
    // ... persist
    return s.notifier.NotifyPlaced(o.ID)
}
```

```go
// package notify — implements the interface but does NOT import order
package notify

import "log"

type EmailNotifier struct{}

func (e *EmailNotifier) NotifyPlaced(orderID int64) error {
    log.Printf("order %d placed, sending confirmation", orderID)
    return nil
}
```

```go
// package main — wires everything together
package main

import (
    "myapp/notify"
    "myapp/order"
)

func main() {
    svc := order.NewService(&notify.EmailNotifier{})
    _ = svc
}
```

`order` depends on nothing from `notify`. `notify` depends on nothing from `order`. `main` is the only package that sees both, and its job is exactly that: composition.

## In The Wild

The Go standard library is the best example of cycle-free design at scale. Look at `encoding/json`: it needs to handle `io.Writer` and `io.Reader`, so it imports `io`. But `io` does not import `encoding/json`. The direction is always from the concrete toward the abstract, from the specific toward the general.

The popular `chi` router deliberately avoids importing any middleware packages. Middleware packages import `chi` if they need it, never the reverse. This unidirectional dependency graph is why you can use a single `chi` middleware without pulling in the entire ecosystem.

In large projects I have worked on, the appearance of a cycle is almost always a signal worth celebrating. It means the compiler just caught a design problem that would otherwise have quietly festered as a tight coupling in the codebase.

## The Gotchas

**Gotcha 1: solving cycles by merging packages.** This silences the compiler but destroys the value of having separate packages in the first place. Merging should only happen if you genuinely conclude that the two concepts are the same thing.

**Gotcha 2: the "event bus" escape hatch.** Some teams break cycles by routing all communication through a global event bus. This works mechanically but hides the coupling rather than eliminating it. You still have the same conceptual dependency; you have just made it invisible and harder to trace.

**Gotcha 3: test packages inadvertently creating cycles.** If you write `package order_test` (external test package) in a test file that imports `order` and also imports a package that imports `order`, you are fine. But if you use `package order` (internal test package) and import a package that also imports `order`, you may trigger a cycle. Use `_test` suffix packages to keep test dependencies clean.

**Gotcha 4: confusing "should not import" with "must not import".** The compiler only catches direct cycles. It is perfectly possible to have an architectural violation — where a low-level package imports a high-level one — without a cycle. Cycles are the worst manifestation of bad dependency direction, but they are not the only one. Lesson 6 in this series goes deeper on that.

## Key Takeaway

When the Go compiler tells you there is a cyclic import, it is handing you a design review for free. Do not fight it. Instead, ask what the cycle is telling you about your model. The three solutions — passing data instead of importing, extracting shared types, and inverting dependencies through interfaces — are not tricks. They are expressions of cleaner design that would be valuable even if the compiler never complained.

A codebase with no cycles is one where you can trace the flow of any dependency from cause to effect without going in circles. That property makes the whole system easier to understand, test, and change.

---

**Series: Go Package & Module Architecture**

- [Lesson 1: Package Boundaries](/post/go/go-pkg-boundaries/)
- Lesson 2: Avoiding Cyclic Dependencies — *you are here*
- [Lesson 3: internal/ Usage Patterns](/post/go/go-pkg-internal/)
- [Lesson 4: Interface Placement](/post/go/go-pkg-interface-placement/)
- [Lesson 5: Monolith vs Multi-Module](/post/go/go-pkg-mono-vs-multi/)
- [Lesson 6: Dependency Direction](/post/go/go-pkg-dependency-direction/)
- [Lesson 7: go.work and Workspace Mode](/post/go/go-pkg-workspaces/)
