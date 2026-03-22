---
title: "Lesson 7: WASI — WebAssembly beyond the browser"
author: Atharva Pandey
series: "Rust WebAssembly"
lesson: 7
keywords: [Rust, rust, WebAssembly, WASM, WASI, Wasmtime, Wasmer, edge computing, sandbox, capability-based security]
tags: [Rust tutorial, rust, webassembly, wasm]
date: '2025-07-15T19:22:41.000Z'
---

About a year ago, I deployed a Rust function as a Cloudflare Worker using WASM. Cold start: 0.5ms. Compare that to a Lambda function in any other language — 50-500ms on a cold start. That's when I realized WASI isn't some academic curiosity. It's the future of server-side compute.

Solomon Hykes — the guy who created Docker — tweeted this back in 2019: "If WASM+WASI existed in 2008, we wouldn't have needed to create Docker." He wasn't being hyperbolic. WASI gives you true sandboxing, near-native performance, cross-platform portability, and sub-millisecond startup. It's what containers promised, but at a fundamentally lower level.

## What WASI Actually Is

WASI stands for WebAssembly System Interface. Remember how `wasm32-unknown-unknown` can't access files, networks, or clocks? WASI provides a standardized API for those system operations — but with a crucial twist: capability-based security.

In traditional operating systems, a process can access any file the user has permissions for. In WASI, a module can only access resources that are *explicitly granted* to it. Want to read `/tmp/data.txt`? Someone has to give you a file descriptor for `/tmp` first. No ambient authority.

```
Traditional:  process → syscall → kernel → resource
WASI:         module → WASI API → runtime → capability check → resource
```

## Setting Up Wasmtime

Wasmtime is the reference WASI runtime, built by the Bytecode Alliance (which includes Mozilla, Fastly, Intel, and Microsoft). It's what I use for development and testing.

```bash
# Install Wasmtime
curl https://wasmtime.dev/install.sh -sSf | bash

# Add the WASI target to Rust
rustup target add wasm32-wasip1
```

Note: the target used to be called `wasm32-wasi`, then `wasm32-wasip1` for WASI Preview 1. The naming is confusing, but `wasm32-wasip1` is what you want today.

## Your First WASI Program

```rust
// src/main.rs
use std::fs;
use std::io::{self, Read, Write};

fn main() {
    println!("Hello from WASI!");

    // Standard I/O works
    eprintln!("This goes to stderr");

    // Command-line arguments work
    let args: Vec<String> = std::env::args().collect();
    println!("Args: {:?}", args);

    // Environment variables work (if granted)
    if let Ok(val) = std::env::var("MY_VAR") {
        println!("MY_VAR = {}", val);
    }

    // File I/O works (if the directory is pre-opened)
    match fs::read_to_string("/data/input.txt") {
        Ok(contents) => {
            println!("File contents: {}", contents);

            // Process and write output
            let upper = contents.to_uppercase();
            fs::write("/data/output.txt", upper).expect("Failed to write");
            println!("Output written to /data/output.txt");
        }
        Err(e) => eprintln!("Could not read file: {}", e),
    }
}
```

Build and run:

```bash
# Build for WASI
cargo build --target wasm32-wasip1 --release

# Run with Wasmtime
wasmtime target/wasm32-wasip1/release/my-wasi-app.wasm

# Grant access to a directory
wasmtime --dir /tmp/mydata::/data target/wasm32-wasip1/release/my-wasi-app.wasm

# Pass environment variables
wasmtime --env MY_VAR=hello target/wasm32-wasip1/release/my-wasi-app.wasm
```

The `--dir /tmp/mydata::/data` flag maps the host directory `/tmp/mydata` to `/data` inside the WASM sandbox. Without this flag, the module can't access any files at all. That's the capability model in action.

## Real-World Example: A CLI Tool as WASM

Let's build something practical — a log analyzer that you can distribute as a single `.wasm` file:

```toml
[package]
name = "log-analyzer"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

```rust
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::{self, BufRead, BufReader};

#[derive(Debug, Deserialize)]
struct LogEntry {
    timestamp: String,
    level: String,
    message: String,
    #[serde(default)]
    service: String,
    #[serde(default)]
    duration_ms: Option<f64>,
}

#[derive(Debug, Serialize)]
struct AnalysisReport {
    total_entries: usize,
    by_level: HashMap<String, usize>,
    by_service: HashMap<String, usize>,
    error_messages: Vec<String>,
    slow_requests: Vec<SlowRequest>,
    avg_duration_ms: f64,
    p99_duration_ms: f64,
}

#[derive(Debug, Serialize)]
struct SlowRequest {
    timestamp: String,
    service: String,
    duration_ms: f64,
    message: String,
}

