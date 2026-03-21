---
title: 'Lesson 4: Code Review Heuristics — What to look for in a Go PR'
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 4
keywords:
  - Go
  - golang
  - code review
  - pull request
  - code quality
  - heuristics
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-10-20T00:00:00.000Z'
---

A good code review is not a diff-reading exercise. It's a transfer of understanding — the reviewer asks "do I understand what this code does, why it does it, and what it doesn't do?" If the answer to any of those is no, that's a comment, not a nitpick. I've done hundreds of Go reviews and the feedback I give clusters into the same ten or fifteen patterns so reliably that I eventually wrote them down as a checklist. This lesson is that checklist, with examples.

## The Problem

The failure mode in most code review cultures is spending all the attention on style — variable names, comment formatting, line length — and none on correctness, concurrency safety, or error handling. Linters already catch style. A reviewer's job is to catch the things linters can't.

```go
// This PR passes the linter. It has three bugs. How fast can you find them?
func (s *Server) handleUpload(w http.ResponseWriter, r *http.Request) {
    body, _ := io.ReadAll(r.Body)

    var req UploadRequest
    json.Unmarshal(body, &req)

    go func() {
        if err := s.store.Save(req); err != nil {
            log.Printf("save failed: %v", err)
        }
    }()

    w.WriteHeader(http.StatusOK)
    json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}
```

Bug 1: `io.ReadAll` error is silently discarded with `_`. Body might be truncated. Bug 2: `json.Unmarshal` error is ignored — malformed JSON silently produces a zero-value struct. Bug 3: The goroutine captures `req` by value but `req` contains a slice field that's a reference — if anything modifies `req.Files` after the goroutine starts, it's a data race. Three bugs, zero linter warnings.

## The Idiomatic Way

My review checklist, in rough priority order:

**1. Error handling first.** Scan for every `_` in error position. Every ignored error is a silent failure waiting to surface in production. The bar for ignoring an error should be high and the reasoning should be in a comment.

**2. Goroutine lifetime.** Every `go func()` in a PR gets a question: when does this goroutine exit? What happens if it doesn't? If there's no context cancellation, no `WaitGroup`, and no channel for the caller to block on, that's a goroutine leak candidate.

```go
// RIGHT — goroutine has a bounded lifetime
func (s *Server) handleUpload(w http.ResponseWriter, r *http.Request) {
    body, err := io.ReadAll(r.Body)
    if err != nil {
        http.Error(w, "read body", http.StatusBadRequest)
        return
    }

    var req UploadRequest
    if err := json.Unmarshal(body, &req); err != nil {
        http.Error(w, "invalid JSON", http.StatusBadRequest)
        return
    }

    // Save synchronously or push to a queue with a bounded worker pool
    if err := s.store.Save(r.Context(), req); err != nil {
        http.Error(w, "save failed", http.StatusInternalServerError)
        return
    }

    w.WriteHeader(http.StatusAccepted)
    json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}
```

**3. Context propagation.** Is context being threaded through the call chain? A function that takes a `ctx` parameter but passes `context.Background()` to its callees has broken the propagation chain — cancellations won't propagate.

**4. Resource cleanup.** Every `Open`, `Create`, `Listen`, `Dial`, and `Begin` should be followed within a few lines by a `defer Close()` or equivalent. If the function can return early before the defer, that resource leaks on that path.

```go
// RIGHT — defer is directly after the resource is acquired
func queryUser(ctx context.Context, db *sql.DB, id int64) (*User, error) {
    rows, err := db.QueryContext(ctx, "SELECT id, email FROM users WHERE id = $1", id)
    if err != nil {
        return nil, err
    }
    defer rows.Close() // immediately after — never an early return between these two

    if !rows.Next() {
        return nil, ErrNotFound
    }
    var u User
    return &u, rows.Scan(&u.ID, &u.Email)
}
```

**5. Test quality, not just test presence.** A test that only checks `err == nil` isn't testing behavior. Look for assertions on the actual returned values, tests for error paths, and table-driven tests for functions with multiple input cases.

## In The Wild

On a fintech project, our review process had a rule: no PR that touches a goroutine, a channel, or `sync.*` gets merged without a second reviewer who specifically looks at the concurrency model. Not just "does the code look right" — actually tracing the goroutine lifecycle and checking for data races.

We backed this up with `-race` in CI. Every test run used `go test -race ./...`. The combination of mandated second review and race detector caught four data races before they hit production in six months. One of them was a goroutine writing to a map that the main goroutine was reading — it would have been catastrophic under load.

## The Gotchas

**Reviewing too much at once.** A PR over 400 lines gets rubber-stamped. The important bugs are in the parts reviewers skip because they're fatigued. Enforce small PRs with a hard size limit, or split review into explicit passes: error handling first, then concurrency, then business logic.

**Conflating style with substance.** If you spend ten comments on variable names and zero on the goroutine that leaks on context cancellation, you've done a bad review. Use a linter to automate style so you can spend your attention on correctness.

**Not checking the test file.** The implementation might look fine because the scary logic is hidden in a helper that has no tests. Review the test file alongside the implementation. If the test file is empty or trivial compared to the implementation, that's a finding.

**Missing the deleted code.** New bugs often aren't in added lines — they're in the deleted lines that were providing a safety net. A removed nil check, a removed mutex lock, a removed context timeout. Scan the red lines as carefully as the green ones.

## Key Takeaway

Effective Go code review is a discipline, not a vibe. Work through the checklist: ignored errors, goroutine lifetimes, context propagation, resource cleanup, test quality. Let the linter handle style so you can focus on correctness. Require a second reviewer for concurrency changes. Review deleted code as carefully as added code. A PR that passes all of this deserves your approval — a PR that fails any of it deserves a clear, specific comment that explains the risk, not just the rule.

---

[← Lesson 3: Idiomatic Naming](/post/go/go-quality-naming) | [Course Index](/series/go-code-quality-maintainability) | [Next → Lesson 5: Small Functions Win](/post/go/go-quality-small-functions)
