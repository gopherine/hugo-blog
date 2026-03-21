---
title: 'Lesson 1: Static Binaries — One file, zero dependencies, deploy anywhere'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 1
keywords:
  - Go
  - golang
  - static binary
  - CGO
  - cross-compilation
  - deployment
  - build flags
  - linux
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2024-06-30T00:00:00.000Z'
---

The first time I deployed a Go binary to a production server, I was braced for the usual ceremony: SSH in, install the right runtime version, copy over config files, fiddle with symlinks. Instead, I ran `scp` and then the binary, and it just worked. No installation. No dependency hell. No "but it works on my machine." The binary contained everything it needed.

This is Go's single greatest operational advantage: the compiler produces a self-contained executable. Understanding exactly how this works — and how it can break — makes the difference between a deploy that takes five minutes and one that takes an afternoon.

## The Problem

"Static binary" means different things depending on how you build:

```bash
# WRONG — CGO enabled (default) produces a dynamically linked binary
go build -o myapp ./cmd/myapp

# Check what it links against
ldd ./myapp
# linux-vdso.so.1, libpthread.so.0, libc.so.6 — dynamic dependencies!
```

If your code, or any of its dependencies, uses CGO — C bindings compiled into the Go binary — the resulting executable links against `glibc` on the build machine. Deploy that binary to a machine with a different glibc version (or a scratch container with no glibc at all) and you get `exec format error` or mysterious segfaults.

The other problem is silent CGO inclusion. You may have written no C code yourself, but packages like `database/sql` with the SQLite driver, certain DNS resolvers, and some crypto packages pull in CGO transitively. You don't know until the binary fails to start on a target machine.

## The Idiomatic Way

The definitive way to produce a static binary is to disable CGO entirely:

```bash
# CGO_ENABLED=0 disables all CGO and forces pure Go implementations
# GOOS=linux forces the target OS regardless of build host
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags="-s -w" \
    -o myapp \
    ./cmd/myapp
```

The `-ldflags="-s -w"` strips the symbol table (`-s`) and DWARF debugging information (`-w`), reducing binary size by 30-50% with no impact on runtime behavior.

Verify the result:

```bash
file ./myapp
# myapp: ELF 64-bit LSB executable, x86-64, statically linked

ldd ./myapp
# not a dynamic executable — exactly what we want
```

For services that need cross-compilation to multiple architectures:

```bash
#!/bin/bash
# build.sh — build for common deployment targets

BINARY_NAME="myapp"
VERSION=$(git describe --tags --always --dirty)
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LDFLAGS="-s -w -X main.version=${VERSION} -X main.buildTime=${BUILD_TIME}"

platforms=("linux/amd64" "linux/arm64" "darwin/amd64" "darwin/arm64")

for platform in "${platforms[@]}"; do
    IFS='/' read -r -a parts <<< "$platform"
    GOOS="${parts[0]}"
    GOARCH="${parts[1]}"

    output="${BINARY_NAME}-${GOOS}-${GOARCH}"

    CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" go build \
        -ldflags="$LDFLAGS" \
        -o "dist/$output" \
        ./cmd/myapp

    echo "Built: dist/$output"
done
```

The `-X main.version=${VERSION}` trick embeds build metadata at link time. In `main.go`:

```go
package main

import "fmt"

// These are set by the build script via -ldflags.
var (
    version   = "dev"
    buildTime = "unknown"
)

func printVersion() {
    fmt.Printf("version: %s\nbuild time: %s\n", version, buildTime)
}
```

Now `./myapp --version` tells you exactly which commit you're running, without any external metadata files.

## In The Wild

The `net` package is the most common source of accidental CGO. By default on Linux, `net.Dial` uses the system resolver, which calls into `glibc`'s `getaddrinfo`. With `CGO_ENABLED=0`, Go switches to its own pure-Go DNS resolver, which reads `/etc/resolv.conf` directly. For most services, the behavior is identical. For services that depend on `nsswitch.conf` lookups (LDAP, NIS, custom NSS modules), the pure-Go resolver doesn't replicate them.

You can force the pure-Go resolver even without disabling CGO globally:

```go
// In your main package, import the net package with the netgo build tag.
// Or set in code:
import _ "net"

// In your application's init, or via build tags:
// go build -tags netgo ./cmd/myapp
```

A sanity check I run in CI on every binary before deployment:

```bash
# Fail the build if the binary has unexpected dynamic dependencies
if ldd ./dist/myapp-linux-amd64 2>&1 | grep -v "not a dynamic executable"; then
    echo "ERROR: binary has dynamic dependencies"
    exit 1
fi

# Check binary size — alert if it unexpectedly doubled
size=$(stat -c%s ./dist/myapp-linux-amd64)
if [ "$size" -gt 50000000 ]; then  # 50MB threshold
    echo "WARNING: binary is larger than expected (${size} bytes)"
fi
```

The size check has caught dependency bloat twice — once when a transitive dependency pulled in a graphics library through a build tag that should have been disabled.

## The Gotchas

**SQLite requires CGO.** If your service uses SQLite via `mattn/go-sqlite3`, you have CGO. The library wraps the SQLite C source file. Your options: switch to a pure-Go SQLite implementation like `modernc.org/sqlite`, or accept CGO and ensure your Docker base image has matching glibc.

**Plugins require CGO.** The `plugin` package in Go's standard library for loading `.so` files at runtime requires CGO. If you use Go plugins, static linking is off the table. Consider an alternative architecture — subprocess communication, RPC, or compile-time interfaces — if you want static binaries.

**The binary is larger without debugging info.** Stripping symbols with `-s -w` makes panics less informative. The stack trace will still show function names (those come from the runtime's `pclntab`, not DWARF), but `addr2line` and `objdump` won't work. For debugging panics in production, use structured logging with stack captures, or keep an unstripped binary with the same commit hash available separately.

**Race detector is CGO.** `go test -race` and `go build -race` use CGO. You can't build a race-detected binary with `CGO_ENABLED=0`. Run the race detector in testing; deploy the static binary to production.

## Key Takeaway

`CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w"` is the command you want for production binaries. It produces a single file that runs on any Linux machine (or container) regardless of what shared libraries are installed. Embed version information at build time, verify with `ldd`, and add a size check to CI. The result is a deploy artifact that is genuinely portable.

---

**Previous:** Series introduction
**Next:** [Lesson 2: Docker for Go — Multi-stage builds that produce tiny images](/post/go/go-deploy-docker/)
