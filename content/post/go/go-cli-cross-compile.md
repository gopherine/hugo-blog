---
title: 'Lesson 5: Cross-Compilation — Build for Linux from your Mac in one command'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 5
keywords:
  - Go
  - golang
  - CLI
  - cross-compilation
  - GOOS
  - GOARCH
  - build constraints
tags:
  - Go tutorial
  - golang
  - CLI
date: '2024-12-10T00:00:00.000Z'
---

One of Go's most practical superpowers is that cross-compilation is a first-class feature, not an afterthought. Two environment variables — `GOOS` and `GOARCH` — are all you need to produce a Linux binary from macOS, a Windows executable from Linux, or an ARM binary from an x86 machine. No Docker containers required, no cross-compilation toolchain setup, no linker flags hunting. Just `GOOS=linux GOARCH=amd64 go build` and you have a production-ready binary for the target platform.

This simplicity is not accidental. Go was designed from the start for systems that need to build once and run anywhere. Understanding the compilation model — and its limits — lets you write portable code confidently.

## The Problem

Developers who come from C or Rust sometimes assume cross-compilation is complicated in Go too. They reach for Docker multi-stage builds or GitHub Actions matrix builds when a Makefile target would do the job:

```bash
# WRONG — unnecessarily complex for pure Go code
docker run --rm -v "$PWD":/app -w /app golang:1.22 \
  go build -o dist/myapp-linux-amd64 ./cmd/myapp
```

For pure Go code with no CGO, this is unnecessary overhead. The second problem is code that uses CGO — C extensions — which breaks cross-compilation immediately:

```go
// WRONG (for cross-compilation) — importing a package that uses CGO
import "github.com/mattn/go-sqlite3" // requires CGO, breaks cross-compile
```

Once you depend on CGO, you need a C cross-compiler for the target platform. This is solvable but significantly more complex.

## The Idiomatic Way

For pure Go code, cross-compilation is a one-liner:

```bash
# Linux AMD64 (most server targets)
GOOS=linux GOARCH=amd64 go build -o dist/myapp-linux-amd64 ./cmd/myapp

# macOS ARM (Apple Silicon)
GOOS=darwin GOARCH=arm64 go build -o dist/myapp-darwin-arm64 ./cmd/myapp

# Windows AMD64
GOOS=windows GOARCH=amd64 go build -o dist/myapp-windows-amd64.exe ./cmd/myapp

# Linux ARM64 (Raspberry Pi 4, AWS Graviton)
GOOS=linux GOARCH=arm64 go build -o dist/myapp-linux-arm64 ./cmd/myapp
```

A `Makefile` target that builds all targets:

```makefile
BINARY=myapp
VERSION=$(shell git describe --tags --always --dirty)
BUILD_FLAGS=-ldflags="-X main.version=$(VERSION)" -trimpath

.PHONY: build-all
build-all:
	GOOS=linux   GOARCH=amd64  go build $(BUILD_FLAGS) -o dist/$(BINARY)-linux-amd64   ./cmd/$(BINARY)
	GOOS=linux   GOARCH=arm64  go build $(BUILD_FLAGS) -o dist/$(BINARY)-linux-arm64   ./cmd/$(BINARY)
	GOOS=darwin  GOARCH=amd64  go build $(BUILD_FLAGS) -o dist/$(BINARY)-darwin-amd64  ./cmd/$(BINARY)
	GOOS=darwin  GOARCH=arm64  go build $(BUILD_FLAGS) -o dist/$(BINARY)-darwin-arm64  ./cmd/$(BINARY)
	GOOS=windows GOARCH=amd64  go build $(BUILD_FLAGS) -o dist/$(BINARY)-windows-amd64.exe ./cmd/$(BINARY)
```

For platform-specific code (file permissions on Unix, registry access on Windows, syscalls), use build constraints to keep it organized:

```go
// file_unix.go — compiled only on Unix-like systems
//go:build !windows

package fs

import (
    "os"
    "syscall"
)

func makeExecutable(path string) error {
    return os.Chmod(path, 0o755)
}

func getOwner(path string) (uid, gid int, err error) {
    info, err := os.Stat(path)
    if err != nil {
        return 0, 0, err
    }
    stat := info.Sys().(*syscall.Stat_t)
    return int(stat.Uid), int(stat.Gid), nil
}
```

