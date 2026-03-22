---
title: "Lesson 2: Server-Side WASM — WASI, edge computing, and the universal binary"
author: Atharva Pandey
keywords:
  - WASI
  - server-side WebAssembly
  - edge computing WASM
  - Wasmtime
  - WebAssembly plugin system
  - universal binary
tags:
  - fundamentals
  - WebAssembly
  - WASM
series: "WebAssembly for Backend Engineers"
lesson: 2
date: '2024-10-11T00:00:00.000Z'
---

When most people talk about WebAssembly, they mean the browser. That's where it started, that's where the tutorials are, and that's where most of the public discourse still lives. But the more interesting story for backend engineers is what happens when you take the same sandboxed, portable binary model and apply it on the server.

Server-side WebAssembly — specifically WebAssembly with WASI (the WebAssembly System Interface) — is not a toy. Cloudflare Workers runs WASM. Fastly Compute runs WASM. Fermyon Spin is built on it. The pattern is spreading from edge providers into general-purpose infrastructure. Understanding it now puts you ahead of where most backend engineers are.

## The Problem With Containers

Containers solved a real problem: application portability across environments. Before Docker, "it works on my machine" was a sincere complaint. Container images bundle everything needed to run an application, and they run consistently across laptops, CI, and production.

But containers have real costs. A container image is typically hundreds of megabytes. Cold start time — from "launch this workload" to "ready to serve traffic" — is measured in hundreds of milliseconds to seconds. Isolation is process-level, managed by the kernel, which means the kernel's attack surface is shared between tenants.

For long-running services, these costs are largely amortized. For short-lived functions, edge workers, or plugin execution, they matter.

WASM with WASI offers an alternative. A WASM binary is kilobytes to megabytes. Cold start time in a mature WASM runtime is microseconds to low milliseconds. Isolation is sandboxed at the language VM level, not the kernel level — the attack surface is the WASM runtime, not the entire OS kernel.

The tradeoff: containers can run anything. WASM requires your code to compile to the WASM target and to work within the WASI interface. That's a constraint, but for the workloads where it fits, the operational advantages are significant.

## WASI: System Access for WASM

A bare WASM module has no access to the outside world. It cannot read files, open network connections, or print to stdout. This is intentional — the sandbox is the whole point.

WASI defines a standard set of system capabilities that a host can optionally grant to a WASM module. Think of it as capability-based access control at the system call level. The host says: "I'll give you read access to this specific directory, but no write access, no network access, and no access to the rest of the filesystem." The module can request capabilities, the host decides whether to grant them.

The WASI spec defines interfaces for:
- Filesystem operations (with path-scoped permissions)
- Clocks and timers
- Random number generation
- Stdin/stdout/stderr
- Environment variables and command-line arguments
- Socket operations (in newer WASI preview 2 proposals)

What WASI does not (yet) include in stable form: most network I/O beyond basic TCP sockets, threading primitives across WASM modules, and graphics. The spec is evolving rapidly; WASI preview 2 (component model) addresses several gaps that preview 1 had.

## Compiling Go for WASI

Go 1.21 added first-class WASI support via the `wasip1` target:

```bash
GOARCH=wasm GOOS=wasip1 go build -o app.wasm ./cmd/app
```

A `wasip1` binary behaves like a command-line application. It reads from stdin, writes to stdout, receives environment variables and arguments, and can access the filesystem within the paths the host grants.

Run it with Wasmtime:

```bash
wasmtime run \
  --dir /tmp/data \
  --env API_KEY=secret \
  app.wasm -- --input /tmp/data/input.json
```

The `--dir /tmp/data` flag grants the module access to that path. Nothing else. It cannot read `/etc/passwd` or access the network unless you explicitly grant those capabilities.

A real use case: data transformation pipelines. You have a business rule for transforming records — written in Go, tested, well-understood. You want to run it as a processing step on every record in a queue, potentially distributed across many machines, potentially provided by a third party. A WASM module is a natural fit: portable, sandboxed, fast to start, and easy to version-swap.

## Edge Computing

Edge computing platforms are the production environment where WASM is most mature right now. The model: instead of serving all requests from a central region, you run your compute at CDN edge nodes — potentially hundreds of locations worldwide — so the logic runs as close as possible to the user.

JavaScript has dominated edge workers until recently because the V8 engine is already embedded in Chrome, which runs at every edge node. But WASM is now a first-class target on Cloudflare Workers, Fastly Compute, and Fermyon Spin.

With Cloudflare Workers and WASM:

