---
title: "Lesson 2: stdin, stdout, stderr — I/O patterns"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 2
keywords: [Rust, rust, CLI, stdin, stdout, stderr, I/O, BufReader, BufWriter, pipes]
tags: [Rust tutorial, rust, cli]
date: '2024-09-03T08:45:00.000Z'
---

A coworker once asked me to review a Rust CLI they'd written. It read a CSV file, transformed some columns, and wrote the result. Worked perfectly — until someone piped in a 2GB file. The tool ate 4GB of RAM, hung for thirty seconds, then crashed. They were reading the entire file into a `String` before processing a single line. Classic.

## Why I/O Is Harder Than It Looks

Unix tools work because of a simple contract: read from stdin, write to stdout, errors to stderr. `cat file | grep pattern | sort | uniq -c`. Each program does one thing. They compose through pipes. It's beautiful when it works.

But getting it right in code means understanding buffering, handling broken pipes without panicking, detecting whether stdin is a terminal or a pipe, and being careful about how much memory you consume. Rust gives you great primitives for all of this — you just have to know which ones to reach for.

## The Basics: Reading and Writing

Here's the minimal version — read lines from stdin, process them, write to stdout:

```rust
use std::io::{self, BufRead, Write};

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    for line in stdin.lock().lines() {
        let line = line?;
        let upper = line.to_uppercase();
        writeln!(out, "{}", upper)?;
    }

    Ok(())
}
```

Two things matter here that beginners miss.

**Locking.** `stdin.lock()` and `stdout.lock()` grab a mutex lock once, rather than on every read/write operation. If you use `println!` in a loop, it locks and unlocks stdout on every single call. For a million lines, that's a million lock acquisitions. Locking once upfront is dramatically faster.

**BufWriter.** Stdout is line-buffered when connected to a terminal and fully buffered when connected to a pipe — but only at the C library level. Rust's `io::stdout()` does its own buffering (or doesn't, depending on the platform). Wrapping it in `BufWriter` guarantees you're not making a syscall for every `write!` call.

## Reading From Files or stdin

Real CLI tools accept both `myapp input.txt` and `cat input.txt | myapp`. Here's how to handle both:

```rust
use clap::Parser;
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read};
use std::path::PathBuf;

#[derive(Parser)]
struct Cli {
    /// Input file (reads from stdin if omitted)
    input: Option<PathBuf>,
}

fn open_input(path: &Option<PathBuf>) -> io::Result<Box<dyn Read>> {
    match path {
        Some(p) if p.to_str() == Some("-") => Ok(Box::new(io::stdin().lock())),
        Some(p) => Ok(Box::new(File::open(p)?)),
        None => Ok(Box::new(io::stdin().lock())),
    }
}

fn main() -> io::Result<()> {
    let cli = Cli::parse();
    let reader = open_input(&cli.input)?;
    let buffered = BufReader::new(reader);

    let mut line_count = 0;
    let mut word_count = 0;
    let mut byte_count = 0;

    for line in buffered.lines() {
        let line = line?;
        line_count += 1;
        word_count += line.split_whitespace().count();
        byte_count += line.len() + 1; // +1 for newline
    }

    println!("{:>8} {:>8} {:>8}", line_count, word_count, byte_count);
    Ok(())
}
```

The `Box<dyn Read>` trick is the standard pattern. You erase the concrete type so the rest of your code doesn't care whether it's reading from a file, stdin, or a network socket. The `-` convention — treating a dash as "stdin" — is expected by most Unix users.

## Writing to Files or stdout

Same pattern in reverse for output:

```rust
use std::fs::File;
use std::io::{self, BufWriter, Write};
use std::path::PathBuf;

fn open_output(path: &Option<PathBuf>) -> io::Result<Box<dyn Write>> {
    match path {
        Some(p) => {
            let file = File::create(p)?;
            Ok(Box::new(BufWriter::new(file)))
        }
        None => Ok(Box::new(BufWriter::new(io::stdout().lock()))),
    }
}

fn main() -> io::Result<()> {
    let output_path: Option<PathBuf> = None; // would come from CLI args
    let mut out = open_output(&output_path)?;

    for i in 0..100 {
        writeln!(out, "Line {}: {}", i, "x".repeat(i))?;
    }

    out.flush()?;
    Ok(())
}
```

