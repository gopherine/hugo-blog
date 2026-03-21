---
title: "Lesson 5: Monolith vs Multi-Module — One go.mod or many? It depends."
author: Atharva Pandey
series: "Go Package & Module Architecture"
lesson: 5
keywords: [Go, golang, packages, modules, architecture]
tags: [Go tutorial, golang, architecture]
date: '2024-11-08T00:00:00.000Z'
---

When I first started structuring larger Go projects, I defaulted to what felt natural: one repository, one `go.mod`. It worked for a long time. Then I joined a project with four services in a single repo, each with different dependency requirements, and suddenly the single `go.mod` was pulling in every dependency of every service for every build. Tests for the email service were slow because the `go test` run was loading the ML inference library needed only by the recommendation service. That was when I started thinking seriously about module boundaries, not just package boundaries.

This lesson is about when to use a single module, when to split into multiple modules, and the concrete trade-offs you accept with each approach.

## The Problem

Most Go projects start as a single module: one `go.mod` file at the root, one module path, all packages under that root. This is the right default. It gives you one version to manage, atomic commits across packages, easy refactoring, and zero `replace` directive complexity.

But as a codebase grows, cracks appear. Some packages need to be versioned and released independently. Some have conflicting transitive dependencies. Some contain internal tools or code generators that should not affect the main module's dependency surface. Some are shared libraries used by multiple teams who need independent release cycles.

The question is not "monorepo vs polyrepo." It is: for a given unit of code, does it need an independent version and release lifecycle? If yes, it needs its own `go.mod`. If no, it belongs in the same module.

## The Idiomatic Way

**Wrong: splitting into multiple modules prematurely**

```
myapp/
  go.mod          ← module github.com/yourorg/myapp
  go.sum
  cmd/
    api/
      go.mod      ← module github.com/yourorg/myapp/cmd/api  ← UNNECESSARY
      main.go
  pkg/
    order/
      go.mod      ← module github.com/yourorg/myapp/pkg/order ← UNNECESSARY
      order.go
    billing/
      go.mod      ← module github.com/yourorg/myapp/pkg/billing ← UNNECESSARY
      billing.go
```

This structure means every change requires navigating multiple `go.mod` and `go.sum` files. Refactoring across `order` and `billing` requires cross-module version bumps. There is no independent versioning need here — `api`, `order`, and `billing` all move together. This is the cost of multi-module with none of the benefit.

**Right: single module for a cohesive application**

```
myapp/
  go.mod          ← module github.com/yourorg/myapp
  go.sum
  cmd/
    api/
      main.go
    worker/
      main.go
  internal/
    config/
    testutil/
  order/
    order.go
    service.go
  billing/
    billing.go
    invoice.go
```

One module. All packages share a version. Refactoring is atomic. `go test ./...` runs everything together. This is the right structure for the vast majority of applications.

**Right: multi-module when release cycles genuinely diverge**

Here is a real scenario where multiple modules make sense: you are building a platform SDK that other teams consume, alongside an internal tool that uses the SDK. The SDK needs semantic versioning and stability guarantees. The tool is internal and moves fast. They should be separate modules.

```
platform/
  sdk/
    go.mod      ← module github.com/yourorg/platform/sdk
    go.sum
    client.go
    models.go
  tools/
    go.mod      ← module github.com/yourorg/platform/tools
    go.sum
    codegen/
      main.go
  internal-app/
    go.mod      ← module github.com/yourorg/platform/internal-app
    go.sum
    main.go
```

```go
// sdk/go.mod — SDK has a stable, versioned release cycle
module github.com/yourorg/platform/sdk

go 1.22

require (
    github.com/go-resty/resty/v2 v2.12.0
)
```

```go
// tools/go.mod — tools can use bleeding-edge dependencies without affecting SDK consumers
module github.com/yourorg/platform/tools

go 1.22

require (
    github.com/yourorg/platform/sdk v1.3.0  // pinned SDK version
    golang.org/x/tools v0.20.0
    github.com/dave/jennifer v1.7.0
)
```

```go
// internal-app/go.mod — app depends on SDK separately, can upgrade on its own schedule
module github.com/yourorg/platform/internal-app

go 1.22

require (
    github.com/yourorg/platform/sdk v1.2.1  // different SDK version from tools, that's fine
    github.com/some/heavy-ml-library v3.0.0 // would pollute SDK if in same module
)
```

