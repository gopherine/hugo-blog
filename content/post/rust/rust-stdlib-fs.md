---
title: "Lesson 4: std::fs and std::path — Filesystem operations done right"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 4
keywords: [Rust, rust, standard library, fs, path, PathBuf, filesystem, file operations]
tags: [Rust tutorial, rust, stdlib]
date: '2024-09-21T16:10:00.000Z'
---

A colleague once deployed a script that used string concatenation for file paths: `dir + "/" + filename`. Worked perfectly on Linux, blew up on Windows, and silently corrupted paths when `dir` ended with a slash. Rust's `Path` type exists specifically to prevent this class of bug.

## Path vs. PathBuf — The &str/String Split

Just like Rust has `&str` (borrowed) and `String` (owned), it has `&Path` (borrowed) and `PathBuf` (owned). The duality is identical:

```rust
use std::path::{Path, PathBuf};

fn main() {
    // Path is a borrowed view — can't be modified
    let p: &Path = Path::new("/usr/local/bin");
    println!("Path: {}", p.display());

    // PathBuf is owned — can be modified
    let mut pb = PathBuf::from("/usr/local");
    pb.push("bin");
    pb.push("rustc");
    println!("PathBuf: {}", pb.display());

    // Convert between them
    let borrowed: &Path = pb.as_path();
    let owned: PathBuf = borrowed.to_path_buf();

    // Functions should accept &Path or impl AsRef<Path>
    print_info(p);
    print_info(&pb);
    print_info("/etc/hosts"); // &str implements AsRef<Path>
}

fn print_info(path: impl AsRef<Path>) {
    let path = path.as_ref();
    println!("  file_name: {:?}", path.file_name());
    println!("  extension: {:?}", path.extension());
    println!("  parent:    {:?}", path.parent());
    println!("  is_absolute: {}", path.is_absolute());
}
```

Accept `impl AsRef<Path>` in your function signatures. This lets callers pass `&str`, `String`, `&Path`, or `PathBuf` — whatever they have. It's the idiomatic approach and eliminates a ton of conversion boilerplate.

## Path Manipulation

Building and dissecting paths without string hacking:

```rust
use std::path::{Path, PathBuf};

fn main() {
    // join() is the safe way to combine paths
    let base = Path::new("/var/log");
    let full = base.join("myapp").join("server.log");
    println!("Joined: {}", full.display());
    // /var/log/myapp/server.log

    // Components — iterate over path segments
    let path = Path::new("/home/user/projects/rust/src/main.rs");
    for component in path.components() {
        println!("  {:?}", component);
    }

    // Ancestors — walk up the directory tree
    println!("\nAncestors:");
    for ancestor in path.ancestors() {
        println!("  {}", ancestor.display());
    }

    // strip_prefix — make a path relative
    let abs = Path::new("/home/user/projects/rust/src/main.rs");
    let base = Path::new("/home/user/projects");
    if let Ok(relative) = abs.strip_prefix(base) {
        println!("\nRelative: {}", relative.display());
        // rust/src/main.rs
    }

    // with_extension and with_file_name
    let source = Path::new("src/parser.rs");
    let test = source.with_file_name("parser_test.rs");
    let compiled = source.with_extension("o");
    println!("\nTest file: {}", test.display());
    println!("Object file: {}", compiled.display());

    // file_stem — filename without extension
    let p = Path::new("archive.tar.gz");
    println!("\nStem: {:?}", p.file_stem());       // "archive.tar"
    println!("Extension: {:?}", p.extension());     // "gz"
}
```

## Reading and Writing Files

The `std::fs` module provides high-level convenience functions and lower-level `File` operations.

```rust
use std::fs;
use std::io;

fn main() -> io::Result<()> {
    // One-shot read/write — the simplest API
    fs::write("/tmp/hello.txt", "Hello, filesystem!")?;
    let contents = fs::read_to_string("/tmp/hello.txt")?;
    println!("Read: {contents}");

    // Binary read/write
    let data: Vec<u8> = (0..256).map(|b| b as u8).collect();
    fs::write("/tmp/binary.dat", &data)?;
    let read_back = fs::read("/tmp/binary.dat")?;
    assert_eq!(data, read_back);

    // These convenience functions read/write the ENTIRE file at once.
    // For large files or streaming, use File + BufReader/BufWriter
    // (covered in the std::io lesson)

    Ok(())
}
```

### OpenOptions — Fine-Grained Control

When you need more control than `File::open()` (read-only) or `File::create()` (write, truncate):

```rust
use std::fs::OpenOptions;
use std::io::{self, Write};

fn main() -> io::Result<()> {
    // Append to a file
    let mut log = OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/app.log")?;

    writeln!(log, "Application started")?;
    writeln!(log, "Processing request...")?;

    // Read and write
    let _file = OpenOptions::new()
        .read(true)
        .write(true)
        .open("/tmp/hello.txt")?;

    // Create only if it doesn't exist (atomic check)
    match OpenOptions::new()
        .write(true)
        .create_new(true) // Fails if file exists
        .open("/tmp/lockfile")
    {
        Ok(_) => println!("Lock acquired"),
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
            println!("Another process holds the lock");
        }
        Err(e) => return Err(e),
    }

    Ok(())
}
```

