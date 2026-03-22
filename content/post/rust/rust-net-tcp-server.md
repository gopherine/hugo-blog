---
title: "Lesson 1: Building a TCP Server from Scratch — Raw sockets"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 1
keywords: [Rust, rust, networking, distributed, TCP, server, sockets, async, tokio]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-12T09:14:00.000Z'
---

Last month I was debugging a flaky microservice at work and realized I couldn't explain what was actually happening between `bind()` and the first byte arriving. I'd been using high-level frameworks for years — Actix, Axum, you name it — but I'd never actually built a TCP server from raw sockets in Rust. That bothered me. So I spent a weekend doing exactly that, and honestly, it changed how I think about every networked service I write.

## Why Start at TCP?

Every HTTP server, every gRPC service, every WebSocket connection — they all sit on top of TCP. When something goes wrong at those higher levels, the debugging trail almost always leads back down to socket behavior. Half-open connections, backpressure, Nagle's algorithm messing with latency — you can't reason about any of it without understanding what's happening at the TCP layer.

Rust makes this especially interesting because the ownership model maps beautifully onto socket lifecycle management. A socket is a resource. It gets created, used, and cleaned up. Sound familiar?

## A Synchronous Echo Server

Let's start with the simplest possible TCP server — one that accepts connections and echoes back whatever it receives. No async runtime, no frameworks, just `std::net`.

```rust
use std::io::{Read, Write};
use std::net::TcpListener;

fn main() {
    let listener = TcpListener::bind("127.0.0.1:8080").expect("Failed to bind");
    println!("Listening on 127.0.0.1:8080");

    for stream in listener.incoming() {
        match stream {
            Ok(mut stream) => {
                let mut buf = [0u8; 1024];
                loop {
                    match stream.read(&mut buf) {
                        Ok(0) => break, // Connection closed
                        Ok(n) => {
                            if stream.write_all(&buf[..n]).is_err() {
                                break;
                            }
                        }
                        Err(e) => {
                            eprintln!("Read error: {e}");
                            break;
                        }
                    }
                }
            }
            Err(e) => eprintln!("Accept error: {e}"),
        }
    }
}
```

This works. You can `telnet 127.0.0.1 8080`, type something, and see it echoed back. But there's a massive problem — it handles one connection at a time. While one client is connected, every other client is stuck waiting in the kernel's accept queue.

## Adding Threads

The classic fix is one thread per connection. Rust's `std::thread::spawn` makes this straightforward, and the borrow checker actually helps — it forces you to `move` the stream into the thread's closure, which is exactly the right ownership semantic.

```rust
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;

fn handle_client(mut stream: TcpStream) {
    let peer = stream.peer_addr().unwrap();
    println!("Client connected: {peer}");

    let mut buf = [0u8; 4096];
    loop {
        match stream.read(&mut buf) {
            Ok(0) => {
                println!("Client disconnected: {peer}");
                break;
            }
            Ok(n) => {
                let received = String::from_utf8_lossy(&buf[..n]);
                println!("From {peer}: {received}");

                // Echo with a prefix
                let response = format!("[echo] {received}");
                if stream.write_all(response.as_bytes()).is_err() {
                    break;
                }
            }
            Err(e) => {
                eprintln!("Error reading from {peer}: {e}");
                break;
            }
        }
    }
}

fn main() {
    let listener = TcpListener::bind("127.0.0.1:8080").expect("Failed to bind");
    println!("Listening on 127.0.0.1:8080");

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                thread::spawn(move || {
                    handle_client(stream);
                });
            }
            Err(e) => eprintln!("Accept error: {e}"),
        }
    }
}
```

This handles concurrent connections, but threads are expensive. Each one takes about 8KB of stack by default (Rust uses smaller stacks than most languages, but it adds up). At 10,000 concurrent connections, you're burning 80MB just on stacks, plus all the context-switching overhead. For a chat server or API gateway, that's a dealbreaker.

## Going Async with Tokio

Here's where Rust's async story shines. Tokio gives you an event-loop-based runtime where thousands of connections share a small pool of OS threads. The API looks almost identical to the synchronous version, which is the whole point.