fn analyze(entries: &[LogEntry]) -> AnalysisReport {
    let mut by_level: HashMap<String, usize> = HashMap::new();
    let mut by_service: HashMap<String, usize> = HashMap::new();
    let mut error_messages = Vec::new();
    let mut durations: Vec<f64> = Vec::new();
    let mut slow_requests = Vec::new();

    for entry in entries {
        *by_level.entry(entry.level.clone()).or_default() += 1;

        if !entry.service.is_empty() {
            *by_service.entry(entry.service.clone()).or_default() += 1;
        }

        if entry.level == "ERROR" {
            error_messages.push(format!(
                "[{}] {}: {}",
                entry.timestamp, entry.service, entry.message
            ));
        }

        if let Some(d) = entry.duration_ms {
            durations.push(d);
            if d > 1000.0 {
                slow_requests.push(SlowRequest {
                    timestamp: entry.timestamp.clone(),
                    service: entry.service.clone(),
                    duration_ms: d,
                    message: entry.message.clone(),
                });
            }
        }
    }

    durations.sort_by(|a, b| a.partial_cmp(b).unwrap());

    let avg_duration = if durations.is_empty() {
        0.0
    } else {
        durations.iter().sum::<f64>() / durations.len() as f64
    };

    let p99_duration = if durations.is_empty() {
        0.0
    } else {
        let idx = (durations.len() as f64 * 0.99) as usize;
        durations[idx.min(durations.len() - 1)]
    };

    // Keep only the top 10 slowest
    slow_requests.sort_by(|a, b| b.duration_ms.partial_cmp(&a.duration_ms).unwrap());
    slow_requests.truncate(10);

    AnalysisReport {
        total_entries: entries.len(),
        by_level,
        by_service,
        error_messages,
        slow_requests,
        avg_duration_ms: avg_duration,
        p99_duration_ms: p99_duration,
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();

    let input_path = args.get(1).map(|s| s.as_str()).unwrap_or("/data/logs.json");
    let output_path = args.get(2).map(|s| s.as_str()).unwrap_or("/data/report.json");

    eprintln!("Reading logs from: {}", input_path);

    let file = match fs::File::open(input_path) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("Failed to open {}: {}", input_path, e);
            std::process::exit(1);
        }
    };

    let reader = BufReader::new(file);
    let mut entries = Vec::new();

    for line in reader.lines() {
        let line = line.expect("Failed to read line");
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<LogEntry>(&line) {
            Ok(entry) => entries.push(entry),
            Err(e) => eprintln!("Skipping malformed line: {}", e),
        }
    }

    eprintln!("Parsed {} log entries", entries.len());

    let report = analyze(&entries);
    let report_json = serde_json::to_string_pretty(&report).expect("Failed to serialize");

    fs::write(output_path, &report_json).expect("Failed to write report");
    println!("{}", report_json);
    eprintln!("Report written to: {}", output_path);
}
```

Build and run:

```bash
cargo build --target wasm32-wasip1 --release

# The binary is ~2MB — a complete log analyzer in a sandboxed module
ls -lh target/wasm32-wasip1/release/log-analyzer.wasm

# Run it
wasmtime --dir ./test-data::/data \
    target/wasm32-wasip1/release/log-analyzer.wasm \
    -- /data/logs.json /data/report.json
```

This single `.wasm` file runs on Linux, macOS, Windows — anywhere Wasmtime (or any WASI runtime) is available. No containers, no dependencies, no dynamic libraries.

## Embedding WASI in Your Rust Application

You can use Wasmtime as a library to run WASM modules from your Rust application. This is how plugin systems work:

```toml
[dependencies]
wasmtime = "19"
wasmtime-wasi = "19"
anyhow = "1"
```

```rust
use anyhow::Result;
use wasmtime::*;
use wasmtime_wasi::preview1::{self, WasiP1Ctx};
use wasmtime_wasi::WasiCtxBuilder;

fn main() -> Result<()> {
    let engine = Engine::default();
    let module = Module::from_file(&engine, "plugin.wasm")?;

    let mut linker = Linker::new(&engine);
    preview1::add_to_linker_sync(&mut linker, |ctx: &mut WasiP1Ctx| ctx)?;

    let wasi_ctx = WasiCtxBuilder::new()
        .inherit_stdout()
        .inherit_stderr()
        .preopened_dir("./data", "/data", wasmtime_wasi::DirPerms::all(), wasmtime_wasi::FilePerms::all())?
        .env("APP_ENV", "production")
        .build_p1();

    let mut store = Store::new(&engine, wasi_ctx);

    let instance = linker.instantiate(&mut store, &module)?;

    // Call the _start function (main)
    let start = instance.get_typed_func::<(), ()>(&mut store, "_start")?;
    start.call(&mut store, ())?;

    Ok(())
}
```

This pattern — embedding a WASM runtime in your application — is how systems like Envoy (for proxy filters), Fermyon Spin (for serverless), and various game engines implement plugin systems. The plugin runs in a sandbox, can't crash the host, and can only access resources you explicitly provide.

## WASI vs Docker

I keep coming back to the Docker comparison because it's the right way to think about WASI's value proposition:

| | Docker | WASI |
|---|---|---|
| **Startup** | 500ms - 2s | 0.5ms - 5ms |
| **Memory overhead** | 50-200MB | 1-10MB |
| **Isolation** | OS-level (cgroups, namespaces) | WASM sandbox |
| **Portability** | Linux containers (cross-arch with emulation) | True cross-platform, cross-arch |
| **Binary size** | 50MB - 1GB (images) | 0.5MB - 10MB |
| **Security** | Root escape vulnerabilities exist | Mathematical sandbox guarantee |
| **Ecosystem** | Massive | Growing |

WASI won't replace Docker for everything. You still need containers for complex applications with many system dependencies. But for compute-focused workloads — serverless functions, edge computing, plugin systems — WASI is objectively better.

## Edge Computing with WASI

This is where WASI really shines. Cloudflare Workers, Fastly Compute, and Fermyon Spin all run WASM modules at the edge:

```rust
// A Spin handler (Fermyon's WASI-based platform)
use spin_sdk::http::{IntoResponse, Request, Response};
use spin_sdk::http_component;

