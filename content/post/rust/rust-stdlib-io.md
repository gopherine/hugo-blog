---
title: "Lesson 3: std::io — Read, Write, BufRead, Seek"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 3
keywords: [Rust, rust, standard library, io, Read, Write, BufRead, Seek, buffered io]
tags: [Rust tutorial, rust, stdlib]
date: '2024-09-19T08:30:00.000Z'
---

Early in my Rust journey, I wrote a log parser that read a 2GB file byte by byte using `read()` without buffering. It took forty minutes. Adding a `BufReader` wrapper — one line of code — brought it down to three seconds. That's when I learned that understanding `std::io` isn't optional.

## The Four Core Traits

Rust's I/O system is built on four traits. Everything — files, network sockets, stdin, pipes, in-memory buffers — implements some combination of these:

- **`Read`** — pull bytes from a source
- **`Write`** — push bytes to a destination
- **`BufRead`** — buffered reading with line-oriented methods
- **`Seek`** — jump to arbitrary positions

These traits are the abstraction boundary. If your function accepts `impl Read`, it works with files, network streams, byte slices, or anything else that produces bytes. That's enormously powerful for testing and composability.

## Read

The `Read` trait gives you raw byte-level input:

```rust
use std::io::{self, Read};
use std::fs::File;

fn count_bytes(mut source: impl Read) -> io::Result<usize> {
    let mut buffer = Vec::new();
    let bytes_read = source.read_to_end(&mut buffer)?;
    Ok(bytes_read)
}

fn main() -> io::Result<()> {
    // Works with files
    let file = File::open("Cargo.toml")?;
    println!("Cargo.toml: {} bytes", count_bytes(file)?);

    // Works with byte slices — great for testing
    let data = b"hello world";
    println!("Slice: {} bytes", count_bytes(&data[..])?);

    // Works with stdin
    // println!("stdin: {} bytes", count_bytes(io::stdin())?);

    Ok(())
}
```

The key methods on `Read`:

```rust
use std::io::{self, Read};

fn main() -> io::Result<()> {
    let data: &[u8] = b"Hello, Rust I/O world!";
    let mut reader = data;

    // read() fills a buffer, returns how many bytes were read
    // It might read FEWER bytes than the buffer size — always check
    let mut buf = [0u8; 8];
    let n = reader.read(&mut buf)?;
    println!("Read {n} bytes: {:?}", std::str::from_utf8(&buf[..n]).unwrap());

    // read_exact() reads exactly N bytes or returns an error
    let mut exact_buf = [0u8; 5];
    reader.read_exact(&mut exact_buf)?;
    println!("Exact: {:?}", std::str::from_utf8(&exact_buf).unwrap());

    // read_to_string() reads everything into a String
    let mut rest = String::new();
    reader.read_to_string(&mut rest)?;
    println!("Rest: {rest:?}");

    // read_to_end() reads everything into a Vec<u8>
    let binary_data: &[u8] = &[0xFF, 0x00, 0xAB, 0xCD];
    let mut reader2 = binary_data;
    let mut buf = Vec::new();
    reader2.read_to_end(&mut buf)?;
    println!("Binary: {buf:?}");

    Ok(())
}
```

The critical thing about `read()`: it can return fewer bytes than you asked for. This isn't an error — it's normal behavior. A network socket might have only 100 bytes available when you asked for 4096. Always check the return value and loop if you need all the bytes. Or just use `read_exact()` when you know exactly how many bytes you need.

## Write

`Write` is the mirror image of `Read`:

```rust
use std::io::{self, Write};
use std::fs::File;

fn write_report(mut dest: impl Write, title: &str, items: &[(&str, f64)]) -> io::Result<()> {
    writeln!(dest, "=== {title} ===")?;
    writeln!(dest)?;

    for (name, value) in items {
        writeln!(dest, "  {name:<20} {value:>8.2}")?;
    }

    writeln!(dest)?;
    writeln!(dest, "Total: {:>20.2}", items.iter().map(|(_, v)| v).sum::<f64>())?;

    dest.flush()?; // Don't forget this
    Ok(())
}

fn main() -> io::Result<()> {
    let items = vec![
        ("Widget A", 29.99),
        ("Widget B", 49.50),
        ("Shipping", 5.99),
    ];

    // Write to stdout
    write_report(io::stdout(), "Invoice", &items)?;

    // Write to a file
    let file = File::create("/tmp/report.txt")?;
    write_report(file, "Invoice", &items)?;

    // Write to an in-memory buffer
    let mut buffer: Vec<u8> = Vec::new();
    write_report(&mut buffer, "Invoice", &items)?;
    println!("\nBuffer contents:\n{}", String::from_utf8(buffer).unwrap());

    Ok(())
}
```

Two things people forget:

1. **`flush()`** — buffered writers don't send data immediately. If your program exits without flushing, you might lose the last chunk of output. Always flush when you're done writing.

2. **`write!` and `writeln!` macros** — these are like `print!` and `println!` but for any `Write` implementor. They return `io::Result`, so use `?` to propagate errors.

## BufRead — The Performance Layer