`create_new(true)` is underappreciated. It's an atomic "create if not exists" — the check and creation happen in a single system call. This is how you implement file-based locks without race conditions.

## Directory Operations

```rust
use std::fs;
use std::io;
use std::path::Path;

fn main() -> io::Result<()> {
    // Create a directory
    fs::create_dir("/tmp/myapp_test")?;

    // Create nested directories (like mkdir -p)
    fs::create_dir_all("/tmp/myapp_test/data/cache/thumbnails")?;

    // Read directory contents
    println!("Contents of /tmp/myapp_test:");
    for entry in fs::read_dir("/tmp/myapp_test")? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let kind = if file_type.is_dir() { "DIR " } else { "FILE" };
        println!("  {kind} {}", entry.file_name().to_string_lossy());
    }

    // Create some test files
    fs::write("/tmp/myapp_test/config.toml", "key = \"value\"")?;
    fs::write("/tmp/myapp_test/data/records.json", "[]")?;

    // Check existence and type
    let path = Path::new("/tmp/myapp_test/config.toml");
    println!("\n{} exists: {}", path.display(), path.exists());
    println!("  is_file: {}", path.is_file());
    println!("  is_dir: {}", path.is_dir());

    // Metadata
    let meta = fs::metadata(path)?;
    println!("  size: {} bytes", meta.len());
    println!("  readonly: {}", meta.permissions().readonly());
    if let Ok(modified) = meta.modified() {
        println!("  modified: {modified:?}");
    }

    // Clean up
    fs::remove_dir_all("/tmp/myapp_test")?;

    Ok(())
}
```

Note that `read_dir()` returns an iterator of `Result<DirEntry>` — each entry can independently fail (e.g., if a file is deleted between listing and accessing). Always handle both the outer `Result` (from `read_dir` itself) and the inner `Result` (from each entry).

## Recursive Directory Walking

The standard library's `read_dir` is non-recursive. You build recursive traversal yourself:

```rust
use std::fs;
use std::io;
use std::path::Path;

fn walk_dir(dir: &Path, depth: usize) -> io::Result<()> {
    if dir.is_dir() {
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            let indent = "  ".repeat(depth);

            if path.is_dir() {
                println!("{indent}{}/", entry.file_name().to_string_lossy());
                walk_dir(&path, depth + 1)?;
            } else {
                let size = entry.metadata()?.len();
                println!(
                    "{indent}{} ({} bytes)",
                    entry.file_name().to_string_lossy(),
                    size
                );
            }
        }
    }
    Ok(())
}

fn find_files_by_extension(dir: &Path, ext: &str) -> io::Result<Vec<std::path::PathBuf>> {
    let mut results = Vec::new();

    fn recurse(dir: &Path, ext: &str, results: &mut Vec<std::path::PathBuf>) -> io::Result<()> {
        if dir.is_dir() {
            for entry in fs::read_dir(dir)? {
                let entry = entry?;
                let path = entry.path();
                if path.is_dir() {
                    recurse(&path, ext, results)?;
                } else if path.extension().and_then(|e| e.to_str()) == Some(ext) {
                    results.push(path);
                }
            }
        }
        Ok(())
    }

    recurse(dir, ext, &mut results)?;
    Ok(results)
}

fn main() -> io::Result<()> {
    // Setup test directory
    fs::create_dir_all("/tmp/walktest/src/utils")?;
    fs::write("/tmp/walktest/Cargo.toml", "")?;
    fs::write("/tmp/walktest/src/main.rs", "")?;
    fs::write("/tmp/walktest/src/lib.rs", "")?;
    fs::write("/tmp/walktest/src/utils/helpers.rs", "")?;
    fs::write("/tmp/walktest/src/utils/mod.rs", "")?;

    println!("Directory tree:");
    walk_dir(Path::new("/tmp/walktest"), 0)?;

    println!("\nRust files:");
    let rs_files = find_files_by_extension(Path::new("/tmp/walktest"), "rs")?;
    for f in &rs_files {
        println!("  {}", f.display());
    }

    // Clean up
    fs::remove_dir_all("/tmp/walktest")?;

    Ok(())
}
```

For production code, consider the `walkdir` crate — it handles symlink loops, permission errors, and sorting. But understanding how to build it yourself is worth the exercise.

## File Copying, Moving, and Renaming

```rust
use std::fs;
use std::io;

fn main() -> io::Result<()> {
    // Setup
    fs::write("/tmp/original.txt", "important data")?;

    // Copy a file
    let bytes_copied = fs::copy("/tmp/original.txt", "/tmp/backup.txt")?;
    println!("Copied {bytes_copied} bytes");

    // Rename/move a file (atomic on the same filesystem)
    fs::rename("/tmp/backup.txt", "/tmp/moved.txt")?;

    // Verify
    let content = fs::read_to_string("/tmp/moved.txt")?;
    println!("Moved file contains: {content}");

    // Remove files
    fs::remove_file("/tmp/original.txt")?;
    fs::remove_file("/tmp/moved.txt")?;

    Ok(())
}
```

