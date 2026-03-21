---
title: "Lesson 24: The Go Toolchain — build, test, vet, fmt — your daily tools"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 24
keywords: ["Go", "golang", "go build", "go test", "go vet", "go fmt", "gofmt", "go generate", "go install", "go env", "GOPATH", "modules", "beginner"]
tags: ["Go tutorial", "golang", "beginner"]
date: "2024-05-07T00:00:00.000Z"
---

One of the things that drew me to Go was that the toolchain ships with the language. You don't need to choose a build tool, a formatter, or a test framework — they're all there from day one, and they're all invoked the same way: `go <command>`. This lesson is a tour of the tools you'll use every single day.

---

## The Basics

### go run and go build

You've used `go run` throughout this course. It compiles your code and immediately executes it — perfect for development. Under the hood it creates a temporary binary and runs it, then throws the binary away.

```
go run main.go
go run .          # run the package in the current directory
```

When you want a binary you can distribute or deploy, use `go build`:

```
go build -o myapp .
```

This produces an executable called `myapp` (or `myapp.exe` on Windows). The `.` means "the package in the current directory." The resulting binary has no dependencies — you can copy it to any machine with the same OS and architecture and it just works. That single fact makes deployment dramatically simpler than most other languages.

### go test

Go has a built-in test runner that needs no configuration. Any file ending in `_test.go` is a test file. Any function named `TestXxx(t *testing.T)` in that file is a test:

```
go test ./...        # run all tests in the current module
go test -v ./...     # verbose — print test names and results
go test -run TestAdd # run only tests whose name contains "TestAdd"
go test -cover ./... # show code coverage percentages
```

The `./...` pattern means "this package and all packages below it." I use `go test -v ./...` constantly during development — verbose mode shows which tests pass and which fail with their output, not just a summary.

```
go test -bench=. ./...   # run benchmarks
go test -race ./...      # run with the race detector enabled
```

The race detector is one of Go's killer features. Run your tests with `-race` regularly, especially if you're writing concurrent code. It will catch data races that would be nearly impossible to find by inspection.

### go vet

`go vet` analyses your code for common mistakes that are syntactically valid but probably wrong. It catches things like:

- Calling `fmt.Printf` with the wrong number of arguments for the format string
- Passing a non-pointer to `json.Unmarshal`
- Unreachable code
- Copying a mutex by value

```
go vet ./...
```

Run `go vet` before every commit. It takes a fraction of a second and catches real bugs. Many CI systems run it automatically — you should too.

### go fmt and gofmt

This is one of my favourite things about Go. There is one official code style and `go fmt` enforces it automatically. No debates about tabs vs spaces, brace placement, or line length.

```
go fmt ./...         # format all files in the module
gofmt -w main.go     # format a single file, write changes back
gofmt -d main.go     # show a diff without applying changes
```

Run `go fmt` before committing. Most editors can be configured to run it on save. After a while you stop thinking about formatting at all and just let the tool handle it. This is one of the reasons Go codebases all look the same regardless of who wrote them — hugely beneficial when reading unfamiliar code.

### go install

`go install` compiles a package and places the resulting binary in your `$GOPATH/bin` directory (or `$GOBIN` if set). This is how you install command-line tools written in Go:

```
go install golang.org/x/tools/gopls@latest
go install github.com/air-verse/air@latest
```

After running this, the tool is available anywhere in your terminal (assuming `$GOPATH/bin` is in your `$PATH`).

### go generate

`go generate` is a convention for running arbitrary commands that are embedded in source comments:

```go
//go:generate stringer -type=Direction
```

When you run `go generate ./...`, Go finds these comments and executes the specified commands. It's commonly used to regenerate code from schemas, run code generators for mocks, or produce string representations for enum-like types. The `//go:generate` comment is just a hint to the tool — Go itself doesn't do anything special with the generated code.

### go env and understanding GOPATH vs modules

`go env` prints all of Go's environment variables:

```
go env           # print everything
go env GOPATH    # print just GOPATH
go env GOMODCACHE # print the module cache location
```

**GOPATH** is the workspace directory used by Go before modules existed. It's still used for two things: `go install` puts binaries in `$GOPATH/bin`, and the module cache lives at `$GOPATH/pkg/mod`. You generally don't need to think about it much anymore.

**Go modules** (the `go.mod` file in your project root) replaced GOPATH-based development. A module is just a collection of Go packages with a declared name and a list of dependencies. You can put your project anywhere on your filesystem — you're not constrained to the `$GOPATH/src` directory. Modules are the default and the only approach you should use for new projects.

```
go mod init github.com/yourname/yourproject   # initialise a new module
go mod tidy                                    # add missing deps, remove unused ones
go mod download                               # download all dependencies locally
```

---

## Try It Yourself

**Exercise 1:** In an existing module, run `go vet ./...`. If it finds anything, read the output and fix it. If it finds nothing, intentionally introduce a mistake — like calling `fmt.Printf("%d", "hello")` — and run `go vet` again to see what it reports.

**Exercise 2:** Run `go fmt ./...` on your project. Open a file, deliberately mess up the indentation (add extra spaces, remove a blank line, change spacing around operators), save it, and run `go fmt` again. Watch it snap back into shape.

**Exercise 3:** Build a binary of your current project with `go build -o myapp .` and run it directly with `./myapp`. Then check `go env GOPATH` and look inside `$GOPATH/bin` to see what tools you've installed there.

---

## Common Mistakes

**Only running go fmt manually**

If you don't configure your editor to run `go fmt` on save, you'll forget it constantly. Take five minutes now to set up your editor. For VS Code with the Go extension, it's automatic. For Vim/Neovim, `vim-go` or `nvim-lspconfig` with gopls handles it. It will save you the embarrassment of committing badly formatted code.

**Not running go vet in CI**

`go vet` catches real bugs that `go build` misses. If your CI pipeline only runs `go test`, add `go vet ./...` as a separate step. It costs nothing and pays dividends.

**Confusing go install with go build**

`go build` produces a binary in the current directory (or wherever you specify with `-o`). `go install` puts the binary in `$GOPATH/bin`. Use `go build` for your own project's binary, `go install` for tools you want available globally in your PATH.

**Editing go.mod by hand**

It's tempting to edit `go.mod` directly when you want to add or remove a dependency. Don't. Use `go get` to add a dependency, `go mod tidy` to clean up unused ones. Manual edits to `go.mod` can leave `go.sum` out of sync, causing verification failures for anyone else who checks out your code.

---

## Key Takeaway

The Go toolchain is one of the language's greatest practical advantages. `go build` compiles to a portable binary, `go test` runs your tests with no configuration, `go vet` catches logic errors, and `go fmt` eliminates all style debates. These aren't third-party add-ons — they ship with Go and work the same way on every machine. Make `go fmt ./...` and `go vet ./...` part of your workflow today, and you'll already be writing more professional Go than a large fraction of working programmers.

---

*[← Previous: Lesson 23 — The Type System](/post/go/go-scratch-type-system) | [Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 25 — What's Next →](/post/go/go-scratch-whats-next)*
