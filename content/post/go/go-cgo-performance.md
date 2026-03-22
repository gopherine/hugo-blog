---
title: "Lesson 2: CGo Performance and Pitfalls — The hidden cost of crossing the boundary"
author: Atharva Pandey
series: "CGo: Calling C from Go"
lesson: 2
keywords:
  - CGo performance
  - cgo overhead
  - goroutine to OS thread
  - CGo callbacks
  - cgo export
  - CGo pitfalls
  - golang C performance
tags:
  - Go tutorial
  - golang
  - CGo
  - systems
date: "2024-10-24T00:00:00.000Z"
---

When I profiled a service that was spending 40% of its time in cgo calls, I thought I was measuring the C library. I was not. I was measuring the overhead of *getting to* the C library. The actual C work was fast. What was slow was the goroutine-to-OS-thread transition, the stack switching, and the runtime bookkeeping that happens every single time Go code crosses the C boundary. Understanding this overhead is what separates cgo code that runs fine from cgo code that becomes a bottleneck.

## The Problem

In Lesson 1, I showed you how to call C from Go. The code worked. But I did not mention what happens at the moment `C.myFunction()` executes. That moment is more expensive than a regular Go function call, and if you call C functions in a tight loop or in the hot path of a request handler, that cost accumulates.

Additionally, when C code needs to call back into Go — for callbacks, event handlers, completion hooks — you need `//export`, and that feature comes with its own set of restrictions and pitfalls that are not obvious until you hit them.

## How It Works

**The boundary crossing cost.**

When a goroutine calls a C function, the Go runtime does several things:

1. The goroutine's stack is checked. C code expects a large, contiguous stack (the OS default is 8MB on most platforms). Go goroutines start with a small stack (8KB) and grow it dynamically. Before calling into C, the runtime may need to move the goroutine to an OS thread stack.
2. The goroutine is locked to its current OS thread for the duration of the C call. Go's scheduler cannot migrate it to another thread.
3. The runtime marks the goroutine as "in system call" so the scheduler knows it is blocked and can schedule other goroutines on other threads.
4. After the C function returns, all of this is unwound.

The overhead for a simple cgo call is roughly **60–200 nanoseconds** on modern hardware — compared to a Go function call which is 1–5 nanoseconds. That is 20–100x slower per call. For a function you call once per HTTP request, this is invisible. For a function you call a million times per second, it is a serious problem.

You can measure it yourself:

```go
// BenchmarkCgoCall measures raw cgo boundary overhead
/*
int noop() { return 0; }
*/
import "C"

func BenchmarkCgoCall(b *testing.B) {
    for i := 0; i < b.N; i++ {
        C.noop()
    }
}
```

Run `go test -bench=BenchmarkCgoCall -benchmem` and you will see the per-call time. Compare it against a benchmark of a Go function that does the same nothing.

**Batching to amortize cost.**

The standard mitigation is batching: instead of calling a C function once per item, call it once per batch. If the C library supports batch APIs, use them. If it does not, build a thin C wrapper that processes a slice:

```go
/*
#include <string.h>

void process_batch(const char **items, int count, int *results) {
    for (int i = 0; i < count; i++) {
        results[i] = (int)strlen(items[i]);
    }
}
*/
import "C"
import "unsafe"

func ProcessBatch(items []string) []int {
    if len(items) == 0 {
        return nil
    }
    // Build C array of char pointers
    cItems := make([]*C.char, len(items))
    for i, s := range items {
        cItems[i] = C.CString(s)
        defer C.free(unsafe.Pointer(cItems[i]))
    }
    results := make([]C.int, len(items))
    C.process_batch(
        (**C.char)(unsafe.Pointer(&cItems[0])),
        C.int(len(items)),
        (*C.int)(unsafe.Pointer(&results[0])),
    )
    out := make([]int, len(items))
    for i, r := range results {
        out[i] = int(r)
    }
    return out
}
```

One cgo boundary crossing for N items instead of N crossings.

**C callbacks into Go: `//export`.**

Sometimes a C library is event-driven and calls a function pointer you provide when something happens. To provide a Go function as a C callback, use `//export`:

```go
// callbacks.go
package mylib

/*
#include "mylib.h"

// Forward declaration for the Go callback
extern void onDataReceived(void *ctx, const char *data, int len);

// Register our Go function as the callback
void register_callback(MyHandle *h, void *ctx) {
    mylib_set_callback(h, onDataReceived, ctx);
}
*/
import "C"
import "unsafe"

//export onDataReceived
func onDataReceived(ctx unsafe.Pointer, data *C.char, length C.int) {
    // Convert C types to Go types
    goData := C.GoBytes(unsafe.Pointer(data), length)
    // Use ctx to find the Go object this callback belongs to
    handle := cgo.Handle(uintptr(ctx))
    obj := handle.Value().(*MyObject)
    obj.handleData(goData)
}
```

