---
title: "Lesson 3: internal/ Usage Patterns — Compiler-enforced encapsulation"
author: Atharva Pandey
series: "Go Package & Module Architecture"
lesson: 3
keywords: [Go, golang, packages, modules, architecture]
tags: [Go tutorial, golang, architecture]
date: '2024-08-18T00:00:00.000Z'
---

Go does not have access modifiers in the Java or Kotlin sense. There is no `protected`, no `package-private`, no friend classes. You get exactly two visibility levels: exported (capital letter) and unexported (lowercase letter). That simplicity is a feature. But it creates a gap: how do you share something across packages within your own module without accidentally exposing it to the outside world?

The answer is `internal/`. It is one of the most underused and underappreciated tools in Go's package system, and it is enforced by the compiler itself.

## The Problem

Suppose you are building a library, say a payment processing SDK. You have several sub-packages: `payment`, `invoice`, and `subscription`. All three of them need access to a shared HTTP client with retry logic, request signing, and a standard error mapper. You want to share this client across your sub-packages.

The natural instinct is to export it. But if you export it, any downstream consumer of your library gets access to it too — including the retry configuration, the signing key helper, and internal error types that you never intended to be part of your public API. Suddenly your implementation details become a contract you have to maintain forever.

The other instinct is to duplicate the client in each package. That works until you need to fix a bug in the retry logic, and you have to fix it in three places.

Without `internal/`, you are stuck choosing between over-exposure and duplication. With it, you can share code freely within your module while the compiler prevents anyone else from touching it.

## The Idiomatic Way

**Wrong: exporting internals just to share them across sub-packages**

```go
// package httpclient — exported so sub-packages can use it,
// but now it's also visible to all external consumers
package httpclient

import (
    "net/http"
    "time"
)

// Client is exported. Any consumer of your module can now
// import and depend on this type, including its internal fields.
type Client struct {
    HTTPClient *http.Client
    SigningKey  []byte        // internal detail, now public
    MaxRetries int
    BaseURL    string
}

func New(baseURL string, key []byte) *Client {
    return &Client{
        HTTPClient: &http.Client{Timeout: 10 * time.Second},
        SigningKey:  key,
        MaxRetries: 3,
        BaseURL:    baseURL,
    }
}
```

Once this is exported, you cannot change `SigningKey` to a struct without a breaking change. You cannot rename `MaxRetries`. External consumers will import it and pin themselves to your implementation choices.