```go
// file_windows.go — compiled only on Windows
//go:build windows

package fs

import "os"

func makeExecutable(path string) error {
    // Windows doesn't have Unix-style executable bits.
    // The file extension (.exe) determines executability.
    return nil
}

func getOwner(path string) (uid, gid int, err error) {
    // Windows uses SIDs, not uid/gid. Return sentinel values.
    return -1, -1, nil
}
```

The Go compiler selects the right file based on the target OS. No `if runtime.GOOS == "windows"` checks needed in the main code.

## In The Wild

When I shipped the first version of an internal tool that ran on developer laptops (macOS) and in CI (Linux), I needed both targets to work. The CI system ran the tests on Linux and the binary in Docker. Developers ran it locally on Apple Silicon Macs.

The entire build pipeline was a three-line shell script:

```bash
#!/usr/bin/env bash
set -euo pipefail

VERSION=$(git describe --tags --always)

GOOS=linux  GOARCH=amd64  go build -ldflags="-X main.version=$VERSION" \
    -o dist/tool-linux-amd64 ./cmd/tool

GOOS=darwin GOARCH=arm64  go build -ldflags="-X main.version=$VERSION" \
    -o dist/tool-darwin-arm64 ./cmd/tool
```

This ran in about eight seconds total. No Docker, no matrix, no fuss. The Linux binary went into the Docker image, the macOS binary was distributed via the team's internal Homebrew tap.

The one place where I hit a real cross-compilation constraint was when I tried to use `github.com/mattn/go-sqlite3` for an embedded database in a tool. SQLite3 requires CGO. The moment someone on the team ran the build on their Mac targeting Linux, it failed with a C compiler error. The fix was to switch to a pure-Go SQLite implementation (`modernc.org/sqlite`), which adds no CGO dependency:

```go
// WRONG — CGO required, breaks cross-compilation
import _ "github.com/mattn/go-sqlite3"

// RIGHT — pure Go, cross-compilation works as expected
import _ "modernc.org/sqlite"
```

The pure-Go version is slightly slower but the difference is imperceptible for CLI tools. The ability to cross-compile was worth the trade-off.

You can verify that a binary has no CGO dependencies with:

```bash
file dist/myapp-linux-amd64
# should output: ELF 64-bit LSB executable, x86-64, statically linked, ...
```

A statically linked binary has no system library dependencies — it runs on any Linux system, containerized or bare metal, regardless of libc version.

## The Gotchas

**CGO is disabled by default during cross-compilation.** If your code has any CGO dependencies and you try to cross-compile, you get a link error, not a runtime panic. This surfaces early. The trickier case is transitive CGO: a dependency you import uses CGO, and you did not notice. Run `CGO_ENABLED=0 go build ./...` as a CI check to verify that your entire dependency tree is CGO-free.

**`runtime.GOOS` and `runtime.GOARCH` are set to the compile target.** In a binary cross-compiled for Linux, `runtime.GOOS == "linux"` is true even if you built it on macOS. This is the expected behavior — the binary knows what it is built for. Build constraints in filenames (`_linux.go`, `_amd64.go`) are evaluated at compile time, not runtime.

**Test cross-compiled binaries.** Building succeeds even if the binary does not behave correctly on the target platform. For subtle platform differences (endianness, path separators, signal semantics), test on the actual target platform or in a Linux container/VM.

## Key Takeaway

Cross-compilation in Go is so simple that there is almost no reason not to do it. Two environment variables and a Makefile target buy you binaries for every platform your users run on, built from any machine in under a minute. The main constraint is CGO — if your dependency tree stays pure Go, cross-compilation is completely frictionless. Keep `CGO_ENABLED=0 go build ./...` in your CI pipeline as a gate, choose pure-Go alternatives when CGO dependencies arise, and you can reliably ship to Linux, macOS, and Windows from a single developer machine.

---

← [Lesson 4: Signal Handling](/post/go/go-cli-signals) | [Course Index](/series/go-cli-tooling) | [Next → Lesson 6: Embedding Assets](/post/go/go-cli-embed)
