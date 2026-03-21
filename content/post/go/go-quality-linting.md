---
title: "Lesson 8: Linting with golangci-lint — Automate what reviewers shouldn''t waste time on"
author: Atharva Pandey
series: "Go Code Quality & Maintainability"
lesson: 8
keywords:
  - Go
  - golang
  - linting
  - golangci-lint
  - code quality
  - static analysis
  - CI
tags:
  - Go tutorial
  - golang
  - code quality
date: '2025-06-01T00:00:00.000Z'
---

I made a rule for myself a few years ago: if I leave a code review comment about something a tool could have caught, I've wasted both the author's time and mine. A linter can catch unused variables, missing error checks, shadowed variables, inefficient string concatenation, and dozens of other patterns automatically — in seconds, every commit, without reviewer fatigue. The code review should be about design and correctness, not about whether someone forgot to handle an error returned by `rows.Close()`.

## The Problem

The default `go vet` tooling catches a useful subset of problems but misses the patterns that cause the most real bugs in Go codebases.

```go
// go vet won't catch these — but golangci-lint will
func processRecords(db *sql.DB) error {
    rows, err := db.Query("SELECT id, name FROM records")
    if err != nil {
        return err
    }
    // rows.Close() is missing — resource leak

    for rows.Next() {
        var id int
        var name string
        rows.Scan(&id, &name) // error ignored — silent data corruption

        result := fmt.Sprintf("id:" + strconv.Itoa(id) + " name:" + name) // inefficient
        _ = result
    }
    return nil
}
```

Three problems: missing `rows.Close()`, ignored error from `rows.Scan()`, and string concatenation that should use `fmt.Sprintf` (or better, a strings.Builder). `go vet` passes this. `golangci-lint` with the right linters configured will fail it.

## The Idiomatic Way

Install and run golangci-lint with a curated configuration. The key is choosing linters carefully — enabling all 100+ linters produces so much noise that engineers start ignoring the output.

```yaml
# .golangci.yml — a practical starting configuration
run:
  timeout: 5m
  go: '1.22'

linters:
  disable-all: true
  enable:
    # Correctness
    - errcheck       # catches ignored errors
    - gosimple       # simplification suggestions
    - govet          # standard go vet checks
    - ineffassign    # detect ineffectual assignment
    - staticcheck    # comprehensive static analysis (SA*, S*, QF*)
    - unused         # detect unused code

    # Code quality
    - gocognit       # cognitive complexity
    - gocritic       # various code style and correctness checks
    - revive         # fast, configurable replacement for golint
    - bodyclose      # ensure http response bodies are closed
    - noctx          # find HTTP requests without context

    # Error handling
    - wrapcheck      # ensure errors from external packages are wrapped

linters-settings:
  gocognit:
    min-complexity: 15
  errcheck:
    check-blank: true
  wrapcheck:
    ignorePackageGlobs:
      - "encoding/*"
      - "github.com/pkg/errors"

issues:
  exclude-rules:
    - path: "_test\\.go"
      linters:
        - wrapcheck   # test code doesn't need to wrap errors
```

Running it is straightforward:

```bash
# Run on the entire module
golangci-lint run ./...

# Run only on changed files relative to main (useful in CI for speed)
golangci-lint run --new-from-rev=main ./...

# Run with automatic fix where possible
golangci-lint run --fix ./...
```

**Integrating into CI** — the most important step. A linter that's optional is a linter that gets skipped.

```yaml
# .github/workflows/lint.yml
name: lint
on: [push, pull_request]

jobs:
  golangci:
    name: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - name: golangci-lint
        uses: golangci/golangci-lint-action@v6
        with:
          version: v1.59
          args: --timeout=5m
```

## In The Wild

On a team that was spending about two hours per PR on review comments about error handling and resource cleanup, we introduced golangci-lint with `errcheck`, `bodyclose`, and `staticcheck` enabled. The first week produced 340 lint failures across the codebase — we fixed them over two weeks, each fix a genuine improvement. After that, the average PR dropped to 3–5 lint-related review comments from 12–15. Reviewers started leaving fewer "did you check this error?" comments because the CI run answered that question before the review started.

The fix that felt most impactful: `bodyclose` found 11 places across the codebase where HTTP response bodies weren't closed. Some of these had been in production for over a year, slowly leaking file descriptors under load.

## The Gotchas

**Too many linters creates alarm fatigue.** If CI fails on every PR with 50 warnings, engineers start ignoring the output or gaming the configuration. Start with 5–8 high-signal linters and add more once the baseline is clean.

**Baseline your existing codebase before enforcing.** Running golangci-lint on an existing codebase for the first time will likely produce hundreds of issues. Use `--new-from-rev=HEAD` in CI to enforce linting only on new code while you incrementally fix the existing issues, or run `golangci-lint run --fix` to auto-fix what can be auto-fixed and manually address the rest.

**`//nolint` directives need a reason.** Suppressing a lint failure without a comment explaining why is a code smell of its own. Enforce this with `revive`'s comment policy or just a team norm: every `//nolint` needs a `// reason:` annotation.

**`staticcheck` SA warnings are near-infallible.** When `staticcheck` says SA4006 (a value is assigned but never used), it's right. When it says SA1019 (deprecated API usage), it's right. These are not opinion — they're correctness findings. Treat them as bugs, not style suggestions.

## Key Takeaway

🎓 **Course Complete!** You've finished "Go Code Quality & Maintainability."

golangci-lint is the automation layer that enforces the standards this course has described — consistent naming, small functions, handled errors, closed resources — without relying on reviewer memory or reviewer time. Configure it with a curated set of high-signal linters, enforce it in CI from day one (or baseline existing issues and enforce on new code), and treat its findings as bugs rather than suggestions. The investment is a one-time configuration cost that pays dividends on every PR for the life of the project. The review time you save is better spent on architecture, design, and the correctness problems no tool can catch yet.

---

[← Lesson 7: Kill the Utils Package](/post/go/go-quality-kill-utils) | [Course Index](/series/go-code-quality-maintainability)