#[http_component]
fn handle_request(req: Request) -> anyhow::Result<impl IntoResponse> {
    let path = req.uri().path();

    match path {
        "/api/health" => Ok(Response::builder()
            .status(200)
            .body("OK")
            .build()),

        "/api/process" => {
            let body = req.body();
            let input: serde_json::Value = serde_json::from_slice(body)?;

            // Do computation...
            let result = process_data(&input);

            Ok(Response::builder()
                .status(200)
                .header("content-type", "application/json")
                .body(serde_json::to_string(&result)?)
                .build())
        }

        _ => Ok(Response::builder()
            .status(404)
            .body("Not Found")
            .build()),
    }
}

fn process_data(input: &serde_json::Value) -> serde_json::Value {
    // Your business logic here
    serde_json::json!({
        "processed": true,
        "input_keys": input.as_object().map(|o| o.len()).unwrap_or(0),
    })
}
```

Deploy with:

```bash
spin build
spin deploy  # Deploys to Fermyon Cloud
```

Your function starts in under a millisecond, runs in a secure sandbox, and scales to zero when not in use. Try doing that with a container.

## WASI Preview 2 and the Async World

WASI is evolving. Preview 1 (what we've been using) provides synchronous, POSIX-like APIs. Preview 2 introduces:

- **Async I/O** — non-blocking networking and file operations
- **The Component Model** — composable modules (we'll cover this in Lesson 8)
- **Typed interfaces** — WIT (WASM Interface Type) definitions for cross-language interop
- **HTTP as a first-class concept** — not just raw sockets

Here's what WASI Preview 2 networking looks like:

```rust
// Using the wasi crate for Preview 2
use wasi::http::types::*;

// This is conceptual — the API is still stabilizing
fn handle_request(request: IncomingRequest) -> OutgoingResponse {
    let method = request.method();
    let path = request.path_with_query().unwrap_or_default();

    let response = OutgoingResponse::new(Fields::new());
    response.set_status_code(200).unwrap();

    let body = response.body().unwrap();
    let stream = body.write().unwrap();
    stream.blocking_write_and_flush(b"Hello from WASI P2!").unwrap();

    response
}
```

The transition from Preview 1 to Preview 2 is happening now. For new projects, check whether your target runtime supports P2 — many are migrating.

## Practical Use Cases I've Actually Deployed

**1. Serverless API handlers** — 0.5ms cold start, runs on Cloudflare's edge network. Each request is isolated by the WASM sandbox.

**2. Data transformation plugins** — Users upload WASM modules that transform their data. The sandbox ensures they can't access other users' data or crash the host.

**3. CI/CD pipeline steps** — Build steps compiled to WASM run identically on every developer's machine and in CI, regardless of OS.

**4. Configuration validation** — Complex validation logic compiled to WASM and embedded in both the server (Rust) and the browser (via wasm-bindgen). Single source of truth, two deployment targets.

## What Doesn't Work in WASI (Yet)

- **Raw network sockets** — Preview 1 doesn't have them. Preview 2 adds HTTP but raw TCP/UDP is still limited.
- **Threading** — WASI threads proposal exists but isn't widely implemented.
- **GPU access** — No WebGPU equivalent in WASI (yet).
- **Dynamic linking** — Limited support. The Component Model (Lesson 8) addresses this.
- **Signals** — No POSIX signal handling.

## The Honest Take

WASI is production-ready for specific use cases: edge computing, plugin systems, sandboxed execution, and portable CLI tools. It's not ready to replace Docker for running a full Rails app or a complex microservice with many system dependencies.

But the trajectory is clear. The ecosystem is growing fast, major companies are investing heavily (Fastly, Cloudflare, Microsoft, Docker Inc.), and the standards are solidifying. If you're building Rust, learning WASI now puts you ahead of the curve.

In the final lesson, we're looking at the Component Model — the piece that makes all of this composable. It's how you build a WASM module in Rust that calls a WASM module written in Python that calls one written in Go. Language boundaries dissolve.
