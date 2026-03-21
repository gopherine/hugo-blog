---
title: "Lesson 7: go.work and Workspace Mode — Developing multiple modules without replace hacks"
author: Atharva Pandey
series: "Go Package & Module Architecture"
lesson: 7
keywords: [Go, golang, packages, modules, architecture]
tags: [Go tutorial, golang, architecture]
date: '2025-02-25T00:00:00.000Z'
---

Before `go.work` existed, developing across multiple local Go modules was genuinely painful. You were working on a library in one directory and an application that depended on it in another. Every time you changed the library, you had to add a `replace` directive to the application's `go.mod`, run your tests, and then remember — always remember — to remove the `replace` before committing. I have seen `replace` directives committed to production `go.mod` files more times than I would like to admit.

Go 1.18 introduced workspace mode via `go.work`, and it solved this problem cleanly. This final lesson in the series covers how workspaces work, when to use them, and the patterns that make them genuinely useful rather than just another layer of configuration.

## The Problem

Consider a realistic scenario: you maintain a `platform/sdk` module consumed by a `platform/app` module. Both live in the same repository. You are adding a new method to the SDK and simultaneously updating the application to use it. Before `go.work`, your workflow looked like this:

```go
// platform/app/go.mod — the "replace hack"
module github.com/yourorg/platform/app

go 1.21

require github.com/yourorg/platform/sdk v1.4.0

// Temporary — DO NOT COMMIT
replace github.com/yourorg/platform/sdk => ../sdk
```

This works mechanically. But `replace` directives are in the committed file. You have to remember to revert them. CI is risky if someone forgets. If you are working across five modules simultaneously, you have five `go.mod` files to edit and un-edit. It is error-prone and tedious.

There is also a subtler problem: `replace` can mask version conflicts. You might be replacing a pinned `v1.4.0` with local code that is actually at `v1.5.0-dev`, and the `go.sum` does not capture the local version at all.

## The Idiomatic Way

**Wrong: the replace hack — fragile and commit-hazardous**

```go
// app/go.mod — do NOT do this for local development
module github.com/yourorg/myapp

go 1.22

require (
    github.com/yourorg/mylib v0.3.1
)

// This replace directive points to local disk.
// It CANNOT be committed. It will break anyone else's build.
// It is invisible to go.sum verification.
replace github.com/yourorg/mylib => /Users/atharva/projects/mylib
```

The moment this lands in version control, every developer without the exact same local path gets a broken build. CI fails. Remote colleagues are confused. The `replace` directive is a development convenience masquerading as a committed configuration.

**Right: go.work at the repo root, never committed to version control**

```bash
# Create a workspace covering both modules
cd /Users/atharva/projects/platform
go work init ./sdk ./app
```

This generates a `go.work` file:

```go
// go.work — lives at the repo root, typically in .gitignore
go 1.22

use (
    ./sdk
    ./app
)
```

Now `go.mod` files stay clean:

```go
// sdk/go.mod — no replace directives, clean and committable
module github.com/yourorg/platform/sdk

go 1.22

require (
    github.com/go-resty/resty/v2 v2.12.0
)
```

```go
// app/go.mod — depends on the published SDK version, no local overrides
module github.com/yourorg/platform/app

go 1.22

require (
    github.com/yourorg/platform/sdk v1.4.0
)
```

With `go.work` present, `go build`, `go test`, and all other toolchain commands automatically prefer the local `./sdk` over the version pinned in `go.mod`. Remove `go.work` (or set `GOWORK=off`) and the toolchain falls back to the published `v1.4.0` exactly as production would.

**Right: adding modules to a workspace dynamically**

```bash
# Add a new module to an existing workspace
go work use ./tools

# Sync go.work.sum after adding modules or changing dependencies
go work sync
```

The workspace also creates a `go.work.sum` file, analogous to `go.sum`, but for the workspace-level dependency graph. This file should be committed if you want reproducible workspace builds across the team.

**Right: a practical multi-service workspace for local development**

This is the pattern I use in practice when developing microservices that share a common library:

```
platform/
  go.work           ← NOT in version control (or in .gitignore)
  go.work.sum       ← IN version control if team shares workspace
  shared/
    go.mod          ← module github.com/yourorg/platform/shared
    config.go
    errors.go
  service-a/
    go.mod          ← module github.com/yourorg/platform/service-a
    main.go
  service-b/
    go.mod          ← module github.com/yourorg/platform/service-b
    main.go
  tools/
    go.mod          ← module github.com/yourorg/platform/tools
    gen/
      main.go
```

```go
// go.work
go 1.22

use (
    ./shared
    ./service-a
    ./service-b
    ./tools
)
```