Each module has an independent `go.sum`, independent dependencies, and independent release tags (`sdk/v1.3.0`, not a repo-level tag that would version everything together).

**Right: the workspace workaround during development**

The problem with multi-module repos during active development: if you are changing `sdk` and `internal-app` simultaneously, you would normally need a `replace` directive in `internal-app/go.mod` to point to your local `sdk`. This is messy and easy to accidentally commit. Lesson 7 covers `go.work` in full, but here is the minimal picture:

```go
// go.work — at the repo root, NOT committed to version control in most cases
go 1.22

use (
    ./sdk
    ./tools
    ./internal-app
)
```

With this file present, the Go toolchain treats all three modules as if they reference each other locally, with no `replace` directives needed in any `go.mod`.

**Right: when to put a module in a sub-directory of a repo**

The standard library itself provides a model for this. `golang.org/x/tools` and `golang.org/x/net` are separate modules, each in their own repository. But `golang.org/x/tools/gopls` was historically in the same repository as `golang.org/x/tools` with its own `go.mod` because `gopls` has a dramatically different (and much larger) dependency set than the rest of `x/tools`. Same repo, different module boundary, legitimate reason.

Ask yourself:
- Does this code need to be independently versioned and tagged?
- Does this code have dependencies that would bloat consumers who do not need them?
- Is this code consumed by external parties who need a stable API contract?

If any answer is yes, a separate `go.mod` is justified.

## In The Wild

The HashiCorp Terraform provider ecosystem is a good example of multi-module done right. Each provider is its own module. They can release independently, depend on different versions of the Terraform plugin SDK, and be maintained by different teams. Putting them all in one module would make every provider release trigger a version bump for every other provider.

On the opposite end, Kubernetes itself — millions of lines of Go — was historically one giant module. The `k8s.io/api`, `k8s.io/client-go`, `k8s.io/apimachinery` split into separate modules happened late and deliberately, driven by external consumer needs (you might want `client-go` without pulling in the entire Kubernetes codebase as a dependency).

In my own work, I start with one module, always. The bar for adding a second module is: does something outside my organization need to import this at a pinned version without pulling in everything else?

## The Gotchas

**Gotcha 1: using multi-module to enforce boundaries you should enforce with `internal/`.** If your motivation is "I don't want team B touching my code," `internal/` is the right tool. Module boundaries are about versioning and dependency surfaces, not access control between internal teams.

**Gotcha 2: forgetting that multi-module means multi-`go.sum`.** Each module maintains its own dependency graph. A security fix in a shared library requires updating and committing `go.sum` in every module that depends on it. With ten modules, that is ten updates. With a single module, it is one.

**Gotcha 3: tagging versions wrong in multi-module repos.** If you have a `sdk/` sub-directory module and you tag `v1.3.0` on the repo, the Go module proxy treats this as a tag for the root module, not for `sdk/`. Sub-directory modules need tags in the format `sdk/v1.3.0`. This trips up almost everyone the first time.

**Gotcha 4: circular dependencies between modules.** Module-level cycles are even harder to fix than package-level cycles because each module has its own versioning. Design the dependency order of your modules before you create them. It is much harder to untangle later.

## Key Takeaway

Start with one module. Almost every application that starts as a single cohesive thing should stay as a single module for its entire life. Add a second module when you have a clear, unavoidable reason: independent versioning, conflicting dependency requirements, or a shared library with external consumers.

The engineering cost of multi-module is real: more `go.sum` files to maintain, more version tags to manage, more cognitive overhead when reading `require` blocks. Make sure the benefits justify that cost before you commit to the split.

---

**Series: Go Package & Module Architecture**

- [Lesson 1: Package Boundaries](/post/go/go-pkg-boundaries/)
- [Lesson 2: Avoiding Cyclic Dependencies](/post/go/go-pkg-cyclic-deps/)
- [Lesson 3: internal/ Usage Patterns](/post/go/go-pkg-internal/)
- [Lesson 4: Interface Placement](/post/go/go-pkg-interface-placement/)
- Lesson 5: Monolith vs Multi-Module — *you are here*
- [Lesson 6: Dependency Direction](/post/go/go-pkg-dependency-direction/)
- [Lesson 7: go.work and Workspace Mode](/post/go/go-pkg-workspaces/)