**Right: move implementation details under internal/**

```go
// internal/httpclient/client.go
// This package is ONLY accessible to code within your module.
// The compiler enforces this — no external import is possible.
package httpclient

import (
    "bytes"
    "context"
    "encoding/json"
    "fmt"
    "net/http"
    "time"
)

type Client struct {
    base       *http.Client
    signingKey []byte
    maxRetries int
    baseURL    string
}

func New(baseURL string, key []byte) *Client {
    return &Client{
        base:       &http.Client{Timeout: 10 * time.Second},
        signingKey: key,
        maxRetries: 3,
        baseURL:    baseURL,
    }
}

func (c *Client) Post(ctx context.Context, path string, body any) (*http.Response, error) {
    data, err := json.Marshal(body)
    if err != nil {
        return nil, fmt.Errorf("marshalling request: %w", err)
    }
    req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(data))
    if err != nil {
        return nil, fmt.Errorf("creating request: %w", err)
    }
    req.Header.Set("Content-Type", "application/json")
    // ... apply signing, retries, etc.
    return c.base.Do(req)
}
```

Now your `payment`, `invoice`, and `subscription` packages can all use `internal/httpclient` freely:

```go
// payment/payment.go
package payment

import (
    "context"

    "github.com/yourorg/paymentsdk/internal/httpclient"
)

type Service struct {
    client *httpclient.Client
}

func NewService(baseURL string, key []byte) *Service {
    return &Service{client: httpclient.New(baseURL, key)}
}

func (s *Service) Charge(ctx context.Context, amount int64) error {
    _, err := s.client.Post(ctx, "/charge", map[string]int64{"amount": amount})
    return err
}
```

An external consumer trying to import your `internal/httpclient` will get a hard compile error:

```
use of internal package github.com/yourorg/paymentsdk/internal/httpclient
not allowed
```

**Right: using internal/ for shared test infrastructure too**

This is the pattern I use most heavily in practice. Test helpers that need to be shared across multiple packages — fake implementations, assertion utilities, builder patterns for test data — are exactly the kind of code that belongs in `internal/`.

```go
// internal/testutil/orders.go
package testutil

import (
    "github.com/yourorg/myapp/order"
)

// OrderBuilder provides a fluent interface for constructing test orders.
// It lives in internal/testutil so it's available across all test packages
// without being exposed to external consumers of the module.
type OrderBuilder struct {
    o order.Order
}

func NewOrder() *OrderBuilder {
    return &OrderBuilder{o: order.Order{
        Status: order.StatusPending,
    }}
}

func (b *OrderBuilder) WithID(id int64) *OrderBuilder {
    b.o.ID = id
    return b
}

func (b *OrderBuilder) WithTotal(total float64) *OrderBuilder {
    b.o.Total = total
    return b
}

func (b *OrderBuilder) Build() order.Order {
    return b.o
}
```

```go
// order/service_test.go — can use internal/testutil freely
package order_test

import (
    "testing"

    "github.com/yourorg/myapp/internal/testutil"
)

func TestOrderFulfillment(t *testing.T) {
    o := testutil.NewOrder().WithID(42).WithTotal(99.99).Build()
    // ... test with o
    _ = o
}
```

```go
// billing/invoice_test.go — also uses internal/testutil freely
package billing_test

import (
    "testing"

    "github.com/yourorg/myapp/internal/testutil"
)

func TestInvoiceGeneration(t *testing.T) {
    o := testutil.NewOrder().WithID(7).WithTotal(250.00).Build()
    // ... test with o
    _ = o
}
```

The `testutil` package is shared across tests throughout the module without being part of the public API.

## In The Wild

The standard library itself uses this pattern. Before Go 1.5 when `internal/` became an official language feature, the Go team had been planning it precisely because they needed a way to share implementation details across standard library packages without exposing them externally.

Look at `golang.org/x/net` — it has multiple `internal` packages including `internal/timeseries` and `internal/socks`. These are implementation details of the extended networking library that the maintainers rightfully chose not to expose.

In large monorepos like the ones I have worked in, `internal/` becomes a structured space: `internal/config` for shared configuration types, `internal/middleware` for HTTP middleware shared across services in the same module, `internal/testutil` for test helpers, and `internal/infra` for shared infrastructure clients (database pools, observability setup).

## The Gotchas

**Gotcha 1: misunderstanding the access rule.** The `internal/` rule is not "only this package can use it." It is "only code rooted at the parent of `internal/` can use it." If your `internal/` is at `github.com/yourorg/myapp/internal/`, then any package under `github.com/yourorg/myapp/` can import it. But another module — even one you own — cannot.

```
myapp/
  internal/
    httpclient/   ← accessible to all packages in myapp/
  payment/        ← can use internal/httpclient ✓
  invoice/        ← can use internal/httpclient ✓

otherapp/
  main.go         ← CANNOT use myapp/internal/httpclient ✗
```

**Gotcha 2: nested internal directories for finer control.** You can put `internal/` deeper in the tree to restrict access further:

```
payment/
  internal/
    signer/   ← only payment/ sub-packages can import this
  service.go
invoice/
  service.go  ← cannot import payment/internal/signer
```

This is a powerful pattern for libraries that want to let specific sub-trees share implementation details while keeping them away from sibling trees.

**Gotcha 3: over-using internal/ to avoid design decisions.** I have seen teams dump every poorly designed package into `internal/` as a way of deferring the decision about whether it should be public or not. This is not what `internal/` is for. Internal packages should be internal for a reason — because they are genuinely implementation details, not because you have not decided yet.

**Gotcha 4: forgetting that internal/ packages still need good design.** Just because external users cannot import your internal packages does not mean they can be a mess. Every lesson in this series applies to internal packages too: clear boundaries, no cycles, well-placed interfaces.

## Key Takeaway

`internal/` is compiler-enforced encapsulation. Use it for implementation details that need to be shared within your module but that you do not want to commit to as a public API. Use it for shared test infrastructure. Use it to create sub-module access controls with nested `internal/` directories.

The rule of thumb I follow: if I would be uncomfortable having to maintain backward compatibility for something forever, it goes in `internal/`. Everything exported is a promise to the world. `internal/` lets you keep implementation private while still benefiting from code sharing across your own packages.

---

**Series: Go Package & Module Architecture**

- [Lesson 1: Package Boundaries](/post/go/go-pkg-boundaries/)
- [Lesson 2: Avoiding Cyclic Dependencies](/post/go/go-pkg-cyclic-deps/)
- Lesson 3: internal/ Usage Patterns — *you are here*
- [Lesson 4: Interface Placement](/post/go/go-pkg-interface-placement/)
- [Lesson 5: Monolith vs Multi-Module](/post/go/go-pkg-mono-vs-multi/)
- [Lesson 6: Dependency Direction](/post/go/go-pkg-dependency-direction/)
- [Lesson 7: go.work and Workspace Mode](/post/go/go-pkg-workspaces/)