```toml
# Cargo.toml
[dependencies]
tokio = { version = "1", features = ["full"] }
```

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() {
    let listener = TcpListener::bind("127.0.0.1:8080")
        .await
        .expect("Failed to bind");

    println!("Listening on 127.0.0.1:8080");

    loop {
        let (mut socket, addr) = match listener.accept().await {
            Ok(conn) => conn,
            Err(e) => {
                eprintln!("Accept error: {e}");
                continue;
            }
        };

        tokio::spawn(async move {
            let mut buf = vec![0u8; 4096];
            println!("Connected: {addr}");

            loop {
                match socket.read(&mut buf).await {
                    Ok(0) => {
                        println!("Disconnected: {addr}");
                        break;
                    }
                    Ok(n) => {
                        if socket.write_all(&buf[..n]).await.is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Error from {addr}: {e}");
                        break;
                    }
                }
            }
        });
    }
}
```

Notice that `tokio::spawn` replaces `thread::spawn`, and `.await` replaces blocking calls. Under the hood, each spawned task is a lightweight state machine — a few hundred bytes instead of kilobytes. You can comfortably handle tens of thousands of concurrent connections on a single machine.

## A Real Protocol: Length-Prefixed Messages

Echo servers are great for demos, but real protocols need framing. TCP is a byte stream — there's no concept of "messages." If a client sends two 100-byte messages back to back, you might receive them as one 200-byte chunk, or as three chunks of 67, 83, and 50 bytes. You need a strategy to delimit message boundaries.

The simplest production-ready approach is length-prefixed framing: every message starts with a 4-byte big-endian length, followed by that many bytes of payload.

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

async fn read_frame(stream: &mut TcpStream) -> Result<Option<Vec<u8>>, Box<dyn std::error::Error>> {
    let mut len_buf = [0u8; 4];

    // Read the length prefix
    match stream.read_exact(&mut len_buf).await {
        Ok(_) => {}
        Err(ref e) if e.kind() == std::io::ErrorKind::UnexpectedEof => {
            return Ok(None); // Clean disconnect
        }
        Err(e) => return Err(e.into()),
    }

    let len = u32::from_be_bytes(len_buf) as usize;

    // Sanity check — don't allocate gigabytes for a bogus length
    if len > 16 * 1024 * 1024 {
        return Err("Frame too large".into());
    }

    let mut payload = vec![0u8; len];
    stream.read_exact(&mut payload).await?;

    Ok(Some(payload))
}

async fn write_frame(
    stream: &mut TcpStream,
    data: &[u8],
) -> Result<(), Box<dyn std::error::Error>> {
    let len = (data.len() as u32).to_be_bytes();
    stream.write_all(&len).await?;
    stream.write_all(data).await?;
    Ok(())
}

#[tokio::main]
async fn main() {
    let listener = TcpListener::bind("127.0.0.1:8080")
        .await
        .expect("Failed to bind");

    println!("Framed server on 127.0.0.1:8080");

    loop {
        let (mut socket, addr) = match listener.accept().await {
            Ok(c) => c,
            Err(e) => {
                eprintln!("Accept error: {e}");
                continue;
            }
        };

        tokio::spawn(async move {
            println!("Connected: {addr}");

            loop {
                match read_frame(&mut socket).await {
                    Ok(Some(payload)) => {
                        let msg = String::from_utf8_lossy(&payload);
                        println!("From {addr}: {msg}");

                        let response = format!("Received {} bytes: {msg}", payload.len());
                        if write_frame(&mut socket, response.as_bytes()).await.is_err() {
                            break;
                        }
                    }
                    Ok(None) => {
                        println!("Disconnected: {addr}");
                        break;
                    }
                    Err(e) => {
                        eprintln!("Frame error from {addr}: {e}");
                        break;
                    }
                }
            }
        });
    }
}
```

That max-length check is critical. Without it, a malicious client can send `0xFFFFFFFF` as the length and force your server to try allocating 4GB. I've seen this bug in production code written by people who should know better. Always validate untrusted input, even at the framing layer.

## Using Tokio's Codec Layer

Writing `read_frame` and `write_frame` by hand works, but Tokio has a built-in abstraction for this — the `Framed` adapter with codecs. It handles buffering and partial reads for you.

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
tokio-util = { version = "0.7", features = ["codec"] }
bytes = "1"
```

```rust
use bytes::{Buf, BufMut, BytesMut};
use tokio::net::TcpListener;
use tokio_util::codec::{Decoder, Encoder, Framed};
use futures::stream::StreamExt;
use futures::sink::SinkExt;

struct LengthPrefixCodec;

impl Decoder for LengthPrefixCodec {
    type Item = Vec<u8>;
    type Error = std::io::Error;

    fn decode(&mut self, src: &mut BytesMut) -> Result<Option<Self::Item>, Self::Error> {
        if src.len() < 4 {
            return Ok(None); // Not enough data for the length prefix
        }

        let len = u32::from_be_bytes([src[0], src[1], src[2], src[3]]) as usize;

        if len > 16 * 1024 * 1024 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "frame too large",
            ));
        }

        if src.len() < 4 + len {
            // Reserve space so the next read is efficient
            src.reserve(4 + len - src.len());
            return Ok(None);
        }

        src.advance(4); // Skip the length prefix
        let payload = src.split_to(len).to_vec();
        Ok(Some(payload))
    }
}

