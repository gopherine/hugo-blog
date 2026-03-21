---
title: 'Lesson 5: CI/CD for Go — GitHub Actions that actually catch bugs'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 5
keywords:
  - Go
  - golang
  - CI/CD
  - GitHub Actions
  - testing
  - linting
  - golangci-lint
  - continuous integration
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2024-12-15T00:00:00.000Z'
---

I've set up CI pipelines for Go services about a dozen times, and every iteration taught me something about what the pipeline should actually catch. The first version ran `go test ./...` and called it done. That caught compilation errors and test failures, but not data races, not staticcheck warnings, not formatting drift, not license issues, not security vulnerabilities in dependencies. Each of those failure categories has caused a production incident at some point. The current version catches most of them before the PR is merged.

A CI pipeline for Go has a natural order of operations: fast checks first, slow checks later, expensive checks only when strictly necessary. You want the feedback loop to be as tight as possible — a developer shouldn't wait 20 minutes to find out their change didn't compile.

## The Problem

The minimal CI is better than nothing but misses entire bug categories:

```yaml
# WRONG — this catches compilation errors and panicking tests, not much else
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.22' }
      - run: go test ./...
```

No formatting check — PRs that mix logic changes with whitespace noise are harder to review. No vet — catches common mistakes the compiler doesn't. No linting — misses entire classes of bugs. No race detection — data races ship to production. No dependency vulnerability scanning — known CVEs in your dependencies go unnoticed.

## The Idiomatic Way

Here's the pipeline I reach for on new Go projects:

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

# Cancel in-progress runs for the same branch on new pushes.
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  GO_VERSION: '1.22'

jobs:
  # ── Fast checks (< 1 min) ────────────────────────────────────────────────
  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-go@v5
        with:
          go-version: ${{ env.GO_VERSION }}
          cache: true

      # go fmt: fail if any file needs formatting
      - name: Check formatting
        run: |
          if [ "$(gofmt -l . | wc -l)" -gt 0 ]; then
            echo "Files need formatting:"
            gofmt -l .
            exit 1
          fi

      # go vet: catch common mistakes
      - name: go vet
        run: go vet ./...

      # golangci-lint: ~70 linters in one tool
      - uses: golangci/golangci-lint-action@v6
        with:
          version: v1.59
          args: --timeout=5m

  # ── Tests (2-5 min) ──────────────────────────────────────────────────────
  test:
    name: Test
    runs-on: ubuntu-latest
    services:
      # Integration test dependencies declared here
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: testdb
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: ${{ env.GO_VERSION }}
          cache: true

      # Unit + integration tests
      - name: Run tests
        env:
          DATABASE_URL: postgres://postgres:test@localhost:5432/testdb?sslmode=disable
        run: go test -v -count=1 -timeout=10m ./...

      # Race detector — separate job because it's ~3x slower
      - name: Run tests with race detector
        env:
          DATABASE_URL: postgres://postgres:test@localhost:5432/testdb?sslmode=disable
        run: go test -race -count=1 -timeout=10m ./...

      # Coverage report
      - name: Coverage
        env:
          DATABASE_URL: postgres://postgres:test@localhost:5432/testdb?sslmode=disable
        run: |
          go test -coverprofile=coverage.out -covermode=atomic ./...
          go tool cover -func=coverage.out | tail -1

  # ── Security (3-5 min) ───────────────────────────────────────────────────
  security:
    name: Security
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: ${{ env.GO_VERSION }}
          cache: true

      # govulncheck: check dependencies against Go vulnerability database
      - name: Install govulncheck
        run: go install golang.org/x/vuln/cmd/govulncheck@latest

      - name: Run govulncheck
        run: govulncheck ./...

  # ── Build (1-2 min) ──────────────────────────────────────────────────────
  build:
    name: Build
    runs-on: ubuntu-latest
    needs: [lint, test]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: ${{ env.GO_VERSION }}
          cache: true

      - name: Build static binary
        run: |
          CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
            -ldflags="-s -w -X main.version=${{ github.sha }}" \
            -o dist/myapp \
            ./cmd/myapp

      # Verify the binary is actually static
      - name: Verify static binary
        run: |
          if ldd dist/myapp 2>&1 | grep -v "not a dynamic executable"; then
            echo "ERROR: binary has unexpected dynamic dependencies"
            exit 1
          fi
```

The `golangci-lint` configuration deserves its own file — it's where you tune which linters run:

```yaml
# .golangci.yml
linters:
  enable:
    - errcheck        # ensure errors are checked
    - staticcheck     # advanced static analysis
    - gosimple        # simplifications
    - govet           # same as go vet
    - ineffassign     # detect unused variable assignments
    - unused          # unused code
    - goimports       # import ordering
    - revive          # opinionated style linter
    - bodyclose       # ensure http response bodies are closed
    - noctx           # detect missing context in HTTP calls
    - sqlcloserows    # ensure sql.Rows are closed

linters-settings:
  errcheck:
    # Don't require checking errors on fmt.Fprintf to stderr
    exclude-functions:
      - fmt.Fprint
      - fmt.Fprintf
      - fmt.Fprintln
  revive:
    rules:
      - name: exported
        arguments: ["checkPrivateReceivers"]

issues:
  exclude-rules:
    # Allow unkeyed struct literals in test files for table-driven tests
    - path: _test\.go
      linters: [govet]
      text: "composites"
```

## In The Wild

A build matrix ensures the code compiles across Go versions. If your service supports multiple Go minor versions — common in libraries — test them all:

```yaml
strategy:
  matrix:
    go: ['1.21', '1.22', '1.23']
```

For services that deploy Docker images, trigger a build and push on merge to main:

```yaml
  # ── Docker (on main only) ──────────────────────────────────────────────
  docker:
    name: Docker Build & Push
    runs-on: ubuntu-latest
    needs: [lint, test, security]
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          push: true
          platforms: linux/amd64,linux/arm64
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          build-args: |
            VERSION=${{ github.sha }}
```

The `cache-from: type=gha` / `cache-to: type=gha` uses GitHub Actions cache for Docker layer caching — dramatically speeds up repeated builds.

## The Gotchas

**`-count=1` prevents test caching.** By default, Go caches test results and won't rerun a test if the inputs haven't changed. In CI, you always want to rerun tests. Add `-count=1` to disable caching.

**Race detector changes timings.** Tests that pass without `-race` can fail with it because the race detector slows execution by 2-20x, sometimes exposing timing-dependent bugs. If a test is flaky only with `-race`, that's a bug in the test or the production code — don't suppress it.

**`govulncheck` vs `nancy` vs `osv-scanner`.** The Go vulnerability database (`golang.org/x/vuln`) is the authoritative source for Go-specific CVEs. It's maintained by the Go team and checks whether your code actually calls the vulnerable function, not just whether the vulnerable package is in your dependency tree. This reduces false positives significantly compared to generic scanners.

**Don't cache `go env GOMODCACHE` incorrectly.** The `actions/setup-go@v5` with `cache: true` handles module caching correctly. Don't add a separate `actions/cache` step for the module cache — they'll conflict.

## Key Takeaway

A Go CI pipeline has five layers: format check, vet, lint, tests with race detector, and vulnerability scan. Each layer catches bugs that the others miss. Keep fast checks early so failures are reported in under a minute. The race detector and integration tests are slower — run them in parallel with lint so the total wall time stays under five minutes.

---

**Previous:** [Lesson 4: Config Injection](/post/go/go-deploy-config-injection/)
**Next:** [Lesson 6: Race Detector in CI — Run -race on every PR or ship bugs](/post/go/go-deploy-race-ci/)
