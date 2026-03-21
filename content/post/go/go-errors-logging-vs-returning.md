---
title: "Lesson 5: Logging vs Returning — Log at the boundary, return everywhere else"
author: Atharva Pandey
series: "Go Error Design at Scale"
lesson: 5
keywords:
  - Go
  - golang
  - error handling
  - error logging
  - structured logging
  - middleware
  - error boundaries
tags:
  - Go tutorial
  - golang
date: '2025-03-17T00:00:00.000Z'
---

There's a smell I encounter in almost every codebase I've reviewed — including ones I wrote early in my Go career. Someone discovers that `err != nil` and, out of an abundance of caution, they log it right there. Then return it. Then the caller logs it again. Then the middleware logs it a third time. By the time a single failed database query reaches the response, it's appeared in the logs four times with slightly different messages and you can't tell whether four things failed or one thing failed four times.

The rule is simple and absolute: **log once, at the top boundary. Return everywhere else.** Understanding why leads you to a much cleaner architecture.

## The Problem

Double-logging (or triple-logging) is what happens when every function that handles an error also logs it before returning it.

```go
// WRONG — logs at every layer, produces duplicate entries
func (r *UserRepo) FindByID(ctx context.Context, id string) (*User, error) {
    var u User
    err := r.db.QueryRowContext(ctx, `SELECT id FROM users WHERE id=$1`, id).Scan(&u.ID)
    if err != nil {
        log.Printf("UserRepo.FindByID error: %v", err) // LOG 1
        return nil, err
    }
    return &u, nil
}

func (s *UserService) GetProfile(ctx context.Context, id string) (*Profile, error) {
    user, err := s.repo.FindByID(ctx, id)
    if err != nil {
        log.Printf("UserService.GetProfile error: %v", err) // LOG 2 — same error
        return nil, err
    }
    return toProfile(user), nil
}

func (h *UserHandler) GetProfile(w http.ResponseWriter, r *http.Request) {
    profile, err := h.svc.GetProfile(r.Context(), r.URL.Query().Get("id"))
    if err != nil {
        log.Printf("UserHandler.GetProfile error: %v", err) // LOG 3 — still same error
        http.Error(w, "error", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(profile)
}
```

You open your log aggregator and search for a user ID that's been reported as broken. You find twelve log lines, all from the same request, all saying slightly different things. You can't correlate them easily without a trace ID. You also can't count error rates — are you seeing 100 errors or 100 occurrences of 1 error?

The second problem is the opposite: logging without returning. The function handles the error locally (logs it) and then continues as if nothing happened, usually returning a zero value or `nil` that causes a panic or silent misbehavior later.

```go
// WRONG — log without returning, execution continues with bad state
func loadConfig(path string) *Config {
    data, err := os.ReadFile(path)
    if err != nil {
        log.Printf("loadConfig: %v", err) // logs it...
        // but doesn't return! falls through to json.Unmarshal with nil data
    }

    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil { // panics if data is nil
        log.Printf("loadConfig parse: %v", err)
    }
    return &cfg // returns zero-value config silently
}
```

This kind of code is dangerous because it looks like it handles the error — there's a log line right there — but the execution path after the log is corrupted.

## The Idiomatic Way

Return errors all the way up. Log exactly once, at the outermost boundary — which in a web service is usually the middleware or the handler, and in a background worker is the job runner.

```go
// RIGHT — return all the way up, log once at the boundary
func (r *UserRepo) FindByID(ctx context.Context, id string) (*User, error) {
    var u User
    err := r.db.QueryRowContext(ctx, `SELECT id, email FROM users WHERE id=$1`, id).
        Scan(&u.ID, &u.Email)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, ErrNotFound
    }
    if err != nil {
        return nil, fmt.Errorf("find user %s: %w", id, err)
    }
    return &u, nil
}

func (s *UserService) GetProfile(ctx context.Context, id string) (*Profile, error) {
    user, err := s.repo.FindByID(ctx, id)
    if err != nil {
        return nil, err // no logging — just return
    }
    return toProfile(user), nil
}

// The boundary — this is the only place that logs
func (h *UserHandler) GetProfile(w http.ResponseWriter, r *http.Request) {
    id := r.URL.Query().Get("id")
    profile, err := h.svc.GetProfile(r.Context(), id)
    if err != nil {
        if !errors.Is(err, ErrNotFound) {
            // only log errors that aren't expected business conditions
            log.Printf("get profile id=%s: %v", id, err)
        }
        respondError(w, err)
        return
    }
    json.NewEncoder(w).Encode(profile)
}
```

One log line. One place to look. And because the log is at the handler, it can include the request ID, the user ID from the path, the HTTP method — all the context that intermediate layers don't have.

## In The Wild

The cleanest implementation I've settled on uses a structured logger passed through context or injected into the handler, and a shared `respondError` function at the middleware level. Here's the full pattern:

```go
// Structured error response — shared across all handlers
type ErrorResponse struct {
    Error   string `json:"error"`
    Code    string `json:"code,omitempty"`
    TraceID string `json:"trace_id,omitempty"`
}

// Central error responder — called at every handler boundary
func respondError(w http.ResponseWriter, r *http.Request, err error, logger *slog.Logger) {
    traceID := traceIDFromContext(r.Context())

    var ae *AppError
    if errors.As(err, &ae) {
        // only log 5xx — 4xx are expected business conditions, not bugs
        if ae.Kind == KindInternal || ae.Kind == KindTransient {
            logger.Error("handler error",
                "trace_id", traceID,
                "error", err.Error(),
                "kind", ae.Kind,
            )
        }
        status := kindToHTTPStatus(ae.Kind)
        w.Header().Set("Content-Type", "application/json")
        w.WriteHeader(status)
        json.NewEncoder(w).Encode(ErrorResponse{
            Error:   ae.Message,
            Code:    ae.Code,
            TraceID: traceID,
        })
        return
    }

    // Unknown error — definitely log this
    logger.Error("unclassified error",
        "trace_id", traceID,
        "error", err.Error(),
    )
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusInternalServerError)
    json.NewEncoder(w).Encode(ErrorResponse{
        Error:   "internal server error",
        TraceID: traceID,
    })
}

// Middleware — wraps every handler, catches anything that slips through
func ErrorMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            // wrap the response writer to capture the status code
            rw := &responseWriter{ResponseWriter: w, statusCode: http.StatusOK}
            next.ServeHTTP(rw, r)

            if rw.statusCode >= 500 {
                logger.Warn("5xx response",
                    "method", r.Method,
                    "path", r.URL.Path,
                    "status", rw.statusCode,
                    "trace_id", traceIDFromContext(r.Context()),
                )
            }
        })
    }
}
```

Background workers follow the same principle — the job runner is the boundary:

```go
// Worker — returns errors, never logs inside
func (w *EmailWorker) Send(ctx context.Context, job EmailJob) error {
    tmpl, err := w.templates.Get(job.TemplateID)
    if err != nil {
        return fmt.Errorf("send email: get template %s: %w", job.TemplateID, err)
    }

    body, err := renderTemplate(tmpl, job.Data)
    if err != nil {
        return fmt.Errorf("send email: render template: %w", err)
    }

    if err := w.smtp.Send(job.To, body); err != nil {
        return fmt.Errorf("send email to %s: %w", job.To, err)
    }
    return nil
}

// Job runner — the boundary, logs once
func runEmailJob(ctx context.Context, job EmailJob, worker *EmailWorker, logger *slog.Logger) {
    if err := worker.Send(ctx, job); err != nil {
        logger.Error("email job failed",
            "job_id", job.ID,
            "recipient", job.To,
            "error", err.Error(),
        )
        // decide whether to requeue based on error kind
        var ae *AppError
        if errors.As(err, &ae) && ae.Kind == KindTransient {
            requeueJob(job)
        }
    }
}
```

## The Gotchas

**Don't confuse "logging" with "handling."** If you're at a library or package boundary where callers don't have access to logs, consider using structured tracing or passing a logger interface. A function in a shared package shouldn't log to `os.Stderr` — that's the application's job.

**Expected errors aren't log-worthy.** A user requesting a resource that doesn't exist is not an error in the infrastructure sense. Don't log `ErrNotFound` at error level. It's noise that drowns out real alerts. If you need to track not-found rates, use a metric counter — not a log line.

**Use `slog` and structured fields, not `fmt.Sprintf` in log messages.** The point of logging at the boundary is to correlate. If your log line has `trace_id`, `user_id`, `method`, and the full error chain as structured fields, you can filter and aggregate. If it's `log.Printf("error: %v", err)`, you're back to grep-and-pray.

```go
// WRONG — unstructured, hard to query
log.Printf("error handling request for user %s: %v", userID, err)

// RIGHT — structured, queryable, correlatable
logger.Error("request failed",
    "user_id", userID,
    "trace_id", traceID,
    "path", r.URL.Path,
    "error", err.Error(),
    "error_kind", errorKind(err),
)
```

**Don't swallow errors to "avoid noise."** I've seen code that logs and returns nil because "this error isn't important." If you're swallowing errors, you're losing the ability to detect regressions. If an error genuinely doesn't matter, document why — don't just drop it silently.

## Key Takeaway

Return errors all the way to the boundary. Log once, there, with full context — request ID, user ID, path, error chain, error kind. Don't log at intermediate layers. Don't log expected business errors at error severity — use metrics instead. And use structured logging so your log entries are actually queryable when things go wrong at 3am. The log line at the boundary should tell the complete story; it's the only one you need.

---

Previous: [Lesson 4: Operational vs Domain Errors](go-errors-operational-vs-domain.md) | Next: [Lesson 6: Error Boundaries Across Layers — Translate at the border, don't leak internals](go-errors-boundaries.md)
