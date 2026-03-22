---
title: "Lesson 7: std::process — Running external commands"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 7
keywords: [Rust, rust, standard library, process, Command, child process, spawn, pipe, exit]
tags: [Rust tutorial, rust, stdlib]
date: '2024-09-29T19:15:00.000Z'
---

I once wrote a deployment script in Bash that grew to 800 lines with nested conditionals, string interpolation bugs, and error handling that amounted to "hope for the best." Rewrote it in Rust using `std::process::Command` — same functionality, but with actual error handling and type safety. The number of failed deployments dropped to zero.

## Command — The Builder

`std::process::Command` is a builder for spawning child processes. You construct the command, configure it, then either run it to completion or spawn it and interact with its I/O.

```rust
use std::process::Command;

fn main() {
    // Simple command — run and wait
    let output = Command::new("echo")
        .arg("Hello from Rust!")
        .output()
        .expect("Failed to run echo");

    println!("Status: {}", output.status);
    println!("Stdout: {}", String::from_utf8_lossy(&output.stdout));
    println!("Stderr: {}", String::from_utf8_lossy(&output.stderr));
}
```

Three ways to execute:

- **`output()`** — runs to completion, captures stdout/stderr into memory
- **`status()`** — runs to completion, inherits stdout/stderr (prints to terminal), returns just the exit code
- **`spawn()`** — starts the process and returns a `Child` handle immediately, lets you interact with stdin/stdout/stderr

```rust
use std::process::Command;

fn main() {
    // status() — output goes directly to terminal
    let status = Command::new("ls")
        .arg("-la")
        .arg("/tmp")
        .status()
        .expect("Failed to run ls");

    println!("\nExit code: {}", status);
    println!("Success: {}", status.success());

    // output() — capture everything
    let output = Command::new("uname")
        .arg("-a")
        .output()
        .expect("Failed to run uname");

    if output.status.success() {
        let system_info = String::from_utf8_lossy(&output.stdout);
        println!("System: {}", system_info.trim());
    } else {
        let err = String::from_utf8_lossy(&output.stderr);
        eprintln!("Error: {err}");
    }
}
```

## Arguments and Environment

```rust
use std::process::Command;
use std::collections::HashMap;

fn main() {
    // Multiple arguments — each one separate (NOT shell-style)
    let output = Command::new("git")
        .args(["log", "--oneline", "-5"])
        .output()
        .expect("git failed");

    println!("Recent commits:\n{}", String::from_utf8_lossy(&output.stdout));

    // Set environment variables
    let output = Command::new("env")
        .env("MY_VAR", "hello")
        .env("MY_OTHER_VAR", "world")
        .output()
        .expect("env failed");

    // Clear all environment and set only what you specify
    let output = Command::new("env")
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", "/tmp")
        .output()
        .expect("env failed");

    println!("Minimal env:\n{}", String::from_utf8_lossy(&output.stdout));

    // Set working directory
    let output = Command::new("pwd")
        .current_dir("/tmp")
        .output()
        .expect("pwd failed");

    println!("Working dir: {}", String::from_utf8_lossy(&output.stdout).trim());
}
```

Important: `Command` does **not** invoke a shell. Arguments are passed directly to the executable. This means no shell expansion, no globbing, no pipes, no redirects. That's a security feature — it prevents shell injection attacks.

```rust
use std::process::Command;

fn main() {
    // This does NOT work — the * won't be expanded
    let output = Command::new("ls")
        .arg("/tmp/*.txt")
        .output()
        .expect("ls failed");

    // If you actually need shell features, invoke the shell explicitly
    // (but be careful with untrusted input!)
    let output = Command::new("sh")
        .args(["-c", "ls /tmp/*.txt 2>/dev/null | head -5"])
        .output()
        .expect("shell failed");

    println!("{}", String::from_utf8_lossy(&output.stdout));
}
```

## Spawning and Streaming I/O

`spawn()` gives you a running child process with handles to its stdin, stdout, and stderr:

```rust
use std::io::{self, BufRead, BufReader, Write};
use std::process::{Command, Stdio};

fn main() -> io::Result<()> {
    // Spawn a process and write to its stdin
    let mut child = Command::new("sort")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()?;

    // Write data to stdin
    if let Some(ref mut stdin) = child.stdin {
        writeln!(stdin, "banana")?;
        writeln!(stdin, "apple")?;
        writeln!(stdin, "cherry")?;
        writeln!(stdin, "date")?;
    }

    // Drop stdin to signal EOF — the child needs this to finish
    drop(child.stdin.take());

    // Read sorted output
    let output = child.wait_with_output()?;
    println!("Sorted:\n{}", String::from_utf8_lossy(&output.stdout));

    Ok(())
}
```

That `drop(child.stdin.take())` is crucial. Many programs read until EOF, and EOF on a pipe only happens when all write handles are closed. If you forget this, your program deadlocks — the child waits for more input, and you wait for the child to finish.

### Streaming Output Line by Line

```rust
use std::io::{self, BufRead, BufReader};
use std::process::{Command, Stdio};

fn main() -> io::Result<()> {
    // Long-running command — read output as it arrives
    let mut child = Command::new("ping")
        .args(["-c", "4", "127.0.0.1"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    // Stream stdout line by line
    let stdout = child.stdout.take().expect("Failed to capture stdout");
    let reader = BufReader::new(stdout);

    for line in reader.lines() {
        let line = line?;
        println!("[PING] {line}");
    }

    let status = child.wait()?;
    println!("\nPing finished with: {status}");

    Ok(())
}
```

## Pipes Between Processes

You can connect one process's stdout to another's stdin — the equivalent of shell pipes:

```rust
use std::io;
use std::process::{Command, Stdio};

fn main() -> io::Result<()> {
    // Equivalent to: echo "one\ntwo\nthree\nfour" | sort | uniq
    let echo = Command::new("echo")
        .arg("one\ntwo\nthree\nfour\ntwo\none")
        .stdout(Stdio::piped())
        .spawn()?;

    let sort = Command::new("sort")
        .stdin(echo.stdout.unwrap())
        .stdout(Stdio::piped())
        .spawn()?;

    let uniq = Command::new("uniq")
        .stdin(sort.stdout.unwrap())
        .output()?;

    println!("Result:\n{}", String::from_utf8_lossy(&uniq.stdout));

    Ok(())
}
```

Each process here is a real OS process running concurrently. The data flows through OS pipes between them, just like in a shell. Rust just gives you explicit handles instead of the `|` syntax.

## Error Handling

Command execution can fail in two different ways:

1. **The command couldn't start** — wrong path, missing executable, permission denied. This is `Err` from `spawn()`/`output()`/`status()`.
2. **The command started but exited with an error** — the program ran but returned a non-zero exit code. This is `Ok` with a non-success status.

```rust
use std::io;
use std::process::Command;

fn run_command(program: &str, args: &[&str]) -> io::Result<String> {
    let output = Command::new(program)
        .args(args)
        .output()?; // Handles case 1: couldn't start

    if !output.status.success() {
        // Case 2: started but failed
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(io::Error::new(
            io::ErrorKind::Other,
            format!("{program} failed ({}): {stderr}", output.status),
        ));
    }

    String::from_utf8(output.stdout)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

fn main() {
    // Success case
    match run_command("echo", &["hello"]) {
        Ok(output) => println!("Output: {}", output.trim()),
        Err(e) => eprintln!("Error: {e}"),
    }

    // Command not found
    match run_command("nonexistent_command_xyz", &[]) {
        Ok(output) => println!("Output: {output}"),
        Err(e) => eprintln!("Error: {e}"),
    }

    // Command exists but fails
    match run_command("ls", &["/nonexistent/path/xyz"]) {
        Ok(output) => println!("Output: {output}"),
        Err(e) => eprintln!("Error: {e}"),
    }
}
```

