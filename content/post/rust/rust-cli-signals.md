---
title: "Lesson 5: Signal Handling and Graceful Shutdown — Clean exits"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 5
keywords: [Rust, rust, CLI, signals, SIGINT, SIGTERM, graceful shutdown, ctrlc, cleanup]
tags: [Rust tutorial, rust, cli]
date: '2024-09-09T09:17:00.000Z'
---

I had a CLI tool that converted video files. Big ones — 10, 20 gigabytes each. The conversion created a temp file, wrote the converted output there, then renamed it to the final destination. Hit Ctrl+C at the wrong moment and you'd get a half-written 15GB temp file sitting on disk. Users ran out of disk space without knowing why. All because I never handled signals properly.

## What Happens When You Press Ctrl+C

When you press Ctrl+C in a terminal, the kernel sends `SIGINT` (signal interrupt) to the foreground process group. By default, this kills your program immediately. No destructors run. No `Drop` implementations execute. Temporary files stay on disk. Database connections aren't closed. Partial writes aren't rolled back.

On Unix, there are several signals that matter for CLI tools:

- **SIGINT** (2) — Ctrl+C. User wants to stop the program.
- **SIGTERM** (15) — `kill <pid>`. Polite termination request.
- **SIGQUIT** (3) — Ctrl+\\. Like SIGINT but generates a core dump.
- **SIGHUP** (1) — Terminal closed. Process should clean up and exit.
- **SIGPIPE** (13) — Pipe reader closed. We covered this in Lesson 2.

Windows has its own mechanism (`CTRL_C_EVENT`, `CTRL_BREAK_EVENT`), but the ctrlc crate abstracts over both platforms.

## The Simple Case: ctrlc Crate

For most CLI tools, the `ctrlc` crate is all you need:

```toml
[dependencies]
ctrlc = "3.4"
```

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn main() {
    let running = Arc::new(AtomicBool::new(true));
    let r = running.clone();

    ctrlc::set_handler(move || {
        eprintln!("\nReceived Ctrl+C, shutting down...");
        r.store(false, Ordering::SeqCst);
    })
    .expect("Error setting Ctrl+C handler");

    // Main work loop
    let mut count = 0;
    while running.load(Ordering::SeqCst) {
        count += 1;
        println!("Working... iteration {}", count);
        thread::sleep(Duration::from_secs(1));
    }

    println!("Cleanup complete. Processed {} iterations.", count);
}
```

The pattern: an `AtomicBool` acts as a flag. The signal handler sets it to `false`. Your main loop checks the flag each iteration. When it goes false, you break out of the loop and run your cleanup code.

Why `AtomicBool` instead of a regular `bool` behind a `Mutex`? Signal handlers have severe restrictions on what you can do. You can't allocate memory, you can't take locks (a lock might be held when the signal fires, causing a deadlock). Atomic operations are signal-safe.

## Cleanup With RAII and Drop

Sometimes the `AtomicBool` pattern isn't enough. You have resources that need cleanup regardless of *how* the program exits — normal completion, error, or signal. Rust's `Drop` trait is your friend, but only if you give it a chance to run:

```rust
use std::fs;
use std::path::{Path, PathBuf};

struct TempFile {
    path: PathBuf,
}