```rust
// This is Rust, but the same pattern applies from Go via TinyGo
use worker::*;

#[event(fetch)]
async fn main(req: Request, env: Env, _ctx: Context) -> Result<Response> {
    // This runs at the edge, microseconds from the user
    let url = req.url()?;
    Response::ok(format!("Hello from the edge, path: {}", url.path()))
}
```

From Go, using TinyGo (which produces smaller binaries suitable for edge deployments):

```bash
tinygo build -o main.wasm -target wasi main.go
wrangler deploy  # Cloudflare deployment
```

The latency improvement for certain workloads is dramatic. An authentication check or geo-routing decision that previously required a round-trip to a US-based API server can now happen at the edge node in London, Tokyo, or São Paulo with milliseconds of latency instead of hundreds.

## Plugin Systems

One of the most underappreciated applications of server-side WASM is plugin systems. The traditional plugin architecture for backend systems is either:

1. **In-process plugins** (shared libraries, `.so` files): Fast, but a plugin crash takes down the host, and a malicious plugin has full access to the host's memory.
2. **Out-of-process plugins** (subprocess, RPC): Safe, but slow — every plugin call involves process spawning or IPC overhead.

WASM offers a third option: in-process execution with memory isolation. The host application embeds a WASM runtime (Wasmtime, Wasmer, or WasmEdge all have Go bindings), and plugins are WASM modules. Plugin calls are function calls — fast. Plugin bugs are sandboxed — they cannot corrupt host memory or escape the sandbox.

A Go host embedding Wasmtime via `wasmtime-go`:

```go
engine := wasmtime.NewEngine()
store := wasmtime.NewStore(engine)

// Load the plugin
module, err := wasmtime.NewModuleFromFile(engine, "plugin.wasm")
if err != nil {
    return fmt.Errorf("loading plugin: %w", err)
}

// Create a linker that controls what the plugin can access
linker := wasmtime.NewLinker(engine)
wasmtime.NewWasiConfig() // Configure filesystem/env access
linker.DefineWasi()

instance, err := linker.Instantiate(store, module)
if err != nil {
    return fmt.Errorf("instantiating plugin: %w", err)
}

// Call a plugin function
fn := instance.GetFunc(store, "process")
result, err := fn.Call(store, inputData)
```

The plugin author writes their plugin in any language that compiles to WASM. The host author defines the interface — what functions the plugin must export, what capabilities it receives. Security is enforced at the sandbox boundary, not by code review of the plugin itself.

This pattern is appearing in real infrastructure: Envoy's WebAssembly filter extension system, OpenPolicyAgent's Rego evaluation (which can run in WASM), and several observability tools that want user-defined transformation logic.

## The Component Model

The biggest limitation of WASM as described so far is the interface between host and module. Passing complex data structures (not just integers and floats) requires manual memory management — you write data into the module's linear memory, pass a pointer, and read the result back. This is error-prone and ties your host and plugin to specific memory layouts.

WASI preview 2 introduces the Component Model, which standardizes how WASM modules define and expose interfaces. Components can export typed interfaces — strings, records, enums, variants — and the toolchain generates the serialization automatically. Communicating between components becomes like calling a typed API, not like doing manual memory management.

As of late 2024, the component model is stable and supported in Wasmtime, with growing support in other runtimes. Go support via TinyGo is advancing. This is the direction the ecosystem is heading, and for new projects it's worth designing toward the component model even if full support isn't universal yet.

## Honest Assessment

Server-side WASM is real and worth knowing. It is not yet the default answer for most backend problems. Mature, well-understood deployment patterns for WASM-based services are still forming. Debugging WASM modules in production is harder than debugging native binaries — limited debugger integration, less familiar tooling. Not all Go libraries work correctly under WASI (anything that makes direct system calls without going through the standard library may behave unexpectedly).

The use cases where it's clearly justified today: edge workers where the latency gains are measurable, plugin systems where sandboxed extensibility is architecturally important, and polyglot environments where sharing compiled business logic across language boundaries justifies the compilation target.

The use cases where containers or native Go binaries are still the better answer: standard HTTP services, anything that needs mature Linux ecosystem integrations, anything where your team is unfamiliar with WASM debugging workflows.

WASM is not the future of all backend computing. But it is solving real problems in specific environments, and understanding it now means you'll be ready to apply it correctly when those problems show up in your work.

---

🎓 Course Complete! You've finished **WebAssembly for Backend Engineers**. You can compile Go to WASM, expose functions to JavaScript environments via `syscall/js`, understand the WASI capability model, and identify where server-side WASM — whether at the edge, in a plugin system, or in a processing pipeline — is genuinely the right tool. That's a set of options most backend engineers don't have yet.
