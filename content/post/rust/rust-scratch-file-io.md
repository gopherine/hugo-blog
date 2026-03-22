---
title: "Lesson 24: Reading and Writing Files — Real I/O in Rust"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 24
keywords: [Rust, rust, file I/O, read file, write file, std::fs, BufReader, BufWriter, file handling]
tags: [Rust tutorial, rust, beginner]
date: '2024-04-16T13:45:00.000Z'
---

Every tutorial teaches you to manipulate data in memory, but real programs read from files and write to files. Rust's file I/O is built on the same ownership and error handling principles you've been learning — and once you see how `Result`, iterators, and traits come together, the entire language design starts to feel cohesive.

## Reading a File — The Quick Way

```rust
use std::fs;

fn main() {
    match fs::read_to_string("hello.txt") {
        Ok(content) => println!("File contents:\n{content}"),
        Err(e) => eprintln!("Error reading file: {e}"),
    }
}
```

`fs::read_to_string` reads the entire file into a `String`. It returns `Result<String, io::Error>` — because file operations can fail (file doesn't exist, permission denied, disk error, etc.).

For binary files, use `fs::read`:

```rust
use std::fs;

fn main() {
    match fs::read("image.png") {
        Ok(bytes) => println!("Read {} bytes", bytes.len()),
        Err(e) => eprintln!("Error: {e}"),
    }
}
```

## Writing a File — The Quick Way

```rust
use std::fs;

fn main() -> Result<(), std::io::Error> {
    fs::write("output.txt", "Hello from Rust!\n")?;
    println!("File written successfully");
    Ok(())
}
```

`fs::write` creates the file if it doesn't exist and overwrites it if it does. Simple, but blunt — you can't append, you can't control permissions, you can't write incrementally.

## File Handles — More Control

For anything beyond "read all / write all," you need file handles:

```rust
use std::fs::File;
use std::io::{self, Read};

fn main() -> io::Result<()> {
    let mut file = File::open("data.txt")?;

    let mut contents = String::new();
    file.read_to_string(&mut contents)?;

    println!("Read {} bytes", contents.len());
    println!("{contents}");
    Ok(())
}
```

`File::open` opens a file for reading. It returns `Result<File, io::Error>`. The `File` type implements `Read`, which gives you methods like `read_to_string`, `read`, and `read_exact`.

For writing:

```rust
use std::fs::File;
use std::io::{self, Write};

fn main() -> io::Result<()> {
    let mut file = File::create("output.txt")?;

    file.write_all(b"First line\n")?;
    file.write_all(b"Second line\n")?;
    writeln!(file, "Third line with formatting: {}", 42)?;

    println!("Done writing");
    Ok(())
}
```

`File::create` creates a new file (or truncates an existing one). The `b"..."` syntax creates a byte string — `write_all` takes bytes, not strings. `writeln!` is like `println!` but writes to any `Write` implementor.

## Appending to a File

```rust
use std::fs::OpenOptions;
use std::io::{self, Write};

fn main() -> io::Result<()> {
    let mut file = OpenOptions::new()
        .append(true)
        .create(true)
        .open("log.txt")?;

    writeln!(file, "Log entry at some time")?;
    writeln!(file, "Another log entry")?;

    println!("Appended to log");
    Ok(())
}
```

`OpenOptions` gives you fine-grained control: read, write, append, create, truncate. Chain the options you need.

## Buffered I/O — For Performance

Reading and writing one byte at a time to the OS is slow. Each call is a system call, and system calls are expensive. Buffered I/O batches many small reads/writes into larger chunks.

### BufReader

```rust
use std::fs::File;
use std::io::{self, BufRead, BufReader};

fn main() -> io::Result<()> {
    let file = File::open("data.txt")?;
    let reader = BufReader::new(file);

    for (line_num, line) in reader.lines().enumerate() {
        let line = line?;  // lines() returns Result<String> for each line
        println!("{}: {}", line_num + 1, line);
    }

    Ok(())
}
```

`BufReader::new` wraps a `File` in a buffer. The `.lines()` method returns an iterator of `Result<String>` — one per line, with newlines stripped. This is the idiomatic way to read a file line by line.

Why `Result<String>` and not just `String`? Because each read can fail — the file could be on a network drive that disconnects mid-read. Rust makes you handle this possibility.

### BufWriter

```rust
use std::fs::File;
use std::io::{self, BufWriter, Write};

fn main() -> io::Result<()> {
    let file = File::create("output.txt")?;
    let mut writer = BufWriter::new(file);

    for i in 0..10_000 {
        writeln!(writer, "Line {i}: some data here")?;
    }

    // BufWriter flushes on drop, but explicit flush is clearer
    writer.flush()?;

    println!("Wrote 10,000 lines");
    Ok(())
}
```

For writing many small pieces, `BufWriter` makes a huge difference. Without it, each `writeln!` is a system call. With it, data is buffered in memory and flushed in larger chunks.

**Always use BufReader/BufWriter when doing line-by-line or incremental I/O.** The convenience functions (`fs::read_to_string`, `fs::write`) already handle buffering internally, so they're fine for read-all/write-all patterns.

## Reading CSV-Style Data

A practical example — parsing a simple data file:

```rust
use std::fs::File;
use std::io::{self, BufRead, BufReader};

#[derive(Debug)]
struct Record {
    name: String,
    age: u32,
    score: f64,
}

fn parse_record(line: &str) -> Result<Record, String> {
    let parts: Vec<&str> = line.split(',').collect();
    if parts.len() != 3 {
        return Err(format!("expected 3 fields, got {}", parts.len()));
    }

    let name = parts[0].trim().to_string();
    let age = parts[1].trim().parse::<u32>()
        .map_err(|e| format!("invalid age: {e}"))?;
    let score = parts[2].trim().parse::<f64>()
        .map_err(|e| format!("invalid score: {e}"))?;

    Ok(Record { name, age, score })
}

fn read_records(path: &str) -> Result<Vec<Record>, Box<dyn std::error::Error>> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);

    let mut records = Vec::new();

    for (i, line) in reader.lines().enumerate() {
        let line = line?;
        let line = line.trim();

        if line.is_empty() || line.starts_with('#') {
            continue;  // skip empty lines and comments
        }

        match parse_record(line) {
            Ok(record) => records.push(record),
            Err(e) => eprintln!("Warning: line {}: {e}", i + 1),
        }
    }

    Ok(records)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // For demo, create a test file first
    std::fs::write("students.csv",
        "# Student data\nAlice, 22, 95.5\nBob, 20, 87.3\nCharlie, 21, 92.1\n")?;

    let records = read_records("students.csv")?;

    println!("Loaded {} records:", records.len());
    for r in &records {
        println!("  {:10} age:{:3} score:{:.1}", r.name, r.age, r.score);
    }

    let avg_score: f64 = records.iter().map(|r| r.score).sum::<f64>()
        / records.len() as f64;
    println!("\nAverage score: {avg_score:.1}");

    // Cleanup
    std::fs::remove_file("students.csv")?;
    Ok(())
}
```

## Working with Paths

Use `std::path::Path` and `PathBuf` for file paths:

```rust
use std::path::{Path, PathBuf};

fn main() {
    // Path is a borrowed reference (like &str)
    let path = Path::new("/home/user/documents/file.txt");

    println!("File name: {:?}", path.file_name());
    println!("Extension: {:?}", path.extension());
    println!("Parent: {:?}", path.parent());
    println!("Exists: {}", path.exists());
    println!("Is file: {}", path.is_file());
    println!("Is dir: {}", path.is_dir());

    // PathBuf is an owned path (like String)
    let mut path_buf = PathBuf::from("/home/user");
    path_buf.push("documents");
    path_buf.push("file.txt");
    println!("Built path: {:?}", path_buf);

    // Join paths
    let base = Path::new("/home/user");
    let full = base.join("documents").join("file.txt");
    println!("Joined: {:?}", full);
}
```

`Path` is to `PathBuf` as `&str` is to `String`. Use `Path` in function parameters, `PathBuf` when you need to own or build a path.

## Directory Operations

```rust
use std::fs;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Create directories
    fs::create_dir_all("test_dir/sub/nested")?;

    // Create some files
    fs::write("test_dir/file1.txt", "hello")?;
    fs::write("test_dir/file2.txt", "world")?;
    fs::write("test_dir/sub/file3.txt", "nested")?;

    // List directory contents
    println!("Contents of test_dir:");
    for entry in fs::read_dir("test_dir")? {
        let entry = entry?;
        let path = entry.path();
        let metadata = entry.metadata()?;

        let kind = if metadata.is_dir() { "DIR " } else { "FILE" };
        let size = metadata.len();

        println!("  {kind} {:30} ({size} bytes)", path.display());
    }

    // Copy, rename, remove
    fs::copy("test_dir/file1.txt", "test_dir/file1_copy.txt")?;
    fs::rename("test_dir/file2.txt", "test_dir/file2_renamed.txt")?;

    // Cleanup
    fs::remove_dir_all("test_dir")?;
    println!("\nCleaned up test_dir");

    Ok(())
}
```

## Error Handling Patterns for I/O

Real I/O code handles errors gracefully:

```rust
use std::fs;
use std::io;
use std::path::Path;

fn safe_read(path: &str) -> Result<String, String> {
    let path = Path::new(path);

    if !path.exists() {
        return Err(format!("File not found: {}", path.display()));
    }

    if !path.is_file() {
        return Err(format!("Not a file: {}", path.display()));
    }

    fs::read_to_string(path)
        .map_err(|e| format!("Failed to read {}: {e}", path.display()))
}

fn safe_write(path: &str, content: &str) -> Result<(), String> {
    let path = Path::new(path);

    // Ensure parent directory exists
    if let Some(parent) = path.parent() {
        if !parent.exists() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create directory: {e}"))?;
        }
    }

    fs::write(path, content)
        .map_err(|e| format!("Failed to write {}: {e}", path.display()))
}

fn main() {
    match safe_read("nonexistent.txt") {
        Ok(content) => println!("{content}"),
        Err(e) => eprintln!("Read error: {e}"),
    }

    match safe_write("output/nested/file.txt", "hello from safe_write") {
        Ok(()) => println!("Written successfully"),
        Err(e) => eprintln!("Write error: {e}"),
    }

    // Cleanup
    let _ = fs::remove_dir_all("output");
}
```

## The Read and Write Traits

`Read` and `Write` are traits — they're not specific to files. Anything can implement them:

```rust
use std::io::{self, Read, Write};

fn count_bytes(reader: &mut impl Read) -> io::Result<usize> {
    let mut buf = Vec::new();
    reader.read_to_end(&mut buf)?;
    Ok(buf.len())
}

fn write_header(writer: &mut impl Write, title: &str) -> io::Result<()> {
    writeln!(writer, "=== {} ===", title)?;
    writeln!(writer, "")?;
    Ok(())
}

fn main() -> io::Result<()> {
    // Works with &[u8] (in-memory bytes)
    let data = b"hello world";
    let count = count_bytes(&mut &data[..])?;
    println!("Byte count: {count}");

    // Works with Vec<u8> as a writer
    let mut buffer = Vec::new();
    write_header(&mut buffer, "My Report")?;
    writeln!(buffer, "Some content here")?;
    println!("Buffer: {}", String::from_utf8_lossy(&buffer));

    Ok(())
}
```

This is the power of traits — your I/O functions work with files, network sockets, in-memory buffers, or anything else that implements `Read`/`Write`. Write your functions against the trait, not the concrete type. This makes testing trivial — pass a `Vec<u8>` instead of a real file.

## A Practical Example: Log File Analyzer

```rust
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};
use std::collections::HashMap;

fn analyze_log(input_path: &str, output_path: &str) -> Result<(), Box<dyn std::error::Error>> {
    let file = File::open(input_path)?;
    let reader = BufReader::new(file);

    let mut level_counts: HashMap<String, u32> = HashMap::new();
    let mut total_lines = 0;
    let mut error_messages = Vec::new();

    for line in reader.lines() {
        let line = line?;
        total_lines += 1;

        // Simple log format: [LEVEL] message
        if let Some(level_end) = line.find(']') {
            if line.starts_with('[') {
                let level = &line[1..level_end];
                *level_counts.entry(level.to_string()).or_insert(0) += 1;

                if level == "ERROR" {
                    error_messages.push(line[level_end + 2..].to_string());
                }
            }
        }
    }

    // Write report
    let mut output = File::create(output_path)?;

    writeln!(output, "Log Analysis Report")?;
    writeln!(output, "===================")?;
    writeln!(output, "Total lines: {total_lines}")?;
    writeln!(output)?;
    writeln!(output, "Level counts:")?;

    let mut sorted_levels: Vec<_> = level_counts.iter().collect();
    sorted_levels.sort_by(|a, b| b.1.cmp(a.1));

    for (level, count) in &sorted_levels {
        writeln!(output, "  {level}: {count}")?;
    }

    if !error_messages.is_empty() {
        writeln!(output)?;
        writeln!(output, "Error messages:")?;
        for msg in &error_messages {
            writeln!(output, "  - {msg}")?;
        }
    }

    println!("Analysis complete. Report written to {output_path}");
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Create sample log for demo
    let sample_log = "\
[INFO] Server started on port 8080
[INFO] Connection from 192.168.1.1
[WARN] Slow query detected (2.3s)
[INFO] Request processed successfully
[ERROR] Database connection timeout
[INFO] Retrying connection
[ERROR] Failed to parse config file
[WARN] Memory usage above 80%
[INFO] Request processed successfully
[INFO] Shutting down gracefully
";
    fs::write("sample.log", sample_log)?;

    analyze_log("sample.log", "report.txt")?;

    // Print the report
    let report = fs::read_to_string("report.txt")?;
    println!("\n{report}");

    // Cleanup
    fs::remove_file("sample.log")?;
    fs::remove_file("report.txt")?;

    Ok(())
}
```

## Key Takeaways

1. **Use `fs::read_to_string` / `fs::write` for simple read-all/write-all** — they handle buffering internally
2. **Use `BufReader` / `BufWriter` for line-by-line or incremental I/O** — massive performance improvement
3. **All file operations return `Result`** — always handle errors
4. **Write functions against `Read` / `Write` traits, not `File`** — makes testing and reuse easy
5. **Use `Path` and `PathBuf` for file paths** — cross-platform, correct
6. **Files are closed automatically when dropped** — ownership handles cleanup

One more lesson to go — where to go from here.