The `//export` directive makes `onDataReceived` callable from C. The comment with `extern` declaration in the preamble must match the function signature exactly.

**Using `runtime/cgo.Handle` for passing Go objects through C.**

You cannot pass a Go pointer to C and store it there — the GC might move the object. The safe way is `cgo.Handle`:

```go
import "runtime/cgo"

// Store a Go object in a handle table, get an integer you can pass to C
h := cgo.NewHandle(myGoObject)
C.register_callback(cHandle, C.uintptr_t(h))

// In the callback, retrieve it
func onCallback(ctx C.uintptr_t) {
    h := cgo.Handle(ctx)
    obj := h.Value().(*MyObject)
    // use obj
    h.Delete() // when done, to free the handle
}
```

`cgo.Handle` stores the pointer in a global map keyed by an integer. This integer is safe to pass through C. The GC sees the entry in the map and keeps the object alive. `h.Delete()` removes it from the map so the GC can eventually collect it.

## In Practice

**Worker pool pattern for cgo calls.** If you have bursty cgo work, a bounded worker pool limits the number of OS threads locked to cgo at any one time:

```go
type CGOWorker struct {
    work chan func()
}

func NewCGOWorker(numWorkers int) *CGOWorker {
    w := &CGOWorker{work: make(chan func(), 100)}
    for i := 0; i < numWorkers; i++ {
        go func() {
            runtime.LockOSThread()
            for fn := range w.work {
                fn()
            }
        }()
    }
    return w
}

func (w *CGOWorker) Do(fn func()) {
    w.work <- fn
}
```

`runtime.LockOSThread()` keeps each worker goroutine permanently on its OS thread, eliminating repeated thread-switching overhead for that goroutine.

**Profiling cgo calls.** The Go CPU profiler does profile through cgo boundaries — you will see C frames in pprof output. However, the resolution inside C code depends on whether the C library was compiled with frame pointers. To see accurate C profiling, compile your C wrapper with `-fno-omit-frame-pointer`. Perf on Linux and Instruments on macOS can also profile the full stack including C.

## The Gotchas

**Blocking C calls block OS threads.** A goroutine calling a blocking C function (like `read()`, `select()`, or a long computation) ties up an OS thread. Go's scheduler spawns new OS threads to compensate, but there is a default limit (`GOMAXPROCS` for Go work, but unbounded for blocked cgo threads). A hundred concurrent blocking cgo calls means a hundred OS threads. `runtime.NumCgoCall()` lets you monitor this.

**`//export` and preamble function definitions are incompatible.** If any file in a package uses `//export`, no file in that package may define C functions in its preamble (only declare them with `extern`). Violating this causes duplicate symbol errors at link time. The fix is to move C function definitions to a `.c` file in the same package.

**Stack unwinding and panics.** A panic in Go code called from a C callback (`//export` function) is dangerous. If the panic propagates into C stack frames, behaviour is undefined. You must recover from all panics inside exported functions:

```go
//export onCallback
func onCallback(ctx unsafe.Pointer) {
    defer func() {
        if r := recover(); r != nil {
            log.Printf("panic in cgo callback: %v", r)
        }
    }()
    // ... your code
}
```

**`C.GoString` copies.** Every `C.GoString(cStr)` allocates a new Go string and copies the bytes. In high-throughput scenarios, these allocations accumulate. If you just need to read the string temporarily, pass the `*C.char` and length directly to a function that works on bytes.

**Thread-local storage in C.** Some C libraries use thread-local storage (`pthread_key_t`) for per-thread state. Because Go goroutines can run on different OS threads between calls, thread-local C state may be inconsistent. If the C library you are wrapping uses TLS for connection state or error reporting, use `runtime.LockOSThread()` for the entire duration of that operation.

## Key Takeaway

The cgo boundary costs 60–200ns per crossing. That is negligible for coarse operations and fatal for tight loops. Amortize the cost by batching C calls, using worker pools with `runtime.LockOSThread()`, and designing wrapper APIs that do as much work per crossing as possible. For callbacks, use `//export` with `cgo.Handle` to pass Go object references safely through C. Recover from all panics in exported functions. The boundary is crossable — you just have to respect its cost.

---

**Previous:** [Lesson 1: CGo Basics](/post/go/go-cgo-basics/)

---

🎓 **Course Complete!** You have finished *CGo: Calling C from Go*. You know how to set up a cgo build, call C functions, manage C memory, expose Go callbacks, and measure and reduce boundary crossing overhead. CGo is a sharp tool — use it deliberately and isolate it carefully.