Always call `flush()` at the end. `BufWriter` might be holding data in its buffer when the program exits. Rust *will* flush on drop, but if the flush fails (disk full, broken pipe), the error gets silently swallowed. Explicit `flush()` lets you handle the error.

## Handling Broken Pipes

Try this: `myapp | head -5`. Your program writes a thousand lines, `head` takes five and exits, the pipe breaks. Without handling this, Rust panics with "Broken pipe (os error 32)" and prints a stack trace. Not a great look.

```rust
use std::io::{self, BufWriter, Write};

fn main() {
    if let Err(e) = run() {
        if e.kind() == io::ErrorKind::BrokenPipe {
            // Silently exit — this is expected behavior
            std::process::exit(0);
        }
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}

fn run() -> io::Result<()> {
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());

    for i in 0..1_000_000 {
        writeln!(out, "Line {}", i)?;
    }

    out.flush()?;
    Ok(())
}
```

The key: broken pipe isn't an error, it's normal Unix behavior. Your program should exit cleanly with code 0 when it happens.

There's also a nightly feature `#![feature(unix_sigpipe)]` that handles this at the signal level, but for stable Rust, the pattern above is the way to go.

## Detecting Terminal vs Pipe

Sometimes you want different behavior depending on whether stdin/stdout is a terminal. Colors, progress bars, interactive prompts — none of these make sense when piped.

```rust
use std::io::{self, IsTerminal};

fn main() {
    if io::stdin().is_terminal() {
        println!("Reading from terminal — type your input, then Ctrl+D:");
    } else {
        // Reading from a pipe or file, no prompt needed
    }

    if io::stdout().is_terminal() {
        // Safe to use colors, progress bars, etc.
        println!("\x1b[32mGreen text!\x1b[0m");
    } else {
        // Output is being piped — plain text only
        println!("Green text!");
    }
}
```

`IsTerminal` landed in stable Rust 1.70. Before that, you needed the `atty` or `is-terminal` crate. Now it's in the standard library where it belongs.

## Processing Large Files Without Blowing Up Memory

The 2GB CSV problem I mentioned? Here's how you handle it properly — streaming, line by line, never holding more than one line in memory:

```rust
use std::io::{self, BufRead, BufWriter, Write};

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());

    let mut buffer = String::new();
    let reader = stdin.lock();
    let mut reader = io::BufReader::new(reader);

    loop {
        buffer.clear();
        let bytes_read = reader.read_line(&mut buffer)?;
        if bytes_read == 0 {
            break; // EOF
        }

        // Process the line — here we just filter for lines containing "ERROR"
        if buffer.contains("ERROR") {
            out.write_all(buffer.as_bytes())?;
        }
    }

    out.flush()?;
    Ok(())
}
```

Notice I'm reusing `buffer` by calling `clear()` each iteration. The `.lines()` iterator allocates a new `String` for every line. For most use cases that's fine — the allocator is fast. But if you're processing tens of millions of lines where every microsecond counts, reusing the buffer avoids allocation churn.

## Binary I/O

Not everything is text. Sometimes you're processing binary data — images, protocol buffers, compressed streams. The `Read` and `Write` traits work with byte slices:

```rust
use std::io::{self, Read, Write};

fn main() -> io::Result<()> {
    let mut stdin = io::stdin().lock();
    let mut stdout = io::stdout().lock();

    let mut buf = [0u8; 8192];

    loop {
        let n = stdin.read(&mut buf)?;
        if n == 0 {
            break;
        }

        // XOR each byte with 0x42 — dumb "encryption"
        for byte in &mut buf[..n] {
            *byte ^= 0x42;
        }

        stdout.write_all(&buf[..n])?;
    }

    stdout.flush()?;
    Ok(())
}
```

