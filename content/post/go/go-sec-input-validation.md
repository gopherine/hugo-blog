---
title: "Lesson 1: Input Validation — Trust nothing from the wire"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 1
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2024-06-05T00:00:00.000Z'
---

The first production security incident I dealt with involved a simple integer field in a JSON body. The client sent a negative number where we expected a positive quantity. We hadn't validated it. The result was a negative charge on a purchase — we were effectively paying customers money. That taught me more about input validation than any security course.

Every byte that arrives over the wire is adversarial by default. Not because every user is malicious, but because your assumptions about the data are not enforced by anything except your own code. HTTP gives you a byte stream. JSON decoding gives you Go values. Neither of those steps checks whether the values make sense for your business logic. That job is yours.

## The Problem

The most common failure mode is trusting the shape of a decoded struct as proof of validity:

```go
// WRONG — decode into struct and use immediately, no validation
type CreateOrderRequest struct {
    ProductID string  `json:"product_id"`
    Quantity  int     `json:"quantity"`
    UserEmail string  `json:"user_email"`
}

func handleCreateOrder(w http.ResponseWriter, r *http.Request) {
    var req CreateOrderRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }
    // req.Quantity might be -9999
    // req.UserEmail might be "not-an-email"
    // req.ProductID might be "../../../etc/passwd" if used in a file path
    processOrder(req)
}
```

Successful JSON decoding only means the bytes were valid JSON. It says nothing about whether `Quantity` is positive, whether `UserEmail` is a real email address, or whether `ProductID` contains path traversal characters. All of those are your responsibility.

A second anti-pattern is validating only the happy path — checking that required fields are present but skipping range checks, format checks, and length limits:

```go
// WRONG — partial validation leaves holes
func validateRequest(req CreateOrderRequest) error {
    if req.ProductID == "" {
        return errors.New("product_id required")
    }
    if req.UserEmail == "" {
        return errors.New("user_email required")
    }
    // forgot to check Quantity > 0
    // forgot to check email format
    // forgot to check ProductID length and character set
    return nil
}
```

Partial validation is often worse than no validation because it creates a false sense of security. You think you validated; you didn't.

## The Idiomatic Way

Define validation as a method on the request struct, and make it exhaustive. Use a library like `github.com/go-playground/validator` or write your own — either way, every field needs explicit constraints:

```go
// RIGHT — comprehensive validation with explicit constraints
import "github.com/go-playground/validator/v10"

type CreateOrderRequest struct {
    ProductID string `json:"product_id" validate:"required,alphanum,min=1,max=64"`
    Quantity  int    `json:"quantity"   validate:"required,min=1,max=10000"`
    UserEmail string `json:"user_email" validate:"required,email"`
}

var validate = validator.New()

func (r CreateOrderRequest) Validate() error {
    return validate.Struct(r)
}

func handleCreateOrder(w http.ResponseWriter, r *http.Request) {
    var req CreateOrderRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "invalid JSON", http.StatusBadRequest)
        return
    }
    if err := req.Validate(); err != nil {
        http.Error(w, err.Error(), http.StatusUnprocessableEntity)
        return
    }
    processOrder(req)
}
```

For cases where you build validation manually — which is fine for simple structs — the pattern is a dedicated validator function that returns a structured error rather than a generic string:

```go
// RIGHT — manual validation with structured errors
type ValidationError struct {
    Field   string
    Message string
}

func (e ValidationError) Error() string {
    return fmt.Sprintf("validation error on field %q: %s", e.Field, e.Message)
}

func validateCreateOrder(req CreateOrderRequest) []ValidationError {
    var errs []ValidationError

    if req.ProductID == "" {
        errs = append(errs, ValidationError{"product_id", "required"})
    } else if len(req.ProductID) > 64 {
        errs = append(errs, ValidationError{"product_id", "must be 64 characters or fewer"})
    } else if !isAlphanumeric(req.ProductID) {
        errs = append(errs, ValidationError{"product_id", "must be alphanumeric"})
    }

    if req.Quantity <= 0 {
        errs = append(errs, ValidationError{"quantity", "must be greater than zero"})
    }
    if req.Quantity > 10000 {
        errs = append(errs, ValidationError{"quantity", "must be 10000 or fewer"})
    }

    if !isValidEmail(req.UserEmail) {
        errs = append(errs, ValidationError{"user_email", "must be a valid email address"})
    }

    return errs
}
```

Returning a slice of errors rather than stopping at the first lets the client fix all problems in one round-trip. That's a usability benefit as much as a correctness one.

## In The Wild

Real applications need to handle a few specific classes of input that are especially dangerous:

**String length limits everywhere.** A 10 MB username field can trigger memory exhaustion or denial-of-service. Cap every string field that comes from the wire.

**Integer overflow and range checks.** An `int` decoded from JSON can be any value the client sends. If you multiply `Quantity` by `UnitPrice` and both are `int64`, an attacker who sends `math.MaxInt64` as quantity will overflow your price calculation.

**File paths and shell input.** If any string is used to construct a file path, reject anything containing `..`, null bytes, or characters outside a known safe set. Do not sanitize — reject and return an error. Sanitization logic is subtle and you will get it wrong.

**Content-type enforcement.** Before you even decode the body, check `Content-Type`. Returning a 415 Unsupported Media Type for non-JSON requests prevents a class of confusion attacks where the decoder interprets unexpected content types in surprising ways.

```go
// RIGHT — enforce content type before decoding
func handleCreateOrder(w http.ResponseWriter, r *http.Request) {
    ct := r.Header.Get("Content-Type")
    if !strings.HasPrefix(ct, "application/json") {
        http.Error(w, "unsupported media type", http.StatusUnsupportedMediaType)
        return
    }
    // ... rest of handler
}
```

## The Gotchas

**Validation at the wrong layer.** I see teams put validation in the service layer, deep inside business logic. By then the HTTP handler has already logged the request, potentially stored a draft, or sent a downstream RPC. Validate at the edge — in the handler, before anything else happens.

**Unicode normalization.** Two strings can look identical in a UI but be different byte sequences — U+0041 (Latin A) and U+FF21 (Fullwidth Latin A). If you validate a username as alphanumeric using `unicode.IsLetter`, both pass. But they'll map to different database rows. Normalize to NFC before validation if your application cares about string uniqueness.

**`r.Body` size limits.** By default, `r.Body` is unbounded. A client that streams 10 GB of JSON into your server will happily fill your memory before `json.Decoder` returns an error. Wrap the body:

```go
// RIGHT — cap body size before reading
r.Body = http.MaxBytesReader(w, r.Body, 1<<20) // 1 MB limit
```

**Validation is not sanitization.** Some teams sanitize HTML in user-submitted text before storing it. That is a separate concern from validation, and it should happen close to the output — not at input time. Validate that the field is a non-empty string within a length limit; decide how to render it safely at output time.

## Key Takeaway

Decode and validate are two separate steps. Decode converts bytes to Go values; validation checks whether those values are acceptable for your application. Skipping validation because the decode succeeded is the root cause of an enormous class of bugs — including the one that cost my team money on our first production incident. Validate every field, every time, at the edge.

---

**Go Security in Production**

Next: [Lesson 2: Secret Handling — Env vars are not a vault](/post/go/go-sec-secrets/)