impl TempFile {
    fn new(path: impl Into<PathBuf>) -> std::io::Result<Self> {
        let path = path.into();
        fs::File::create(&path)?;
        Ok(Self { path })
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempFile {
    fn drop(&mut self) {
        if self.path.exists() {
            if let Err(e) = fs::remove_file(&self.path) {
                eprintln!("Warning: failed to clean up '{}': {}", self.path.display(), e);
            }
        }
    }
}

fn main() {
    // Set up signal handler so Drop runs
    let running = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
    let r = running.clone();

    ctrlc::set_handler(move || {
        r.store(false, std::sync::atomic::Ordering::SeqCst);
    })
    .unwrap();

    // TempFile will be cleaned up when it goes out of scope
    let temp = TempFile::new("/tmp/myapp_work.tmp").unwrap();
    println!("Created temp file: {}", temp.path().display());

    // Simulate work
    let mut i = 0;
    while running.load(std::sync::atomic::Ordering::SeqCst) {
        i += 1;
        if i > 10 {
            break;
        }
        println!("Processing step {}...", i);
        std::thread::sleep(std::time::Duration::from_secs(1));
    }

    // `temp` dropped here, file cleaned up automatically
    println!("Done. Temp file cleaned up.");
}
```

The key insight: by catching the signal and setting a flag instead of exiting immediately, we allow the normal Rust control flow to unwind. Stack-allocated values get dropped. `Drop` implementations run. Temp files are cleaned up.

If you call `std::process::exit()` from a signal handler, none of this happens. Don't do it.

## File Operations: Atomic Writes

For the video converter problem I mentioned at the start, the real fix is atomic writes. Don't write directly to the output file — write to a temp file in the same directory, then rename:

```rust
use std::fs;
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};

struct AtomicFile {
    temp_path: PathBuf,
    final_path: PathBuf,
    writer: BufWriter<fs::File>,
}

impl AtomicFile {
    fn create(final_path: impl Into<PathBuf>) -> io::Result<Self> {
        let final_path = final_path.into();
        let temp_path = final_path.with_extension("tmp");

        let file = fs::File::create(&temp_path)?;
        let writer = BufWriter::new(file);

        Ok(Self {
            temp_path,
            final_path,
            writer,
        })
    }

    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        self.writer.write_all(data)
    }

    fn commit(mut self) -> io::Result<()> {
        self.writer.flush()?;
        // Ensure data is on disk
        self.writer.get_ref().sync_all()?;
        // Atomic rename
        fs::rename(&self.temp_path, &self.final_path)?;
        // Prevent Drop from deleting the file
        self.temp_path = PathBuf::new();
        Ok(())
    }
}

impl Drop for AtomicFile {
    fn drop(&mut self) {
        // If commit() wasn't called, clean up the temp file
        if self.temp_path.as_os_str().len() > 0 {
            let _ = fs::remove_file(&self.temp_path);
        }
    }
}

fn main() -> io::Result<()> {
    let mut file = AtomicFile::create("output.txt")?;

    for i in 0..1000 {
        file.write_all(format!("Line {}\n", i).as_bytes())?;
    }

    file.commit()?;
    println!("File written successfully");
    Ok(())
}
```

If Ctrl+C hits during the write, `Drop` runs and deletes the temp file. The original `output.txt` is never touched. If the write completes, `commit()` does an atomic rename — which on most filesystems is guaranteed to either fully succeed or fully fail.

## signal-hook: The Full-Power Option

For more complex signal handling — catching SIGTERM, SIGHUP, handling multiple signals differently — the `signal-hook` crate gives you more control:

```toml
[dependencies]
signal-hook = "0.3"
```

```rust
use signal_hook::consts::{SIGINT, SIGTERM, SIGHUP};
use signal_hook::iterator::Signals;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn main() {
    let running = Arc::new(AtomicBool::new(true));

    // Handle signals in a dedicated thread
    let r = running.clone();
    let mut signals = Signals::new(&[SIGINT, SIGTERM, SIGHUP])
        .expect("Failed to register signal handlers");

    thread::spawn(move || {
        for sig in signals.forever() {
            match sig {
                SIGINT => {
                    eprintln!("\nInterrupted (SIGINT)");
                    r.store(false, Ordering::SeqCst);
                }
                SIGTERM => {
                    eprintln!("\nTerminated (SIGTERM)");
                    r.store(false, Ordering::SeqCst);
                }
                SIGHUP => {
                    eprintln!("\nHangup (SIGHUP) — reloading config");
                    // In a real app, trigger config reload here
                }
                _ => unreachable!(),
            }
        }
    });

    // Main work loop
    while running.load(Ordering::SeqCst) {
        println!("Working...");
        thread::sleep(Duration::from_secs(1));
    }

    println!("Graceful shutdown complete.");
}
```

The `SIGHUP` reload pattern is how daemons like nginx handle config reloads. Send `kill -HUP <pid>` and the process reloads its configuration without restarting. Very useful for long-running CLI tools.

## Timeout on Shutdown

Sometimes cleanup takes too long. A database connection might hang, a network request might not respond. You want to give cleanup a deadline:

```rust
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use std::thread;

