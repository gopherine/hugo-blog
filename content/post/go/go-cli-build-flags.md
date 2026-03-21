---
title: 'Lesson 7: Build Flags and ldflags — Inject version info at compile time'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 7
keywords:
  - Go
  - golang
  - CLI
  - build flags
  - ldflags
  - version injection
  - build constraints
tags:
  - Go tutorial
  - golang
  - CLI
date: '2025-04-05T00:00:00.000Z'
---

A CLI tool that cannot tell you what version it is running is a frustrating tool to operate. When something breaks, the first question is "which version?" Without a proper answer, debugging becomes archaeology. The good news is that Go's build system provides two mechanisms for injecting metadata at compile time — `ldflags` for injecting variable values from the shell, and build constraints for including or excluding code based on the build context — and both are straightforward once you understand their syntax.

Injecting version information takes about ten minutes to set up. Not having it wastes hours.

## The Problem

The naive approach is hardcoding version strings:

```go
// WRONG — hardcoded version that goes stale immediately
const Version = "1.2.3"
```

You update this manually, forget to update it after releases, and end up with binaries that all claim to be "1.2.3" while behaving differently. Worse, many teams just omit version information entirely:

```go
// WRONG — no version at all
var versionCmd = &cobra.Command{
    Use:   "version",
    Short: "Print version",
    Run: func(cmd *cobra.Command, args []string) {
        fmt.Println("myapp")
    },
}
```

When a user reports a bug with "myapp is broken," you have no idea which of the fifteen builds deployed across twelve machines they are talking about.

## The Idiomatic Way

Define your version variables in a dedicated package with zero-value defaults:

```go
// internal/version/version.go
package version

// These variables are set at build time via -ldflags.
// They default to "dev" so local builds are clearly labeled.
var (
    Version   = "dev"
    Commit    = "none"
    BuildTime = "unknown"
    BuiltBy   = "unknown"
)

type Info struct {
    Version   string
    Commit    string
    BuildTime string
    BuiltBy   string
}

func Get() Info {
    return Info{
        Version:   Version,
        Commit:    Commit,
        BuildTime: BuildTime,
        BuiltBy:   BuiltBy,
    }
}

func (i Info) String() string {
    return fmt.Sprintf("%s (commit: %s, built: %s, by: %s)",
        i.Version, i.Commit, i.BuildTime, i.BuiltBy)
}
```

Build with `-ldflags` to inject the actual values at compile time:

```bash
go build \
  -ldflags="-X 'github.com/yourorg/myapp/internal/version.Version=1.3.0' \
            -X 'github.com/yourorg/myapp/internal/version.Commit=$(git rev-parse --short HEAD)' \
            -X 'github.com/yourorg/myapp/internal/version.BuildTime=$(date -u +%Y-%m-%dT%H:%M:%SZ)' \
            -X 'github.com/yourorg/myapp/internal/version.BuiltBy=$(whoami)'" \
  -o dist/myapp ./cmd/myapp
```

In a `Makefile`, this becomes maintainable:

```makefile
VERSION     := $(shell git describe --tags --always --dirty)
COMMIT      := $(shell git rev-parse --short HEAD)
BUILD_TIME  := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
BUILT_BY    := $(shell whoami)

LDFLAGS := -ldflags="-s -w \
  -X 'github.com/yourorg/myapp/internal/version.Version=$(VERSION)' \
  -X 'github.com/yourorg/myapp/internal/version.Commit=$(COMMIT)' \
  -X 'github.com/yourorg/myapp/internal/version.BuildTime=$(BUILD_TIME)' \
  -X 'github.com/yourorg/myapp/internal/version.BuiltBy=$(BUILT_BY)'"

.PHONY: build
build:
	go build $(LDFLAGS) -trimpath -o dist/myapp ./cmd/myapp
```

The `-s -w` flags strip debug information and DWARF tables, reducing binary size by 20–30%. The `-trimpath` flag removes absolute file paths from the binary, making builds reproducible across machines.

The version command in Cobra uses the package directly:

```go
// cmd/version.go
var versionCmd = &cobra.Command{
    Use:   "version",
    Short: "Print version information",
    Run: func(cmd *cobra.Command, args []string) {
        info := version.Get()
        if jsonOutput {
            json.NewEncoder(os.Stdout).Encode(info)
            return
        }
        fmt.Println(info)
    },
}
```

## In The Wild

Build constraints give you a second compile-time customization tool — the ability to include or exclude entire files based on the build environment. This is different from `ldflags`, which injects values; constraints control which source files are compiled.

The most common use beyond OS-specific code is a debug build that includes additional instrumentation:

```go
// debug_enabled.go — compiled only with: go build -tags debug
//go:build debug

package myapp

import "log/slog"

func init() {
    slog.SetLogLoggerLevel(slog.LevelDebug)
}

func debugLog(msg string, args ...any) {
    slog.Debug(msg, args...)
}
```

```go
// debug_disabled.go — compiled in all other cases
//go:build !debug

package myapp

func debugLog(msg string, args ...any) {
    // no-op in production builds
}
```

Build with debug logging: `go build -tags debug ./cmd/myapp`
Build for production: `go build ./cmd/myapp`

I use this pattern for a data pipeline tool that has two instrumentation modes. The production binary has zero-overhead no-op debug functions. The debug build writes detailed trace logs to stderr. Same binary, different compilation flags.

For running only specific tests: `go test -tags integration ./...` lets you mark integration tests with `//go:build integration` and exclude them from the default `go test ./...` run.

## The Gotchas

**`-ldflags` values must match Go variable names exactly.** The path in `-X` is the full import path of the package, not the file path: `github.com/yourorg/myapp/internal/version.Version`, not `./internal/version.Version`. A typo produces no error — the variable is just not set.

**Variables injected by `-ldflags` must be package-level `var`, not `const`.** Constants are baked in at compile time by the Go compiler before `ldflags` runs. If you use `const Version = "dev"`, the linker flag is silently ignored.

**`git describe` requires annotated tags.** `git describe --tags --always` uses the most recent tag reachable from HEAD. If you have never tagged your repository, it falls back to the commit hash. If you use lightweight tags, you may need `--tags` explicitly. Check what your CI system produces.

**Binary size with `-s -w`.** Stripping debug info prevents `dlv` from attaching to a running process and makes stack traces slightly less detailed. For CLI tools distributed to end users, this is the right trade-off. For tools your team debugs in production, consider keeping debug info in a separate unstripped build.

## Key Takeaway

Version injection with `-ldflags` is a one-time setup that pays dividends for the lifetime of your tool. Run `myapp version` on any machine, in any environment, and you know exactly which source commit is running. Combined with `-trimpath` for reproducible builds and `-s -w` for size, the build flags pattern is a foundational part of production CLI tooling in Go. Build constraints give you a complementary mechanism for compile-time feature flags — debug instrumentation, integration test gates, platform-specific code — that costs nothing at runtime. Together, these tools make your binary both self-documenting and precisely controlled.

---

← [Lesson 6: Embedding Assets](/post/go/go-cli-embed) | [Course Index](/series/go-cli-tooling) | [Next → Lesson 8: Packaging and Distributing](/post/go/go-cli-distribution)