The 8192-byte buffer is a good default. It aligns with typical filesystem block sizes and pipe buffer sizes. You could use `io::copy` if you just need to shuttle bytes through without transformation:

```rust
use std::io;

fn main() -> io::Result<()> {
    let mut stdin = io::stdin().lock();
    let mut stdout = io::stdout().lock();
    io::copy(&mut stdin, &mut stdout)?;
    Ok(())
}
```

`io::copy` uses an optimized internal buffer and will use `sendfile` or `splice` on Linux when possible. Don't reinvent it.

## stderr Is Not Just for Errors

A pattern I use constantly: progress information and status messages go to stderr, actual output goes to stdout. This way, piping works correctly:

```rust
use std::io::{self, BufRead, BufWriter, Write};

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());

    let mut count = 0;
    for line in stdin.lock().lines() {
        let line = line?;
        count += 1;

        if count % 10_000 == 0 {
            eprint!("\rProcessed {} lines...", count);
        }

        if line.len() > 80 {
            writeln!(out, "{}", line)?;
        }
    }

    eprintln!("\rProcessed {} lines total.", count);
    out.flush()?;
    Ok(())
}
```

`eprint!` and `eprintln!` write to stderr. So `myapp < huge.txt > output.txt` shows progress in the terminal while the actual filtered output goes to the file. Users expect this.

## Putting It All Together

Here's a complete, production-ready pattern that combines everything — file or stdin input, file or stdout output, broken pipe handling, buffering, terminal detection:

```rust
use clap::Parser;
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, IsTerminal, Read, Write};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "linefilter", about = "Filter lines by length")]
struct Cli {
    /// Input file (stdin if omitted)
    input: Option<PathBuf>,

    /// Output file (stdout if omitted)
    #[arg(short, long)]
    output: Option<PathBuf>,

    /// Minimum line length
    #[arg(short = 'n', long, default_value = "1")]
    min_length: usize,

    /// Maximum line length
    #[arg(short = 'x', long, default_value = "10000")]
    max_length: usize,
}

fn open_input(path: &Option<PathBuf>) -> io::Result<Box<dyn Read>> {
    match path {
        Some(p) => Ok(Box::new(File::open(p)?)),
        None => Ok(Box::new(io::stdin().lock())),
    }
}

fn open_output(path: &Option<PathBuf>) -> io::Result<Box<dyn Write>> {
    match path {
        Some(p) => Ok(Box::new(BufWriter::new(File::create(p)?))),
        None => Ok(Box::new(BufWriter::new(io::stdout().lock()))),
    }
}

fn run() -> io::Result<()> {
    let cli = Cli::parse();
    let reader = BufReader::new(open_input(&cli.input)?);
    let mut writer = open_output(&cli.output)?;

    let show_progress = io::stderr().is_terminal() && cli.input.is_some();
    let mut count = 0u64;
    let mut matched = 0u64;

    for line in reader.lines() {
        let line = line?;
        count += 1;

        if show_progress && count % 50_000 == 0 {
            eprint!("\r{} lines processed, {} matched", count, matched);
        }

        let len = line.len();
        if len >= cli.min_length && len <= cli.max_length {
            matched += 1;
            writeln!(writer, "{}", line)?;
        }
    }

    if show_progress {
        eprintln!("\r{} lines processed, {} matched", count, matched);
    }

    writer.flush()?;
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        if e.kind() == io::ErrorKind::BrokenPipe {
            std::process::exit(0);
        }
        eprintln!("linefilter: {}", e);
        std::process::exit(1);
    }
}
```

This is the skeleton I start with for any CLI that processes text. It handles every edge case: piped input, file input, broken pipes, progress reporting, buffered output. Boring, reliable, fast.

Next up — configuration files and environment variables. Because once your CLI has more than five flags, you need a config file.