impl Encoder<Vec<u8>> for LengthPrefixCodec {
    type Error = std::io::Error;

    fn encode(&mut self, item: Vec<u8>, dst: &mut BytesMut) -> Result<(), Self::Error> {
        dst.put_u32(item.len() as u32);
        dst.extend_from_slice(&item);
        Ok(())
    }
}

#[tokio::main]
async fn main() {
    let listener = TcpListener::bind("127.0.0.1:8080")
        .await
        .expect("Failed to bind");

    println!("Codec server on 127.0.0.1:8080");

    loop {
        let (socket, addr) = match listener.accept().await {
            Ok(c) => c,
            Err(e) => {
                eprintln!("Accept error: {e}");
                continue;
            }
        };

        tokio::spawn(async move {
            let mut framed = Framed::new(socket, LengthPrefixCodec);
            println!("Connected: {addr}");

            while let Some(result) = framed.next().await {
                match result {
                    Ok(payload) => {
                        let msg = String::from_utf8_lossy(&payload);
                        println!("From {addr}: {msg}");

                        let response = format!("Got it: {msg}").into_bytes();
                        if framed.send(response).await.is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Codec error from {addr}: {e}");
                        break;
                    }
                }
            }

            println!("Disconnected: {addr}");
        });
    }
}
```

The codec approach is cleaner because it separates framing concerns from business logic. Your handler just receives complete messages and sends complete messages — no manual buffer management.

## Graceful Shutdown

Production servers need to shut down cleanly. You can't just kill the process — you need to stop accepting new connections, let in-flight requests finish, and then exit. Tokio's `select!` macro makes this elegant.

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::signal;
use tokio::sync::broadcast;

#[tokio::main]
async fn main() {
    let listener = TcpListener::bind("127.0.0.1:8080")
        .await
        .expect("Failed to bind");

    let (shutdown_tx, _) = broadcast::channel::<()>(1);

    println!("Server running. Press Ctrl+C to shut down.");

    loop {
        tokio::select! {
            result = listener.accept() => {
                match result {
                    Ok((mut socket, addr)) => {
                        let mut shutdown_rx = shutdown_tx.subscribe();

                        tokio::spawn(async move {
                            let mut buf = vec![0u8; 4096];

                            loop {
                                tokio::select! {
                                    result = socket.read(&mut buf) => {
                                        match result {
                                            Ok(0) | Err(_) => break,
                                            Ok(n) => {
                                                if socket.write_all(&buf[..n]).await.is_err() {
                                                    break;
                                                }
                                            }
                                        }
                                    }
                                    _ = shutdown_rx.recv() => {
                                        println!("Shutting down connection: {addr}");
                                        let _ = socket.write_all(b"Server shutting down\n").await;
                                        break;
                                    }
                                }
                            }
                        });
                    }
                    Err(e) => eprintln!("Accept error: {e}"),
                }
            }
            _ = signal::ctrl_c() => {
                println!("\nShutdown signal received");
                let _ = shutdown_tx.send(());
                break;
            }
        }
    }

    // Give connections a moment to finish
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    println!("Server stopped.");
}
```

The broadcast channel propagates the shutdown signal to every active connection handler. Each handler can then finish its current work and exit cleanly. This pattern is basically mandatory for any production TCP service.

## Performance Considerations

A few things I've learned the hard way about TCP server performance in Rust:

**Buffer sizing matters.** A 1KB buffer means lots of syscalls for large messages. A 64KB buffer wastes memory when most messages are small. Profile your actual traffic patterns and size accordingly. For most services, 4-8KB is a reasonable default.

**TCP_NODELAY is your friend for low-latency services.** Nagle's algorithm batches small writes together, which adds up to 40ms of latency. If you're building anything interactive, disable it:

```rust
socket.set_nodelay(true)?;
```

**SO_REUSEADDR prevents "address already in use" errors** when restarting your server. The std library doesn't expose this directly, but `socket2` does:

```rust
use socket2::{Domain, Socket, Type};
use std::net::SocketAddr;

let socket = Socket::new(Domain::IPV4, Type::STREAM, None)?;
socket.set_reuse_address(true)?;
let addr: SocketAddr = "127.0.0.1:8080".parse()?;
socket.bind(&addr.into())?;
socket.listen(128)?;

let listener: std::net::TcpListener = socket.into();
listener.set_nonblocking(true)?;
let listener = tokio::net::TcpListener::from_std(listener)?;
```

## What's Next

This is the foundation everything else in this series builds on. HTTP, gRPC, WebSockets — they're all TCP with extra layers of protocol on top. Understanding how bytes flow through sockets, how framing works, and how to manage connection lifecycles gives you the mental model you need to debug anything at the higher levels.

Next up, we'll look at HTTP clients with reqwest and hyper — where TCP meets the protocol that runs the internet.
