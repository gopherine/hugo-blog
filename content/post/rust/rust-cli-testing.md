---
title: "Lesson 10: Testing CLI Applications — End-to-end CLI tests"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 10
keywords: [Rust, rust, CLI, testing, assert_cmd, predicates, integration tests, snapshot testing, trycmd]
tags: [Rust tutorial, rust, cli]
date: '2024-09-22T12:10:00.000Z'
---

I shipped a CLI tool with 100% unit test coverage on the core logic. Users immediately found three bugs. The argument parser accepted `--port` but the value wasn't being passed to the server. The `--json` flag produced output that wasn't valid JSON because of a stray debug print. And `--help` showed the wrong default for `--timeout`. None of these bugs lived in the "core logic." They lived in the glue between clap, the output formatter, and the actual binary. Unit tests didn't catch them because unit tests don't run the actual binary.

## Why CLI Tests Are Different

A CLI application's contract isn't a function signature — it's a process boundary. It takes arguments and stdin as input. It produces stdout, stderr, and an exit code as output. It might read files and write files. Testing this requires running the compiled binary as a subprocess and examining what comes out.

Rust has great tools for this: `assert_cmd` for running binaries, `predicates` for asserting on output, and `trycmd` for snapshot testing.

## Setup

```toml
[dev-dependencies]
assert_cmd = "2"
predicates = "3"
tempfile = "3"
```

Your CLI needs to be a binary crate (has a `main.rs` and a `[[bin]]` target). `assert_cmd` finds the compiled binary automatically from your crate metadata.

## Basic Binary Tests

```rust
// tests/cli.rs
use assert_cmd::Command;
use predicates::prelude::*;

#[test]
fn test_help_flag() {
    Command::cargo_bin("myapp")
        .unwrap()
        .arg("--help")
        .assert()
        .success()
        .stdout(predicate::str::contains("Usage:"))
        .stdout(predicate::str::contains("--output"));
}

#[test]
fn test_version_flag() {
    Command::cargo_bin("myapp")
        .unwrap()
        .arg("--version")
        .assert()
        .success()
        .stdout(predicate::str::starts_with("myapp "));
}

#[test]
fn test_missing_required_argument() {
    Command::cargo_bin("myapp")
        .unwrap()
        // No arguments provided, but input is required
        .assert()
        .failure()
        .stderr(predicate::str::contains("required"));
}

#[test]
fn test_invalid_flag() {
    Command::cargo_bin("myapp")
        .unwrap()
        .arg("--nonexistent-flag")
        .assert()
        .failure()
        .stderr(predicate::str::contains("unexpected argument"));
}
```

`Command::cargo_bin("myapp")` builds and locates your binary. `.assert()` runs it and returns an assertion builder. `.success()` checks exit code 0. `.failure()` checks non-zero. The predicate functions check stdout and stderr content.

## Testing With Input Files

Most CLI tools process files. Use `tempfile` to create test inputs:

```rust
use assert_cmd::Command;
use predicates::prelude::*;
use std::io::Write;
use tempfile::NamedTempFile;

#[test]
fn test_process_file() {
    let mut input = NamedTempFile::new().unwrap();
    writeln!(input, "hello world").unwrap();
    writeln!(input, "foo bar baz").unwrap();
    writeln!(input, "hello rust").unwrap();

    Command::cargo_bin("myapp")
        .unwrap()
        .arg(input.path())
        .arg("--pattern")
        .arg("hello")
        .assert()
        .success()
        .stdout(predicate::str::contains("hello world"))
        .stdout(predicate::str::contains("hello rust"))
        .stdout(predicate::str::contains("foo bar").not());
}

#[test]
fn test_output_to_file() {
    let input = NamedTempFile::new().unwrap();
    let output = NamedTempFile::new().unwrap();

    std::fs::write(input.path(), "line1\nline2\nline3\n").unwrap();

    Command::cargo_bin("myapp")
        .unwrap()
        .arg(input.path())
        .arg("--output")
        .arg(output.path())
        .assert()
        .success();

    let result = std::fs::read_to_string(output.path()).unwrap();
    assert!(result.contains("line1"));
    assert_eq!(result.lines().count(), 3);
}

#[test]
fn test_nonexistent_input() {
    Command::cargo_bin("myapp")
        .unwrap()
        .arg("/nonexistent/file.txt")
        .assert()
        .failure()
        .stderr(predicate::str::contains("not found").or(predicate::str::contains("No such file")));
}
```

