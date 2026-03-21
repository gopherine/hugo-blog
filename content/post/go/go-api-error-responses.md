---
title: "Lesson 4: Error Responses — Your API errors are your documentation"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 4
keywords: [Go, golang, API, backend, error handling, error responses, HTTP errors]
tags: [Go tutorial, golang, backend]
date: '2024-09-20T00:00:00.000Z'
---

I once integrated with an API that returned HTTP 200 for everything — successes and failures alike. The actual outcome was buried in a `status` field inside the JSON body. To know whether a request worked, you had to parse the response, check `status`, then switch on a string value that was inconsistently named across endpoints. Integrating with that API felt like defusing a bomb in the dark.

How you design error responses is not a detail. It is part of your public interface.

## The Problem

Error responses in HTTP APIs fail in predictable ways. The common failure modes are: using the wrong status code (returning `200` with `{"error": "not found"}` in the body), returning HTML error pages instead of JSON when something breaks, returning different error shapes from different endpoints, and providing error messages that are useful for debugging but harmful to expose to clients.

Each failure makes the API harder to integrate with. SDKs become unpredictable. Retry logic breaks. Monitoring that alerts on HTTP status codes misses application-level failures. And error messages that leak internal details — stack traces, SQL queries, internal hostnames — are a security concern.

## The Idiomatic Way

Define a canonical error structure and use it everywhere. Here is the shape I use across projects:

```go
package api

import (
    "encoding/json"
    "net/http"
)

// APIError is the canonical error response envelope
type APIError struct {
    // Code is a machine-readable error identifier (e.g. "user_not_found")
    Code    string         `json:"code"`
    // Message is a human-readable summary safe to show in a UI
    Message string         `json:"message"`
    // Details contains field-level errors for validation failures
    Details map[string]string `json:"details,omitempty"`
    // RequestID helps correlate the response with server logs
    RequestID string        `json:"request_id,omitempty"`
}

func WriteError(w http.ResponseWriter, r *http.Request, status int, code, message string, details map[string]string) {
    resp := APIError{
        Code:      code,
        Message:   message,
        Details:   details,
        RequestID: RequestIDFromContext(r.Context()),
    }
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    json.NewEncoder(w).Encode(resp)
}
```

Now every error your API ever returns has the same shape. A `404` looks like:

```json
{
  "code": "user_not_found",
  "message": "No user exists with that ID.",
  "request_id": "7f3a2c1b-..."
}
```

A `422` with validation failures looks like:

```json
{
  "code": "validation_failed",
  "message": "The request contains invalid fields.",
  "details": {
    "email": "must be a valid email address",
    "age": "must be between 0 and 150"
  },
  "request_id": "9d1e4f2a-..."
}
```

The `code` field is what clients switch on. The `message` field is what users see. The `details` field contains structured field errors. The `request_id` ties a user complaint to a specific server log line.

## In The Wild

Mapping internal Go errors to HTTP responses is where this pattern does the most work. I define application error types that carry their HTTP status code with them:

```go
package apperr

import "net/http"

type AppError struct {
    HTTPStatus int
    Code       string
    Message    string
    Cause      error // never exposed to the client
}

func (e *AppError) Error() string { return e.Message }
func (e *AppError) Unwrap() error  { return e.Cause }

// Constructors for common cases
func NotFound(resource string) *AppError {
    return &AppError{
        HTTPStatus: http.StatusNotFound,
        Code:       resource + "_not_found",
        Message:    "The requested " + resource + " could not be found.",
    }
}

func Conflict(resource string) *AppError {
    return &AppError{
        HTTPStatus: http.StatusConflict,
        Code:       resource + "_already_exists",
        Message:    "A " + resource + " with that identifier already exists.",
    }
}

func Internal(cause error) *AppError {
    return &AppError{
        HTTPStatus: http.StatusInternalServerError,
        Code:       "internal_error",
        Message:    "An unexpected error occurred. Please try again later.",
        Cause:      cause,
    }
}
```

The handler can then use `errors.As` to detect application errors and convert them to the right HTTP response:

```go
func (s *Server) handleGetUser(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")
    user, err := s.users.Get(r.Context(), id)
    if err != nil {
        var appErr *apperr.AppError
        if errors.As(err, &appErr) {
            WriteError(w, r, appErr.HTTPStatus, appErr.Code, appErr.Message, nil)
            return
        }
        // Unexpected error — log it, return generic 500
        s.logger.Error("unhandled error in handleGetUser", "error", err)
        WriteError(w, r, http.StatusInternalServerError,
            "internal_error", "An unexpected error occurred.", nil)
        return
    }
    writeJSON(w, http.StatusOK, user)
}
```

With this in place, your service layer can return domain errors, your handler layer converts them to HTTP, and the client always receives a consistent structure.

```go
// In the service layer — no HTTP dependency
func (s *UserService) Get(ctx context.Context, id string) (*User, error) {
    user, err := s.repo.FindByID(ctx, id)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, apperr.NotFound("user")
    }
    if err != nil {
        return nil, apperr.Internal(err)
    }
    return user, nil
}
```

## The Gotchas

**Never return raw errors from the service layer as HTTP responses.** A raw `err.Error()` string might say `pq: duplicate key value violates unique constraint "users_email_key"`. That leaks your database schema, your ORM, and your column names. Map errors at the HTTP boundary to client-safe messages.

**Status codes communicate semantics.** `400 Bad Request` means the client sent something malformed. `401 Unauthorized` means the client is not authenticated. `403 Forbidden` means they are authenticated but lack permission. `409 Conflict` means a resource-level conflict (e.g. duplicate email). `422 Unprocessable Entity` means the request was parseable but semantically invalid. `429 Too Many Requests` means rate limited. Using the right code lets clients act intelligently without parsing the body.

**Log the cause, return the message.** For internal errors, always log the actual underlying error (the database error, the network error, the unexpected state) but return a generic message to the client. The `Cause` field in `AppError` is never serialised to JSON — it exists only for your logger.

**Include a request ID in every error.** When a user emails support saying "I got an error," the first thing you want to do is find the log entry. If every error response includes a `request_id` that matches your log lines, that search takes two seconds instead of twenty minutes.

## Key Takeaway

Treat error responses as a first-class part of your API contract. Define one canonical error structure and use it everywhere. Separate machine-readable error codes from human-readable messages. Map application-layer errors to HTTP status codes at the HTTP boundary. Log the cause, return the message. And always include a request ID. An API with good error responses tells its integrators exactly what went wrong and exactly how to fix it — which is far more valuable than documentation that only describes the happy path.

---

**Series: Go API and Service Design**

[← Lesson 3: Request Validation](/post/go/go-api-validation) | [Lesson 5: Pagination Done Right →](/post/go/go-api-pagination)