## Process Exit

Your own program can control its exit:

```rust
use std::process;

fn validate_config(path: &str) -> Result<String, String> {
    // Simulate validation
    if path.ends_with(".toml") {
        Ok("Config valid".to_string())
    } else {
        Err(format!("Expected .toml file, got: {path}"))
    }
}

fn main() {
    let config_path = "config.json"; // Wrong extension for demo

    match validate_config(config_path) {
        Ok(msg) => println!("{msg}"),
        Err(e) => {
            eprintln!("Fatal: {e}");
            process::exit(1);
        }
    }

    // process::exit() does NOT run destructors.
    // If you need destructors to run, return from main() instead:
    // fn main() -> Result<(), Box<dyn std::error::Error>> { ... }

    // Get your own process ID
    println!("My PID: {}", process::id());
}
```

Prefer returning from `main()` over calling `process::exit()`. Exit codes work the same way, but returning lets destructors run (closing files, flushing buffers, cleaning up temp files).

## Killing Child Processes

```rust
use std::io;
use std::process::{Command, Stdio};
use std::time::Duration;
use std::thread;

fn main() -> io::Result<()> {
    let mut child = Command::new("sleep")
        .arg("30")
        .stdout(Stdio::null())
        .spawn()?;

    println!("Started sleep process (PID: {})", child.id());

    // Give it a moment, then kill it
    thread::sleep(Duration::from_secs(1));

    child.kill()?;
    println!("Killed the process");

    // Must still wait() to reap the zombie
    let status = child.wait()?;
    println!("Exit status: {status}");

    Ok(())
}
```

Always call `wait()` or `wait_with_output()` after `kill()`. On Unix, a killed process becomes a zombie until its parent reads its exit status. Forgetting to `wait()` leaks zombie processes.

## Practical Example: Build Script Runner

```rust
use std::io::{self, Write};
use std::process::{Command, Stdio};
use std::time::Instant;

struct Step {
    name: &'static str,
    program: &'static str,
    args: Vec<&'static str>,
}

fn run_steps(steps: &[Step]) -> io::Result<()> {
    let total_start = Instant::now();

    for (i, step) in steps.iter().enumerate() {
        print!("[{}/{}] {} ... ", i + 1, steps.len(), step.name);
        io::stdout().flush()?;

        let start = Instant::now();

        let output = Command::new(step.program)
            .args(&step.args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()?;

        let elapsed = start.elapsed();

        if output.status.success() {
            println!("OK ({:.1}s)", elapsed.as_secs_f64());
        } else {
            println!("FAILED ({:.1}s)", elapsed.as_secs_f64());
            eprintln!("\nSTDERR:\n{}", String::from_utf8_lossy(&output.stderr));
            eprintln!("STDOUT:\n{}", String::from_utf8_lossy(&output.stdout));
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("Step '{}' failed with {}", step.name, output.status),
            ));
        }
    }

    println!(
        "\nAll {} steps passed in {:.1}s",
        steps.len(),
        total_start.elapsed().as_secs_f64()
    );

    Ok(())
}

fn main() -> io::Result<()> {
    let steps = vec![
        Step { name: "Check formatting", program: "cargo", args: vec!["fmt", "--check"] },
        Step { name: "Lint", program: "cargo", args: vec!["clippy", "--", "-D", "warnings"] },
        Step { name: "Test", program: "cargo", args: vec!["test"] },
        Step { name: "Build release", program: "cargo", args: vec!["build", "--release"] },
    ];

    run_steps(&steps)?;

    Ok(())
}
```

This is a pattern I come back to again and again. Step-by-step execution with timing, captured output on failure, and early termination on error. The type system guarantees you handle both "command not found" and "command failed" — try getting that guarantee from a shell script.

`std::process` is one of those modules that bridges Rust's safe world with the messy reality of OS processes. The API is small but complete, and understanding it means you can replace fragile shell scripts with robust, testable Rust programs.