`NamedTempFile` creates a file that's automatically deleted when the test ends. The path is unique, so tests running in parallel don't collide.

## Testing stdin

```rust
use assert_cmd::Command;
use predicates::prelude::*;

#[test]
fn test_stdin_input() {
    Command::cargo_bin("myapp")
        .unwrap()
        .write_stdin("hello\nworld\nrust\n")
        .assert()
        .success()
        .stdout(predicate::str::contains("3 lines"));
}

#[test]
fn test_stdin_pipe_behavior() {
    // When stdin is piped, the tool should not prompt for input
    Command::cargo_bin("myapp")
        .unwrap()
        .write_stdin("data")
        .assert()
        .success()
        .stderr(predicate::str::contains("Reading from terminal").not());
}

#[test]
fn test_empty_stdin() {
    Command::cargo_bin("myapp")
        .unwrap()
        .write_stdin("")
        .assert()
        .success()
        .stdout(predicate::str::contains("0 lines"));
}
```

`.write_stdin()` pipes data into the process's stdin. This is equivalent to `echo "hello" | myapp`.

## Testing Exit Codes

```rust
use assert_cmd::Command;

#[test]
fn test_success_exit_code() {
    Command::cargo_bin("myapp")
        .unwrap()
        .arg("--check")
        .arg("valid_input.txt")
        .assert()
        .code(0);
}

#[test]
fn test_error_exit_code() {
    Command::cargo_bin("myapp")
        .unwrap()
        .arg("--check")
        .arg("invalid_input.txt")
        .assert()
        .code(1);
}

#[test]
fn test_usage_error_exit_code() {
    Command::cargo_bin("myapp")
        .unwrap()
        .arg("--invalid-flag")
        .assert()
        .code(2); // Convention: 2 for usage errors
}
```

Specific exit codes matter. Scripts check `$?` after running your tool. If you return 0 on validation failure, somebody's CI pipeline will silently pass when it shouldn't.

## Environment Variables

```rust
use assert_cmd::Command;
use predicates::prelude::*;

#[test]
fn test_env_variable_config() {
    Command::cargo_bin("myapp")
        .unwrap()
        .env("MYAPP_LOG_LEVEL", "debug")
        .env("MYAPP_PORT", "9090")
        .arg("config")
        .assert()
        .success()
        .stdout(predicate::str::contains("log_level: debug"))
        .stdout(predicate::str::contains("port: 9090"));
}

#[test]
fn test_cli_overrides_env() {
    Command::cargo_bin("myapp")
        .unwrap()
        .env("MYAPP_PORT", "9090")
        .arg("--port")
        .arg("3000") // CLI flag should win
        .assert()
        .success()
        .stdout(predicate::str::contains("port: 3000"));
}

#[test]
fn test_no_color_env() {
    Command::cargo_bin("myapp")
        .unwrap()
        .env("NO_COLOR", "1")
        .arg("status")
        .assert()
        .success()
        .stdout(predicate::str::contains("\x1b[").not()); // No ANSI codes
}
```

`.env()` sets environment variables for the subprocess without affecting the test process. Clean isolation.

## Snapshot Testing With trycmd

Writing expected output by hand gets tedious. `trycmd` lets you write tests as markdown files — the command and expected output in a single `.md` or `.trycmd` file:

```toml
[dev-dependencies]
trycmd = "0.15"
```

Create a test file `tests/cmd/help.trycmd`:

```
$ myapp --help
Usage: myapp [OPTIONS] <INPUT>

Arguments:
  <INPUT>  Input file to process

Options:
  -o, --output <OUTPUT>  Output file (defaults to stdout)
  -v, --verbose          Enable verbose output
  -h, --help             Print help
  -V, --version          Print version

```

And the test runner:

```rust
// tests/cli_snapshots.rs
#[test]
fn cli_tests() {
    trycmd::TestCases::new().case("tests/cmd/*.trycmd");
}
```

