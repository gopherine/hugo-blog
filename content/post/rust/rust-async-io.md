---
title: "Lesson 12: Async I/O — Files, sockets, DNS"
author: Atharva Pandey
series: "Async Rust Masterclass"
lesson: 12
keywords: [Rust, rust, async, tokio, io, files, sockets, tcp, udp, dns, AsyncRead, AsyncWrite]
tags: [Rust tutorial, rust, async, tokio]
date: '2025-01-28T08:55:13.000Z'
---

Here's an uncomfortable truth about async file I/O: on most operating systems, it doesn't really exist. When you call `tokio::fs::read_to_string`, Tokio dispatches the operation to a thread pool because Linux's file I/O isn't truly asynchronous (yes, there's io_uring, but Tokio doesn't use it by default). Network I/O, on the other hand, is genuinely async through epoll/kqueue.

Understanding this distinction matters. It changes how you architect things.

## The Async I/O Traits

Tokio defines two core traits that mirror their std counterparts:

```rust
// Simplified — the real versions use Pin
trait AsyncRead {
    fn poll_read(self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &mut ReadBuf<'_>)
        -> Poll<io::Result<()>>;
}

trait AsyncWrite {
    fn poll_write(self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &[u8])
        -> Poll<io::Result<usize>>;
    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>)
        -> Poll<io::Result<()>>;
    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>)
        -> Poll<io::Result<()>>;
}
```

You'll rarely implement these directly. Instead, you'll use the extension traits `AsyncReadExt` and `AsyncWriteExt` which give you the ergonomic methods:

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
```

## TCP — The Bread and Butter

### TCP Server

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:8080").await?;
    println!("Server listening on 127.0.0.1:8080");

    loop {
        let (mut socket, addr) = listener.accept().await?;
        println!("Connection from {addr}");

        tokio::spawn(async move {
            let mut buf = vec![0u8; 4096];

            loop {
                let n = match socket.read(&mut buf).await {
                    Ok(0) => return, // Connection closed
                    Ok(n) => n,
                    Err(e) => {
                        eprintln!("Read error: {e}");
                        return;
                    }
                };

                // Echo back with a prefix
                let response = format!("Echo: {}", String::from_utf8_lossy(&buf[..n]));
                if let Err(e) = socket.write_all(response.as_bytes()).await {
                    eprintln!("Write error: {e}");
                    return;
                }
            }
        });
    }
}
```

### TCP Client

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut stream = TcpStream::connect("127.0.0.1:8080").await?;
    println!("Connected to server");

    // Send a message
    stream.write_all(b"Hello, server!").await?;

    // Read the response
    let mut buf = vec![0u8; 4096];
    let n = stream.read(&mut buf).await?;
    println!("Response: {}", String::from_utf8_lossy(&buf[..n]));

    Ok(())
}
```

### Split Read/Write

Sometimes you need to read and write concurrently on the same connection:

```rust
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;

async fn bidirectional_chat(stream: TcpStream) -> Result<(), Box<dyn std::error::Error>> {
    let (reader, mut writer) = stream.into_split();
    let mut reader = BufReader::new(reader);

    // Read task
    let read_handle = tokio::spawn(async move {
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line).await {
                Ok(0) => break,
                Ok(_) => println!("Received: {}", line.trim()),
                Err(e) => {
                    eprintln!("Read error: {e}");
                    break;
                }
            }
        }
    });

    // Write task
    let write_handle = tokio::spawn(async move {
        for i in 0..5 {
            writer.write_all(format!("Message {i}\n").as_bytes()).await.unwrap();
            tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
        }
    });

    tokio::try_join!(read_handle, write_handle)?;
    Ok(())
}
```

## UDP

```rust
use tokio::net::UdpSocket;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Server
    let server = UdpSocket::bind("127.0.0.1:9090").await?;
    println!("UDP server on 127.0.0.1:9090");

    // Client
    let client = UdpSocket::bind("127.0.0.1:0").await?;
    client.connect("127.0.0.1:9090").await?;

    // Send from client
    client.send(b"hello UDP").await?;

    // Receive on server
    let mut buf = [0u8; 1024];
    let (len, addr) = server.recv_from(&mut buf).await?;
    println!("Server got {} bytes from {}: {}",
        len, addr, String::from_utf8_lossy(&buf[..len]));

    // Reply
    server.send_to(b"got it!", addr).await?;

    // Client receives reply
    let len = client.recv(&mut buf).await?;
    println!("Client got reply: {}", String::from_utf8_lossy(&buf[..len]));

    Ok(())
}
```

## DNS Resolution

Tokio provides async DNS through `tokio::net::lookup_host`:

```rust
use tokio::net::lookup_host;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let addrs: Vec<_> = lookup_host("example.com:80").await?.collect();
    println!("example.com resolves to:");
    for addr in &addrs {
        println!("  {addr}");
    }

    // Or connect directly — TcpStream::connect does DNS resolution
    // let stream = TcpStream::connect("example.com:80").await?;

    Ok(())
}
```

## File I/O — The "Fake" Async

As I mentioned, file I/O on most platforms goes through `spawn_blocking`:

```rust
use tokio::fs;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Write a file
    fs::write("test.txt", "Hello, async file I/O!").await?;

    // Read a file
    let contents = fs::read_to_string("test.txt").await?;
    println!("Contents: {contents}");

    // More control with File
    let mut file = fs::File::create("output.txt").await?;
    file.write_all(b"Line 1\n").await?;
    file.write_all(b"Line 2\n").await?;
    file.flush().await?;

    // Read with buffering
    let file = fs::File::open("output.txt").await?;
    let mut reader = tokio::io::BufReader::new(file);
    let mut line = String::new();
    while reader.read_line(&mut line).await? > 0 {
        print!("Read: {line}");
        line.clear();
    }

    // Cleanup
    fs::remove_file("test.txt").await?;
    fs::remove_file("output.txt").await?;

    Ok(())
}

