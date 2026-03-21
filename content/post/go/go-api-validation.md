---
title: "Lesson 3: Request Validation — Never trust the caller"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 3
keywords: [Go, golang, API, backend, validation, request validation, input sanitization]
tags: [Go tutorial, golang, backend]
date: '2024-08-08T00:00:00.000Z'
---

I once shipped an endpoint that accepted a `limit` query parameter for pagination. The valid range was 1–100. I did not validate it. Someone called it with `limit=-1` and my database query returned every row in the table. The query took 45 seconds and brought the service to its knees. The fix was a two-line bounds check that I should have written from the start.

Validation is not optional. It is the contract between your API and the outside world.

## The Problem

JSON decoding succeeds even when the decoded struct is semantically invalid. An empty string where a name is required, a negative number where a positive one is expected, an email with no `@` — `json.Decoder` does not care. By the time execution reaches your business logic, the data has already been accepted as if it were legitimate.

The other half of the problem is where validation lives. Scattering validation checks across handler bodies, service methods, and repository calls leads to inconsistent behaviour and makes it impossible to return useful error messages. A caller who sends a request with three invalid fields should receive a response that identifies all three — not just the first one your code happens to check.

## The Idiomatic Way

Go does not have a built-in validation framework, and that is a feature. Validation is just logic. Write it explicitly where it belongs: between decoding and processing.

Start with a `Validate()` method on your request types:

```go
package api

import (
    "errors"
    "fmt"
    "net/mail"
    "strings"
)

// ValidationErrors collects all field errors rather than stopping at the first
type ValidationErrors map[string]string

func (v ValidationErrors) Error() string {
    msgs := make([]string, 0, len(v))
    for field, msg := range v {
        msgs = append(msgs, fmt.Sprintf("%s: %s", field, msg))
    }
    return strings.Join(msgs, "; ")
}

type CreateUserRequest struct {
    Name     string `json:"name"`
    Email    string `json:"email"`
    Age      int    `json:"age"`
    Role     string `json:"role"`
}

func (r CreateUserRequest) Validate() error {
    errs := make(ValidationErrors)

    if strings.TrimSpace(r.Name) == "" {
        errs["name"] = "required"
    } else if len(r.Name) > 100 {
        errs["name"] = "must be 100 characters or fewer"
    }

    if strings.TrimSpace(r.Email) == "" {
        errs["email"] = "required"
    } else if _, err := mail.ParseAddress(r.Email); err != nil {
        errs["email"] = "must be a valid email address"
    }

    if r.Age < 0 || r.Age > 150 {
        errs["age"] = "must be between 0 and 150"
    }

    validRoles := map[string]bool{"admin": true, "user": true, "viewer": true}
    if !validRoles[r.Role] {
        errs["role"] = fmt.Sprintf("must be one of: admin, user, viewer")
    }

    if len(errs) > 0 {
        return errs
    }
    return nil
}
```

Then in the handler, decode and validate as a two-step gate before any business logic runs:

```go
func (s *Server) handleCreateUser(w http.ResponseWriter, r *http.Request) {
    var req CreateUserRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        writeError(w, http.StatusBadRequest, "invalid JSON", nil)
        return
    }

    if err := req.Validate(); err != nil {
        var ve ValidationErrors
        if errors.As(err, &ve) {
            writeValidationError(w, ve)
            return
        }
        writeError(w, http.StatusBadRequest, "validation failed", nil)
        return
    }

    // From here on, req is guaranteed to be valid
    user, err := s.users.Create(r.Context(), req)
    if err != nil {
        writeError(w, http.StatusInternalServerError, "failed to create user", nil)
        return
    }
    writeJSON(w, http.StatusCreated, user)
}

func writeValidationError(w http.ResponseWriter, ve ValidationErrors) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusUnprocessableEntity)
    json.NewEncoder(w).Encode(map[string]any{
        "error":  "validation failed",
        "fields": ve,
    })
}
```

The response for a bad request now looks like:

```json
{
  "error": "validation failed",
  "fields": {
    "email": "must be a valid email address",
    "role": "must be one of: admin, user, viewer"
  }
}
```

That is a response a client can actually act on.

## In The Wild

Query parameter validation deserves the same rigor as body validation. Here is a reusable helper I use for numeric query params:

```go
// QueryInt reads a query parameter as an int with bounds checking.
// Returns defaultVal if the parameter is absent.
// Returns an error if the value is present but invalid or out of range.
func QueryInt(r *http.Request, key string, defaultVal, min, max int) (int, error) {
    raw := r.URL.Query().Get(key)
    if raw == "" {
        return defaultVal, nil
    }
    n, err := strconv.Atoi(raw)
    if err != nil {
        return 0, fmt.Errorf("%s must be an integer", key)
    }
    if n < min || n > max {
        return 0, fmt.Errorf("%s must be between %d and %d", key, min, max)
    }
    return n, nil
}

func (s *Server) handleListUsers(w http.ResponseWriter, r *http.Request) {
    errs := make(ValidationErrors)

    limit, err := QueryInt(r, "limit", 20, 1, 100)
    if err != nil {
        errs["limit"] = err.Error()
    }

    offset, err := QueryInt(r, "offset", 0, 0, 1_000_000)
    if err != nil {
        errs["offset"] = err.Error()
    }

    if len(errs) > 0 {
        writeValidationError(w, errs)
        return
    }

    users, err := s.users.List(r.Context(), limit, offset)
    if err != nil {
        writeError(w, http.StatusInternalServerError, "failed to list users", nil)
        return
    }
    writeJSON(w, http.StatusOK, users)
}
```

This pattern — collect all errors first, then gate on the collection — means callers always get a complete picture of what went wrong rather than playing whack-a-mole with one error at a time.

## The Gotchas

**Validating before sanitising.** If you normalise data (trim whitespace, lowercase email) before validating, your validation reflects what you will actually store. If you validate first and then sanitise, a value like `" admin "` might pass the role check but fail later because you trimmed it to `"admin"` which is actually valid. Normalise first, validate second.

**JSON decode does not enforce required fields.** If the caller omits `"name"` entirely (not `"name": ""`), `json.Decoder` will leave the field at its zero value — an empty string. Your `Validate()` method must explicitly check for the zero value if the field is required. Go does not distinguish "field not present" from "field present and empty" unless you use a pointer (`*string`) and check for nil.

**`422 Unprocessable Entity` vs `400 Bad Request`.** I use `400` for malformed JSON (the server could not parse the request) and `422` for validation errors (the server parsed the request but the data is semantically invalid). This distinction is semantically meaningful and helps clients decide how to respond programmatically.

**Do not validate inside your domain/service layer.** Validation is a boundary concern. It belongs in the HTTP layer, not in the service that orchestrates business logic. If your service has a `CreateUser` method that also validates, you end up with two validation passes and two sets of error messages. Put validation at the entry point and trust it downstream.

## Key Takeaway

Validation is not a chore to be deferred. Every endpoint that accepts input needs an explicit validation step that runs before any business logic, collects all errors rather than stopping at the first, and returns a machine-readable error structure that clients can act on. Write `Validate()` methods on your request types. Write helpers for common checks like bounded integers. Use `422` for semantic failures and `400` for parse failures. The fifteen minutes you spend on validation today will save hours of debugging production incidents later.

---

**Series: Go API and Service Design**

[← Lesson 2: Middleware Patterns](/post/go/go-api-middleware) | [Lesson 4: Error Responses →](/post/go/go-api-error-responses)