When you first run the test, it captures the actual output. If the output changes, the test fails and shows you the diff. Run with `TRYCMD=overwrite` to update snapshots:

```bash
TRYCMD=overwrite cargo test
```

This is brilliant for testing `--help` output, error messages, and command behavior. If you change a flag name and forget to update the help text, the snapshot test catches it.

More complex `.trycmd` files:

```
# Test basic filtering
$ myapp filter --min-length 5 input.txt
hello world
this is longer

# Test with environment variable
$ MYAPP_FORMAT=json myapp status
{"status": "ok", "version": "0.1.0"}

# Test error case (expect non-zero exit)
$ myapp --invalid
? 2
error: unexpected argument '--invalid' found

```

The `? 2` line means "expect exit code 2." Lines starting with `$` are commands. Everything else is expected output.

## Testing JSON Output

If your tool has a `--json` flag, validate the JSON structure:

```rust
use assert_cmd::Command;
use serde_json::Value;

#[test]
fn test_json_output_is_valid() {
    let output = Command::cargo_bin("myapp")
        .unwrap()
        .args(["status", "--format", "json"])
        .output()
        .unwrap();

    assert!(output.status.success());

    let stdout = String::from_utf8(output.stdout).unwrap();
    let json: Value = serde_json::from_str(&stdout)
        .expect("Output should be valid JSON");

    assert_eq!(json["status"], "ok");
    assert!(json["version"].is_string());
    assert!(json["uptime_seconds"].is_number());
}

#[test]
fn test_json_list_output() {
    let output = Command::cargo_bin("myapp")
        .unwrap()
        .args(["list", "--format", "json"])
        .output()
        .unwrap();

    let stdout = String::from_utf8(output.stdout).unwrap();
    let json: Value = serde_json::from_str(&stdout).unwrap();

    assert!(json.is_array());
    let items = json.as_array().unwrap();
    assert!(!items.is_empty());

    // Each item should have required fields
    for item in items {
        assert!(item["name"].is_string());
        assert!(item["id"].is_number());
    }
}
```

JSON output tests catch two classes of bugs: invalid JSON (stray print statements, debug output) and missing/wrong fields in the schema.

## Testing With a Test Fixture Directory

For tools that operate on directory trees, set up fixtures:

```rust
use assert_cmd::Command;
use predicates::prelude::*;
use std::fs;
use tempfile::TempDir;

fn setup_project_fixture() -> TempDir {
    let dir = TempDir::new().unwrap();

    // Create a realistic project structure
    fs::create_dir_all(dir.path().join("src")).unwrap();
    fs::write(
        dir.path().join("src/main.rs"),
        "fn main() { println!(\"hello\"); }\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("src/lib.rs"),
        "pub fn add(a: i32, b: i32) -> i32 { a + b }\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("Cargo.toml"),
        "[package]\nname = \"test\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    fs::write(dir.path().join("README.md"), "# Test Project\n").unwrap();

    dir
}

#[test]
fn test_scan_project() {
    let project = setup_project_fixture();

    Command::cargo_bin("myapp")
        .unwrap()
        .arg("scan")
        .arg(project.path())
        .assert()
        .success()
        .stdout(predicate::str::contains("4 files"))
        .stdout(predicate::str::contains(".rs: 2"));
}

#[test]
fn test_scan_with_extension_filter() {
    let project = setup_project_fixture();

    Command::cargo_bin("myapp")
        .unwrap()
        .arg("scan")
        .arg(project.path())
        .arg("--ext")
        .arg("rs")
        .assert()
        .success()
        .stdout(predicate::str::contains("2 files"))
        .stdout(predicate::str::contains("README").not());
}
```

`TempDir` creates a temporary directory that's automatically cleaned up when the variable goes out of scope. Tests are isolated — each gets its own directory.

## Structuring Your Code for Testability

The biggest mistake: putting all logic in `main()`. If your business logic is tangled up with argument parsing and I/O, you can't unit-test it.

```rust
// src/main.rs — thin wrapper
use std::process::ExitCode;

mod app;
mod cli;

fn main() -> ExitCode {
    let args = cli::parse();

    match app::run(args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("Error: {}", e);
            ExitCode::FAILURE
        }
    }
}
```