use tokio::io::AsyncBufReadExt;
```

### When to Use tokio::fs vs spawn_blocking + std::fs

```rust
use tokio::task;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // For simple operations, tokio::fs is fine
    let data = tokio::fs::read_to_string("Cargo.toml").await?;
    println!("Cargo.toml is {} bytes", data.len());

    // For complex file operations, spawn_blocking gives you more control
    let result = task::spawn_blocking(|| -> Result<Vec<String>, std::io::Error> {
        use std::io::BufRead;
        let file = std::fs::File::open("Cargo.toml")?;
        let reader = std::io::BufReader::new(file);
        let lines: Vec<String> = reader
            .lines()
            .filter_map(|l| l.ok())
            .filter(|l| l.contains("tokio"))
            .collect();
        Ok(lines)
    }).await??;

    println!("Lines containing 'tokio': {result:?}");

    Ok(())
}
```

## Buffered I/O

Unbuffered reads and writes are expensive — each one is a system call. Buffer them:

```rust
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, BufWriter};
use tokio::net::{TcpListener, TcpStream};

async fn handle_client(stream: TcpStream) {
    let (reader, writer) = stream.into_split();
    let mut reader = BufReader::new(reader);
    let mut writer = BufWriter::new(writer);

    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line).await {
            Ok(0) => break,
            Ok(_) => {
                // Process the line
                let response = format!("Processed: {}", line.trim());
                writer.write_all(response.as_bytes()).await.unwrap();
                writer.write_all(b"\n").await.unwrap();
                writer.flush().await.unwrap(); // Don't forget to flush!
            }
            Err(e) => {
                eprintln!("Error: {e}");
                break;
            }
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:8080").await?;
    loop {
        let (stream, _) = listener.accept().await?;
        tokio::spawn(handle_client(stream));
    }
}
```

## Copy — Piping Streams Together

`tokio::io::copy` is incredibly useful for proxies:

```rust
use tokio::io;
use tokio::net::{TcpListener, TcpStream};

async fn proxy(client: TcpStream, upstream_addr: &str) -> io::Result<()> {
    let upstream = TcpStream::connect(upstream_addr).await?;

    let (client_read, client_write) = client.into_split();
    let (upstream_read, upstream_write) = upstream.into_split();

    // Copy data in both directions simultaneously
    let client_to_upstream = io::copy(&mut client_read.compat(), &mut upstream_write.compat());
    let upstream_to_client = io::copy(&mut upstream_read.compat(), &mut client_write.compat());

    // Actually, let's use the simpler approach
    let (mut cr, mut cw) = tokio::io::split(client);
    let (mut ur, mut uw) = tokio::io::split(upstream);

    let c2u = tokio::io::copy(&mut cr, &mut uw);
    let u2c = tokio::io::copy(&mut ur, &mut cw);

    tokio::select! {
        r = c2u => r?,
        r = u2c => r?,
    };

    Ok(())
}
```

## Practical: A Simple HTTP-like Protocol

```rust
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:8080").await?;
    println!("Listening on 127.0.0.1:8080");

    loop {
        let (stream, addr) = listener.accept().await?;

        tokio::spawn(async move {
            let (reader, mut writer) = stream.into_split();
            let mut reader = BufReader::new(reader);
            let mut request_line = String::new();

            // Read the request line
            if reader.read_line(&mut request_line).await.is_err() {
                return;
            }

            let request_line = request_line.trim();
            println!("[{addr}] {request_line}");

            // Skip headers (read until empty line)
            let mut header = String::new();
            loop {
                header.clear();
                if reader.read_line(&mut header).await.is_err() {
                    return;
                }
                if header.trim().is_empty() {
                    break;
                }
            }

            // Send response
            let body = format!("Hello from async Rust!\nYou requested: {request_line}\n");
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nContent-Type: text/plain\r\n\r\n{}",
                body.len(),
                body
            );

            let _ = writer.write_all(response.as_bytes()).await;
        });
    }
}
```

## Key Takeaways for I/O Architecture

1. **Network I/O is truly async** — use Tokio's TCP/UDP types directly
2. **File I/O is faked** — it runs on a thread pool, so don't expect it to scale like network I/O
3. **Always buffer** — `BufReader` and `BufWriter` are your friends
4. **Split for bidirectional** — use `into_split()` when you need concurrent read/write
5. **Remember cancellation safety** — `read()` and `read_exact()` are NOT cancellation-safe in `select!`

The I/O layer is where async Rust really shines for network services. A single Tokio runtime can handle tens of thousands of concurrent connections with minimal memory overhead. But you need to be aware of the async/sync boundary, especially for file operations.

Next: building HTTP clients with reqwest — because raw TCP is fun, but you probably want to talk HTTP.
