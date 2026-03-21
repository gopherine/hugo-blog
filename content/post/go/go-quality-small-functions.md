---
title: "Lesson 5: Small Functions Win — If you can''t name it clearly, it does too much"
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 5
keywords:
  - Go
  - golang
  - functions
  - code quality
  - single responsibility
  - readability
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-12-06T00:00:00.000Z'
---

There is a simple test I apply to any function I'm about to merge: can I name it clearly without using "and"? If the most honest name for a function is `validateAndSaveAndNotifyUser`, the function is three functions pretending to be one. The naming test doesn't lie — it's a direct readout of the function's responsibility. I've seen this test convince engineers who were unmoved by SOLID principles or Uncle Bob quotes, because it's visceral: if you can't say what the function does in a few words, you already know something is wrong.

## The Problem

Large functions accumulate because it's always easier to add a line to an existing function than to figure out where a new function belongs. The result is functions that outlive the original design intent by hundreds of lines.

```go
// WRONG — a function that does six things with no clear name possible
func handleCheckout(w http.ResponseWriter, r *http.Request) {
    // 1. parse and validate body
    var req CheckoutRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "bad request", 400)
        return
    }
    if req.UserID == 0 || len(req.Items) == 0 {
        http.Error(w, "missing fields", 400)
        return
    }

    // 2. load user
    user, err := db.QueryRow("SELECT * FROM users WHERE id = ?", req.UserID).Scan(...)
    if err != nil {
        http.Error(w, "user not found", 404)
        return
    }

    // 3. compute totals
    var total float64
    for _, item := range req.Items {
        total += item.Price * float64(item.Quantity)
    }
    if couponCode != "" {
        total *= 0.9 // 10% discount
    }

    // 4. charge payment
    chargeResp, err := stripe.Charge(user.PaymentMethodID, total)
    if err != nil {
        http.Error(w, "payment failed", 402)
        return
    }

    // 5. create order record
    orderID, err := db.Exec("INSERT INTO orders ...", ...)

    // 6. send confirmation email
    sendEmail(user.Email, orderID)

    w.WriteHeader(200)
}
```

This function is 60 lines and tests exactly none of steps 3, 4, 5, or 6 in isolation. When step 4 (Stripe) changes its API, you're editing a 60-line function and hoping you don't accidentally change step 3 or 6.

## The Idiomatic Way

Each numbered comment in that function is a function name waiting to be extracted. The extraction is mechanical.

```go
// RIGHT — handler orchestrates; each step is testable independently
func (h *CheckoutHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    req, err := decodeCheckoutRequest(r)
    if err != nil {
        http.Error(w, err.Error(), http.StatusBadRequest)
        return
    }

    user, err := h.users.Find(r.Context(), req.UserID)
    if err != nil {
        http.Error(w, "user not found", http.StatusNotFound)
        return
    }

    total := computeTotal(req.Items, req.CouponCode)

    charge, err := h.payments.Charge(r.Context(), user.PaymentMethodID, total)
    if err != nil {
        http.Error(w, "payment failed", http.StatusPaymentRequired)
        return
    }

    order, err := h.orders.Create(r.Context(), user.ID, charge.ID, req.Items)
    if err != nil {
        // payment succeeded but order record failed — compensating logic here
        http.Error(w, "order creation failed", http.StatusInternalServerError)
        return
    }

    h.mailer.SendConfirmation(user.Email, order.ID) // fire and forget
    w.WriteHeader(http.StatusCreated)
    json.NewEncoder(w).Encode(order)
}
```

Now each extracted function can be tested with straightforward unit tests:

```go
func TestComputeTotal(t *testing.T) {
    tests := []struct {
        name   string
        items  []Item
        coupon string
        want   float64
    }{
        {"no coupon", []Item{{Price: 10, Quantity: 2}}, "", 20.0},
        {"with coupon", []Item{{Price: 10, Quantity: 2}}, "SAVE10", 18.0},
        {"empty cart", nil, "", 0.0},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := computeTotal(tt.items, tt.coupon)
            if got != tt.want {
                t.Errorf("got %.2f, want %.2f", got, tt.want)
            }
        })
    }
}
```

`computeTotal` has no I/O, no context, no dependencies — pure function, trivially testable.

## In The Wild

A service I worked on had a function called `syncInventory` that was 280 lines long. It fetched inventory from two external APIs, merged the results with deduplification logic, computed restock recommendations, updated the database, and triggered webhook notifications. One developer described it as "the function I always need a coffee before touching."

We spent a sprint decomposing it. The final result was nine functions averaging 30 lines each. The merge/dedup logic, the restock algorithm, and the webhook dispatch each became their own functions with their own tests. `syncInventory` itself shrank to 25 lines — a readable description of the steps.

Three weeks later a bug was reported in the restock recommendations. Finding it took 10 minutes. In the old code it would have taken an hour to isolate which part of the 280-line function was responsible.

## The Gotchas

**Don't extract for extraction's sake.** A five-line function that's used in exactly one place and whose name requires reading the body to understand isn't better than inline code. Extract when it enables independent testing, when the extracted unit has a name that's meaningful on its own, or when the logic appears in more than one place.

**Helper functions aren't free.** Every extracted function is a new thing a reader has to find and read. If the extraction moves understanding further away rather than closer, it's wrong. Keep related logic close together — in the same file, in the same block if the scope is small enough.

**Unexported helpers can still be tested.** You can test unexported functions from the same package. Put the test in the same package (not `_test`). There's no reason to promote a function to exported just because you want a test for it.

**Long functions often indicate missing types.** When a function is long because it's wrangling raw primitives, the real fix might be a new struct. A function that manually extracts fields from a `map[string]interface{}` probably needs a struct with a proper decoder, not just smaller functions.

## Key Takeaway

The naming test — "can I name this function without using 'and'?" — is your first and most reliable signal that a function is too large. Extract when the extracted piece has a clear name, can be tested in isolation, or appears in multiple places. Keep the orchestration function short enough that it reads as a description of the process. Each extracted helper should be small enough that its test cases fit on one screen. You'll write more functions, but you'll read faster, test more thoroughly, and find bugs in minutes instead of hours.

---

[← Lesson 4: Code Review Heuristics](/post/go/go-quality-code-review) | [Course Index](/series/go-code-quality-maintainability) | [Next → Lesson 6: Package Cohesion](/post/go/go-quality-package-cohesion)