Raw `Read` does a system call for every `read()`. System calls are expensive — context switch to the kernel, copy data, context switch back. `BufReader` wraps any `Read` and adds an internal buffer (default 8KB), so you make far fewer system calls.

```rust
use std::io::{self, BufRead, BufReader};
use std::fs::File;

fn main() -> io::Result<()> {
    // BufReader wraps any Read
    let file = File::open("Cargo.toml")?;
    let reader = BufReader::new(file);

    // BufRead gives you line-oriented reading
    for (i, line) in reader.lines().enumerate() {
        let line = line?;
        println!("{:>4}: {line}", i + 1);
    }

    Ok(())
}
```

`BufRead` provides methods that raw `Read` doesn't:

```rust
use std::io::{self, BufRead, BufReader};

fn main() -> io::Result<()> {
    let data = b"line one\nline two\nline three\npartial";
    let mut reader = BufReader::new(&data[..]);

    // lines() splits on \n, strips the newline, returns Result<String>
    // (Already shown above — most common method)

    // read_line() reads one line into a reusable String buffer
    // More efficient than lines() when you want to reuse allocations
    let data2 = b"first\nsecond\nthird\n";
    let mut reader2 = BufReader::new(&data2[..]);
    let mut line_buf = String::new();

    loop {
        line_buf.clear(); // Reuse the allocation
        let bytes_read = reader2.read_line(&mut line_buf)?;
        if bytes_read == 0 {
            break; // EOF
        }
        print!("Got: {line_buf}"); // Includes the trailing \n
    }

    // split() splits on any byte delimiter
    let csv_data = b"alice,bob,charlie,diana";
    let reader3 = BufReader::new(&csv_data[..]);
    let fields: Vec<String> = reader3.split(b',')
        .map(|r| String::from_utf8(r.unwrap()).unwrap())
        .collect();
    println!("Fields: {fields:?}");

    Ok(())
}
```

### When to Use BufReader vs. read_to_string

If the file fits in memory and you need all of it, `read_to_string()` is fine — it's simple and clear. Use `BufReader` when:

- The file is too large to fit in memory
- You're processing line by line
- You want to stop reading early (e.g., searching for a pattern)
- You're reading from a stream that produces data gradually (network, pipe)

```rust
use std::io::{self, BufRead, BufReader, Read};
use std::fs::File;

// Fine for small files
fn small_file_approach(path: &str) -> io::Result<String> {
    let mut contents = String::new();
    File::open(path)?.read_to_string(&mut contents)?;
    Ok(contents)
}

// Better for large files or early termination
fn find_in_file(path: &str, needle: &str) -> io::Result<Option<(usize, String)>> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);

    for (line_num, line) in reader.lines().enumerate() {
        let line = line?;
        if line.contains(needle) {
            return Ok(Some((line_num + 1, line)));
        }
    }
    Ok(None)
}

fn main() -> io::Result<()> {
    if let Some((line, content)) = find_in_file("Cargo.toml", "name")? {
        println!("Found at line {line}: {content}");
    }
    Ok(())
}
```

## BufWriter — The Write Counterpart

Just as `BufReader` reduces read system calls, `BufWriter` batches writes:

```rust
use std::io::{self, BufWriter, Write};
use std::fs::File;

fn main() -> io::Result<()> {
    // Without BufWriter: each writeln! is a separate system call
    // With BufWriter: writes are batched into 8KB chunks
    let file = File::create("/tmp/output.txt")?;
    let mut writer = BufWriter::new(file);

    for i in 0..10_000 {
        writeln!(writer, "Line {i}: some data here")?;
    }

    // BufWriter flushes automatically when dropped,
    // but explicit flush lets you handle errors
    writer.flush()?;

    // Custom buffer size
    let file2 = File::create("/tmp/output2.txt")?;
    let mut writer2 = BufWriter::with_capacity(64 * 1024, file2); // 64KB buffer

    for i in 0..10_000 {
        writeln!(writer2, "Line {i}")?;
    }
    writer2.flush()?;

    Ok(())
}
```

One subtlety: `BufWriter` flushes when it's dropped, but if that flush fails, the error is silently ignored. If you care about write errors (and you should), always call `flush()` explicitly before the `BufWriter` goes out of scope.

## Seek — Random Access

`Seek` lets you jump to arbitrary positions in a stream. Files support this, network sockets don't.

```rust
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::fs::File;

fn main() -> io::Result<()> {
    // Create a file with known content
    {
        let mut file = File::create("/tmp/seektest.bin")?;
        file.write_all(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")?;
    }

    let mut file = File::open("/tmp/seektest.bin")?;

    // SeekFrom::Start — absolute position from beginning
    file.seek(SeekFrom::Start(10))?;
    let mut buf = [0u8; 5];
    file.read_exact(&mut buf)?;
    println!("At offset 10: {}", std::str::from_utf8(&buf).unwrap()); // KLMNO

    // SeekFrom::Current — relative to current position
    file.seek(SeekFrom::Current(-5))?; // Go back 5
    file.read_exact(&mut buf)?;
    println!("Back 5: {}", std::str::from_utf8(&buf).unwrap()); // KLMNO again

    // SeekFrom::End — relative to the end
    file.seek(SeekFrom::End(-3))?;
    let mut rest = String::new();
    file.read_to_string(&mut rest)?;
    println!("Last 3: {rest}"); // XYZ

    // Get current position
    file.seek(SeekFrom::Start(0))?;
    let pos = file.stream_position()?;
    println!("Current position: {pos}");

    Ok(())
}
```