fn main() {
    let running = Arc::new(AtomicBool::new(true));
    let signal_count = Arc::new(AtomicU8::new(0));

    let r = running.clone();
    let sc = signal_count.clone();

    ctrlc::set_handler(move || {
        let count = sc.fetch_add(1, Ordering::SeqCst);
        match count {
            0 => {
                eprintln!("\nShutting down gracefully... (press Ctrl+C again to force)");
                r.store(false, Ordering::SeqCst);
            }
            _ => {
                eprintln!("\nForced exit!");
                std::process::exit(130); // 128 + SIGINT(2)
            }
        }
    })
    .unwrap();

    // Main work
    while running.load(Ordering::SeqCst) {
        println!("Working...");
        thread::sleep(Duration::from_secs(1));
    }

    // Cleanup with timeout
    let deadline = Instant::now() + Duration::from_secs(5);

    eprintln!("Cleaning up (timeout: 5s)...");
    cleanup_with_timeout(deadline);

    eprintln!("Shutdown complete.");
}

fn cleanup_with_timeout(deadline: Instant) {
    // Simulate cleanup tasks
    let tasks = vec!["closing connections", "flushing buffers", "removing temp files"];

    for task in tasks {
        if Instant::now() > deadline {
            eprintln!("Cleanup timeout reached, skipping remaining tasks");
            return;
        }
        eprint!("  {}... ", task);
        thread::sleep(Duration::from_millis(500)); // simulate cleanup
        eprintln!("done");
    }
}
```

The double Ctrl+C pattern is important UX. First press: graceful shutdown. Second press: immediate exit. This is what Docker, kubectl, and most production tools do. Users expect it.

## Async Signal Handling With Tokio

If you're writing an async CLI tool with tokio, signal handling integrates with the runtime:

```rust
use tokio::signal;
use tokio::time::{sleep, Duration};

#[tokio::main]
async fn main() {
    let shutdown = async {
        signal::ctrl_c()
            .await
            .expect("Failed to install CTRL+C handler");
        println!("\nReceived shutdown signal");
    };

    tokio::select! {
        _ = run_app() => {
            println!("App completed normally");
        }
        _ = shutdown => {
            println!("Shutting down...");
            // Run async cleanup
            cleanup().await;
            println!("Cleanup complete");
        }
    }
}

async fn run_app() {
    loop {
        println!("Processing...");
        sleep(Duration::from_secs(1)).await;
    }
}

async fn cleanup() {
    println!("  Closing connections...");
    sleep(Duration::from_millis(200)).await;
    println!("  Flushing data...");
    sleep(Duration::from_millis(200)).await;
    println!("  Done.");
}
```

`tokio::select!` races the main work against the shutdown signal. When Ctrl+C fires, the main task is dropped and cleanup runs. Clean, idiomatic, no atomic bools needed.

## Exit Codes

One last thing people get wrong — exit codes. Your tool's exit code communicates success or failure to scripts, CI systems, and other tools:

```rust
use std::process::ExitCode;

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,         // 0
        Err(AppError::NotFound) => ExitCode::from(1),
        Err(AppError::Permission) => ExitCode::from(2),
        Err(AppError::Interrupted) => ExitCode::from(130), // 128 + SIGINT
        Err(AppError::Other(msg)) => {
            eprintln!("Error: {}", msg);
            ExitCode::FAILURE // 1
        }
    }
}

enum AppError {
    NotFound,
    Permission,
    Interrupted,
    Other(String),
}

fn run() -> Result<(), AppError> {
    // Your app logic
    Ok(())
}
```

`ExitCode` was stabilized in Rust 1.61. Before that, you had to use `std::process::exit()` which doesn't run destructors. `ExitCode` returned from `main` does the right thing — it lets all cleanup happen, then reports the code.

Convention: 0 for success, 1 for general errors, 2 for usage errors, 130 for SIGINT (128 + signal number). Tools like `grep` use specific codes (1 = no matches, 2 = error). Document yours.

Signal handling is one of those things that separates a script from a tool. Get it right and users never think about it. Get it wrong and they lose data. Next up — subcommands and building git-style CLI interfaces.
