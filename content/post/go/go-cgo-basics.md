---
title: "Lesson 1: CGo Basics — When you need C and how to call it safely"
author: Atharva Pandey
series: "CGo: Calling C from Go"
lesson: 1
keywords:
  - CGo
  - cgo
  - calling C from Go
  - C interop
  - golang C
  - foreign function interface
  - CGo basics
tags:
  - Go tutorial
  - golang
  - CGo
  - systems
date: "2024-07-22T00:00:00.000Z"
---

There is a saying in the Go community: "cgo is not Go." It is not an insult. It is a warning. The moment you add `import "C"` to a file, you are no longer writing a pure Go program — you are writing a Go program that manages a C boundary, and all the things that make Go comfortable (fast builds, easy cross-compilation, the race detector, straightforward stack traces) become harder. You are doing it on purpose, because the alternative is worse.

I first reached for cgo when I needed to call a hardware vendor's SDK that was only available as a C library. Then again to wrap a cryptographic implementation where correctness was paramount and a Go port was not an option. Then a third time to call into SQLite with the performance characteristics the vendor library guaranteed. In each case, cgo was the right tool. The key is understanding what it costs and how to use it without making your codebase unmaintainable.

## The Problem

Go has a rich ecosystem, but it does not cover everything. You will encounter:

- **Vendor SDKs** with C-only APIs (hardware devices, embedded systems, proprietary databases).
- **Battle-tested C libraries** where a pure-Go port does not exist or has not achieved the same maturity (libssl, libsqlite3, libgit2, FFmpeg).
- **Performance-critical code** where an existing C implementation is highly optimised and verified, and rewriting it in Go is not justifiable.
- **System calls and OS APIs** that Go's `syscall` and `golang.org/x/sys` packages do not yet expose.

In these cases, cgo lets you call C code directly from Go, pass data across the boundary, and use C libraries as if they were Go libraries — with significant caveats.

## How It Works

A cgo-enabled file includes the magic comment `// import "C"` (with no blank line between the comment and the import). Any C code you put in comments immediately above that import is compiled as C:

```go
package main

/*
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// You can define C functions inline
int add(int a, int b) {
    return a + b;
}
*/
import "C"

import "fmt"

func main() {
    result := C.add(3, 4)
    fmt.Println(int(result)) // 7
}
```

The `C` pseudo-package exposes every symbol visible in the C preamble. Functions, types, constants, macros.

**Passing strings** is the most common operation, and it requires explicit memory management:

```go
/*
#include <string.h>
size_t my_strlen(const char *s) {
    return strlen(s);
}
*/
import "C"
import "unsafe"

func GoStringLen(s string) int {
    cs := C.CString(s)           // allocates C memory, copies the string
    defer C.free(unsafe.Pointer(cs)) // YOU must free it
    return int(C.my_strlen(cs))
}
```

`C.CString` allocates memory in C's heap and returns a `*C.char`. Go's garbage collector does not manage this memory. If you forget `C.free`, you leak memory. Every `C.CString` must be paired with a `C.free`.

**Type conversions** between Go and C types are explicit:

```go
var goInt int = 42
cInt := C.int(goInt)       // Go int → C int
backToGo := int(cInt)      // C int → Go int

goBytes := []byte("hello")
cBytes := (*C.uchar)(unsafe.Pointer(&goBytes[0]))
```

**Calling C library functions** by linking against external libraries requires `#cgo` directives in the preamble:

```go
/*
#cgo LDFLAGS: -lsqlite3
#include <sqlite3.h>
*/
import "C"
```

Or to specify include and library paths:

```go
/*
#cgo CFLAGS: -I/usr/local/include
#cgo LDFLAGS: -L/usr/local/lib -lmylib
#include "mylib.h"
*/
import "C"
```

You can make these conditional on build tags or OS:

```go
/*
#cgo linux LDFLAGS: -ldl
#cgo darwin LDFLAGS: -framework CoreFoundation
*/
```

## In Practice

A realistic pattern: wrapping a C library with a clean Go API so the rest of your code never touches cgo directly. I always isolate cgo into its own package — call it `internal/mylib` — and expose a pure Go interface from it.

```go
// internal/sqlite/sqlite.go
package sqlite

/*
#cgo LDFLAGS: -lsqlite3
#include <sqlite3.h>
#include <stdlib.h>
*/
import "C"
import (
    "fmt"
    "unsafe"
)

type DB struct {
    handle *C.sqlite3
}

func Open(path string) (*DB, error) {
    cpath := C.CString(path)
    defer C.free(unsafe.Pointer(cpath))

    var db *C.sqlite3
    rc := C.sqlite3_open(cpath, &db)
    if rc != C.SQLITE_OK {
        errMsg := C.GoString(C.sqlite3_errmsg(db))
        C.sqlite3_close(db)
        return nil, fmt.Errorf("sqlite3_open: %s", errMsg)
    }
    return &DB{handle: db}, nil
}

func (db *DB) Close() error {
    rc := C.sqlite3_close(db.handle)
    if rc != C.SQLITE_OK {
        return fmt.Errorf("sqlite3_close: %d", int(rc))
    }
    return nil
}
```

The package that uses `sqlite.DB` never imports `"C"`. It does not know cgo exists. This isolation keeps the cgo complexity contained and lets you write pure-Go unit tests for the business logic that depend on the wrapper's interface.

**Error handling from C.** Most C APIs return an integer error code. Convert it to a Go `error` immediately at the cgo boundary. Do not return `C.int` to callers. Wrap the C error string with `C.GoString` and return a Go `error`:

```go
func checkRC(rc C.int, handle *C.sqlite3) error {
    if rc == C.SQLITE_OK {
        return nil
    }
    return fmt.Errorf("sqlite error %d: %s", int(rc), C.GoString(C.sqlite3_errmsg(handle)))
}
```

## The Gotchas

**No Go pointers in C memory.** You cannot store a Go pointer inside C memory. The GC may move objects, and C code cannot be notified. If a C callback needs to invoke Go code, you need the `//export` mechanism and a global handle map — a topic for Lesson 2.

**`C.CString` allocates off-heap.** Memory allocated by C functions (including `C.CString`) is not managed by the Go GC. The profiler will not show it. The race detector will not track it. You must track these allocations manually.

**Builds become slow.** cgo invokes the C compiler for every cgo file. A pure-Go `go build` is fast; a cgo build is notably slower. Cross-compilation also breaks: `go build -target linux/arm64` on a macOS host requires a cross-compiler toolchain, whereas pure Go cross-compiles natively.

**The race detector is limited at the boundary.** The race detector can detect races in Go code, but it cannot see into C code. A race in C is invisible to `go test -race`.

**`//export` and preamble don't mix freely.** If you use `//export` to expose Go functions back to C, the preamble across all files in the package must not define any functions (only declarations and includes). This is a common compile error when building multi-file cgo packages.

**CGO_ENABLED=0 disables everything.** Some build environments set `CGO_ENABLED=0` for portability. Your cgo code will not compile. Know your deployment environment before committing to cgo.

## Key Takeaway

CGo is the right tool when you need to call C code and there is no acceptable pure-Go alternative. The pattern is: isolate cgo into its own internal package, expose a clean Go API, convert C errors to Go errors at the boundary, and free every C allocation you create. Understanding `C.CString`, `C.GoString`, `C.free`, and `#cgo` directives covers 80% of practical cgo work. The other 20% — callbacks, exported functions, and performance tuning — is what the next lesson covers.

---

**Next:** [Lesson 2: CGo Performance and Pitfalls — The hidden cost of crossing the boundary](/post/go/go-cgo-performance/)