## Cursor — In-Memory Seek

`Cursor<T>` wraps a byte buffer and implements `Read`, `Write`, `BufRead`, and `Seek`. It's invaluable for testing code that expects seekable I/O.

```rust
use std::io::{self, Cursor, Read, Seek, SeekFrom, Write};

fn process_binary_header(mut reader: impl Read + Seek) -> io::Result<(u32, u32)> {
    // Read a 4-byte magic number
    let mut magic = [0u8; 4];
    reader.read_exact(&mut magic)?;

    // Skip 8 bytes
    reader.seek(SeekFrom::Current(8))?;

    // Read two u32 values
    let mut buf = [0u8; 4];
    reader.read_exact(&mut buf)?;
    let width = u32::from_le_bytes(buf);

    reader.read_exact(&mut buf)?;
    let height = u32::from_le_bytes(buf);

    Ok((width, height))
}

fn main() -> io::Result<()> {
    // Build test data in memory
    let mut data = Vec::new();
    data.extend_from_slice(b"MAGIC"); // magic (4 bytes, we only read 4)
    data.extend_from_slice(&[0u8; 8]); // padding (skip 8)

    // But wait — magic is 5 bytes and we read 4, let me fix that
    let mut data = Vec::new();
    data.extend_from_slice(b"MGIC");           // 4 bytes magic
    data.extend_from_slice(&[0u8; 8]);          // 8 bytes padding
    data.extend_from_slice(&1920u32.to_le_bytes()); // width
    data.extend_from_slice(&1080u32.to_le_bytes()); // height

    let cursor = Cursor::new(data);
    let (w, h) = process_binary_header(cursor)?;
    println!("Dimensions: {w}x{h}");

    Ok(())
}
```

## Chaining Readers

You can combine multiple readers into one with `chain()`:

```rust
use std::io::{self, Read};

fn main() -> io::Result<()> {
    let header = b"HEADER\n" as &[u8];
    let body = b"body content here\n" as &[u8];
    let footer = b"FOOTER\n" as &[u8];

    let mut combined = header.chain(body).chain(footer);
    let mut output = String::new();
    combined.read_to_string(&mut output)?;

    println!("{output}");

    Ok(())
}
```

## Error Handling Patterns

`std::io::Error` has a `kind()` method that returns an `ErrorKind` enum. Use it for intelligent error recovery:

```rust
use std::io::{self, ErrorKind, Read, Write};
use std::fs::File;

fn read_or_create(path: &str, default_content: &str) -> io::Result<String> {
    match File::open(path) {
        Ok(mut file) => {
            let mut contents = String::new();
            file.read_to_string(&mut contents)?;
            Ok(contents)
        }
        Err(e) if e.kind() == ErrorKind::NotFound => {
            // File doesn't exist — create it with defaults
            let mut file = File::create(path)?;
            file.write_all(default_content.as_bytes())?;
            Ok(default_content.to_string())
        }
        Err(e) => Err(e), // Permission denied, etc — propagate
    }
}

fn main() -> io::Result<()> {
    let config = read_or_create("/tmp/app_config.txt", "key=value\ndebug=false\n")?;
    println!("Config:\n{config}");
    Ok(())
}
```

## Copy and Pipe

`io::copy` efficiently transfers bytes between a `Read` and a `Write`:

```rust
use std::io::{self, Read, Write};
use std::fs::File;

fn main() -> io::Result<()> {
    // Copy file contents
    let mut source = File::open("Cargo.toml")?;
    let mut dest = File::create("/tmp/cargo_copy.toml")?;

    let bytes_copied = io::copy(&mut source, &mut dest)?;
    println!("Copied {bytes_copied} bytes");

    // Copy with a size limit using .take()
    let data = b"This is a long string that we only want part of" as &[u8];
    let mut limited = data.take(20);
    let mut output = Vec::new();
    io::copy(&mut limited, &mut output)?;
    println!("Limited: {}", String::from_utf8(output).unwrap());

    Ok(())
}
```

## The Practical Takeaways

Here's my mental checklist for I/O code:

1. **Always buffer.** Wrap files in `BufReader`/`BufWriter`. The only exception is when you're doing one big `read_to_string()` or `write_all()`.

2. **Accept traits, not concrete types.** Write functions that take `impl Read` or `impl Write`, not `File`. Your code becomes testable and composable instantly.

3. **Flush your writers.** Especially before the program exits or before you need the data to be visible to other processes.

4. **Use `?` for error propagation.** Every I/O operation returns `Result`. Don't `unwrap()` in production code.

5. **Use `Cursor` for testing.** Don't create temp files in your tests when `Cursor::new(data)` works just as well.

I/O isn't glamorous, but it's the foundation of every useful program. Get these patterns right and you'll avoid entire categories of bugs — lost data, slow performance, and untestable code.