`fs::rename` is atomic on the same filesystem — the file either moves completely or not at all. This is useful for safe file updates: write to a temp file, then rename it over the target. If the rename fails, the original file is untouched.

```rust
use std::fs;
use std::io::{self, Write, BufWriter};

fn atomic_write(path: &str, content: &str) -> io::Result<()> {
    let tmp_path = format!("{path}.tmp");

    // Write to temp file
    {
        let file = fs::File::create(&tmp_path)?;
        let mut writer = BufWriter::new(file);
        writer.write_all(content.as_bytes())?;
        writer.flush()?;
    } // File is closed here

    // Atomic rename
    fs::rename(&tmp_path, path)?;
    Ok(())
}

fn main() -> io::Result<()> {
    atomic_write("/tmp/config.json", r#"{"version": 2, "debug": false}"#)?;
    println!("Config written atomically");

    let content = fs::read_to_string("/tmp/config.json")?;
    println!("Content: {content}");

    fs::remove_file("/tmp/config.json")?;

    Ok(())
}
```

## Symlinks and Metadata

```rust
use std::fs;
use std::io;
use std::path::Path;

fn main() -> io::Result<()> {
    fs::write("/tmp/real_file.txt", "I'm the real file")?;

    // Create a symlink (Unix)
    #[cfg(unix)]
    {
        // Remove if exists from a previous run
        let _ = fs::remove_file("/tmp/link_to_file.txt");
        std::os::unix::fs::symlink("/tmp/real_file.txt", "/tmp/link_to_file.txt")?;

        let link_path = Path::new("/tmp/link_to_file.txt");

        // metadata() follows symlinks
        let meta = fs::metadata(link_path)?;
        println!("metadata (follows link): {} bytes", meta.len());

        // symlink_metadata() does NOT follow symlinks
        let link_meta = fs::symlink_metadata(link_path)?;
        println!("symlink_metadata: is_symlink = {}", link_meta.is_symlink());

        // canonicalize resolves symlinks and relative paths
        let canonical = fs::canonicalize(link_path)?;
        println!("Canonical: {}", canonical.display());

        // Read the symlink target
        let target = fs::read_link(link_path)?;
        println!("Link target: {}", target.display());

        fs::remove_file("/tmp/link_to_file.txt")?;
    }

    fs::remove_file("/tmp/real_file.txt")?;

    Ok(())
}
```

## Temporary Files and Directories

The standard library doesn't have a temp file API, but the pattern is straightforward:

```rust
use std::fs;
use std::io;
use std::path::PathBuf;

fn temp_dir_with_prefix(prefix: &str) -> io::Result<PathBuf> {
    let mut path = std::env::temp_dir();
    path.push(format!(
        "{prefix}-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&path)?;
    Ok(path)
}

fn main() -> io::Result<()> {
    let tmp = temp_dir_with_prefix("myapp")?;
    println!("Temp dir: {}", tmp.display());

    // Use it
    fs::write(tmp.join("data.txt"), "temporary data")?;

    // Clean up
    fs::remove_dir_all(&tmp)?;

    Ok(())
}
```

For production, use the `tempfile` crate — it handles cleanup automatically via RAII and avoids race conditions in temp file creation.

## Common Pitfalls

**TOCTOU races.** Checking `path.exists()` and then opening the file is a race condition — another process could delete the file between your check and your open. Instead, just try the operation and handle the error:

```rust
use std::fs;
use std::io;

fn main() -> io::Result<()> {
    // Bad: TOCTOU race
    // if Path::new("/tmp/data.txt").exists() {
    //     let contents = fs::read_to_string("/tmp/data.txt")?; // might fail!
    // }

    // Good: just try it
    match fs::read_to_string("/tmp/data.txt") {
        Ok(contents) => println!("Got: {contents}"),
        Err(e) if e.kind() == io::ErrorKind::NotFound => {
            println!("File doesn't exist — creating it");
            fs::write("/tmp/data.txt", "default")?;
        }
        Err(e) => return Err(e),
    }

    fs::remove_file("/tmp/data.txt")?;
    Ok(())
}
```

**Path encoding.** Not all filenames are valid UTF-8, especially on Unix. That's why `Path` methods return `OsStr` instead of `&str`. Use `to_string_lossy()` for display, but be aware it replaces invalid UTF-8 with the replacement character.

**Cross-platform paths.** Use `Path::join()` and `std::path::MAIN_SEPARATOR` instead of hardcoding `/` or `\`. Your future self (or your Windows users) will thank you.

The filesystem is one of those areas where "works on my machine" is the default and "works everywhere" requires intentional effort. Rust's type system pushes you toward correct code, but you still have to meet it halfway.
