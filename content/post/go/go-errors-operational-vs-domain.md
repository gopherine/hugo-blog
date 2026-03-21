---
title: "Lesson 4: Operational vs Domain Errors — Not all errors deserve the same treatment"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 4
keywords:
  - Go
  - golang
  - error handling
  - operational errors
  - domain errors
  - transient errors
  - HTTP status codes
  - error classification
tags:
  - Go tutorial
  - golang
date: '2025-02-28T00:00:00.000Z'
---

One of the more expensive lessons I learned was treating all errors the same. When a database connection drops, you retry. When a user sends an invalid email address, you return a 400 and explain what's wrong. When someone tries to access a resource that doesn't belong to them, you return a 403. These are completely different situations — different causes, different remedies, different communication needs — and collapsing them into a single `err != nil` branch produces services that retry permanent failures, expose internal details to users, and log noise that drowns out real alerts.

The mental model I use now: errors have a **kind**, and that kind should determine how every layer in the system responds.

## The Problem

The naive approach is to let errors float up unclassified and let whoever reaches the boundary figure it out. In a small service this is manageable. In anything real, it produces two predictable failure modes.

```go
// WRONG — treating all errors identically
func (s *PaymentService) Charge(ctx context.Context, userID string, amount int) error {
    user, err := s.users.Get(ctx, userID)
    if err != nil {
        return err // could be: not found? db timeout? network blip?
    }

    if err := s.stripe.Charge(user.StripeID, amount); err != nil {
        return err // could be: card declined? network error? rate limited?
    }

    return nil
}

// Handler has no idea what to do
func (h *Handler) Charge(w http.ResponseWriter, r *http.Request) {
    err := h.svc.Charge(r.Context(), "user-1", 1000)
    if err != nil {
        // we don't know if this is a user error, a transient error, or a bug
        // so we always return 500 and log it — even for "card declined"
        log.Printf("charge error: %v", err)
        http.Error(w, "payment failed", http.StatusInternalServerError)
    }
}
```

The user gets a 500 when their card is declined. Your alerting fires on every declined payment. The retry logic (if you have any) retries "card declined" forever. None of this is acceptable.

The second mistake is adding HTTP status codes directly to your domain types — coupling your business logic to your transport layer.

```go
// WRONG — domain types know about HTTP
type AppError struct {
    Message    string
    StatusCode int // business logic shouldn't know about HTTP
}

func (e *AppError) Error() string { return e.Message }

func (s *UserService) Get(ctx context.Context, id string) (*User, error) {
    // service now has to decide HTTP codes — that's the handler's job
    return nil, &AppError{Message: "not found", StatusCode: 404}
}
```

This works until you want to use the same service from a gRPC endpoint, a background job, or a CLI tool — none of which speak HTTP status codes.

## The Idiomatic Way

Classify errors into kinds at definition time. The handler (or any boundary) maps kinds to transport codes. The classification lives in the domain; the mapping lives at the edge.

```go
// RIGHT — error kind as an enum, mapping happens at the boundary
type ErrorKind int

const (
    KindNotFound     ErrorKind = iota // permanent, 404
    KindUnauthorized                  // permanent, 401/403
    KindValidation                    // permanent, 400
    KindConflict                      // permanent, 409
    KindTransient                     // retry-able, 503
    KindInternal                      // bug, 500
)

type AppError struct {
    Kind    ErrorKind
    Message string // user-safe message
    Detail  string // internal detail for logging
    Err     error  // underlying cause
}

func (e *AppError) Error() string {
    if e.Err != nil {
        return fmt.Sprintf("%s: %v", e.Detail, e.Err)
    }
    return e.Detail
}

func (e *AppError) Unwrap() error { return e.Err }

// Constructors — convenient and self-documenting
func ErrNotFound(detail string, cause error) *AppError {
    return &AppError{Kind: KindNotFound, Message: "resource not found", Detail: detail, Err: cause}
}
func ErrValidation(message, detail string) *AppError {
    return &AppError{Kind: KindValidation, Message: message, Detail: detail}
}
func ErrTransient(detail string, cause error) *AppError {
    return &AppError{Kind: KindTransient, Message: "service temporarily unavailable", Detail: detail, Err: cause}
}
```

Now the handler does the mapping:

```go
// RIGHT — boundary maps kind to HTTP code
func httpStatus(err error) int {
    var ae *AppError
    if !errors.As(err, &ae) {
        return http.StatusInternalServerError
    }
    switch ae.Kind {
    case KindNotFound:
        return http.StatusNotFound
    case KindUnauthorized:
        return http.StatusUnauthorized
    case KindValidation:
        return http.StatusBadRequest
    case KindConflict:
        return http.StatusConflict
    case KindTransient:
        return http.StatusServiceUnavailable
    default:
        return http.StatusInternalServerError
    }
}

func (h *Handler) Charge(w http.ResponseWriter, r *http.Request) {
    err := h.svc.Charge(r.Context(), "user-1", 1000)
    if err != nil {
        status := httpStatus(err)
        if status >= 500 {
            log.Printf("charge: internal error: %v", err) // only log 5xx
        }
        var ae *AppError
        if errors.As(err, &ae) {
            json.NewEncoder(w).Header().Set("Content-Type", "application/json")
            w.WriteHeader(status)
            json.NewEncoder(w).Encode(map[string]string{"error": ae.Message})
            return
        }
        http.Error(w, "internal error", status)
    }
}
```

## In The Wild

Here's how classification flows through a real payment service:

```go
// Repository — classifies database errors
func (r *PaymentRepo) GetCard(ctx context.Context, userID string) (*Card, error) {
    var c Card
    err := r.db.QueryRowContext(ctx,
        `SELECT id, stripe_id FROM cards WHERE user_id=$1 AND active=true`, userID,
    ).Scan(&c.ID, &c.StripeID)

    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrNotFound(fmt.Sprintf("no card for user %s", userID), nil)
    }
    if isConnectionError(err) {
        return nil, ErrTransient(fmt.Sprintf("get card for user %s", userID), err)
    }
    if err != nil {
        return nil, &AppError{Kind: KindInternal, Detail: fmt.Sprintf("get card %s", userID), Err: err}
    }
    return &c, nil
}

// External client — classifies provider errors
func (c *StripeClient) Charge(stripeID string, amount int) error {
    resp, err := c.http.Post(stripeChargeURL, stripeID, amount)
    if err != nil {
        // network error — transient
        return ErrTransient("stripe network error", err)
    }
    switch resp.Code {
    case "card_declined":
        return ErrValidation("card was declined", "stripe: card_declined")
    case "insufficient_funds":
        return ErrValidation("insufficient funds", "stripe: insufficient_funds")
    case "rate_limit":
        return ErrTransient("stripe rate limited", nil)
    default:
        if resp.HTTPStatus >= 500 {
            return ErrTransient(fmt.Sprintf("stripe server error: %d", resp.HTTPStatus), nil)
        }
        return &AppError{Kind: KindInternal, Detail: fmt.Sprintf("stripe unexpected: %s", resp.Code)}
    }
}

// Service — classifies business rule violations
func (s *PaymentService) Charge(ctx context.Context, userID string, amount int) error {
    if amount <= 0 {
        return ErrValidation("amount must be positive", fmt.Sprintf("charge: invalid amount %d", amount))
    }

    card, err := s.cards.GetCard(ctx, userID)
    if err != nil {
        return err // already classified
    }

    if err := s.stripe.Charge(card.StripeID, amount); err != nil {
        return err // already classified
    }

    return nil
}
```

The retry logic in the background worker only needs to check one thing:

```go
func processWithRetry(ctx context.Context, svc *PaymentService, job PaymentJob) error {
    for attempt := 0; attempt < 3; attempt++ {
        err := svc.Charge(ctx, job.UserID, job.Amount)
        if err == nil {
            return nil
        }
        var ae *AppError
        if !errors.As(err, &ae) || ae.Kind != KindTransient {
            return err // permanent failure or unknown — don't retry
        }
        // transient — back off and retry
        time.Sleep(time.Duration(attempt+1) * 500 * time.Millisecond)
    }
    return fmt.Errorf("charge %s: exceeded retry limit", job.UserID)
}
```

## The Gotchas

**Don't classify too granularly at first.** Start with four kinds: validation, not-found, transient, internal. Add more kinds as you find you need to distinguish them in handlers. Premature taxonomy creates a lot of boilerplate for little benefit.

**Don't let external error codes bleed through without translation.** Stripe's `card_declined` string shouldn't appear in your `AppError.Message`. Translate it at the client boundary. Your domain errors should speak your domain's language.

**Transient errors need a ceiling.** If you retry transient errors without a cap, a downed dependency becomes an infinite loop. Always bound your retry count and total duration, and surface the final failure as internal after the limit.

**Validation errors should be user-readable.** The `Message` field on a validation `AppError` goes to the client. Write it like a human, not like a log message. "email is required" — not "validation: field email: empty string".

## Key Takeaway

Classify errors by kind — not-found, validation, transient, internal — at the point they're created. Let every downstream layer pass them through unchanged. Let the boundary (HTTP handler, gRPC interceptor, background job runner) do the mapping from kind to transport code or retry decision. This separation means your service logic stays clean, your retries only fire on the right failures, and your users get meaningful error messages instead of generic 500s.

---

Previous: [Lesson 3: Wrapping Strategy](go-errors-wrapping-strategy.md) | Next: [Lesson 5: Logging vs Returning — Log at the boundary, return everywhere else](go-errors-logging-vs-returning.md)