With this in place, making a change to `shared/config.go` is immediately visible when you run `go test ./...` from `service-a/` or `service-b/`. No version bumps needed during development. No `replace` directives. When you are ready to release, you tag `shared` at the new version, update `require` in the service `go.mod` files, and remove the workspace from consideration for the published artifact.

**Right: using GOWORK=off for CI verification**

One of the most important workspace patterns is also the one that is easiest to miss: always test the publishable state of your modules in CI by disabling the workspace.

```yaml
# .github/workflows/ci.yml (excerpt)
jobs:
  test-with-workspace:
    name: Test (workspace mode)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - run: go test ./...

  test-without-workspace:
    name: Test (published versions)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - run: GOWORK=off go test ./...
        working-directory: ./app
```

The first job tests your current state across all modules together. The second job tests whether `app` builds correctly against the published `sdk` version, catching the case where you have changed `sdk` locally but forgotten to tag and release it before updating `app/go.mod`.

## In The Wild

The Go standard library development itself uses workspace concepts internally. The `golang.org/x/` family of modules — `x/tools`, `x/net`, `x/sys`, and others — are developed with workspaces to allow cross-module changes without the release-before-use friction that `replace` used to require.

The popular `buf` CLI (Protocol Buffer tooling for Go) maintains a multi-module repository where the CLI module, the generated SDK, and various plugins are separate modules developed together. Their workspace setup means contributors can modify a plugin and the CLI that uses it in a single pull request without the old dance of updating `replace` directives.

In practice, I have found that the biggest productivity win from `go.work` is not the local development ergonomics (though that is significant) — it is being able to run `go test ./...` from the workspace root and see all failures across all modules at once. The workspace gives you a single pane of glass for the whole repository.

## The Gotchas

**Gotcha 1: committing go.work with absolute paths.** `go work init` uses whatever paths you specify. If you use absolute paths (`go work init /Users/atharva/projects/platform/sdk`), the `go.work` file becomes machine-specific and breaks for everyone else. Always use relative paths (`./sdk`).

**Gotcha 2: go.work masking real version incompatibilities.** Because the workspace prefers local code over published versions, you may not notice that `app` requires `sdk@v1.4.0` but your local `sdk` is already at features beyond that. Run `GOWORK=off go mod tidy` in each module periodically to keep published version requirements honest.

**Gotcha 3: forgetting go.work.sum in version control.** `go.work.sum` contains checksums for workspace-mode dependencies. If your team is all using the workspace, committing `go.work.sum` ensures everyone gets the same checksum validation. Leaving it out means each developer's first `go build` downloads and checksums independently.

**Gotcha 4: using workspaces as a permanent substitute for publishing.** Workspaces are for development. They are not a way to avoid tagging releases. If `service-a` depends on `shared` in production, `shared` needs a published version. The workspace is scaffolding for development; `go.mod` with proper `require` versions is the permanent record.

**Gotcha 5: workspace mode and vendoring.** `go mod vendor` operates per-module, not per-workspace. If you vendor dependencies in a workspace setup, you need to run `go mod vendor` in each module separately. Workspace mode and vendoring have limited interoperability, and this is a known rough edge as of Go 1.22.

## Key Takeaway

`go.work` is the right tool for developing multiple related modules simultaneously. It eliminates the `replace` hack, keeps your `go.mod` files clean and committable, and gives you a repository-wide view of your module landscape during development. Use it during development, disable it (`GOWORK=off`) to verify your published module boundaries, and never let it become a substitute for proper versioning and release of your modules.

This closes the series. From package boundaries to workspace mode, the common thread has been the same: Go gives you strong, composable tools for managing code structure. The discipline is in using them intentionally — small packages with clear names, dependency arrows that point inward, interfaces that belong to their consumers, and modules that earn their independence.

---

**Series: Go Package & Module Architecture**

- [Lesson 1: Package Boundaries](/post/go/go-pkg-boundaries/)
- [Lesson 2: Avoiding Cyclic Dependencies](/post/go/go-pkg-cyclic-deps/)
- [Lesson 3: internal/ Usage Patterns](/post/go/go-pkg-internal/)
- [Lesson 4: Interface Placement](/post/go/go-pkg-interface-placement/)
- [Lesson 5: Monolith vs Multi-Module](/post/go/go-pkg-mono-vs-multi/)
- [Lesson 6: Dependency Direction](/post/go/go-pkg-dependency-direction/)
- Lesson 7: go.work and Workspace Mode — *you are here*

---

**Course Complete.** You have reached the end of the Go Package & Module Architecture series. The seven lessons build on each other: clean boundaries prevent cycles, `internal/` enforces encapsulation, interfaces belong to their consumers, module splits earn their complexity, dependency arrows point inward, and workspaces make multi-module development practical. These are not rules to memorize — they are design instincts worth building.
