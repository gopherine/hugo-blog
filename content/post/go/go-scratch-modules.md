---
title: "Lesson 17: Go Modules — Managing dependencies without pain"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 17
keywords: [Go, golang, beginner, modules, go mod, go.mod, go.sum, go get, dependencies, semantic versioning]
tags: [Go tutorial, golang, beginner]
date: '2024-04-02T00:00:00.000Z'
---

Before Go modules existed, managing dependencies was genuinely painful. People used all sorts of third-party tools with their own conventions, and getting a new contributor up and running on a project could eat half a day. I started learning Go after modules became the standard, and I took for granted how smooth the experience was — until I read the old blog posts describing what came before. It made me appreciate `go mod init` in a way that's hard to describe.

If you've been following this series, you've been writing programs that only use the standard library. That's fine for learning, but real-world Go programs almost always pull in at least a few external packages. Modules are how Go tracks, downloads, and verifies those dependencies. In this lesson we'll go from "what even is a module?" all the way to running a project with external code.

---

## The Basics

### What is a module?

A module is a collection of related Go packages that are versioned and distributed together. Every Go project that uses modules has a `go.mod` file at its root. That file defines the module's identity and lists its dependencies.

Think of a module as the equivalent of `package.json` in Node or `Cargo.toml` in Rust — it's the manifest that says "here is my project, here is what it needs."

### `go mod init`

You create a new module with:

```
go mod init example.com/myapp
```

The argument is the *module path* — a unique identifier for your module. For public code, this is typically a URL like `github.com/yourusername/yourrepo`. For private or local-only projects, anything descriptive works. Pick something and be consistent.

After running this, you'll have a `go.mod` file:

```
module example.com/myapp

go 1.22
```

That `go 1.22` line tells the toolchain which minimum Go version this module requires. It also affects a few language semantics, so keep it accurate.

### The `go.mod` file

Let's say you add a dependency. After running `go get github.com/fatih/color`, your `go.mod` will look like:

```
module example.com/myapp

go 1.22

require (
    github.com/fatih/color v1.16.0
    github.com/mattn/go-colorable v0.1.13 // indirect
    github.com/mattn/go-isatty v0.0.20 // indirect
)
```

The `require` block lists your direct dependency (`color`) and its transitive dependencies marked `// indirect` — packages that `color` itself needs. You didn't add those manually; Go discovered and added them automatically.

### The `go.sum` file

Alongside `go.mod`, Go maintains a `go.sum` file. It contains cryptographic checksums of every module version your project depends on (directly or transitively). When Go downloads a dependency, it checks the downloaded content against these checksums to ensure nothing has been tampered with.

You should commit both `go.mod` and `go.sum` to version control. Never edit `go.sum` by hand — Go manages it for you.

### Adding and removing dependencies: `go get` and `go mod tidy`

To add a dependency:

```
go get github.com/fatih/color@v1.16.0
```

The `@v1.16.0` pins the exact version. You can also use `@latest` to get the newest release.

To remove a dependency, delete its usage from your code, then run:

```
go mod tidy
```

`go mod tidy` is your best friend. It looks at all your `.go` files, figures out what's actually imported, and updates `go.mod` and `go.sum` to match — adding missing entries and removing unused ones. Run it before every commit.

```go
// main.go
package main

import (
    "github.com/fatih/color"
)

func main() {
    color.Green("This text is green!")
    color.Red("This text is red!")
    color.Blue("This text is blue!")
}
```

After adding the import and running `go mod tidy`, then `go run main.go`, you'll see coloured output in your terminal.

### Semantic versioning

Go modules follow *semantic versioning* (semver): `MAJOR.MINOR.PATCH`.

- **PATCH** (`v1.2.1 → v1.2.2`): bug fixes, backward compatible
- **MINOR** (`v1.2.1 → v1.3.0`): new features, backward compatible
- **MAJOR** (`v1.x → v2.0.0`): breaking changes

Go treats major versions as different modules. A `v2` release of a package lives at a different import path:

```go
import "github.com/some/package/v2"
```

This means upgrading a major version is an explicit, visible change in your source code — which is a good thing. No silent breaking changes.

### Replacing dependencies

Sometimes you need to use a local version of a package (say, you're fixing a bug in a dependency and want to test the fix before it's merged):

```
// go.mod
replace github.com/fatih/color => ../color-fork
```

The `replace` directive points the toolchain at a local directory or an alternate module path. This is a development tool — remove `replace` directives before you ship, since they don't make sense in a published module.

### When to vendor

Go supports vendoring: copying all your dependencies into a `vendor/` directory inside your project.

```
go mod vendor
```

After running this, you can build your project with `go build -mod=vendor` and it will use the local copies instead of downloading from the internet. Vendoring is useful when:

- You're building in an environment with no internet access (e.g., some CI systems)
- You want a fully self-contained repository
- You need to audit every line of third-party code that ships with your binary

For most projects, vendoring adds more maintenance overhead than it's worth. The module proxy and `go.sum` checksums already give you reproducible, verified builds without needing to check in megabytes of third-party code. Use vendoring when you have a specific reason, not by default.

---

## Try It Yourself

Create a new folder, run `go mod init yourname/greetapp`, and add a dependency on `github.com/fatih/color` (or any package that interests you). Write a `main.go` that imports and uses it, then run `go mod tidy` and `go run main.go`. Check what `go.mod` and `go.sum` look like after.

Then delete the import from `main.go`, run `go mod tidy` again, and watch the dependency disappear from `go.mod`. This is the workflow you'll use every day.

---

## Common Mistakes

**Not committing `go.sum`**

Some people treat `go.sum` as a generated file and add it to `.gitignore`. Don't. It's a security file. Without it, other contributors on the project can't verify that the dependencies they download are the same ones you tested with.

**Running `go get` to remove a dependency**

`go get some/package@none` technically works, but it's easier and safer to just delete the import and run `go mod tidy`. Tidy handles everything cleanly.

**Forgetting to remove `replace` directives before publishing**

A `replace` pointing at a local path will break everyone else who tries to use your module. Always audit `go.mod` before tagging a release.

**Using `go get` to update all dependencies at once without reading changelogs**

`go get -u ./...` updates every dependency to its latest minor/patch version. This can pull in breaking changes (despite semver promises, bugs happen). Update dependencies intentionally, one at a time, and test after each.

---

## Key Takeaway

Every Go project starts with `go mod init`. The `go.mod` file tracks your module identity and dependencies; `go.sum` verifies their integrity. Use `go get` to add packages, `go mod tidy` to clean up, and commit both files to version control. Semantic versioning tells you what kind of change a new version brings, and Go's module system enforces major version boundaries at the import path level. You now have everything you need to bring external packages into your projects safely and reproducibly.

---

*[Course Index: Go from Scratch](/series/go-from-scratch) | [← Lesson 16: Packages and Imports](/post/go/go-scratch-packages) | [Lesson 18: The sync Package →](/post/go/go-scratch-sync)*