```rust
// src/app.rs — testable business logic
use std::io::{self, Read, Write};

pub struct Config {
    pub pattern: String,
    pub ignore_case: bool,
    pub count_only: bool,
}

pub fn filter_lines(
    input: &mut dyn Read,
    output: &mut dyn Write,
    config: &Config,
) -> io::Result<usize> {
    let mut count = 0;
    let reader = io::BufReader::new(input);

    for line in io::BufRead::lines(reader) {
        let line = line?;
        let matches = if config.ignore_case {
            line.to_lowercase().contains(&config.pattern.to_lowercase())
        } else {
            line.contains(&config.pattern)
        };

        if matches {
            count += 1;
            if !config.count_only {
                writeln!(output, "{}", line)?;
            }
        }
    }

    if config.count_only {
        writeln!(output, "{}", count)?;
    }

    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_filter_basic() {
        let input = "hello world\nfoo bar\nhello rust\n";
        let mut output = Vec::new();
        let config = Config {
            pattern: "hello".to_string(),
            ignore_case: false,
            count_only: false,
        };

        let count = filter_lines(
            &mut input.as_bytes(),
            &mut output,
            &config,
        )
        .unwrap();

        assert_eq!(count, 2);
        let result = String::from_utf8(output).unwrap();
        assert_eq!(result, "hello world\nhello rust\n");
    }

    #[test]
    fn test_filter_case_insensitive() {
        let input = "Hello World\nfoo bar\nHELLO RUST\n";
        let mut output = Vec::new();
        let config = Config {
            pattern: "hello".to_string(),
            ignore_case: true,
            count_only: false,
        };

        let count = filter_lines(
            &mut input.as_bytes(),
            &mut output,
            &config,
        )
        .unwrap();

        assert_eq!(count, 2);
    }

    #[test]
    fn test_filter_count_only() {
        let input = "a\nb\na\nc\na\n";
        let mut output = Vec::new();
        let config = Config {
            pattern: "a".to_string(),
            ignore_case: false,
            count_only: true,
        };

        filter_lines(&mut input.as_bytes(), &mut output, &config).unwrap();

        let result = String::from_utf8(output).unwrap();
        assert_eq!(result.trim(), "3");
    }

    #[test]
    fn test_filter_no_matches() {
        let input = "foo\nbar\nbaz\n";
        let mut output = Vec::new();
        let config = Config {
            pattern: "xyz".to_string(),
            ignore_case: false,
            count_only: false,
        };

        let count = filter_lines(
            &mut input.as_bytes(),
            &mut output,
            &config,
        )
        .unwrap();

        assert_eq!(count, 0);
        assert!(output.is_empty());
    }
}
```

The core logic takes `dyn Read` and `dyn Write` — so unit tests use byte slices and `Vec<u8>`, while the real binary uses files and stdout. Integration tests with `assert_cmd` verify the end-to-end flow. Both levels of testing catch different bug classes.

## Test Organization Checklist

For a production CLI, I test at three levels:

1. **Unit tests** (in `src/`) — Core logic, algorithms, data transformations. Fast, no I/O.
2. **Integration tests** (`tests/cli.rs`) — Run the actual binary with `assert_cmd`. Test argument parsing, I/O routing, exit codes, error messages.
3. **Snapshot tests** (`tests/cmd/*.trycmd`) — Capture and verify exact output. Catch help text drift, formatting changes, accidental debug prints.

Run them all in CI. Unit tests on every push, integration tests on every PR, snapshot updates as a deliberate choice when behavior changes.

```bash
# Run all tests
cargo test

# Run only integration tests
cargo test --test cli

# Update snapshots
TRYCMD=overwrite cargo test

# Run with output for debugging
cargo test -- --nocapture
```

That's the complete Rust CLI & Tooling course. Ten lessons from argument parsing to testing. The key takeaway across all of them: Rust's type system and ecosystem make it unusually good for CLI development. clap gives you argument parsing that can't get out of sync with your code. The ownership model prevents the resource leaks that plague long-running tools. Cross-compilation actually works for pure Rust projects. And the testing story — from unit tests to full binary integration tests — is as strong as anything I've used.

Build something. Ship it. Put it on Homebrew. Your future self will thank you.
