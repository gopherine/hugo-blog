---
title: "Lesson 1: WASM from Go — Compile Go to WebAssembly and run it anywhere"
author: Atharva Pandey
keywords:
  - WebAssembly Go
  - compile Go to WASM
  - Go WASM tutorial
  - WebAssembly browser
  - GOARCH wasm
  - syscall/js
tags:
  - fundamentals
  - WebAssembly
  - WASM
series: "WebAssembly for Backend Engineers"
lesson: 1
date: '2024-07-12T00:00:00.000Z'
---

I came to WebAssembly skeptically. The pitch — "run code anywhere, at near-native speed, in a sandboxed environment" — sounded like the kind of claim that looks great in a conference talk and falls apart in production. It took a specific use case to make me take it seriously: I needed to run the same validation logic in three environments — a Go backend, a JavaScript frontend, and a CLI tool — without maintaining three separate implementations of the same business rules.

Compiling Go to WebAssembly solved that problem cleanly. The same logic, compiled once, running in the browser, in Node, and as a standalone binary. That's when I stopped being skeptical and started paying attention.

## What WebAssembly Actually Is

WebAssembly is a binary instruction format for a stack-based virtual machine. It was designed to be a compilation target — you don't typically write WASM by hand. You write in Rust, Go, C, or another language and compile to `.wasm`.

The key properties that make it interesting:

**Sandboxed execution.** WASM modules cannot access memory outside their sandbox, cannot make system calls, and cannot access the filesystem unless the host explicitly provides those capabilities. This is a security model, not a limitation. It means you can run arbitrary WASM code without trusting the source.

**Portable binary format.** A `.wasm` file is not tied to an OS or CPU architecture. The same file runs on x86_64, ARM64, in Chrome, in Node, in a Wasmtime runtime, or anywhere a WASM engine exists.

**Near-native performance.** WASM is not interpreted. It's compiled to native machine code by the host before execution. The overhead compared to native code is small — typically 10-30% depending on the workload. Not as fast as hand-optimized native code, but much faster than JavaScript or a scripting runtime.

## Compiling Go to WASM

Go has had WASM support since 1.11. The compilation target is `GOARCH=wasm GOOS=js` for browser/Node environments.

Start with a simple function you want to expose:

```go
//go:build js && wasm

package main

import (
    "syscall/js"
)

func validateEmail(this js.Value, args []js.Value) interface{} {
    if len(args) != 1 {
        return js.ValueOf("error: expected 1 argument")
    }
    email := args[0].String()
    if !isValidEmail(email) {
        return js.ValueOf("invalid")
    }
    return js.ValueOf("valid")
}

func isValidEmail(email string) bool {
    // Your actual validation logic
    return len(email) > 3 && contains(email, "@") && contains(email, ".")
}

func main() {
    js.Global().Set("validateEmail", js.FuncOf(validateEmail))
    // Block main from returning — WASM module must stay alive
    select {}
}
```

Compile it:

```bash
GOARCH=wasm GOOS=js go build -o validate.wasm .
```

You'll also need `wasm_exec.js`, which is Go's JavaScript glue file for the WASM runtime. Copy it from your Go installation:

```bash
cp "$(go env GOROOT)/misc/wasm/wasm_exec.js" .
```

Load it in the browser:

```html
<script src="wasm_exec.js"></script>
<script>
  const go = new Go();
  WebAssembly.instantiateStreaming(fetch("validate.wasm"), go.importObject)
    .then((result) => {
      go.run(result.instance);
      // Now validateEmail is available as a global function
      console.log(validateEmail("user@example.com")); // "valid"
    });
</script>
```

## The syscall/js Package

The `syscall/js` package is Go's interface to the JavaScript host environment. It lets you read and write JavaScript values, call JavaScript functions, and register Go functions to be called from JavaScript.

The type system is deliberately loose — `js.Value` can represent anything. You're responsible for type checking at the boundaries. This is the main friction point when coming from Go's strong type system.

Some patterns I use consistently:

**Returning errors as objects.** Rather than returning raw strings for errors, return a JavaScript object with a `success` boolean and either a `value` or `error` field. This gives JavaScript callers a consistent interface:

```go
func safeResult(value string) map[string]interface{} {
    return map[string]interface{}{
        "success": true,
        "value":   value,
    }
}

func errorResult(msg string) map[string]interface{} {
    return map[string]interface{}{
        "success": false,
        "error":   msg,
    }
}
```

**Registering multiple functions.** Rather than one function per compilation, register all your exported functions in main:

```go
func main() {
    js.Global().Set("validateEmail", js.FuncOf(validateEmail))
    js.Global().Set("parseAddress", js.FuncOf(parseAddress))
    js.Global().Set("formatCurrency", js.FuncOf(formatCurrency))
    select {}
}
```

**Async callbacks.** If your Go function does I/O or computation that should be async from JavaScript's perspective, you can call a provided callback when done:

```go
func processAsync(this js.Value, args []js.Value) interface{} {
    callback := args[0]
    go func() {
        result := doSlowThing()
        callback.Invoke(js.Null(), js.ValueOf(result))
    }()
    return js.Undefined()
}
```

## Binary Size

Go's WASM binaries are large by default. A minimal "hello world" compiles to about 2MB. A real program with dependencies can easily hit 8-15MB. This is Go's runtime — garbage collector, goroutine scheduler, reflection support — baked in.

For browser delivery, this matters. A 10MB WASM file is a significant load-time cost. Mitigations:

**Compression.** WASM compresses extremely well. A 10MB binary typically gzips to 2-3MB and brotli-compresses even further. Serve it with `Content-Encoding: br` or `Content-Encoding: gzip`. Make sure your CDN or web server is doing this.

**Caching.** WASM modules are cached aggressively by browsers once loaded. A first-visit penalty is much less bad than a penalty on every page load. Set long-lived cache headers on your `.wasm` file with content-hashed filenames.

**TinyGo.** For very size-sensitive use cases, TinyGo is a Go compiler designed for constrained environments. It produces much smaller binaries by excluding parts of the standard library and using a simpler runtime. The tradeoff: not all Go packages work with TinyGo, and some language features behave differently. For pure computation logic with no exotic dependencies, TinyGo works well and can reduce binary size by 10x.

## Running WASM Outside the Browser

`GOARCH=wasm GOOS=js` produces a binary for JavaScript environments (browser or Node). But there's a second target: `GOARCH=wasm GOOS=wasip1`, introduced in Go 1.21 for the WASI interface.

WASI (WebAssembly System Interface) is a standard that gives WASM modules controlled access to filesystem, network, and OS primitives — without a JavaScript runtime. A WASI binary can run in Wasmtime, WasmEdge, or any WASI-compliant runtime.

```bash
GOARCH=wasm GOOS=wasip1 go build -o tool.wasm .
wasmtime run tool.wasm
```

This is the backend story, which the next lesson covers in depth. WASI opens up use cases far beyond the browser: edge functions, serverless workers, plugin systems, and portable CLI tools.

## A Real Use Case

The validation example I started with is real. We had email validation logic that needed to run in three places. The Go implementation was the authoritative one because it had the most test coverage and handled all the edge cases. Rather than port it to TypeScript and risk drift, I compiled it to WASM and loaded it in the browser.

The JavaScript client loads the WASM once, and every validation call is a synchronous in-browser function call with Go semantics. No network round-trip for validation. The frontend and backend now reject exactly the same set of invalid addresses because they're literally running the same code.

That's the promise of WASM: not just "runs anywhere," but "the same binary runs everywhere, and you only write the logic once." The next lesson explores what happens when you take that idea to the server.
